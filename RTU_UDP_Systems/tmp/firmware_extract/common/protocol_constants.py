"""
RTU UDP Protocol Constants
Based on RTU UDP Protocol V1.1.0
Version: 1.1.0

Manufacturer: (주) 솔라라이즈
Model: CM4-ETH-RS485-BASE-B

Changes in 1.0.0:
- UDP version (no TLS, no security authentication)

Changes in 2.0.0 (TCP reference):
- Migrated from UDP to TCP/TLS
- Added TCP frame header (SYNC + LENGTH)
- Added TLS configuration constants
- Added connection management constants
- Removed UDP-specific retry logic constants

Changes in 1.0.6 (UDP):
- Final UDP version before TCP migration
"""

# =============================================================================
# Protocol Version
# =============================================================================
PROTOCOL_VERSION = "V3.0.1"
PROGRAM_VERSION = "2.2.0"

# =============================================================================
# Network Configuration
# =============================================================================
DEFAULT_SERVER_HOST = "localhost"
DEFAULT_SERVER_PORT = 13132
DEFAULT_SERVER_PORT_SECONDARY = 13133
DEFAULT_RTU_LOCAL_PORT = 9100  # Not used in TCP client mode

# =============================================================================
# TCP Frame Constants
# =============================================================================
TCP_FRAME_SYNC = 0xAA55
TCP_FRAME_SYNC_BYTES = b'\xAA\x55'
TCP_FRAME_HEADER_SIZE = 4  # SYNC (2) + LENGTH (2)
TCP_FRAME_HEADER_FORMAT = '>HH'  # Big-endian: sync (uint16), length (uint16)
TCP_MAX_PAYLOAD_SIZE = 65535
TCP_RECV_BUFFER_SIZE = 8192

# =============================================================================
# TLS Configuration
# =============================================================================
TLS_MIN_VERSION = "TLSv1.2"
TLS_DEFAULT_CA_PATH = "/etc/ssl/certs/ca-certificates.crt"
TLS_VERIFY_DEFAULT = True

# =============================================================================
# Connection Management
# =============================================================================
# Reconnection settings
RECONNECT_DELAY_INITIAL = 5      # Initial delay (seconds)
RECONNECT_DELAY_MAX = 60         # Maximum delay (seconds)
RECONNECT_DELAY_MULTIPLIER = 2   # Exponential backoff multiplier

# Connection timeouts
CONNECT_TIMEOUT = 30             # TCP connect timeout (seconds)
TLS_HANDSHAKE_TIMEOUT = 30       # TLS handshake timeout (seconds)
RECV_TIMEOUT = 90                # Receive timeout (seconds)
SEND_TIMEOUT = 30                # Send timeout (seconds)

# Keep-alive settings
TCP_KEEPALIVE_ENABLE = True
TCP_KEEPALIVE_IDLE = 60          # Start keepalive after idle (seconds)
TCP_KEEPALIVE_INTERVAL = 10      # Keepalive probe interval (seconds)
TCP_KEEPALIVE_COUNT = 3          # Number of probes before disconnect

# =============================================================================
# Packet Versions
# =============================================================================
VERSION_H01 = 0x01
VERSION_H02 = 0x02
VERSION_H03 = 0x03
VERSION_H04 = 0x04
VERSION_H05 = 0x05
VERSION_H06 = 0x06
VERSION_H07 = 0x07  # Firmware Update Request (Server -> RTU)
VERSION_H08 = 0x08  # Firmware Update Response (RTU -> Server)
VERSION_H09 = 0x09  # PCAP Capture Request (Server -> RTU)
VERSION_H10 = 0x0A  # PCAP Capture Response (RTU -> Server)

# =============================================================================
# Device Types
# =============================================================================
DEVICE_RTU = 0
DEVICE_INVERTER = 1
DEVICE_SENSOR = 2
DEVICE_POWER_METER = 3
DEVICE_PROTECTION_RELAY = 4
DEVICE_WEATHER_STATION = 5

DEVICE_TYPE_NAMES = {
    DEVICE_RTU: "RTU",
    DEVICE_INVERTER: "INV",
    DEVICE_SENSOR: "SENSOR",
    DEVICE_POWER_METER: "METER",
    DEVICE_PROTECTION_RELAY: "RELAY",
    DEVICE_WEATHER_STATION: "WEATHER"
}

