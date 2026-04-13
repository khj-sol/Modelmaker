# CM4 RTU 최초 리눅스 포팅 가이드

## 1. 개요

새 CM4(Compute Module 4) 보드에 OS를 설치하고, RTU UDP Client를 배포하는 전체 절차입니다.

### 전체 흐름
```
1. CM4에 Raspberry Pi OS 설치 (Raspberry Pi Imager)
2. 부트 설정 (UART 활성화)
3. RTU 프로그램 원클릭 설치 (INSTALL_RTU_DEV.bat)
4. 동작 확인
```

### 필요 장비
- Windows PC (설치 도구 실행)
- CM4-ETH-RS485-BASE-B 보드 (Raspberry Pi CM4 탑재)
- USB Type-C 케이블 (CM4 eMMC 플래싱용, SD카드 없는 모델)
- 이더넷 케이블 (PC ↔ CM4 동일 네트워크)
- RS485 장비 (인버터, 보호 릴레이, 기상센서) — 설치 후 연결

---

## 2. Raspberry Pi OS 설치

### 방법 A: Raspberry Pi Imager (온라인, 권장)

#### 2-1. Imager 설치
1. https://www.raspberrypi.com/software/ 에서 **Raspberry Pi Imager** 다운로드 및 설치

#### 2-2. CM4 eMMC 모드 진입
CM4는 SD카드 슬롯이 없는 eMMC 모델이 대부분입니다.

1. CM4 보드의 **BOOT 점퍼**(nRPIBOOT)를 **ON** 으로 설정
2. USB Type-C로 PC와 CM4 연결
3. CM4 전원 인가
4. Windows에서 **rpiboot** 도구 실행 (Raspberry Pi 공식 제공)
   - 다운로드: https://github.com/raspberrypi/usbboot/releases
   - `rpiboot.exe` 실행 → CM4 eMMC가 USB 드라이브로 인식됨

#### 2-3. OS 이미지 쓰기
1. Raspberry Pi Imager 실행
2. **장치 선택**: Raspberry Pi Compute Module 4
3. **OS 선택**: Raspberry Pi OS Lite (64-bit) — Desktop 불필요, Lite 권장
4. **저장소 선택**: CM4 eMMC 드라이브
5. **설정 (톱니바퀴 아이콘)** 클릭:
   - **호스트 이름**: `rtu-01` (구분용)
   - **SSH 활성화**: 체크
   - **사용자 이름**: `pi`
   - **비밀번호**: `raspberry` (또는 원하는 비밀번호)
   - **Wi-Fi**: 설정 불필요 (이더넷 사용)
   - **로케일**: Asia/Seoul, KR
6. **쓰기** 클릭 → 완료까지 대기 (5~15분)

#### 2-4. 쓰기 완료 후
1. USB 케이블 분리
2. **BOOT 점퍼를 OFF**로 복원 (정상 부팅 모드)
3. 이더넷 케이블 연결
4. 전원 인가 → 첫 부팅 (1~2분 소요)

### 방법 B: 오프라인 이미지 (네트워크 불가 시)

Imager로 온라인 다운로드가 안 되는 환경에서 사용합니다.

#### 준비된 이미지 파일
```
manuals\2025-12-04-raspios-trixie-arm64-lite.img.xz
```

#### 2-1. 이미지 쓰기
1. CM4 eMMC 모드 진입 (방법 A의 2-2와 동일)
2. Raspberry Pi Imager 실행
3. **OS 선택** → 맨 아래 **"사용자 정의 이미지 사용"** 클릭
4. `manuals\2025-12-04-raspios-trixie-arm64-lite.img.xz` 선택
5. **저장소**: CM4 eMMC 드라이브
6. **설정**: 방법 A의 2-3 항목 5번과 동일하게 설정 (SSH, 사용자, 로케일)
7. **쓰기** 클릭

#### 2-2. 이미지 파일이 없는 경우 미리 다운로드
```
https://www.raspberrypi.com/software/operating-systems/
→ Raspberry Pi OS Lite (64-bit) 다운로드
→ manuals/ 디렉토리에 .img.xz 파일 저장
```

### 첫 부팅 후 SSH 접속

#### CM4 IP 주소 확인
```bash
# 방법 1: mDNS (같은 네트워크)
ping rtu-01.local

# 방법 2: 공유기 관리 페이지에서 DHCP 목록 확인

# 방법 3: 네트워크 스캔 (Windows CMD)
arp -a
```

