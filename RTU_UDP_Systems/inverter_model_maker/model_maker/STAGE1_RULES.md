# Stage 1 — 레지스터 추출 기본 룰

PDF 또는 Excel 파일에서 인버터 Modbus 레지스터맵을 추출하는 기준을 정의한다.

---

## 1. 추출 대상 파일

- Modbus RTU 프로토콜 문서 (PDF 또는 Excel)
- 추출 대상: **읽기(Read) 레지스터** 위주 (FC03, FC04)
- 쓰기(Write) 레지스터는 DER-AVM 제어 항목에 해당하는 경우에만 포함

---

## 2. H01 모니터링 데이터 추출 (Body Type 4)

RTU가 서버로 전송하는 H01 패킷의 인버터 모니터링 데이터에 해당하는 레지스터를 추출한다.

### 2-1. 필수 추출 항목 (있으면 반드시 포함)

| 항목 | 설명 | 단위 |
|------|------|------|
| AC 전압 (R/S/T 또는 Line) | 계통 연계 출력 전압 | V |
| AC 전류 (R/S/T) | 계통 연계 출력 전류 | A |
| AC 출력 전력 | 유효전력 (Active Power) | W / kW |
| AC 주파수 | 계통 주파수 | Hz |
| 역률 (Power Factor) | | - |
| 무효전력 (Reactive Power) | | VAr / kVAr |
| 피상전력 (Apparent Power) | 있으면 포함 | VA / kVA |
| 일일 발전량 (Daily Energy) | | kWh |
| 누적 발전량 (Total Energy) | | kWh |
| 인버터 상태 / 운전 모드 | 동작 상태 코드 | - |
| 인버터 온도 | 방열판 또는 내부 온도 | °C |
| 오류/경보 코드 | Fault / Alarm / Warning | - |

### 2-2. 인버터 상태 및 알람 레지스터 추출 규칙

인버터 상태와 알람은 레지스터 주소뿐 아니라 **각 값/비트의 의미 정의도 함께 추출**한다.

#### 인버터 상태 레지스터

- 레지스터 주소, 데이터 타입 추출
- **상태 코드 정의 테이블 추출** (값 → 상태명 매핑)

| 추출 예시 | |
|-----------|--|
| `0x00` | Initial / Waiting |
| `0x01` | Standby |
| `0x02` | On-Grid (Normal) |
| `0x03` | Fault / Error |
| `0x04` | Shutdown |
| 기타 | PDF에 정의된 모든 상태 코드 포함 |

- 용어 탐색: `Running Status`, `Operating State`, `Inverter State`, `Work Mode`, `System State`

#### 알람/오류 레지스터

- 알람 레지스터가 **복수**인 경우 (Alarm1, Alarm2, Fault Code 등) **전부 추출**
- **비트별 정의 테이블 추출** (비트 번호 → 알람 내용 매핑)

| 추출 예시 | |
|-----------|--|
| Bit 0 | Grid Overvoltage |
| Bit 1 | Grid Undervoltage |
| Bit 2 | Grid Overfrequency |
| ... | PDF에 정의된 모든 비트 포함 |

- 값 기반 알람 코드(비트맵이 아닌 단일 코드값)인 경우에도 **코드 → 내용 테이블** 추출
- 용어 탐색: `Alarm Code`, `Fault Code`, `Warning Code`, `Error Code`, `Protection Flag`

#### H01 alarm1/2/3 배분 규칙

추출된 알람/상태 정의를 H01의 `alarm1`, `alarm2`, `alarm3` (각 2byte = 16bit) 에 배분한다.

**H01 알람 필드 용량:**

| 필드 | 크기 | 수용 가능 비트 수 |
|------|------|-----------------|
| `alarm1` | 2byte (U16) | 16비트 |
| `alarm2` | 2byte (U16) | 16비트 |
| `alarm3` | 2byte (U16) | 16비트 |
| **합계** | **6byte** | **최대 48비트** |

**배분 원칙:**

1. PDF 알람 레지스터가 1개인 경우 → `alarm1`에 그대로 배분, `alarm2/3 = 0`
2. PDF 알람 레지스터가 2개인 경우 → `alarm1`, `alarm2` 순서대로 배분, `alarm3 = 0`
3. PDF 알람 레지스터가 3개 이상인 경우 → 중요도 높은 순서로 `alarm1` → `alarm2` → `alarm3` 배분
4. 인버터 상태 레지스터가 별도로 있는 경우 → H01 `status` 필드(2byte)에 배분 (alarm과 분리)