# =============================================================================
# Device Model Numbers
# =============================================================================
INV_MODEL_SOLARIZE = 1
INV_MODEL_HUAWEI = 2
INV_MODEL_KSTAR = 3
INV_MODEL_SOLARIZE_50K = 4      # Solarize VK50 (solarize1)
INV_MODEL_HUAWEI_50K = 5        # Huawei 50K (huawei1)
INV_MODEL_KSTAR_60K = 6         # Kstar 60K (kstar1)
INV_MODEL_SUNGROW = 7           # Sungrow (sungrow1)
INV_MODEL_EKOS = 8              # EKOS (ekos1)
INV_MODEL_SOLARIZE_VK = 9       # Solarize VK (solarize_vk)
INV_MODEL_SOLARIZE1_ALT = 10    # Solarize Alt
INV_MODEL_EKOS2 = 11            # EKOS2

RELAY_MODEL_KDU300 = 1
RELAY_MODEL_VIPAM3500C = 2

WEATHER_MODEL_SEM5046 = 1

INV_MODEL_NAMES = {
    INV_MODEL_SOLARIZE: "Solarize",
    INV_MODEL_HUAWEI: "Huawei",
    INV_MODEL_KSTAR: "Kstar",
    INV_MODEL_SOLARIZE_50K: "Solarize 50K",
    INV_MODEL_HUAWEI_50K: "Huawei 50K",
    INV_MODEL_KSTAR_60K: "Kstar 60K",
    INV_MODEL_SUNGROW: "Sungrow",
    INV_MODEL_EKOS: "EKOS",
    INV_MODEL_SOLARIZE_VK: "Solarize",
    INV_MODEL_SOLARIZE1_ALT: "Solarize",
    INV_MODEL_EKOS2: "EKOS2",
}

# =============================================================================
# H01 Body Types
# =============================================================================
# Negative body types: Header only, no body
INV_BODY_NIGHTTIME = -4     # Nighttime standby (Kstar only, 20:00-05:00)
INV_BODY_ZEE_SKIP = -3      # ZEE Control Enable Data Skip
INV_BODY_ERROR = -2         # Communication Data Packet Error
INV_BODY_FAIL = -1          # Communication Connection Failed

# Positive body types: Header + Body
INV_BODY_BASIC = 1
INV_BODY_BASIC_MPPT = 2
INV_BODY_BASIC_STRING = 3
INV_BODY_BASIC_MPPT_STRING = 4
INV_BODY_SINGLE_PHASE = 5   # Single phase inverter basic data

RELAY_BODY_BASIC_DATA = 1

WEATHER_BODY_BASIC_DATA = 1  # SEM5046 Weather station data

INV_BODY_TYPE_NAMES = {
    INV_BODY_BASIC: "BASIC",
    INV_BODY_BASIC_MPPT: "BASIC_MPPT",
    INV_BODY_BASIC_STRING: "BASIC_STRING",
    INV_BODY_BASIC_MPPT_STRING: "BASIC_MPPT_STRING",
    INV_BODY_SINGLE_PHASE: "SINGLE_PHASE",
}

# =============================================================================
# H05 Body Types
# =============================================================================
BODY_TYPE_HEARTBEAT = 0       # Heartbeat/Ping (header only, no body)
BODY_TYPE_RTU_INFO = 1
BODY_TYPE_RTU_EVENT = 2
BODY_TYPE_POWER_OUTAGE = 3
BODY_TYPE_POWER_RESTORE = 4
BODY_TYPE_INVERTER_MODEL = 11
BODY_TYPE_IV_SCAN_SUCCESS = 12
BODY_TYPE_CONTROL_CHECK = 13
BODY_TYPE_CONTROL_RESULT = 14
BODY_TYPE_IV_SCAN_DATA = 15
BODY_TYPE_RTU_STATUS = 16     # RTU status (grid, modbus, server)
BODY_TYPE_CONFIG_DATA = 17    # Configuration data response

# =============================================================================
# Connection Status
# =============================================================================
CONN_STATUS_NORMAL = 0        # Normal operation
CONN_STATUS_SERVER_DOWN = 1   # Server not responding
CONN_STATUS_NETWORK_DOWN = 2  # Network unreachable
CONN_STATUS_TLS_ERROR = 3     # TLS handshake failed
CONN_STATUS_RECONNECTING = 4  # Reconnecting

CONN_STATUS_NAMES = {
    CONN_STATUS_NORMAL: "NORMAL",
    CONN_STATUS_SERVER_DOWN: "SERVER_DOWN",
    CONN_STATUS_NETWORK_DOWN: "NETWORK_DOWN",
    CONN_STATUS_TLS_ERROR: "TLS_ERROR",
    CONN_STATUS_RECONNECTING: "RECONNECTING"
}

# Heartbeat configuration
DEFAULT_HEARTBEAT_INTERVAL = 30   # seconds (increased for TCP)
DEFAULT_HEARTBEAT_TIMEOUT = 10    # seconds
DEFAULT_HEARTBEAT_FAIL_COUNT = 3  # failures before reconnect

