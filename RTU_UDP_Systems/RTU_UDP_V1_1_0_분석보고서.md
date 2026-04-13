# RTU UDP V1.1.0 프로젝트 분석 보고서

**분석일**: 2026-03-31
**프로젝트**: Solarize Modbus Protocol V2.0.11 기반 RTU UDP 시스템
**제조사**: (주) 솔라라이즈
**하드웨어**: Raspberry Pi CM4 (CM4-ETH-RS485-BASE-B)

---

## 1. 프로젝트 규모

총 Python 파일 약 86개 (핵심 모듈), 총 코드 약 58,755줄 (model_maker 포함)

| 모듈 | 파일 수 | 코드 라인 | 역할 |
|------|---------|----------|------|
| rtu_program/ | 20 | 10,332 | RTU 클라이언트 (CM4 탑재) |
| common/ | 21 | 7,066 | 프로토콜 상수, 레지스터맵 |
| pc_programs/ | 4 | 5,752 | UDP 서버, 시뮬레이터, 테스트 도구 |
| web_server_prod/ | 6 | 3,680 | 운영 대시보드 (FastAPI + React) |
| model_maker/ | 22 | 25,151 | 레지스터맵 생성기 GUI |
| model_maker_web/ | 13 | 6,774 | 레지스터맵 생성기 웹 버전 |

### 핵심 파일 Top 10

| 파일 | 라인 | 역할 |
|------|------|------|
| modbus_handler.py | 3,699 | 멀티디바이스 Modbus RTU 핸들러 |
| equipment_simulator.py | 2,761 | 멀티디바이스 장비 시뮬레이터 |
| rtu_client.py | 1,653 | RTU 메인 클라이언트 |
| udp_engine.py | 1,207 | 대시보드 UDP 엔진 |
| udp_server.py | 1,183 | 운영 UDP 서버 |
| modbus_master.py | 1,006 | 범용 Modbus RTU 마스터 |
| api_routes.py | 1,065 | 대시보드 REST API |
| udp_test_server.py | 961 | 대화형 테스트 서버 |
| der_avm_slave.py | 885 | DER-AVM Modbus 슬레이브 |
| kstar_registers.py | 736 | Kstar 인버터 레지스터맵 |

---

## 2. 아키텍처 개요

시스템은 3개 축으로 구성됩니다.

### 2.1 RTU Client (CM4 현장 장비)

RTUClient 클래스가 5개 데몬 스레드를 운영합니다:

- **receive_thread**: UDP 수신 (H02 ACK, H03 제어명령, H06 ACK)
- **send_thread**: 60초 주기 H01 데이터 전송 + 백업 복구
- **modbus_poll_thread**: 10초 주기 Modbus RTU 폴링 (인버터/릴레이/기상대)
- **heartbeat_thread**: 30초 주기 H05 하트비트
- **backup_monitor_thread**: 백업 DB 감시 및 미전송 데이터 재전송

RS485 통신은 4가지 모드를 자동 감지합니다:
1. CM4 네이티브 UART (pyserial)
2. Waveshare 2-CH RS485 HAT (SPI/SC16IS752)
3. PC USB-RS485 (pymodbus)
4. 시뮬레이션 모드 (하드웨어 없이 개발용)

### 2.2 UDP Server (클라우드/PC)

- ACK-First 패턴: 데이터 수신 즉시 ACK 전송 후 파싱/저장 (RTU 대기시간 최소화)
- SQLite WAL 모드 + 배치 커밋으로 동시 다수 RTU 처리
- 중복 감지 (sequence + timestamp 기반)
- Rate limiting (RTU당 초당 10패킷 제한)
- 데이터 보존 정책: 원본 → 5분 평균 → 1시간 평균 (단계적 집계)
- 내장 FTP 서버로 펌웨어 배포

### 2.3 Production Dashboard (FastAPI + React 18)

- FastAPI 백엔드 + WebSocket 실시간 브로드캐스트
- SQLite WAL + 계층적 데이터 보존
- SFTP 경로 화이트리스트, Stale RTU 감지
- React 18 프론트엔드 (index.html + 번들)

---

## 3. 통신 프로토콜

### 3.1 패킷 구조

모든 패킷은 20바이트 공통 헤더를 공유합니다:

```
[Version(1)][Sequence(2)][RTU_ID(4)][Timestamp(8)]
[DeviceType(1)][DeviceNumber(1)][Model(1)][BackupFlag(1)][BodyType(1)]
```

Format: `>BHIQBBBBb` (Big-endian)

### 3.2 패킷 타입 (H01~H10)

