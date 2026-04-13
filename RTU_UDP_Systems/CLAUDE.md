# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 작업 규칙

**모든 코드 수정은 승인 없이 바로 실행한다.**
- 파일 수정, 생성, 삭제 — 사용자 확인 없이 즉시 수행
- 코드 수정 완료 시 항상 자동으로 커밋 → push → main merge 실행
- worktree에서 작업 시: `git add → commit → push origin <branch> → cd main && git merge <branch> --no-edit`
- V2 웹 서버가 main 디렉토리에서 실행되므로 merge까지 해야 반영됨
- pyc 캐시 삭제도 자동 실행
- **모델**: claude-opus-4-6[1m] (Opus 4.6, 1M context) 기본 사용

## Project Overview

RTU UDP System V1.1.0 — a Python-based Remote Terminal Unit (RTU) implementing the **Solarize Modbus Protocol V2.0.11**. Communicates with a cloud server via UDP using custom binary packets (H01–H06), and with solar inverters/protection relays/weather stations via Modbus RTU over RS485.

## Running the System

```bash
# Install dependencies (only pymodbus for real hardware; optional)
pip install -r requirements.txt

# Run the RTU client (simulation mode if pymodbus absent or no serial port)
python -m rtu_program.rtu_client

# Run the production dashboard (web UI + UDP engine)
python -m web_server_prod.main

# Run the production UDP server (standalone, no web UI)
python pc_programs/udp_server.py

# Run the test server (interactive, for debugging)
python pc_programs/udp_test_server.py

# Run the equipment simulator (multi-device Modbus slave on PC)
python pc_programs/equipment_simulator.py

# Run the DER-AVM master (control utility)
python pc_programs/der_avm_master.py

# Run RS485 cross-test (hardware diagnostics)
python -m rtu_program.rs485_cross_test
```

All batch launchers are in `launchers/`:
- `START_DASHBOARD.bat` — 운영 대시보드 (web_server_prod/, watchdog auto-restart)
- `START_DASHBOARD_DEV.bat` — 개발 대시보드 (web_server/, DB 초기화)
- `START_UDP_SERVER.bat` — 운영 UDP 서버 (auto-restart)
- `START_TEST_SERVER.bat` — 테스트 UDP 서버
- `START_SIMULATOR.bat` — 장비 시뮬레이터
- ~~START_MODEL_MAKER.bat~~ — **Model Maker는 `inverter_model_maker/` 디렉토리로 이전됨**
- `INSTALL_RTU_DEV.bat` — RTU 원클릭 설치 (305-line comprehensive setup)
- `SETUP_CM4_BOOT.bat` — CM4 부트 설정

There is no build step, no test runner, and no lint configuration.

## Architecture

### Module Layout

- **`common/protocol_constants.py`** — Single source of truth for all protocol values: packet versions (H01–H06), device types (RTU/Inverter/Sensor/PowerMeter/Relay/Weather), models, timing (60s H01 cycle, 30s ACK timeout), network defaults (server port 13132, RTU port 9100), control command codes, and response codes.
- **`common/config_loader.py`** — Configuration loading utilities for INI files (`config/rtu_config.ini`, `config/rs485_ch*.ini`, `config/device_models.ini`).
- **`common/*_registers.py`** — Register map modules per inverter brand (solarize, huawei, kstar, sungrow, ekos, goodwe, senergy) and device type (relay, weather). Each `*_mm_registers.py` variant is the Model Maker reference copy.
- **`rtu_program/rtu_client.py`** — Main application. Three daemon threads: receive (H02/H03/H06), send (periodic H01 every 60s + backup recovery), and backup monitor (timeout/retry/cleanup). Tracks per-device control state (`on_off`, `active_power_limit`, `power_factor`, `reactive_power`).
- **`rtu_program/protocol_handler.py`** — Packet serialization/deserialization. 20-byte binary header + variable body. Implements all six packet types.
- **`rtu_program/modbus_handler.py`** (V3.0.0) — `MultiDeviceModbusHandler` with four RS485 modes: CM4 native UART, Waveshare 2-CH RS485 HAT (SPI/SC16IS752), PC USB-RS485 (pymodbus), or simulation. Mode auto-detected via `config/rtu_config.ini [RS485] mode`. Dynamic register module loading (`load_register_module`) for new inverter brands.
- **`rtu_program/lib/`** — RS485 channel abstraction layer (new in V1.1.0):
  - `rs485_channel.py` — Abstract RS485 channel interface
  - `modbus_master.py` — Generic Modbus RTU master (1,006 lines)
  - `modbus_utils.py` — CRC, framing utilities
  - `cm4_serial/` — CM4 native UART via pyserial
  - `waveshare_2_CH_RS485_HAT/` — Waveshare HAT driver (SPI/SC16IS752)
