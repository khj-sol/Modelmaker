# RTU 클라이언트 사용 매뉴얼

## 1. 개요

RTU UDP Client는 CM4 보드에서 실행되는 메인 프로그램입니다.
RS485로 연결된 인버터/릴레이/기상센서의 데이터를 Modbus RTU로 수집하고,
UDP 패킷(H01~H06)으로 서버에 전송합니다.

---

## 2. 실행 방법

### CM4에서 서비스로 실행 (운영)
```bash
sudo systemctl start rtu      # 시작
sudo systemctl stop rtu       # 중지
sudo systemctl restart rtu    # 재시작
sudo systemctl status rtu     # 상태 확인
```

### 수동 실행 (디버그)
```bash
# 서비스 먼저 중지
sudo systemctl stop rtu

# 직접 실행
python3 rtu_program/rtu_client.py
python3 rtu_program/rtu_client.py -d              # 디버그 로그
python3 rtu_program/rtu_client.py -s              # 시뮬레이션 모드
python3 rtu_program/rtu_client.py -c /path/config # 설정 경로 지정
```

### PC에서 시뮬레이션 실행 (개발/테스트)
```bash
python -m rtu_program.rtu_client -s
```

### 커맨드라인 옵션
| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `-c, --config` | 설정 디렉토리 경로 | `../config` (상대 경로) |
| `-s, --simulation` | 시뮬레이션 모드 (하드웨어 없이 가상 데이터) | 꺼짐 |
| `-d, --debug` | DEBUG 레벨 로깅 | INFO 레벨 |

---

## 3. 설정 파일

### rtu_config.ini (시스템 설정)

```ini
[SYSTEM]
log_level = INFO                    # 로그 레벨

[RTU]
rtu_id = 12345678                   # RTU 고유 ID (8자리)
local_port = 9100                   # UDP 로컬 포트
communication_period = 60           # H01 전송 주기 (초)

[SERVER]
mode = primary                      # primary / secondary / dual
primary_host = 172.30.1.4           # 서버 IP 또는 도메인
primary_port = 13132                # 서버 UDP 포트

[RS485]
mode = auto                         # auto / cm4_serial / hat_spi / serial
master_port = /dev/ttyAMA3          # CH1 (장비 통신)
slave_port = /dev/ttyAMA4           # CH2 (DER-AVM)
serial_port = COM3                  # PC USB-RS485 (serial 모드)
baudrate = 9600

[DER_AVM]
enabled = false                     # DER-AVM 통신 활성화
inverter_count = 1                  # 관리 인버터 수
slave_id = 1                        # Modbus 슬레이브 ID
channel = 2                         # RS485 채널 (2 = CH2)
```

### rs485_ch1.ini (장비 설정)

```ini
[device_1]
slave_id = 1                        # Modbus 슬레이브 주소
installed = YES                     # YES = 활성, NO = 비활성
device_number = 1                   # H01 패킷 내 장비 번호
device_type = 1                     # 1=인버터, 4=릴레이, 5=기상
protocol = solarize                 # 레지스터맵 프로토콜
model = 1                           # 모델 ID
mppt_count = 4                      # MPPT 수
string_count = 8                    # 스트링 수
iv_scan = true                      # IV 스캔 지원
control = DER_AVM                   # NONE / DER_AVM / ZEE
simulation = false                  # 시뮬레이션 데이터 사용
```

---

## 4. 동작 구조

### 스레드 구성
| 스레드 | 기능 | 주기 |
|--------|------|------|
| UDP-RX | H02 ACK, H03 제어명령, H06 이벤트 ACK 수신 | 상시 (0.5초 타임아웃) |
| Modbus-Poll | RS485 장비 데이터 폴링 | 10초 |
| UDP-TX | H01 주기 데이터 전송 | 60초 (설정 가능) |
| Heartbeat | H05 하트비트 전송 | 30초 |
| Backup | 백업 DB 복구 전송 | 60초 |
| DER-AVM (선택) | DER-AVM 마스터 제어 감시 | 1초 |

### 통신 흐름
```
1. 시작 → H05 최초 연결 전송
2. 60초 대기 (서버 등록 시간)
3. 매 60초: Modbus 데이터 수집 → H01 패킷 생성 → UDP 전송
4. H02 ACK 수신 → 정상 확인
5. H03 수신 시: 제어 실행 → H04 응답 → H05 결과 전송
```

### 백업 시스템
- H02 ACK를 30초 내 수신하지 못하면 **백업 DB**에 저장
- 연속 3회 실패 시 **복구 모드** 진입
- 서버 연결 복구 시 백업 데이터 자동 재전송
- 48시간 이상 된 백업은 자동 삭제

---

## 5. 시뮬레이션 모드

pymodbus가 없거나 RS485 포트가 없으면 **자동으로** 시뮬레이션 모드가 활성화됩니다.

시뮬레이션 데이터 특성:
- 시간 기반 일사량 패턴 (6AM 일출 → 12PM 피크 → 6PM 일몰)
- PV 전압: 200~450V (일사량 비례)
- AC 전력: 일사량 × 정격용량
- 보호 릴레이: 3상 220V ±5V, 주파수 60Hz
- 기상센서: 일사량 0~1000 W/m², 온도 15~35°C

---

## 6. 제어 명령 (H03)

서버에서 수신하는 제어 명령:

| 명령 | 코드 | 값 범위 | 설명 |
|------|------|---------|------|
| 인버터 ON/OFF | 1 | 0=ON, 1=OFF | 인버터 운전/정지 |
| 유효전력 제한 | 3 | 0~1000 | 0~100.0% |
| 역률 | 4 | -1000~1000 | -1.000~1.000 |
| 무효전력 | 5 | -484~484 | -48.4%~48.4% |
| 제어 초기화 | 6 | 0 | 제어값 초기화 |
| 제어 상태 확인 | 13 | 0 | 현재 제어 상태 조회 |
| IV 스캔 | 11 | 0 | I-V 특성 곡선 측정 |
| RTU 재부팅 | 21 | 0 | RTU 재시작 |

---

## 7. 워치독 (Watchdog Supervisor)

RTU 프로세스를 감시하고 비정상 종료 시 자동 재시작합니다.

### 실행
```bash
python3 rtu_program/watchdog_supervisor.py
```

### 감시 방식
| 항목 | 값 | 설명 |
|------|-----|------|
| 체크 주기 | 5초 | 프로세스 상태 확인 |
| 하트비트 파일 | `/tmp/rtu_heartbeat` | RTU가 주기적으로 갱신 |
| 타임아웃 | 30초 | 하트비트 미갱신 시 프리즈 판정 |
| 재시작 대기 | 5초 | 재시작 전 대기 시간 |

### systemd 등록 (운영)
```ini
[Unit]
Description=RTU Watchdog Supervisor
After=rtu.service

[Service]
Type=simple
User=pi
ExecStart=/usr/bin/python3 /home/pi/rtu_program/watchdog_supervisor.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

---

## 8. 펌웨어 업데이트

서버에서 H07 명령을 보내면 RTU가 자동으로 펌웨어를 업데이트합니다.

### 절차
1. 서버가 H07 (FTP 정보 포함) 전송
2. RTU가 FTP에서 `.tar.gz` 다운로드
3. 현재 코드 백업 (`/home/pi/backup/`)
4. 새 코드 적용 (설정 병합)
5. H08 완료 응답 전송
6. RTU 서비스 재시작