| 패킷 | 방향 | 크기 | 용도 |
|------|------|------|------|
| H01 | RTU → Server | 20+44~var | 주기적 데이터 (인버터/릴레이/기상대) |
| H02 | Server → RTU | 4 | H01 ACK |
| H03 | Server → RTU | 8 | 제어 명령 (ON/OFF, 출력제한 등) |
| H04 | RTU → Server | 9 | H03 응답 (<100ms) |
| H05 | RTU → Server | 20+var | 이벤트/하트비트/RTU 정보 |
| H06 | Server → RTU | 4 | H05 ACK |
| H07 | Server → RTU | var | 펌웨어 업데이트 요청 |
| H08 | RTU → Server | 6 | 펌웨어 업데이트 응답 |
| H09 | Server → RTU | 8 | PCAP 캡처 요청 |
| H10 | RTU → Server | 8 | PCAP 캡처 응답 |

### 3.3 통신 흐름

```
RTU Client                          UDP Server
   |                                     |
   |-- H05 (First Connection) --------->|
   |<------- H06 ACK -------------------|
   |  [60초 초기 대기]                    |
   |                                     |
   |== 주기적 루프 (60초) ===============|
   |  [10초 Modbus 폴링 x 6회]          |
   |-- H01 Batch (디바이스별) --------->|
   |<------- H02 ACK (배치) ------------|
   |                                     |
   |-- H05 Heartbeat (30초) ----------->|
   |<------- H06 ACK -------------------|
   |                                     |
   |<------- H03 Control Request -------|
   |-- H04 Control Response (<100ms) -->|
   |======================================|
```

### 3.4 H01 Body 변형

| BodyType | 크기 | 설명 |
|----------|------|------|
| BASIC (1) | 44B | 기본 인버터 데이터 |
| BASIC_MPPT (2) | 44B + MPPT | MPPT 채널별 전압/전류 |
| BASIC_STRING (3) | 44B + String | 스트링별 전류 |
| BASIC_MPPT_STRING (4) | 44B + MPPT + String | 풀 데이터 |
| SINGLE_PHASE (5) | 단상 | 단상 인버터 |
| RELAY (별도) | 68B | 보호 릴레이 (3상 V/I/P/E + DO/DI) |
| WEATHER (별도) | 26B | 기상대 (온도/습도/풍속/일사량 등 13필드) |

### 3.5 제어 명령 (H03)

| Code | 명령 | 값 범위 |
|------|------|---------|
| 15 | 인버터 ON/OFF | 0=OFF, 1=ON |
| 16 | 유효전력 제한 | 0~1000 (0~100.0%) |
| 17 | 역률 | -1000~1000 (-1.0~1.0) |
| 18 | 무효전력 | signed short |
| 12 | IV Scan | 트리거 |
| 1 | RTU 재부팅 | - |

---

## 4. 지원 장비

### 인버터 (11개 모델)

| 모델번호 | 브랜드 | 프로토콜 | 레지스터맵 파일 |
|----------|--------|----------|----------------|
| 1 | Solarize | solarize | solarize_registers.py |
| 2 | Huawei | huawei | huawei_registers.py |
| 3 | Kstar | kstar | kstar_registers.py |
| 4 | Solarize 50K | solarize | (solarize 공유) |
| 7 | Sungrow | sungrow | sungrow_mm_registers.py |
| 8 | EKOS | ekos | ekos_registers.py |
| 9 | Solarize VK | solarize | (solarize 공유) |
| - | Goodwe | goodwe | goodwe_registers.py |
| - | Senergy | senergy | senergy_registers.py |

### 보호 릴레이

- KDU-300 (model=1)
- VIPAM3500C-DG (model=2)

### 기상대

- SEM5046 (model=1): 13개 센서 필드

---

## 5. 핵심 설계 패턴

### 5.1 Backup Manager (2-Tier 장애 복구)

1단계 (Short-term): 메모리 큐 + 3회 재시도
2단계 (Long-term): SQLite `rtu_backup.db`, 48시간 보존
3회 연속 ACK 실패 시 복구 모드 활성화
모든 H01 헤더에 BackupFlag(0/1)로 실시간/복구 데이터 구분

### 5.2 Dynamic Register Loading

`modbus_handler.py`의 `load_register_module()`이 protocol name으로 레지스터맵을 동적 로딩. 새 인버터 브랜드 추가 시 코드 수정 없이 `*_registers.py` 파일과 INI 설정만으로 확장 가능.

### 5.3 DER-AVM Slave

RS485 CH2에서 Modbus RTU 슬레이브로 동작. 외부 DER-AVM 마스터가 FC03(읽기)/FC06(쓰기)/FC16(다중쓰기)로 인버터 데이터 조회 및 제어 명령 전달.

### 5.4 Simulation Mode