- **`rtu_program/der_avm_slave.py`** — Modbus RTU slave for DER-AVM master integration. Runs on RS485 CH2, exposes real-time inverter data (FC03) and accepts control commands (FC06/FC16) per inverter slave ID.
- **`rtu_program/watchdog_supervisor.py`** — Process supervisor for CM4 deployment. Monitors RTU heartbeat file, auto-restarts on crash or freeze (30s timeout).
- **`rtu_program/backup_manager.py`** — Two-tier fault tolerance: short-term in-memory with 3 retries, long-term SQLite (`rtu_backup.db`) storing packets for 48 hours. Recovery mode activates after 3+ consecutive ACK failures.
- **`pc_programs/udp_server.py`** (V1.1.0) — Production UDP server with SQLite persistence, duplicate detection, rate limiting, data retention, built-in FTP server for firmware distribution.
- **`pc_programs/udp_test_server.py`** — Interactive test server for debugging. Parses packets, sends ACKs, provides menu for H03 commands.
- **`pc_programs/equipment_simulator.py`** — Multi-device Modbus slave simulator (2,633 lines). Config via `simulator_config.json`.
- **`pc_programs/der_avm_master.py`** — DER-AVM master control utility for testing slave integration.
- **`web_server_prod/`** — Production dashboard (FastAPI + SQLite WAL + WebSocket + React 18 frontend). Env vars: `RTU_UDP_PORT`, `RTU_WEB_PORT`, `RTU_DB_PATH`, `RTU_FTP_USER`, `RTU_FTP_PASS`. Includes SFTP path whitelist, duplicate detection, rate limiting, data retention, stale RTU detection.
- **`inverter_model_maker/`** — 독립 실행 Model Maker 패키지 (다른 PC로 이동 가능). 3종류:
  - `model_maker/` — GUI 도구 (`START_모델메이커_GUI.bat`)
  - `model_maker_web/` — 웹 UI v1 포트 8181 (`START_모델메이커_WEB_v1.bat`)
  - `model_maker_web_v2/` — 웹 UI v2 Stage1/2/3 포트 8082 (`START_모델메이커_WEB_v2.bat`)
  - 생성된 `*_registers.py`는 `inverter_model_maker/common/` → 프로젝트 `common/`에 복사하면 RTU/시뮬레이터에서 즉시 사용 가능

### Communication Flow

```
RTU Client                          Server / Test Server
   |-- H05 RTU First Connection ------->|
   |<------- H06 ACK ------------------|
   |  [60-second initial delay]
   |-- H01 Periodic Data (every 60s) -->|
   |<------- H02 ACK ------------------|
   |<------- H03 Control Request -------|
   |-- H04 Control Response (<100ms) -->|
   |-- H05 Event/Result (optional) ---->|
   |<------- H06 ACK ------------------|
```

### Packet Format

All packets share a **20-byte header**: Version(1) + Sequence(2) + RTU_ID(4) + Timestamp(8) + DeviceType(1) + DeviceNumber(1) + Model(1) + BackupFlag(1) + BodyType(1).

- **H01 Inverter body:** 44 bytes base + variable MPPT (4 channels x 4 bytes) + String arrays
- **H01 Relay body:** 68 bytes (3-phase voltages, currents, power, energy, DO/DI)
- **H03/H04:** 8-9 bytes (control type + value)

### Key Design Decisions

- **Simulation mode** activates automatically when pymodbus is unavailable or no RS485 port is configured — no code changes needed.
- **Backup flag** in every H01 packet header distinguishes live vs. recovered backup data on the server side.
- **Sequence numbers** are used to match ACKs to sent packets; the backup manager tracks unacknowledged sequences.
- **Control values are scaled integers**: active power limit 0-1000 = 0-100%, power factor -1000-1000 = -1.0-1.0.
- **ACK-first pattern**: server sends ACK before parsing/storing data to minimize RTU wait time.
- Default RTU ID is `12345678`; configured in `config/rtu_config.ini [RTU] rtu_id`.

### Supported Device Types

| Type | Models | Protocol Names |
|------|--------|---------------|
| Inverter | Solarize, Huawei, Kstar, Sungrow, EKOS, Goodwe, Senergy | `solarize`, `huawei`, `kstar`, `sungrow`, `ekos`, `goodwe`, `senergy` |
| Protection Relay | KDU-300, VIPAM3500C-DG | `relay`, `vipam` |
| Weather Station | SEM5046 | `weather` |

Device configuration in `config/rs485_ch*.ini`. Model-to-protocol mapping in `config/device_models.ini`.

## RTU 수정 시 필수 절차

RTU 관련 코드(`rtu_program/`, `common/`, `config/`)를 수정한 후 반드시:

1. **펌웨어 패키지 생성**: Model Maker GUI의 "Build Package" 버튼 또는 아래 명령
2. **배포**: `pc_programs/firmware/` 디렉토리에 `.tar.gz` 생성됨 -> 대시보드 Firmware 탭으로 Pi에 배포 가능

