# DER-AVM 통신 매뉴얼

## 1. 개요

DER-AVM(Distributed Energy Resource - Advanced Volt-Var Management)은
배전 자동화 마스터가 RTU를 통해 인버터를 제어하는 프로토콜입니다.

### 구성
```
DER-AVM Master ←(RS485 CH2)→ RTU (Slave) ←(RS485 CH1)→ 인버터
```

- **RTU**: Modbus 슬레이브로 동작 (CH2)
  - DER-AVM 마스터의 FC03/FC06/FC16 명령 수신
  - 인버터 실시간 데이터 제공 + 제어 명령 전달
- **DER-AVM 마스터**: Modbus 마스터로 동작
  - 역률, 유효/무효전력, 운전 모드 제어

---

## 2. RTU 측 설정 (DER-AVM Slave)

### rtu_config.ini
```ini
[DER_AVM]
enabled = true          # DER-AVM 통신 활성화
inverter_count = 1      # 관리 인버터 수 (1~10)
slave_id = 1            # Modbus 슬레이브 ID
baudrate = 9600         # RS485 통신 속도
channel = 2             # RS485 채널 (CH2 = ttyAMA4)
simulation = false      # 시뮬레이션 모드
```

### 레지스터 맵

#### 실시간 데이터 (FC03 읽기, 0x03E8~0x03FD)
| 주소 | 이름 | 타입 | 스케일 | 단위 |
|------|------|------|--------|------|
| 0x03E8~09 | L1 전류 | S32 | 0.1 | A |
| 0x03EA~EB | L2 전류 | S32 | 0.1 | A |
| 0x03EC~ED | L3 전류 | S32 | 0.1 | A |
| 0x03EE~EF | L1 전압 | S32 | 0.1 | V |
| 0x03F0~F1 | L2 전압 | S32 | 0.1 | V |
| 0x03F2~F3 | L3 전압 | S32 | 0.1 | V |
| 0x03F4~F5 | 유효전력 | S32 | 0.1 | kW |
| 0x03F6~F7 | 무효전력 | S32 | 1 | Var |
| 0x03F8~F9 | 역률 | S32 | 0.001 | - |
| 0x03FA~FB | 주파수 | S32 | 0.1 | Hz |
| 0x03FC~FD | 상태 플래그 | S32 | - | 비트맵 |

#### 상태 플래그 비트 (0x03FC)
| 비트 | 설명 | 값 |
|------|------|-----|
| Bit1 | 인버터 동작 | 0=정지, 1=운전 |
| Bit2 | CB 동작 | 0=미동작, 1=동작 |
| Bit3 | 운전 모드 | 0=DER-AVM, 1=자기제어 |
| Bit4 | ACK 확인 | 0=미수신, 1=수신 |

#### 제어 레지스터 (FC06/FC16 쓰기)
| 주소 | 이름 | 값 범위 | 설명 |
|------|------|---------|------|
| 0x07D0 | 역률 설정 | -1000~1000 | -1.000~1.000 |
| 0x07D1 | 동작 모드 | 0/2/5 | 0=자기제어, 2=DER-AVM, 5=Q(V) |
| 0x07D2 | 무효전력(%) | -484~484 | -48.4%~48.4% |
| 0x07D3 | 유효전력(%) | 0~1100 | 0~110.0% |
| 0x0834 | 인버터 ON/OFF | 0/1 | 0=ON, 1=OFF |

---

## 3. DER-AVM 마스터 테스트 도구

### 실행
```bash
python pc_programs/der_avm_master.py
python pc_programs/der_avm_master.py --port COM3 --slave 1
python pc_programs/der_avm_master.py --slaves 1,2,3 --auto 60
```

### 커맨드라인 옵션
| 옵션 | 설명 |
|------|------|
| `--port` | 시리얼 포트 (미지정 시 대화형 선택) |
| `--baudrate` | 통신 속도 (기본 9600) |
| `--slave` | 단일 슬레이브 ID |
| `--slaves` | 다중 슬레이브 ID (쉼표 구분) |
| `--read` | 1회 읽기 후 종료 |
| `--auto SEC` | 자동 읽기 모드 (초 간격) |
| `--list` | COM 포트 목록 표시 |

### 대화형 메뉴
| 번호 | 기능 |
|------|------|
| 1 | 실시간 데이터 읽기 (0x03E8~0x03FD) |
| 2 | 제어 파라미터 읽기 (0x07D0~0x07D3, 0x0834) |
| 3 | 역률 쓰기 (0x07D0) |
| 4 | 동작 모드 쓰기 (0x07D1) |
| 5 | 무효전력 쓰기 (0x07D2) |
| 6 | 유효전력 쓰기 (0x07D3) |
| 7 | 인버터 ON/OFF (0x0834) |
| 8 | 자동 읽기 (1분 간격, 다중 슬레이브 순환) |
| 9 | 통계 표시 |

### 브로드캐스트
- slave_id = 0 으로 쓰기 시 모든 인버터에 동시 전송
- 응답 없음 (브로드캐스트)
- 100ms 프레임 간격

---

## 4. 테스트 시나리오

### 시뮬레이터 + RTU + DER-AVM 마스터
```
[PC COM10] equipment_simulator.py  ←(RS485)→  [PC COM3] rtu_client.py
                                                    ↕ (내부 CH2)
                                              [PC COM11] der_avm_master.py
```

1. 시뮬레이터 실행 (COM10, solarize 인버터)
2. RTU 실행 (COM3, DER_AVM enabled, simulation=false)
3. DER-AVM 마스터 실행 (COM11, slave 1)
4. 마스터에서 실시간 데이터 읽기 확인
5. 역률 변경 → RTU가 인버터에 제어 전달 확인
