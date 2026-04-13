# 운영 UDP 서버 사용 매뉴얼

## 1. 개요

웹 UI 없이 UDP 데이터 수집만 수행하는 독립형 서버입니다.
SQLite에 데이터를 저장하고, 중복 감지 및 속도 제한 기능을 포함합니다.

---

## 2. 실행 방법

### 배치파일 실행
```
launchers\START_UDP_SERVER.bat
```
- 자동 재시작 포함 (비정상 종료 시 5초 후 재실행)

### 직접 실행
```bash
python pc_programs/udp_server.py
python pc_programs/udp_server.py --port 13132
python pc_programs/udp_server.py --db mydata.db --retention-days 60
python pc_programs/udp_server.py --headless --log-level DEBUG
```

### 커맨드라인 옵션
| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `--port` | 13132 | UDP 수신 포트 |
| `--db` | udp_server.db | SQLite DB 파일 경로 |
| `--log-level` | INFO | 로그 레벨 |
| `--log-dir` | logs/ | 로그 디렉토리 |
| `--retention-days` | 30 | 데이터 보존 기간 (일) |
| `--headless` | 꺼짐 | 대화형 메뉴 없이 실행 |

---

## 3. 주요 기능

### 데이터 수신 및 저장
- H01 패킷 수신 → H02 ACK 즉시 전송 (ACK-first 패턴)
- 인버터/릴레이/기상 데이터 SQLite 저장
- 배치 커밋 (100건 또는 2초 간격)

### 중복 감지
- 300초 TTL, 최대 50,000건 캐시
- 동일 패킷 재전송 방지

### 속도 제한
- 장비별 최소 10초 간격
- 과도한 데이터 저장 방지

### RTU 연결 추적
- RTU별 마지막 통신 시간 기록
- 120~360초 무응답 시 오프라인 판정

### FTP 서버 (내장)
- 포트: 21
- 루트: `pc_programs/firmware/`
- 기본 계정: rtu / 1234
- pyftpdlib 필요 (없으면 FTP 비활성)

### 로그
- 로테이팅 파일 핸들러 (10MB x 5백업)
- 형식: `YYYY-MM-DD HH:MM:SS [LEVEL] name: message`

---

## 4. 데이터베이스 스키마

### 주요 테이블
| 테이블 | 설명 |
|--------|------|
| rtu_registry | RTU 등록 정보 (ID, IP, 최종 통신 시간) |
| inverter_data | 인버터 주기 데이터 |
| relay_data | 릴레이 주기 데이터 |
| event_log | 이벤트 로그 (H05) |

### 자동 정리
- `--retention-days` 기간 초과 데이터 자동 삭제
- WAL 모드로 동시 읽기/쓰기 지원