```bash
# 수동 펌웨어 빌드 (Model Maker 없이)
python -c "
import tarfile, os
from datetime import datetime
project_dir = os.getcwd()  # run from project root
firmware_dir = os.path.join(project_dir, 'pc_programs', 'firmware')
os.makedirs(firmware_dir, exist_ok=True)
ts = datetime.now().strftime('%Y%m%d_%H%M%S')
pkg = os.path.join(firmware_dir, f'rtu_firmware_{ts}.tar.gz')
skip = {'__pycache__', '.claude', 'firmware'}
with tarfile.open(pkg, 'w:gz') as tar:
    for folder, exts, recurse in [('rtu_program',('.py',),True),('common',('.py',),False),('config',('.ini',),False)]:
        src = os.path.join(project_dir, folder)
        if not os.path.isdir(src): continue
        for root, dirs, files in os.walk(src):
            dirs[:] = [d for d in dirs if d not in skip]
            for f in sorted(files):
                if any(f.endswith(e) for e in exts):
                    fp = os.path.join(root, f)
                    tar.add(fp, arcname=os.path.relpath(fp, project_dir).replace(chr(92),'/'))
            if not recurse: break
print(f'Created: {pkg}')
"
```

## Model Maker — 새 인버터 레지스터맵 생성 규칙

### 개요

`inverter_model_maker/` 디렉토리의 Model Maker 웹 UI v2 (Stage1→2→3)를 사용해 인버터 Modbus PDF → RTU 호환 `*_registers.py` 파일 자동 생성. 생성된 파일을 `common/`에 복사하면 `rtu_program/modbus_handler.py`의 동적 로딩(`load_register_module`)으로 즉시 사용 가능.

### 새 업체 PDF로 레지스터맵 생성하는 절차

1. **Model Maker 실행**: `inverter_model_maker/START_모델메이커_WEB_v2.bat` → http://localhost:8082
2. **PDF 로드**: Tab1에서 제조사 Modbus Protocol PDF 열기
3. **자동 매핑**: Tab2에서 MPPT/String 수 설정 후 "자동 매핑 실행"
4. **Code Generator**: Tab6에서 설정 후 "Generate Code" -> "Save"
   - **Protocol Name**: `config/rs485_ch*.ini`에서 사용할 이름 (예: `newbrand`)
   - **Class Name**: 반드시 `RegisterMap` (modbus_handler 호환)
   - **IV Scan / DER-AVM 체크**: Solarize 프로토콜이면 둘 다 ON
5. **INI 설정**: `config/rs485_ch*.ini`에 `protocol = newbrand` 추가

### 생성 파일이 RTU와 호환되려면 반드시 포함해야 할 요소

| 요소 | 설명 |
|------|------|
| `class RegisterMap` | 레지스터 주소 상수 클래스 (필수 이름) |
| `class InverterMode` | `.INITIAL`, `.STANDBY`, `.ON_GRID`, `.FAULT`, `.SHUTDOWN` + `to_string()` |
| `SCALE dict` | 일반 키 필수: `voltage`, `current`, `power`, `frequency`, `power_factor` |
| `registers_to_u32()` | U32 조합 헬퍼 |
| `registers_to_s32()` | S32 조합 헬퍼 |

### SCALE 키 규칙

```python
SCALE = {
    'voltage': 0.1,         # PV/AC 전압 스케일
    'current': 0.01,        # PV/AC 전류 스케일
    'power': 0.1,           # 전력 스케일 (W 단위)
    'frequency': 0.01,      # 주파수 스케일
    'power_factor': 0.001,  # 역률 스케일
    # 제조사별 추가 키 가능 (ac_voltage, temperature 등)
}
```

### Solarize 프로토콜 인버터 (VerterKing, VK50 등)

같은 Modbus PDF 기반이므로 `_SOLARIZE_ADDR_TO_NAME` 테이블에 의해 `solarize_registers.py`와 **동일한 속성명**으로 자동 생성됨. 추가 DER-AVM alias(`POWER_FACTOR_SET`, `OPERATION_MODE` 등)도 자동 포함.

### 비-Solarize 프로토콜 인버터 (Kstar, Huawei 등)

- `_SOLARIZE_ADDR_TO_NAME`에 없는 주소는 PDF 필드명에서 자동 생성
- 전용 핸들러(`ModbusHandlerKstar`, `ModbusHandlerHuawei`)가 이미 있으면 동적 로딩 불필요
- 새 전용 핸들러 없이 동적 로딩만으로 사용하려면 위 필수 요소 확인

### 보호 파일

`solarize_registers.py`, `kstar_registers.py`, `huawei_registers.py` 등은 `_PROTECTED_FILES`로 보호되어 Model Maker Save로 덮어쓸 수 없음. 테스트용은 별도 protocol name 사용 (예: `newbrand_test`).

### 생성 파일 배포 절차

1. Model Maker v2 Stage3에서 `{protocol}_registers.py` 생성 → `inverter_model_maker/common/{protocol}_registers.py`에 자동 저장
2. 해당 파일을 프로젝트 `common/`에 복사: `cp inverter_model_maker/common/{protocol}_registers.py common/`
3. `config/rs485_ch*.ini`에 `protocol = {protocol}` 추가
4. RTU `modbus_handler.py`가 `load_register_module('{protocol}')` 자동 로딩