**알람 정의가 48비트를 초과하는 경우 (우선순위 기준 축약):**

| 우선순위 | 항목 유형 | 처리 |
|---------|----------|------|
| 1순위 | 계통 보호 (과전압/저전압/과전류/주파수) | 반드시 포함 |
| 2순위 | 인버터 하드웨어 보호 (IGBT, 과온, 단락) | 반드시 포함 |
| 3순위 | PV 입력 보호 (PV 과전압/과전류) | 포함 |
| 4순위 | 통신/기타 알람 | 공간이 남으면 포함, 초과 시 제외 |

- 제외된 항목은 `alarm_dropped` 컬럼에 기록하여 Stage 2에서 확인 가능하게 한다
- 비트 번호는 PDF 원본 기준을 유지하되, 레지스터 간 재배치 시 새 비트 위치를 명시한다

#### 추출 누락 방지 원칙

- 상태/알람 정의 테이블이 PDF 내 별도 페이지나 부록에 있는 경우도 반드시 탐색하여 포함
- 정의 테이블을 찾지 못한 경우 `정의 미확인` 으로 표시하고 Stage 2에서 수동 보완

### 2-3. PV 입력 (MPPT / String)

- **MPPT별 전압·전류** — 반드시 추출 (채널 수에 관계없이 전부)
- **String별 전류** — 있으면 추출 (String 수에 관계없이 전부)
- MPPT만 있고 String 정보가 없는 인버터도 정상 처리

#### H01 PV 전압 계산 규칙

H01의 `pv_voltage`(단일값)는 MPPT 전압들로부터 다음 규칙으로 계산한다.

> **100V 초과 MPPT 전압만 유효로 판단하여 평균**한다.
> (100V 이하는 해당 MPPT에 PV가 연결되지 않은 것으로 간주)

```
pv_voltage = average(MPPTn_voltage for n if MPPTn_voltage > 100V)
```

- 유효 MPPT가 없으면 `pv_voltage = 0`
- 스케일 적용 후 비교 (예: 레지스터값 1000 → 100.0V)

#### H01 PV 전류 계산 규칙

H01의 `pv_current`(단일값)는 아래 우선순위로 합산한다.

| 우선순위 | 조건 | 계산 방법 |
|---------|------|----------|
| 1순위 | String별 전류 레지스터가 있는 경우 | **전체 String 전류 합계** |
| 2순위 | String 레지스터 없는 경우 | **전체 MPPT 전류 합계** |

```
# String 있는 경우
pv_current = sum(STRINGn_current for all n)

# String 없는 경우
pv_current = sum(MPPTn_current for all n)
```

#### String 전압 처리 규칙

PDF에 String별 전압 레지스터가 없는 경우:
- **같은 MPPT에 연결된 모든 String의 전압 = 해당 MPPT 전압**으로 처리

이를 위해 **MPPT 개수**와 **MPPT별 String 개수**가 반드시 필요하다.

| 정보 | 출처 |
|------|------|
| MPPT 개수 | PDF 사양표 우선, 없으면 레지스터에서 확인 |
| MPPT별 String 개수 | PDF 사양표 우선, 없으면 **사용자가 Model Maker에서 직접 입력** |

> PDF에 MPPT/String 구성 정보가 없으면 Stage 1 결과에 `mppt_count: 미확인`, `strings_per_mppt: 미확인` 으로 표시하고 Model Maker UI에서 사용자 입력을 받는다.

### 2-4. 레지스터 이름 규칙

추출된 레지스터 이름은 **제조사 원문 표현을 그대로 사용**한다.

- PDF/Excel에 기재된 필드명을 원본 언어(영문) 그대로 유지
- 공백은 `_`로 치환, 특수문자 제거
- 예: `Grid Total Active Power` → `GRID_TOTAL_ACTIVE_POWER`
- **Solarize 표준 이름으로 변환하지 않는다** (Stage 2에서 매핑)
- 추출된 원문 이름들은 추후 **제조사별 용어 동의어 사전** 구축에 활용된다

### 2-5. 용어 매핑 원칙

용어가 달라도 **개념이 동일하면 같은 항목**으로 처리한다.