# =============================================================================
# Control Types (H03)
# =============================================================================
CTRL_RTU_REBOOT = 1
CTRL_RTU_INFO = 2
CTRL_CONFIG_READ = 3      # Read RTU/device configuration
CTRL_CONFIG_WRITE = 4     # Write RTU/device configuration
CTRL_INV_MODEL = 11
CTRL_INV_IV_SCAN = 12
CTRL_INV_CONTROL_CHECK = 13
CTRL_INV_CONTROL_INIT = 14
CTRL_INV_ON_OFF = 15
CTRL_INV_ACTIVE_POWER = 16
CTRL_INV_POWER_FACTOR = 17
CTRL_INV_REACTIVE_POWER = 18
CTRL_INV_BODY_TYPE = 19       # Set H01 body_type per inverter

CONTROL_TYPE_NAMES = {
    CTRL_RTU_REBOOT: "RTU Reboot",
    CTRL_RTU_INFO: "RTU Info",
    CTRL_CONFIG_READ: "Config Read",
    CTRL_CONFIG_WRITE: "Config Write",
    CTRL_INV_MODEL: "Inverter Model",
    CTRL_INV_IV_SCAN: "IV Scan",
    CTRL_INV_CONTROL_CHECK: "Control Check",
    CTRL_INV_CONTROL_INIT: "Control Init",
    CTRL_INV_ON_OFF: "Inverter ON/OFF",
    CTRL_INV_ACTIVE_POWER: "Active Power Limit",
    CTRL_INV_POWER_FACTOR: "Power Factor",
    CTRL_INV_REACTIVE_POWER: "Reactive Power",
    CTRL_INV_BODY_TYPE: "H01 Body Type"
}

# =============================================================================
# Inverter Status
# =============================================================================
INV_STATUS_INITIAL = 0x00
INV_STATUS_STANDBY = 0x01
INV_STATUS_ON_GRID = 0x03
INV_STATUS_FAULT = 0x05

INV_STATUS_NAMES = {
    INV_STATUS_INITIAL: "Initial",
    INV_STATUS_STANDBY: "Standby",
    INV_STATUS_ON_GRID: "On-Grid",
    INV_STATUS_FAULT: "Fault"
}

# =============================================================================
# Timing Constants
# =============================================================================
H01_SEND_INTERVAL = 60
H01_ACK_TIMEOUT = 30
H05_ACK_TIMEOUT = 60
DEVICE_SEND_INTERVAL = 0.1
FIRST_CONNECTION_WAIT = 60
BACKUP_CHECK_INTERVAL = 10
MAX_RETRY_COUNT = 3
BACKUP_RETENTION_HOURS = 48

# =============================================================================
# RTU Events
# =============================================================================
EVENT_FIRST_CONNECTION = "RTU First Connection"
EVENT_COMM_RESTORED = "Communication Restored"
EVENT_RECONNECTED = "TCP Reconnected"

# =============================================================================
# Response Codes
# =============================================================================
RESPONSE_SUCCESS = 0
RESPONSE_FAILURE = -1
RESPONSE_INVALID_PARAM = -2
RESPONSE_NOT_FOUND = -3
RESPONSE_TIMEOUT = -4
RESPONSE_BUSY = -5
RESPONSE_AUTH_FAIL = -6
RESPONSE_COMM_FAIL = -7

RESPONSE_NAMES = {
    RESPONSE_SUCCESS: "SUCCESS",
    RESPONSE_FAILURE: "ERROR",
    RESPONSE_INVALID_PARAM: "INVALID_PARAM",
    RESPONSE_NOT_FOUND: "NOT_FOUND",
    RESPONSE_TIMEOUT: "TIMEOUT",
    RESPONSE_BUSY: "BUSY",
    RESPONSE_AUTH_FAIL: "AUTH_FAIL",
    RESPONSE_COMM_FAIL: "COMM_FAIL"
}

# =============================================================================
# Packet Structures
# =============================================================================
HEADER_SIZE = 20
HEADER_FORMAT = '>BHIQBBBBb'
H02_FORMAT = '>BHb'
H02_SIZE = 4
H03_FORMAT = '>BHBBBh'
H03_SIZE = 8
H04_FORMAT = '>BHBBBhb'
H04_SIZE = 9
H06_FORMAT = '>BHb'
H06_SIZE = 4
H08_FORMAT = '>BHBBb'  # version, sequence, device_type, device_number, response
H08_SIZE = 6
# H09: PCAP Capture Request (Server -> RTU)
# Format: version(1) + sequence(2) + command(1) + duration(2) + max_packets(2)
H09_FORMAT = '>BHBHH'
H09_SIZE = 8
# H10: PCAP Capture Response (RTU -> Server)
# Format: version(1) + sequence(2) + status(1) + file_size(4)
H10_FORMAT = '>BHbI'
H10_SIZE = 8
INV_BASIC_FORMAT = '>HHIHHHHHHIhHQHHHH'
INV_BASIC_SIZE = 44
RELAY_BASIC_FORMAT = '>ffffffffffffddHH'
RELAY_BASIC_SIZE = 68
# Weather: air_temp, humidity, pressure, wind_speed, wind_dir, module1, h_rad, h_accum,
#          i_rad, i_accum, module2, module3, module4 (13 x signed short)
WEATHER_BASIC_FORMAT = '>hhHhhhhHhHhhh'
WEATHER_BASIC_SIZE = 26