#### PuTTY로 SSH 접속 (권장)

1. **PuTTY 다운로드**: https://www.putty.org/ 에서 설치
2. PuTTY 실행 후 아래와 같이 설정:

```
┌─ PuTTY Configuration ──────────────────────┐
│                                             │
│  Host Name: <CM4_IP>   (예: 192.168.1.100) │
│  Port:      22                              │
│  Connection type: ● SSH                     │
│                                             │
│  [Saved Sessions]                           │
│  이름 입력: RTU-01                          │
│  → [Save] 클릭 (다음부터 더블클릭으로 접속)│
│                                             │
│  → [Open] 클릭                              │
└─────────────────────────────────────────────┘
```

3. 첫 접속 시 "Server's host key" 경고 → **Accept** 클릭
4. 로그인:
   - `login as:` → `pi`
   - `password:` → `raspberry` (또는 Imager에서 설정한 비밀번호)

#### PuTTY 유용한 설정

| 항목 | 경로 | 값 | 설명 |
|------|------|-----|------|
| 폰트 | Window → Appearance | Consolas 12pt | 한글 깨짐 방지 |
| 인코딩 | Window → Translation | UTF-8 | 한글 지원 |
| 키얼라이브 | Connection | 30 | 30초마다 keep-alive (연결 유지) |
| 스크롤백 | Window | 10000 | 로그 확인용 버퍼 증가 |

#### 명령줄 SSH (Windows Terminal / Git Bash)
```bash
ssh pi@<CM4_IP>
# 비밀번호: raspberry (또는 설정한 비밀번호)
```

---

## 3. UART 부트 설정 (SETUP_CM4_BOOT.bat)

CM4의 4채널 UART를 활성화합니다. **최초 1회만** 실행합니다.

### 실행
```
launchers\SETUP_CM4_BOOT.bat
```

### 변경 내용

#### /boot/firmware/config.txt에 추가
```
dtoverlay=uart0
dtoverlay=uart3
dtoverlay=uart4
dtoverlay=uart5
dtoverlay=disable-bt
enable_uart=1
```

#### 시리얼 콘솔 비활성화
```bash
# /boot/firmware/cmdline.txt에서 제거
console=serial0,115200

# 시리얼 getty 서비스 비활성화
sudo systemctl disable serial-getty@ttyAMA0
sudo systemctl disable serial-getty@ttyAMA3
sudo systemctl disable serial-getty@ttyAMA4
sudo systemctl disable serial-getty@ttyAMA5
```

### CM4-ETH-RS485-BASE-B 채널 할당

```
┌─────────────────────────────────────────────────┐
│           CM4-ETH-RS485-BASE-B 보드             │
│                                                 │
│  [CH0] /dev/ttyAMA0  ── 디버거 (콘솔/로그)     │
│  [CH1] /dev/ttyAMA3  ── 설비 통신 (인버터,     │
│                          릴레이, 기상센서)       │
│  [CH2] /dev/ttyAMA4  ── DER-AVM 마스터 연결    │
│  [CH3] /dev/ttyAMA5  ── 예비                    │
│                                                 │
│  [ETH] RJ45          ── UDP 서버 통신           │
└─────────────────────────────────────────────────┘
```

| 채널 | UART | 디바이스 | 용도 | 설정 파일 |
|------|------|---------|------|-----------|
| CH0 | UART0 | /dev/ttyAMA0 | 디버거 (시리얼 콘솔/로그 확인) | - |
| CH1 | UART3 | /dev/ttyAMA3 | 설비 통신 (인버터, 릴레이, 기상센서) | `rtu_config.ini [RS485] master_port` |
| CH2 | UART4 | /dev/ttyAMA4 | DER-AVM 마스터 연결 | `rtu_config.ini [DER_AVM] channel=2` |
| CH3 | UART5 | /dev/ttyAMA5 | 예비 (향후 확장용) | - |

**CH0 디버거 사용법**: PuTTY 등 시리얼 터미널에서 COM 포트로 직접 연결하면
CM4 리눅스 콘솔에 접속할 수 있습니다 (네트워크 없이도 가능).
보드레이트 115200, 데이터 8비트, 패리티 없음, 스톱 1비트.

---

## 4. RTU 원클릭 설치 (INSTALL_RTU_DEV.bat)

### 실행
```
launchers\INSTALL_RTU_DEV.bat
```

### 대화형 입력 항목