| 문서 표현 예시 | 매핑 항목 |
|---------------|-----------|
| Output Power, Active Power Output, Grid Power | AC 출력 전력 |
| Grid Voltage, Line Voltage, Phase Voltage | AC 전압 |
| PV1 Voltage, MPPT1 Voltage, DC Input 1 Voltage | MPPT 전압 |
| String1 Current, PV String1 Current | String 전류 |
| Today Energy, Daily Generation, Daily Yield | 일일 발전량 |
| Total Energy, Cumulative Energy, Lifetime Energy | 누적 발전량 |
| Run Status, Operating Mode, Inverter State | 인버터 상태 |

---

## 3. 인버터 정보 레지스터 추출

인버터 식별 정보 및 사양 레지스터는 **H01 모니터링과 무관하더라도 무조건 추출**한다.
인버터 정보 요청(모델 확인, 펌웨어 조회 등)에 대응하기 위해 레지스터맵에 반드시 포함한다.

### 3-1. 필수 추출 항목

| 항목 | 설명 | H01 활용 |
|------|------|---------|
| 모델명 (Device Model) | 인버터 모델 문자열 | H05 등록, 모델 확인 |
| 시리얼 번호 (Serial Number) | 고유 식별 번호 | 인버터 식별 |
| 펌웨어 버전 (Firmware Version) | 마스터/슬레이브/EMS 등 | 버전 조회 |
| MPPT 개수 (MPPT Count) | MPPT 채널 수 | **PV 전압 평균 계산에 필수** |
| 정격 출력 (Nominal Power) | 정격 전력 (W) | 이상값 필터 기준 |
| 정격 전압 (Nominal Voltage) | 계통 기준 전압 | 계통 판단 기준 |
| 정격 주파수 (Nominal Frequency) | 계통 기준 주파수 | 계통 판단 기준 |
| 상 수 (Grid Phase Number) | 단상/삼상 | r/s/t 채널 유효 여부 결정 |

### 3-2. 처리 원칙

- 모델명·시리얼·펌웨어는 **H01에 미사용**이지만 레지스터맵에서 제외하지 않는다
- `MPPT_COUNT` 레지스터가 있으면 PDF 사양표보다 **레지스터 값을 우선** 신뢰
- 정격 출력이 U32(2레지스터)인 경우 LOW/HIGH 쌍으로 모두 추출

---

## 4. DER-AVM 제어/모니터링 데이터 추출

**모든 인버터는 DER-AVM을 지원한다고 가정한다.** PDF에 DER-AVM 항목이 없어도 아래 기준으로 해당 레지스터를 찾아 추출한다.

### 3-1. DER-AVM 제어 항목 (Write 레지스터 포함)

| 제어 항목 | 설명 |
|-----------|------|
| 유효전력 제한 (Active Power Limit) | 출력 제한 (0~100% 또는 0~정격W) |
| 역률 설정 (Power Factor Set) | 지상/진상 역률 제어 |
| 무효전력 설정 (Reactive Power Set) | Q 설정 (VAr 또는 %) |
| 운전/정지 (ON/OFF) | 인버터 기동/정지 명령 |
| 운전 모드 설정 | 일반/제한/무효전력 모드 등 |

### 3-2. DER-AVM 모니터링 항목

H01 데이터와 겹치는 항목은 동일 레지스터를 공유한다. 추가로 아래 항목도 추출한다.

| 모니터링 항목 | 설명 |
|--------------|------|
| 현재 유효전력 제한값 | 설정된 출력 제한 읽기 |
| 현재 역률 설정값 | 설정된 역률 읽기 |
| 현재 무효전력 설정값 | 설정된 Q 읽기 |
| 현재 운전 모드 | 현재 적용 중인 운전 모드 |

### 3-3. 레지스터 탐색 방법

PDF에 명시적으로 없는 경우 아래 방법으로 유추한다.

1. **유사 용어 검색**: Power Limit, Output Regulation, Curtailment, Derate, Cos φ, PF Control, Q Control, VAr Control
2. **쓰기 가능 레지스터** 중 전력·역률·무효전력 관련 항목 우선 채택
3. 찾지 못한 항목은 빈 값(`-`)으로 표시하고 Stage 2에서 수동 매핑

---

## 5. IV 스캔 지원 여부 판단

IV 스캔은 **모든 인버터가 지원하지 않는다.** 아래 기준 중 하나라도 해당하면 IV 스캔을 지원하는 것으로 처리한다.

### 4-1. 현재 IV 스캔 지원 인버터 (확정)

- Solarize, Kstar, Senergy

### 4-2. PDF/Excel에서 IV 스캔 지원 자동 감지 조건

다음 중 하나라도 해당하면 IV 스캔 지원으로 판단한다.