# =============================================================================
# Firmware Update Constants
# =============================================================================
# Download protocols
UPDATE_PROTO_FTP = 1
UPDATE_PROTO_FTPS = 2
UPDATE_PROTO_HTTPS = 3  # HTTPS firmware download

# Update response codes
UPDATE_RESP_SUCCESS = 0           # H07 received, starting update
UPDATE_RESP_COMPLETE = 1          # Update completed successfully
UPDATE_RESP_ERROR = -1            # H07 packet/data error
UPDATE_RESP_FTP_CONNECT_FAIL = -2 # FTP connection failed
UPDATE_RESP_FTP_LOGIN_FAIL = -3   # FTP login failed
UPDATE_RESP_FTP_DOWNLOAD_FAIL = -4 # FTP download failed
UPDATE_RESP_EXTRACT_FAIL = -5     # Archive extraction failed
UPDATE_RESP_APPLY_FAIL = -6       # Update apply failed
UPDATE_RESP_BUSY = -7             # Already updating
UPDATE_RESP_HASH_FAIL = -8        # SHA-256 hash verification failed

# Update status (for internal tracking)
UPDATE_STATUS_IDLE = 0
UPDATE_STATUS_DOWNLOADING = 1
UPDATE_STATUS_EXTRACTING = 2
UPDATE_STATUS_BACKING_UP = 3
UPDATE_STATUS_APPLYING = 4
UPDATE_STATUS_RESTARTING = 5
UPDATE_STATUS_COMPLETE = 6
UPDATE_STATUS_FAILED = -1

UPDATE_STATUS_NAMES = {
    UPDATE_STATUS_IDLE: "IDLE",
    UPDATE_STATUS_DOWNLOADING: "DOWNLOADING",
    UPDATE_STATUS_EXTRACTING: "EXTRACTING",
    UPDATE_STATUS_BACKING_UP: "BACKING_UP",
    UPDATE_STATUS_APPLYING: "APPLYING",
    UPDATE_STATUS_RESTARTING: "RESTARTING",
    UPDATE_STATUS_COMPLETE: "COMPLETE",
    UPDATE_STATUS_FAILED: "FAILED"
}

# =============================================================================
# PCAP Capture Constants (H09/H10)
# =============================================================================
# H09 Commands
CAPTURE_CMD_START = 1      # Start capture
CAPTURE_CMD_STOP = 0       # Stop capture (reserved)

# H10 Response codes
CAPTURE_RESP_STARTED = 0       # Capture started
CAPTURE_RESP_COMPLETED = 1     # Capture completed, uploading
CAPTURE_RESP_UPLOADED = 2      # Upload successful
CAPTURE_RESP_ERROR = -1        # General error
CAPTURE_RESP_NO_TCPDUMP = -2   # tcpdump not available
CAPTURE_RESP_BUSY = -3         # Already capturing
CAPTURE_RESP_UPLOAD_FAIL = -4  # Upload failed

# External FTP configuration (legacy support)
DEFAULT_FTP_HOST = "solarize.ddns.net"
DEFAULT_FTP_PORT = 21
DEFAULT_FTP_TIMEOUT = 30
DEFAULT_DOWNLOAD_DIR = "/home/pi/update"
DEFAULT_BACKUP_DIR = "/home/pi/backup"
DEFAULT_PROGRAM_DIR = "/home/pi/rtu_program"

# HTTPS firmware download (recommended)
DEFAULT_HTTPS_PORT = 443
DEFAULT_FIRMWARE_URL_PATH = "/firmware"

# Built-in FTP Server Configuration (legacy)
DEFAULT_BUILTIN_FTP_PORT = 21
DEFAULT_FTP_ROOT_DIR = "./firmware"
DEFAULT_FTP_USER = "rtu"
DEFAULT_FTP_PASSWORD = ""  # No default password - must be set by user

# =============================================================================
# Serial Port Configuration
# =============================================================================
DEFAULT_SERIAL_PORT_WIN = "COM4"
DEFAULT_SERIAL_PORT_LINUX = "/dev/ttyUSB0"
DEFAULT_BAUDRATE = 9600
DEFAULT_SLAVE_ID = 1
