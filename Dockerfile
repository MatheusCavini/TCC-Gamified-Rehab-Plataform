# syntax=docker/dockerfile:1
#
# Dockerfile - TCC Gamified Rehab Platform (ROS2 Humble / Ubuntu 22.04)
#
# NOTA: o pipeline antigo via ESP32 + micro-ROS Agent está deprecado e foi
# removido daqui. Hoje o encoder_guidao_node lê a serial diretamente
# (pyserial, /dev/ttyACM0) e cria seu próprio nó ROS2 no computador -
# não há mais nenhum nó ROS2 rodando em microcontrolador.
#
# Este Dockerfile agora tem 2 estágios:
#   1) workspace_builder -> compila os pacotes do projeto + ros_tcp_endpoint (Unity)
#   2) imagem final       -> runtime enxuto, só com os artefatos compilados

ARG ROS_DISTRO=humble

# ---------------------------------------------------------------------------
# STAGE 1: build do workspace do projeto (pacotes do TCC + ros_tcp_endpoint)
# ---------------------------------------------------------------------------
FROM ros:${ROS_DISTRO}-ros-base AS workspace_builder

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        python3-pip \
        python3-serial \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /ros2_ws

# 1) Copia só os pacotes do projeto (o resto do repo é ignorado via .dockerignore)
COPY src ./src

# 2) Traz o ROS-TCP-Endpoint da Unity para dentro do workspace como pacote-fonte.
#    IMPORTANTE: a branch "main" desse repositório é a versão ROS1 (catkin) -
#    ela não tem ament_cmake e quebra o colcon build. A versão ROS2 vive numa
#    tag separada. Se precisar trocar de versão no futuro, veja as tags
#    disponíveis em: https://github.com/Unity-Technologies/ROS-TCP-Endpoint/tags
#    (procure por tags no formato "ROS2vX.Y.Z").
RUN git clone --depth 1 -b ROS2v0.7.0 \
        https://github.com/Unity-Technologies/ROS-TCP-Endpoint.git \
        src/ros_tcp_endpoint

# 3) Resolve dependências declaradas nos package.xml (rclpy, std_msgs, etc.)
RUN . /opt/ros/${ROS_DISTRO}/setup.sh && \
    apt-get update && \
    rosdep update --rosdistro ${ROS_DISTRO} && \
    rosdep install --from-paths src --ignore-src -r -y && \
    rm -rf /var/lib/apt/lists/*

# 4) Build de todo o workspace (mensagens custom: hal_interfaces, thesis_interfaces)
RUN . /opt/ros/${ROS_DISTRO}/setup.sh && \
    colcon build --cmake-args -DCMAKE_BUILD_TYPE=Release && \
    rm -rf log build

# ---------------------------------------------------------------------------
# STAGE 2: imagem final de runtime
# ---------------------------------------------------------------------------
FROM ros:${ROS_DISTRO}-ros-base AS runtime

ARG ROS_DISTRO=humble
ENV DEBIAN_FRONTEND=noninteractive \
    ROS_DISTRO=${ROS_DISTRO}

# python3-serial: usado diretamente por encoder_guidao_node (pyserial, /dev/ttyACM0)
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-serial \
        udev \
    && rm -rf /var/lib/apt/lists/*

# Usuário não-root, mas com acesso a /dev/ttyACM* (grupo dialout)
RUN useradd -m -s /bin/bash rosdev && \
    usermod -aG dialout rosdev

# Artefatos compilados do workspace
COPY --from=workspace_builder /ros2_ws/install /ros2_ws/install

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER rosdev
WORKDIR /ros2_ws

ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]