pymodbus 미설치 또는 시리얼 포트 미설정 시 자동 활성화. 코드 변경 없이 PC에서 전체 통신 로직 테스트 가능.

---

## 6. 모듈 의존성

```
rtu_client.py
  ├── common/protocol_constants.py  (프로토콜 상수)
  ├── protocol_handler.py           (패킷 직렬화/역직렬화)
  ├── modbus_handler.py             (RS485/Modbus 통신)
  │     ├── lib/modbus_master.py    (범용 Modbus 마스터)
  │     ├── lib/rs485_channel.py    (추상 RS485 인터페이스)
  │     ├── lib/cm4_serial/         (CM4 UART 드라이버)
  │     └── lib/waveshare_2CH/      (Waveshare HAT 드라이버)
  ├── backup_manager.py             (2-tier 백업)
  ├── der_avm_slave.py              (DER-AVM 슬레이브)
  └── common/*_registers.py         (인버터별 레지스터맵)

udp_server.py (독립 실행)
  └── common/protocol_constants.py

web_server_prod/main.py
  ├── udp_engine.py                 (UDP 수신/파싱 엔진)
  ├── db.py                         (SQLite WAL 데이터베이스)
  ├── api_routes.py                 (REST API)
  ├── ws_manager.py                 (WebSocket 매니저)
  └── common/protocol_constants.py
```

---

## 7. 설정 파일 구조

| 파일 | 용도 |
|------|------|
| config/rtu_config.ini | RTU ID, 서버 주소, RS485 모드, DER-AVM 설정 |
| config/rs485_ch1.ini | CH1 디바이스 목록 (protocol, slave_id, model 등) |
| config/rs485_ch1_simulator.ini | 시뮬레이터용 CH1 설정 |
| config/device_models.ini | 모델번호 ↔ 프로토콜명 매핑 |
| config/ai_settings.ini | Model Maker AI 설정 |

---

## 8. 아키텍처 평가

### 강점

1. **무손실 데이터 보장**: BackupManager의 2-tier 설계로 네트워크 단절 시에도 48시간 데이터 보존 후 자동 복구
2. **ACK-First 패턴**: 서버가 ACK 먼저 전송 후 파싱하여 RTU 대기시간 최소화
3. **확장성 높은 레지스터맵**: 동적 로딩으로 새 인버터 추가 시 코드 수정 불필요
4. **Graceful 시뮬레이션**: 하드웨어 없이 전체 시스템 테스트 가능
5. **다중 RS485 모드**: CM4 UART / Waveshare HAT / USB-RS485 / 시뮬레이션 자동 감지
6. **Model Maker**: PDF에서 레지스터맵 자동 생성 (AI 지원 포함)

### 개선 권장사항

1. **보안**: UDP 패킷에 인증 없음. HMAC-SHA256 또는 DTLS 추가 권장
2. **modbus_handler.py 분리**: 3,699줄 단일 파일 → 브랜드별/기능별 모듈 분리 필요
3. **테스트 프레임워크**: 단위 테스트 없음. pytest + mock으로 프로토콜 핸들러/백업 매니저 테스트 추가 권장
4. **데이터베이스**: 대규모 환경(100+ RTU) 대비 SQLite → TimescaleDB/InfluxDB 전환 검토
5. **로깅 표준화**: 모듈별 로깅 레벨/포맷 통일 필요
6. **Type Hints**: 핵심 함수에 타입 힌트 추가로 유지보수성 향상

---

## 9. 배포 구조

### 런처 (.bat)

| 런처 | 대상 |
|------|------|
| START_DASHBOARD.bat | 운영 대시보드 (watchdog auto-restart) |
| START_DASHBOARD_DEV.bat | 개발 대시보드 (DB 초기화) |
| START_UDP_SERVER.bat | 운영 UDP 서버 (auto-restart) |
| START_TEST_SERVER.bat | 테스트 UDP 서버 |
| START_SIMULATOR.bat | 장비 시뮬레이터 |
| START_MODEL_MAKER.bat | 레지스터맵 생성기 |
| INSTALL_RTU_DEV.bat | RTU 원클릭 설치 (305줄) |
| SETUP_CM4_BOOT.bat | CM4 부트 설정 |

### 펌웨어 배포 플로우

1. 코드 수정 → Model Maker "Build Package" 또는 수동 tar.gz 생성
2. `pc_programs/firmware/`에 패키지 저장
3. 대시보드 Firmware 탭 → FTP로 Pi에 배포
4. watchdog_supervisor가 업데이트 감지 후 RTU 재시작

---

*보고서 생성: Claude | 분석 기준 코드 버전: V1.1.0 (Protocol V3.0.1, Program V2.2.0)*
