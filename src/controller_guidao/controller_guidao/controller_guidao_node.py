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
import socket
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
        self.declare_parameter('num_load_cells', 0)
        self.declare_parameter('load_cell_timeout_s', 0.2)
        self.declare_parameter('game_state_timeout_s', 0.5)
        self.declare_parameter('sample_time', 0.01)   # Período de amostragem de 10ms
        self.declare_parameter('output_min', -178.5)   # Limite PWM (70% de 255)
        self.declare_parameter('output_max', 178.5)

        # O modo resistivo aplica torque limitado para afastar o guidão da trajetória.
        self.declare_parameter('control_mode', 'assisted')
        self.declare_parameter('resistive_gain', 30.0)
        self.declare_parameter('resistive_output_limit', 35.0)
        self.declare_parameter('resistive_deadband_rad', 0.005)
        self.declare_parameter('resistive_slew_rate', 100.0)

        # --- Modulação opcional por foco EEG ---
        self.declare_parameter('use_eeg_modulation', False)
        self.declare_parameter('eeg_device_id', 'emotiv_eeg')
        self.declare_parameter('eeg_state_timeout_s', 3.0)
        self.declare_parameter('eeg_saturation_min', 0.0)
        self.declare_parameter('eeg_saturation_max', 1.0)

        # --- Modulação opcional por esforço EMG ---
        self.declare_parameter('use_emg_modulation', False)
        self.declare_parameter('telemetry_udp_host', '')
        self.declare_parameter('telemetry_udp_port', 8765)
        self.declare_parameter('emg_device_id', 'noraxon_emg')
        self.declare_parameter('emg_state_timeout_s', 0.5)
        self.declare_parameter('emg_saturation_min', 0.0)
        self.declare_parameter('emg_saturation_max', 1.0)

        self._read_parameters()
        self.control_mode = (
            self._normalize_control_mode(self.get_parameter('control_mode').value) or 'assisted'
        )
        self.resistive_output = 0.0

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

        # Estado EEG recebido pela abstração do HAL.
        self._eeg_focus = None
        self._last_eeg_state_time = None
        self._emg_intensity = None
        self._last_emg_state_time = None

        # --- Publishers ---
        self.pub_command_rich = self.create_publisher(String, '/control/command', 10)
        self.pub_actuator = self.create_publisher(Float32, '/device/actuator_command', 10)

        # --- Subscriptions ---
        self.create_subscription(String, '/game/state', self._on_game_state, 10)
        self.create_subscription(String, '/control/mode', self._on_control_mode, 10)
        self.create_subscription(String, '/hal/device_state', self._on_device_state, 10)
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
        self.resistive_gain = max(0.0, float(g('resistive_gain')))
        self.resistive_output_limit = max(0.0, float(g('resistive_output_limit')))
        self.resistive_deadband_rad = max(0.0, float(g('resistive_deadband_rad')))
        self.resistive_slew_rate = max(0.0, float(g('resistive_slew_rate')))
        self.use_eeg_modulation = bool(g('use_eeg_modulation'))
        self.eeg_device_id = str(g('eeg_device_id'))
        self.eeg_state_timeout_s = max(0.0, g('eeg_state_timeout_s'))
        self.eeg_saturation_min = max(0.0, min(1.0, g('eeg_saturation_min')))
        self.eeg_saturation_max = max(0.0, min(1.0, g('eeg_saturation_max')))
        if self.eeg_saturation_max < self.eeg_saturation_min:
            self.eeg_saturation_min, self.eeg_saturation_max = (
                self.eeg_saturation_max,
                self.eeg_saturation_min,
            )

        self.use_emg_modulation = bool(g('use_emg_modulation'))
        self.telemetry_udp_host = str(g('telemetry_udp_host')).strip()
        self.telemetry_udp_port = int(g('telemetry_udp_port'))
        self._telemetry_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.emg_device_id = str(g('emg_device_id'))
        self.emg_state_timeout_s = max(0.0, g('emg_state_timeout_s'))
        self.emg_saturation_min = max(0.0, min(1.0, g('emg_saturation_min')))
        self.emg_saturation_max = max(0.0, min(1.0, g('emg_saturation_max')))
        if self.emg_saturation_max < self.emg_saturation_min:
            self.emg_saturation_min, self.emg_saturation_max = (
                self.emg_saturation_max,
                self.emg_saturation_min,
            )

    # ------------------------------------------------------------------
    # Callbacks dos Tópicos ROS
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_control_mode(mode):
        mode = str(mode).strip().lower()
        return mode if mode in ('assisted', 'resistive') else None

    def _set_control_mode(self, mode):
        normalized = self._normalize_control_mode(mode)
        if normalized is None:
            self.get_logger().warn(f"Modo de controle inválido: {mode!r}; use assisted ou resistive")
            return
        if normalized != self.control_mode:
            self.control_mode = normalized
            self.integral_sum = 0.0
            self.pos_error_prev = 0.0
            self.resistive_output = 0.0
            self.get_logger().info(f"Modo de controle alterado para {normalized}")

    def _on_control_mode(self, msg: String):
        self._set_control_mode(msg.data)

    def _on_game_state(self, msg: String):
        try:
            payload = json.loads(msg.data)
            lat_err = float(payload.get('lateral_error', 0.0))
            head_err_deg = float(payload.get('heading_error_deg', 0.0))
            speed = float(payload.get('speed', 1.0))
            requested_mode = payload.get('control_mode')
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            self.get_logger().warn(f"Payload inválido em /game/state: {e}")
            return

        with self._lock:
            self._lateral_error = lat_err
            self._heading_error_rad = math.radians(head_err_deg)
            self._vehicle_speed = max(0.01, speed)
            self._last_game_state_time = time.monotonic()
        if requested_mode is not None:
            self._set_control_mode(requested_mode)

    def _on_device_state(self, msg: String):
        """Read normalized EEG/EMG values from the HAL device-state topic."""
        try:
            payload = json.loads(msg.data)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return

        device_type = payload.get('type')
        device_id = payload.get('device_id')
        normalized_value = payload.get('normalized_value')

        if device_type == 'eeg':
            if self.eeg_device_id and device_id != self.eeg_device_id:
                return
            try:
                focus = float(normalized_value)
            except (TypeError, ValueError):
                return
            if not math.isfinite(focus):
                return

            with self._lock:
                self._eeg_focus = max(0.0, min(1.0, focus))
                self._last_eeg_state_time = time.monotonic()
            return

        if device_type != 'emg':
            return
        if self.emg_device_id and device_id != self.emg_device_id:
            return

        # EMG is channelized; use the geometric mean of the normalized
        # channel intensities as the single EMG intensity used by the
        # control-factor mapping.
        values = (
            normalized_value
            if isinstance(normalized_value, (list, tuple))
            else [normalized_value]
        )
        try:
            intensities = [float(value) for value in values]
        except (TypeError, ValueError):
            return
        intensities = [
            max(0.0, min(1.0, value))
            for value in intensities
            if math.isfinite(value)
        ]
        if not intensities:
            return

        with self._lock:
            self._emg_intensity = max(
                0.0, min(1.0, math.prod(intensities) ** (1.0 / len(intensities)))
            )
            self._last_emg_state_time = time.monotonic()

    @staticmethod
    def _saturated_factor(value: float, low: float, high: float) -> float:
        """Map a normalized biosignal to the PDF's piecewise-linear factor."""
        if value <= low:
            return 0.0
        if value >= high or high == low:
            return 1.0
        return (value - low) / (high - low)

    def _eeg_modulation_factor(self, now: float) -> float:
        """Convert calibrated EEG intensity to the PDF's [0, 1] factor."""
        if not self.use_eeg_modulation:
            return 1.0

        with self._lock:
            focus = self._eeg_focus
            last_eeg_time = self._last_eeg_state_time

        # No EEG state means normal controller operation remains available.
        if focus is None or last_eeg_time is None:
            return 1.0
        if now - last_eeg_time > self.eeg_state_timeout_s:
            return 1.0

        return self._saturated_factor(
            focus, self.eeg_saturation_min, self.eeg_saturation_max
        )

    def _emg_modulation_factor(self, now: float) -> float:
        """Convert calibrated EMG intensity to the PDF's [0, 1] factor."""
        if not self.use_emg_modulation:
            return 1.0

        with self._lock:
            intensity = self._emg_intensity
            last_emg_time = self._last_emg_state_time

        # Keep the controller available until a valid EMG stream is present.
        if intensity is None or last_emg_time is None:
            return 1.0
        if now - last_emg_time > self.emg_state_timeout_s:
            return 1.0

        return self._saturated_factor(
            intensity, self.emg_saturation_min, self.emg_saturation_max
        )

    def _biosignal_modulation_factor(self, now: float) -> float:
        """Combine EEG and EMG factors using their geometric average."""
        eeg_factor = self._eeg_modulation_factor(now)
        emg_factor = self._emg_modulation_factor(now)
        return math.sqrt(max(0.0, eeg_factor * emg_factor))

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
            with self._lock:
                calibrated_focus = self._eeg_focus
            self._publish_command(0.0, 0.0, 0.0, encoder_angle, stale=True,
                                  modulation_factor=1.0, ideal_output=0.0,
                                  calibrated_focus=calibrated_focus)
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

        # 4. Seleciona o PID assistivo ou a resistência limitada que afasta do setpoint.
        pos_error = setpoint - encoder_angle

        if self.control_mode == 'resistive':
            # Inverte o sentido da assistência para afastar o guidão da trajetória.
            # A zona morta, o limite de saída e a taxa de variação restringem o comando.
            error_for_resistance = math.copysign(
                max(0.0, abs(pos_error) - self.resistive_deadband_rad), pos_error
            )
            target_output = max(
                -self.resistive_output_limit,
                min(self.resistive_output_limit, -self.resistive_gain * error_for_resistance),
            )
            target_output = max(self.output_min, min(self.output_max, target_output))
            max_delta = self.resistive_slew_rate * dt
            self.resistive_output += max(
                -max_delta, min(max_delta, target_output - self.resistive_output)
            )
            control_output = self.resistive_output
            self.integral_sum = 0.0
        else:
            self.integral_sum += self.ki * pos_error * dt
            self.integral_sum = max(self.output_min, min(self.output_max, self.integral_sum))
            stiffness_term = self.kp * pos_error + self.integral_sum
            d_error = (pos_error - self.pos_error_prev) / dt
            derivative_term = self.kd * d_error
            control_output = max(
                self.output_min, min(self.output_max, stiffness_term + derivative_term)
            )
            self.resistive_output = 0.0
        self.pos_error_prev = pos_error

        # Modula o comando final do atuador somente quando habilitado.
        # EEG e EMG são combinados pela média geométrica definida na estratégia de controle.
        ideal_output = control_output
        modulation_factor = self._biosignal_modulation_factor(now)
        with self._lock:
            calibrated_focus = self._eeg_focus
        control_output *= modulation_factor

        # 5. Publicação de Comandos
        self._publish_command(control_output, setpoint, self.theta_target_smoothed,
                              encoder_angle, stale=False,
                              modulation_factor=modulation_factor,
                              ideal_output=ideal_output,
                              calibrated_focus=calibrated_focus)

    def _publish_command(self, output: float, setpoint: float, target: float, pos: float,
                         stale: bool, modulation_factor: float = 1.0,
                         ideal_output: float = 0.0, calibrated_focus=None):
        payload = {
            "guidao_pwm": output,
            "calibrated_focus": calibrated_focus,
            "modulation_factor": modulation_factor,
            "ideal_control": ideal_output,
            "modulated_control": output,
            "setpoint_rad": setpoint,
            "target_stanley_rad": target,
            "encoder_pos_rad": pos,
            "control_mode": self.control_mode,
            "stale_input": stale
        }
        serialized = json.dumps(payload)
        self.pub_command_rich.publish(String(data=serialized))
        if self.telemetry_udp_host:
            try:
                self._telemetry_socket.sendto(
                    serialized.encode('utf-8'),
                    (self.telemetry_udp_host, self.telemetry_udp_port),
                )
            except OSError as exc:
                self.get_logger().warn(f'Falha ao enviar telemetria UDP: {exc}',
                                       throttle_duration_sec=5.0)
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
