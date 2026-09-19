graph LR

%% =====================================================
%% rqt_graph STYLING
%% =====================================================

classDef node fill:#e1f5fe,stroke:#01579b,stroke-width:2px;
classDef topic fill:#f3e5f5,stroke:#4a148c,stroke-width:2px;
classDef hw fill:#fafafa,stroke:#9e9e9e,stroke-width:2px,stroke-dasharray: 5 5;
classDef service fill:#fff3e0,stroke:#e65100,stroke-width:2px;
classDef action fill:#ede7f6,stroke:#4527a0,stroke-width:2px;

%% =====================================================
%% PHYSICAL DEVICE LAYER
%% =====================================================

subgraph DEV ["Physical Devices & micro-ROS"]
    direction LR

    HW_ENC[Rotary Encoder]:::hw
    HW_IMU[IMU]:::hw
    HW_EMG[EMG Sensor]:::hw
    HW_EEG[EMOTIV EEG Headset]:::hw
    HW_MOT[Motor / Actuator]:::hw

    N_ENC([/esp/encoder_node]):::node
    N_IMU([/esp/imu_node]):::node
    N_EMG([/esp/emg_node]):::node
    N_EEG([/emotiv_eeg_node]):::node
    N_ACT([/esp/actuator_interface_node]):::node

    HW_ENC -.-> N_ENC
    HW_IMU -.-> N_IMU
    HW_EMG -.-> N_EMG
    HW_EEG -.-> N_EEG

    N_ACT -.-> HW_MOT
end

%% =====================================================
%% RAW SIGNAL TOPICS
%% =====================================================

T_EncRaw[ /device/encoder/raw ]:::topic
T_ImuRaw[ /device/imu/raw ]:::topic
T_EmgRaw[ /biosignals/emg/raw ]:::topic
T_EegRaw[ /device/emotiv_eeg/raw<br/>focus, engagement, stress ]:::topic
T_EegCommand[ /device/emotiv_eeg/mental_command ]:::topic

N_ENC --> T_EncRaw
N_IMU --> T_ImuRaw
N_EMG --> T_EmgRaw
N_EEG --> T_EegRaw
N_EEG --> T_EegCommand

%% =====================================================
%% DEVICE DISCOVERY / REGISTRATION
%% =====================================================

T_DevInfo[ /device/info ]:::topic
T_DevAvail[ /devices/available ]:::topic

N_ENC --> T_DevInfo
N_IMU --> T_DevInfo
N_EMG --> T_DevInfo
N_EEG --> T_DevInfo
N_ACT --> T_DevInfo

%% =====================================================
%% HARDWARE ABSTRACTION LAYER
%% =====================================================

subgraph HAL ["Hardware Abstraction Layer"]

    N_DEV([/hal/device_manager_node]):::node

    N_SIG([/hal/signal_processing_node]):::node

end

%% =====================================================
%% DEVICE MANAGEMENT FLOW
%% =====================================================

T_DevInfo --> N_DEV
N_DEV --> T_DevAvail

%% =====================================================
%% SIGNAL PROCESSING FLOW
%% =====================================================

T_EncRaw --> N_SIG
T_ImuRaw --> N_SIG
T_EmgRaw --> N_SIG
T_EegRaw --> N_SIG
T_EegCommand --> N_SIG

%% =====================================================
%% DEVICE CAPABILITIES → SIGNAL PROCESSING
%% =====================================================

T_DevAvail --> N_SIG

%% =====================================================
%% CALIBRATION SERVICES
%% =====================================================

S_CalStart[[ /calibration/start ]]:::service
S_CalSave[[ /calibration/save_profile ]]:::service
S_CalLoad[[ /calibration/load_profile ]]:::service

S_CalStart -.-> N_SIG
S_CalSave -.-> N_SIG
S_CalLoad -.-> N_SIG

T_EegCalRequest[ /device/emotiv_eeg/calibration/request ]:::topic
T_EegCalState[ /device/emotiv_eeg/calibration/state ]:::topic
N_SIG --> T_EegCalRequest --> N_EEG
N_EEG --> T_EegCalState --> N_SIG

%% =====================================================
%% THERAPEUTIC CALIBRATION ACTION
%% =====================================================

A_ROM[[ /calibration/run_rom_assessment ]]:::action

A_ROM -.-> N_SIG

%% =====================================================
%% CALIBRATION TOPICS
%% =====================================================

T_CalState[ /calibration/state ]:::topic
T_CalProfile[ /calibration/profile ]:::topic

N_SIG --> T_CalState
N_SIG --> T_CalProfile

%% =====================================================
%% ABSTRACT DEVICE STATE
%% =====================================================

T_DevState[ /hal/device_state ]:::topic

N_SIG --> T_DevState

%% =====================================================
%% GAME LAYER
%% =====================================================

subgraph GAME ["Gamified Interface Layer"]

    N_INPUT([/game/input_mapping_node]):::node

    N_GAME([/game/session_node]):::node

    N_UNITY([/game/unity_bridge_node]):::node

end

%% =====================================================
%% GAME INPUT FLOW
%% =====================================================

T_DevState --> N_INPUT

T_GameInput[ /game/input_state ]:::topic

N_INPUT --> T_GameInput --> N_GAME

%% =====================================================
%% DEVICE AVAILABILITY TO GAME
%% =====================================================

T_DevAvail --> N_GAME

%% =====================================================
%% GAME OUTPUTS
%% =====================================================

T_GameState[ /game/state ]:::topic
T_GameMetrics[ /game/performance_metrics ]:::topic
T_ActCmd[ /device/actuator_command ]:::topic

N_GAME --> T_GameState
N_GAME --> T_GameMetrics
N_GAME --> T_ActCmd

%% =====================================================
%% UNITY / GAME ENGINE BRIDGE
%% =====================================================

T_GameState --> N_UNITY

%% =====================================================
%% FEEDBACK / ACTUATION
%% =====================================================

T_ActCmd --> N_ACT

%% =====================================================
%% DATA & MONITORING LAYER
%% =====================================================

subgraph DATA ["Data & Monitoring"]

    N_LOG([/data/session_logger_node]):::node

    N_MON([/data/monitor_node]):::node

    N_DB([/data/database_node]):::node

end

%% =====================================================
%% LOGGER SUBSCRIPTIONS
%% =====================================================

T_DevState --> N_LOG
T_GameState --> N_LOG
T_GameMetrics --> N_LOG
T_CalProfile --> N_LOG

%% =====================================================
%% MONITOR SUBSCRIPTIONS
%% =====================================================

T_DevState --> N_MON
T_GameState --> N_MON
T_GameMetrics --> N_MON
T_CalState --> N_MON

%% =====================================================
%% DATABASE STORAGE
%% =====================================================

T_DbWrite[ /database/write_stream ]:::topic

N_LOG --> T_DbWrite --> N_DB