| 항목 | 기본값 | 설명 |
|------|--------|------|
| RTU IP | 네트워크 스캔 | CM4 IP 주소 (자동 검색 또는 수동 입력) |
| RTU ID | 10000001 | 8자리 고유 식별자 |
| RTU UDP Port | 9100 | RTU 로컬 UDP 포트 |
| Communication Period | 60 | H01 전송 주기 (초) |
| Server Host | solarize.ddns.net | 서버 호스트명 또는 IP |
| Reboot | Y | 설치 후 CM4 재부팅 여부 |

### 설치 단계 (9단계)

#### [1/9] SSH 키 설정
- PC에 SSH 키 쌍이 없으면 자동 생성 (`ssh-keygen -t rsa -b 2048`)
- CM4에 공개키 등록 (`ssh-copy-id`)
- 이후 비밀번호 없이 SSH 접속 가능

#### [2/9] 플랫폼 확인
- `/proc/device-tree/model` 읽어 CM4 확인
- CM4가 아니면 설치 중단

#### [3/9] 디렉토리 생성
```
/home/pi/
  ├── rtu_program/     # RTU 소스 코드
  ├── common/          # 공용 모듈 (레지스터맵 등)
  ├── config/          # 설정 파일
  ├── backup/          # 백업 DB
  └── logs/            # 로그 파일
```

#### [4/9] 파일 복사
- `rtu_program/*.py` → CM4
- `common/*.py` → CM4
- `config/*.ini` → CM4

#### [5/9] RTU 설정 적용
- `rtu_config.ini`에 사용자 입력값 반영 (rtu_id, port, server_host 등)

#### [6/9] 패키지 설치
```bash
sudo apt-get install p7zip-full python3-serial
pip3 install pymodbus --break-system-packages
```

#### [7/9] RS485 하드웨어 설정
- UART 오버레이 활성화 (부트 설정과 동일)
- 시리얼 콘솔 비활성화

#### [8/9] SSH 보안 강화
```
PermitRootLogin no
MaxAuthTries 5
```

#### [9/9] 시스템 서비스 등록
```ini
[Unit]
Description=RTU UDP Client Service
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/rtu_program
ExecStart=/usr/bin/python3 /home/pi/rtu_program/rtu_client.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable rtu    # 부팅 시 자동 시작
sudo systemctl start rtu     # 즉시 시작
```

### 파일 권한
| 대상 | 권한 | 설명 |
|------|------|------|
| config/*.ini | 600 | 소유자만 읽기/쓰기 |
| config/, backup/, logs/ | 700 | 소유자만 접근 |

### 설치 로그
- 위치: `%TEMP%\rtu_udp_install_YYYYMMDD_HHmmss.txt`

---

## 5. 설치 후 확인

### RTU 서비스 상태 확인
```bash
ssh pi@<RTU_IP>
sudo systemctl status rtu
```

### 로그 확인
```bash
journalctl -u rtu -f          # 실시간 로그
journalctl -u rtu --since today  # 오늘 로그
```

### RTU 수동 실행 (디버그)
```bash
sudo systemctl stop rtu                    # 서비스 중지
cd /home/pi
python3 rtu_program/rtu_client.py -d       # 디버그 모드 직접 실행
```

### RTU 서비스 재시작
```bash
sudo systemctl restart rtu
```

---

## 6. 문제 해결

| 증상 | 원인 | 해결 |
|------|------|------|
| CM4가 USB 드라이브로 안 잡힘 | BOOT 점퍼 OFF 상태 | 점퍼를 ON으로 설정 후 rpiboot 실행 |
| rpiboot 실행 안됨 | 드라이버 미설치 | usbboot releases에서 rpiboot 재설치 |
| Imager에서 OS 다운로드 실패 | 네트워크 문제 | 방법 B (오프라인 이미지) 사용 |
| 첫 부팅 후 SSH 안됨 | SSH 미활성화 | Imager 설정에서 SSH 활성화 후 재설치 |
| SSH 접속 불가 | CM4 네트워크 미연결 | 이더넷 케이블 확인, `ping <RTU_IP>` |
| 플랫폼 감지 실패 | CM4가 아닌 보드 | `/proc/device-tree/model` 확인 |
| pymodbus 설치 실패 | pip 권한 문제 | `--break-system-packages` 옵션 확인 |
| RS485 통신 안됨 | UART 미활성화 | 재부팅 필요, `ls /dev/ttyAMA*` 확인 |
| 서비스 시작 안됨 | Python 경로 오류 | `which python3` 확인 |