| 조건 | 설명 |
|------|------|
| IV Start 레지스터 존재 | IV 스캔 시작 명령 레지스터 (Write) |
| IV 상태 레지스터 존재 | IV 스캔 진행/완료 상태 레지스터 (IV Start와 동일 레지스터일 수 있음) |
| IV 스캔 결과 레지스터 존재 | 스트링당 복수의 V·I 포인트 결과 레지스터 블록 |

### 4-3. 탐색 용어

| 문서 표현 예시 |
|---------------|
| IV Scan, IV Curve, I-V Scan, IV Test |
| IV Start, Start IV, IV Trigger |
| IV Status, IV State, IV Result |
| IV Point, IV Data, String IV |

### 4-4. 사용자 지정

위 조건에 해당하지 않더라도 **사용자가 IV 스캔 지원을 명시적으로 알려주는 경우** 지원으로 처리한다. 이 경우 IV 관련 레지스터 주소를 사용자에게 확인한다.

### 4-5. IV 스캔 추출 항목

IV 스캔 지원으로 판단된 경우 아래 항목을 추출한다.

| 항목 | 설명 |
|------|------|
| IV Start / Status 레지스터 | 시작 명령 및 상태 확인 |
| IV 결과 데이터 블록 | 스트링당 V·I 포인트 (포인트 수 함께 기록) |

---

## 6. 제외 항목

- **REMS 프로토콜** 관련 레지스터는 제외 (아래 근거 참조)
- 통신 설정 레지스터 (Baud Rate, Slave ID 등) 제외
- 보호 파라미터 설정 레지스터 (과전압 보호값 등) 제외
- 펌웨어 버전, 시리얼 번호 등 식별 정보는 **제외하지 않고 섹션 3(인버터 정보)으로 추출**

### REMS 제외 근거

REMS 프로토콜 데이터 항목은 H01 Body Type 4(인버터)와 비교 시 H01이 상위 호환이므로 별도 추출이 불필요하다.

| 항목 | REMS 단상 (26B) | REMS 삼상 (38B) | H01 인버터 | 비고 |
|------|----------------|----------------|-----------|------|
| PV 전압 | ① 2byte | ① 2byte (평균) | `pv_voltage` 2byte | 동일 |
| PV 전류 | ② 2byte | ② 2byte (합) | `pv_current` 2byte | 동일 |
| PV 출력 | ③ 2byte | ③ 4byte | `pv_power` 4byte | H01은 항상 4byte |
| 계통 전압 | ④ 단상 2byte | ④⑤⑥ 3상 각 2byte | `r/s/t_voltage` 각 2byte | H01은 항상 3상 |
| 계통 전류 | ⑤ 단상 2byte | ⑦⑧⑨ 3상 각 2byte | `r/s/t_current` 각 2byte | H01은 항상 3상 |
| 현재 출력 | ⑥ 2byte | ⑩ 4byte | `ac_power` 4byte | H01은 항상 4byte |
| 역률 | ⑦ 2byte | ⑪ 2byte | `power_factor` 2byte (×10배) | 동일 |
| 주파수 | ⑧ 2byte | ⑫ 2byte | `frequency` 2byte (×10배) | 동일 |
| 누적 발전량 | ⑨ 8byte | ⑬ 8byte | `cumulative_energy` 8byte | 동일 |
| 고장여부 | ⑩ 2byte (16비트 플래그) | ⑭ 2byte | `alarm1/2/3` 각 2byte = 6byte | **H01이 더 세분화** |
| MPPT 전압·전류 | **없음** | **없음** | 가변 (채널×4byte) | **H01만 있음** |
| String 전류 | **없음** | **없음** | 가변 (스트링×2byte) | **H01만 있음** |
| 인버터 상태 | **없음** | **없음** | `status` 2byte | **H01만 있음** |

**고장여부 비교:**
- REMS: 16비트 단일 워드 (Bit0=동작유무, Bit1=PV과전압, Bit2=PV저전압, Bit3=PV과전류, Bit4=IGBT에러, Bit5=인버터과온, Bit6=계통과전압, Bit7=계통저전압, Bit8=계통과전류, Bit9=계통과주파수, Bit10=계통저주파수, Bit11=단독운전, Bit12=지락)
- H01: `alarm1`(2byte) + `alarm2`(2byte) + `alarm3`(2byte) = 48비트 — 인버터 제조사별 세부 고장코드를 그대로 수용

