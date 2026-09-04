#!/bin/bash
set -e

# Ambiente base do ROS2
source "/opt/ros/${ROS_DISTRO}/setup.bash"

# Workspace do projeto (pacotes do TCC + ros_tcp_endpoint)
if [ -f "/ros2_ws/install/local_setup.bash" ]; then
    source "/ros2_ws/install/local_setup.bash"
fi

exec "$@"
