HAL Device Manager Node
Overview

The device_manager_node acts as the central hardware registry for the rehabilitation system. It bridges the Physical Device Layer and the Hardware Abstraction Layer (HAL) by dynamically discovering connected microcontrollers and monitoring their health via a heartbeat mechanism.
Device Agnosticism

This node is completely hardware-agnostic. It does not contain any hardcoded references to encoders, IMUs, or specific actuators. It treats all devices as generic data sources based on a standardized JSON contract.

To ensure future devices (e.g., an EMG sensor) are compatible, their firmware simply needs to publish a JSON string with, at minimum, a device_id key.

    Example Compatible Payload: > {"device_id":"mpu6050_imu","type":"imu","capabilities":["orientation","acceleration"]}

Node Interfaces
Subscriptions (Inputs)
Topic	Message Type	Purpose
/device/info	std_msgs/msg/String	Listens for JSON payloads from hardware nodes. Used to discover capabilities and act as a liveliness heartbeat.
Publishers (Outputs)
Topic	Message Type	Purpose
/devices/available	std_msgs/msg/String	Publishes a JSON array of all currently active and healthy devices. Downstream game nodes use this to map inputs dynamically.
Core Mechanics

    Dynamic Registration: When a new device_id is detected on /device/info, it is immediately added to the active registry.

    Heartbeat & Timeout: The node tags every incoming message with a timestamp. A timer runs at 2 Hz checking these timestamps. If a device fails to send a message within 3.0 seconds, it is assumed disconnected (e.g., USB unplugged) and is automatically pruned from the /devices/available list.