**결론:** H01은 REMS 항목을 모두 포함하며 MPPT/String/인버터상태/세분화된 알람까지 추가로 지원한다. REMS 전용 레지스터를 별도 추출할 필요가 없다.

---

## 7. 추출 결과 카테고리 분류

추출된 모든 레지스터는 아래 카테고리 중 하나로 분류한다.

| 카테고리 | 설명 |
|---------|------|
| `INFO` | 인버터 정보 (모델명, 시리얼, 펌웨어, 정격값) |
| `MONITORING` | H01 모니터링 데이터 (PV, AC, 에너지, 온도) |
| `STATUS` | 인버터 운전 상태 레지스터 |
| `ALARM` | 알람/오류/고장 레지스터 |
| `DER_CONTROL` | DER-AVM 제어 (Write) |
| `DER_MONITOR` | DER-AVM 모니터링 (Read) |
| `IV_SCAN` | IV 스캔 제어/결과 |
| `REVIEW` | H01 매핑이 불명확하거나 용어가 애매한 항목 — 사용자 확정 필요 |

### `REVIEW` 카테고리 분류 기준

제조사마다 용어가 다르고(영문/한글 혼용) H01 프로토콜과의 대응이 불명확한 항목은 자동으로 `REVIEW`로 분류하여 사용자가 확인 후 확정한다.

| 상황 | 예시 |
|------|------|
| H01 필드와 유사하지만 용어가 달라 확신 불가 | `Output Current` → `r_current`인지 `pv_current`인지 불명확 |
| 단위·스케일이 특이해서 변환 방법 불확실 | `Power in 0.01kW` |
| 한글 용어라 의미 파악 필요 | `계통연계출력`, `직류입력전류` |
| 같은 물리량으로 보이는 레지스터가 중복 존재 | `Active Power`와 `Output Power` 둘 다 있는 경우 |
| DER-AVM 해당 여부가 불분명한 Write 레지스터 | `Power Setting` 류 |
| 알람 비트 정의를 찾지 못한 경우 | 비트 정의 미확인 항목 |

`REVIEW` 항목은 Stage 1 결과 Excel에 **별도 시트 또는 하이라이트**로 표시한다.

---

## 8. Stage 1 / Stage 2 역할 분리

### Stage 1 — PDF 전체 추출 (MPPT/String 제한 없음)

Stage 1은 PDF에 정의된 **모든 MPPT 채널과 모든 String 채널**에 대한 레지스터를 예외 없이 추출한다.

- PDF에 MPPT 9채널이 정의되어 있으면 MPPT1~9 전부 추출
- PDF에 String 24채널이 정의되어 있으면 String1~24 전부 추출
- 인버터 실제 탑재 채널 수와 관계없이 **PDF 기준 최대 채널 수로 추출**
- IV 스캔 데이터 블록도 지원 트래커 수 전부 추출

> 이 방식으로 Stage 1 결과 하나로 해당 제조사의 모든 용량 모델에 대응한다.

### Stage 2 — 인버터 용량별 필터링

Stage 2에서 사용자가 실제 인버터의 **MPPT 수**와 **String 수**를 입력하면,
Stage 1에서 추출한 레지스터맵에서 해당 채널에 해당하는 레지스터만 추출하여 최종 파일을 생성한다.

| 입력 항목 | 설명 |
|----------|------|
| `mppt_count` | 실제 사용할 MPPT 채널 수 (예: 4) |
| `strings_per_mppt` | MPPT당 String 수 (예: 2) |
| `total_strings` | 총 String 수 = `mppt_count × strings_per_mppt` |

**필터링 기준:**
- `MPPT{n}_VOLTAGE / CURRENT / POWER` — `n ≤ mppt_count`인 채널만 포함
- `STRING{n}_VOLTAGE / CURRENT` — `n ≤ total_strings`인 채널만 포함
- IV 스캔 트래커 — `tracker ≤ mppt_count`인 블록만 포함
- INFO, MONITORING, STATUS, ALARM, DER_CONTROL, DER_MONITOR 항목은 채널 수와 무관하게 전부 포함

---

## 9. Stage 2 인계 원칙

- **애매한 항목은 제외하지 않고 포함**한다. `REVIEW`로 분류하여 Stage 2에서 사용자가 확정한다.
- 유사 항목이 여러 개이면 모두 추출해두고 Stage 2에서 최적 항목을 선택한다.
- 스케일(Scale) 정보가 있으면 반드시 함께 기록한다.
- 데이터 타입(U16, S16, U32, S32)과 레지스터 주소를 정확히 기록한다.
