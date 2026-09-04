#!/usr/bin/env python3
"""
ROS 2 Impedance Controller Node for Handlebar (Guidao) Rehabilitation System.

Nó corrigido com:
  1. Controlador Stanley com trava de saturação do ângulo alvo de esterçamento (±35°).
  2. Gerador de Setpoint por Impedância com bypass direto ao alvo suavizado quando
     não houver força externa exercida.
  3. Controle de Impedância Real com ação derivativa aplicada sobre a derivada discreta 
     do erro de posição (Kd * de/dt).
  4. Leitura dinâmica de Células de Carga com fallback imediato de torque zero.
"""

import json
import math
import threading
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32, Float32MultiArray, String


class MovingAverage:
    """Filtro de média móvel circular para suavização de sinais."""

    def __init__(self, size: int):
        self.size = max(1, size)
        self.buffer = [0.0] * self.size
        self.index = 0
        self.total = 0.0

    def update(self, new_value: float) -> float:
        self.total -= self.buffer[self.index]
        self.buffer[self.index] = new_value
        self.total += new_value
        self.index = (self.index + 1) % self.size
        return self.total / self.size


class ImpedanceControllerNode(Node):
    def __init__(self):
        super().__init__('impedance_controller_node')

        # --- Parâmetros do Controlador Stanley ---
        self.declare_parameter('k_stanley', 0.3)
        self.declare_parameter('v_soft', 0.5)            # Constante de amaciamento de velocidade (m/s)
        self.declare_parameter('smoothing_alpha', 0.1)   # Fator de suavização do alvo angular (EMA)
        self.declare_parameter('max_steer_deg', 35.0)    # Limite físico de esterçamento do guidão (graus)

        # --- Parâmetros do Modelo de Impedância (I_c, B_c, K_c) ---
        self.declare_parameter('I_c', 0.000001)  # Inércia virtual
        self.declare_parameter('B_c', 0.000001)  # Amortecimento virtual
        self.declare_parameter('K_c', 0.000001)  # Rigidez virtual

        # --- Ganhos do Controlador (Rigidez P+I, Derivativo do Erro Kd) ---
        self.declare_parameter('kp', 130.0)
        self.declare_parameter('ki', 10.0)
        self.declare_parameter('kd', 40.0)        # Aplicado à taxa de variação do erro (de/dt)

        # --- Parâmetros Operacionais e de Hardware ---
        self.declare_parameter('num_load_cells', 6)
        self.declare_parameter('load_cell_timeout_s', 0.2)
        self.declare_parameter('game_state_timeout_s', 0.5)
        self.declare_parameter('sample_time', 0.01)   # Período de amostragem de 10ms
        self.declare_parameter('output_min', -178.5)   # Limite PWM (70% de 255)
        self.declare_parameter('output_max', 178.5)

        self._read_parameters()

        # --- Filtros e Estados Internos ---
        self.force_filter = MovingAverage(250)
        self.setpoint_filter = MovingAverage(50)

        self.theta_target_smoothed = 0.0
        self.setpoint_prev = 0.0
        self.setpoint_prev2 = 0.0
        self.pos_error_prev = 0.0                     # Histórico de erro para derivada discreta
        self.last_control_time = time.monotonic()
        self.integral_sum = 0.0

        self._lock = threading.Lock()

        # Estado do Jogo (Stanley)
        self._lateral_error = 0.0
        self._heading_error_rad = 0.0
        self._vehicle_speed = 1.0
        self._last_game_state_time = None

        # Estado do Encoder
        self._encoder_angle_rad = 0.0
        self._encoder_velocity_rad_s = 0.0

        # Estado das Células de Carga
        self._load_cell_values = {}
        self._load_cell_times = {}

        # --- Publishers ---
        self.pub_command_rich = self.create_publisher(String, '/control/command', 10)
        self.pub_actuator = self.create_publisher(Float32, '/device/actuator_command', 10)

        # --- Subscriptions ---
        self.create_subscription(String, '/game/state', self._on_game_state, 10)
        self.create_subscription(Float32MultiArray, '/device/encoder/raw', self._on_encoder_raw, 10)

        # Inscrição dinâmica nos tópicos de células de carga
        for i in range(1, self.num_load_cells + 1):
            lc_id = f"load_cell_guidao_{i}"
            topic = f"/device/{lc_id}/raw"
            self.create_subscription(
                Float32MultiArray,
                topic,
                self._make_load_cell_callback(lc_id),
                10
            )

        # Loop de controle executado a cada sample_time
        self.create_timer(self.sample_time, self._control_loop)

        self.get_logger().info(
            f"Nó do Controlador de Impedância iniciado. Células ativas: {self.num_load_cells}, "
            f"Período: {self.sample_time}s"
        )

    def _read_parameters(self):
        g = lambda name: self.get_parameter(name).value
        self.k_stanley = g('k_stanley')
        self.v_soft = g('v_soft')
        self.smoothing_alpha = g('smoothing_alpha')
        self.max_steer_deg = g('max_steer_deg')

        self.I_c = g('I_c')
        self.B_c = g('B_c')
        self.K_c = g('K_c')

        self.kp = g('kp')
        self.ki = g('ki')
        self.kd = g('kd')

        self.num_load_cells = max(0, min(6, g('num_load_cells')))
        self.load_cell_timeout_s = g('load_cell_timeout_s')
        self.game_state_timeout_s = g('game_state_timeout_s')
        self.sample_time = g('sample_time')
        self.output_min = g('output_min')
        self.output_max = g('output_max')

    # ------------------------------------------------------------------
    # Callbacks dos Tópicos ROS
    # ------------------------------------------------------------------
    def _on_game_state(self, msg: String):
        try:
            payload = json.loads(msg.data)
            lat_err = float(payload.get('lateral_error', 0.0))
            head_err_deg = float(payload.get('heading_error_deg', 0.0))
            speed = float(payload.get('speed', 1.0))
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            self.get_logger().warn(f"Payload inválido em /game/state: {e}")
            return

        with self._lock:
            self._lateral_error = lat_err
            self._heading_error_rad = math.radians(head_err_deg)
            self._vehicle_speed = max(0.01, speed)
            self._last_game_state_time = time.monotonic()

    def _on_encoder_raw(self, msg: Float32MultiArray):
        if len(msg.data) < 2:
            return
        angle_deg, velocity_deg_s = -msg.data[0], msg.data[1]

        with self._lock:
            self._encoder_angle_rad = math.radians(angle_deg)
            self._encoder_velocity_rad_s = math.radians(velocity_deg_s)

    def _make_load_cell_callback(self, lc_id: str):
        def callback(msg: Float32MultiArray):
            if len(msg.data) > 0:
                with self._lock:
                    self._load_cell_values[lc_id] = float(msg.data[0])
                    self._load_cell_times[lc_id] = time.monotonic()
        return callback

    # ------------------------------------------------------------------
    # Cálculo de Torque Externo com Fallback
    # ------------------------------------------------------------------
    def _compute_external_torque(self, now: float) -> float:
        """Calcula o torque externo medido. Retorna 0.0 se desativado ou sem dados válidos."""
        if self.num_load_cells == 0:
            return 0.0

        valid_readings = {}
        for lc_id in [f"load_cell_guidao_{i}" for i in range(1, self.num_load_cells + 1)]:
            t_last = self._load_cell_times.get(lc_id, 0.0)
            if (now - t_last) <= self.load_cell_timeout_s:
                valid_readings[lc_id] = self._load_cell_values.get(lc_id, 0.0)

        if not valid_readings:
            return 0.0

        s1 = valid_readings.get("load_cell_guidao_1", 0.0)
        s2 = valid_readings.get("load_cell_guidao_2", 0.0)
        s6 = valid_readings.get("load_cell_guidao_6", 0.0)

        load_cell_main = s2
        load_cell_aux1 = -(s6 - 4096.0) if "load_cell_guidao_6" in valid_readings else 0.0
        load_cell_aux2 = -(s1 - 4096.0) if "load_cell_guidao_1" in valid_readings else 0.0

        force_sample = 1.0 * load_cell_main + 0.0 * load_cell_aux1 + 0.0 * load_cell_aux2
        force_filtered = self.force_filter.update(force_sample)

        measured_torque = (force_filtered * 100000.0 / 4096.0) * 5 * 10 * 0.12 * 0.8
        return measured_torque

    # ------------------------------------------------------------------
    # Loop de Controle
    # ------------------------------------------------------------------
    def _control_loop(self):
        now = time.monotonic()
        dt = now - self.last_control_time
        if dt <= 0.0:
            dt = 1e-4
        self.last_control_time = now

        with self._lock:
            lat_err = self._lateral_error
            head_err_rad = self._heading_error_rad
            v_speed = self._vehicle_speed
            last_game_time = self._last_game_state_time
            encoder_angle = self._encoder_angle_rad

        # Trava de Segurança (Watchdog para o jogo)
        is_stale = (last_game_time is None) or ((now - last_game_time) > self.game_state_timeout_s)
        if is_stale:
            self._publish_command(0.0, 0.0, 0.0, encoder_angle, stale=True)
            return

        # 1. Alvo de Direção Dinâmico via Controlador Stanley
        stanley_target_raw = head_err_rad + math.atan2(self.k_stanley * lat_err, v_speed + self.v_soft)

        # Limite físico angular do esterçamento do guidão (radianos)
        max_steer_rad = math.radians(self.max_steer_deg)
        stanley_target_raw = max(-max_steer_rad, min(max_steer_rad, stanley_target_raw))

        # Suavização por Média Móvel Exponencial (EMA)
        self.theta_target_smoothed += self.smoothing_alpha * (stanley_target_raw - self.theta_target_smoothed)

        # 2. Leitura de Células de Carga
        tau_ext = self._compute_external_torque(now)

        # 3. Gerador de Setpoint por Modelo de Impedância Discreto
        if self.num_load_cells == 0 or abs(tau_ext) < 1e-5:
            setpoint = self.theta_target_smoothed
            self.setpoint_prev = setpoint
            self.setpoint_prev2 = setpoint
        else:
            dt2 = dt * dt
            inertia_term = self.I_c / dt2
            damping_term = self.B_c / dt
            denom = inertia_term + damping_term + self.K_c

            if denom <= 1e-9:
                setpoint_raw = self.theta_target_smoothed
            else:
                setpoint_raw = (
                    tau_ext
                    + self.setpoint_prev * (2.0 * inertia_term + damping_term)
                    - self.setpoint_prev2 * inertia_term
                    + self.K_c * self.theta_target_smoothed
                ) / denom

            setpoint = self.setpoint_filter.update(setpoint_raw)
            self.setpoint_prev2 = self.setpoint_prev
            self.setpoint_prev = setpoint

        # 4. Controle PID sobre o Erro de Posição
        pos_error = setpoint - encoder_angle

        # Termo Proporcional e Integral
        self.integral_sum += self.ki * pos_error * dt
        self.integral_sum = max(self.output_min, min(self.output_max, self.integral_sum))
        stiffness_term = self.kp * pos_error + self.integral_sum

        # Termo Derivativo calculado diretamente sobre o Erro de Posição: (e[k] - e[k-1]) / dt
        d_error = (pos_error - self.pos_error_prev) / dt
        self.pos_error_prev = pos_error
        derivative_term = self.kd * d_error

        # Sinal total de atuação
        control_output = stiffness_term + derivative_term
        control_output = max(self.output_min, min(self.output_max, control_output))

        # 5. Publicação de Comandos
        self._publish_command(control_output, setpoint, self.theta_target_smoothed, encoder_angle, stale=False)

    def _publish_command(self, output: float, setpoint: float, target: float, pos: float, stale: bool):
        payload = {
            "guidao_pwm": output,
            "setpoint_rad": setpoint,
            "target_stanley_rad": target,
            "encoder_pos_rad": pos,
            "stale_input": stale
        }
        self.pub_command_rich.publish(String(data=json.dumps(payload)))
        self.pub_actuator.publish(Float32(data=float(output)))


def main(args=None):
    rclpy.init(args=args)
    node = ImpedanceControllerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()