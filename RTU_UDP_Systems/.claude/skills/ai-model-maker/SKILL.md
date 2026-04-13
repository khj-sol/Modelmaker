---
name: ai-model-maker
description: AI로 새 인버터 Modbus PDF를 분석하여 Stage 1 매칭을 완성합니다. 모델메이커 자동 매칭이 실패하거나 X 필드가 있을 때 사용합니다.
argument-hint: "[PDF 파일 경로 또는 제조사명]"
disable-model-invocation: true
allowed-tools: Bash Read Write Edit Grep Glob
---

# AI Model Maker - 인버터 PDF 자동 매칭

새 인버터 Modbus PDF를 AI로 분석하여 H01 필드, MPPT/String, STATUS/ALARM 정의를 완벽히 매칭합니다.

## 작업 디렉토리

```
model_maker_web_v2/backend/pipeline/
├── __init__.py          # RegisterRow, detect_channel_number, 패턴 RE, synonym_db
├── stage1.py            # run_stage1, assign_h01_field, scan_definition_tables,
│                        # _scan_pdf_definitions, _parse_sunspec_text_registers,
│                        # _is_control_reg, _group_consecutive_regs, _suggest_info_field
├── rules.py             # classify_register_with_rules, _alarm_score, should_exclude
├── sunspec.py           # SunSpec 표준 처리 (is_sunspec_pdf, classify_sunspec_register,
│                        # apply_sunspec_definitions, detect_sunspec_mppt)
├── definitions/         # 제조사별 정의 JSON
│   ├── sma_definitions.json
│   ├── solaredge_definitions.json  (SunSpec 에러코드 349개)
│   └── {manufacturer}_definitions.json ...
└── solaredge_definitions.json  (레거시 위치 — definitions/ 와 동일)
```

## 실행 방법

```bash
cd C:/Users/kyuch/Solarize/CM4_4RS485/RTU_UDP/V2_0_0/.claude/worktrees/determined-volhard
python -c "
import sys, tempfile; sys.path.insert(0, '.')
from model_maker_web_v2.backend.pipeline.stage1 import run_stage1
result = run_stage1('PDF_PATH', tempfile.mkdtemp(), progress=lambda m,l='info': print(f'[{l}] {m}'))
for item in result['h01_match']['table']:
    print(item['status'], item['field'].ljust(25), str(item.get('address','-')).ljust(14), str(item.get('definition','-'))[:30])
"
```

---

## 실행 절차

### 1단계: Stage 1 자동 실행 + 진단

`run_stage1(pdf_path, output_dir)` 실행 후 H01 매칭 결과 확인:
- **O**: 매칭 완료
- **X**: 미매칭 → 2단계로 분석
- **-**: 해당 없음 (보조 슬롯)

결과 구조:
```python
result['h01_match']['matched']   # 매칭 수
result['h01_match']['total']     # 전체 필요 수
result['h01_match']['table']     # 필드별 상세 리스트
result['counts']                 # {'INFO': N, 'STATUS': N, 'ALARM': N, ...}
result['meta']                   # {'max_mppt': N, 'max_string': N, ...}
```

---

### 2단계: X 필드 AI 분석

X 필드가 있으면 PDF 텍스트를 직접 파싱:

```python
from model_maker_web_v2.backend.pipeline.stage1 import extract_pdf_text_and_tables
pages = extract_pdf_text_and_tables(pdf_path)
# pages[i]['text'], pages[i]['tables']
```

#### pv_power 미매칭
- DC power, PV power, input power, output power, pac 키워드 검색
- `assign_h01_field`에 키워드 추가 또는 HANDLER fallback

#### cumulative_energy 미매칭
- total energy, cumulative, lifetime energy, total yield, energy total, energy since 검색
- Growatt 형식: Eac/Einv total, EG4 형식: Einv all
- SunSpec 테이블 형식: I_AC_Energy_WH
- **SunSpec 텍스트 약어**: `WH` → `assign_h01_field`에서 `defn_lower.strip() == 'wh'` 정확 일치로 처리

#### status 미매칭
- inverter mode, work mode, operating mode/state, running status, device status 검색
- 단독 이름: "State", "Running", **"St"** (SunSpec 약어)
- nospace 패턴: workmode, invworkmode, sysstatemode, workingmodes, currentstatus
- `assign_h01_field` STATUS 섹션: `defn_lower.strip() in ('state', 'running', 'st')`
- **SunSpec**: `SUNSPEC_STATUS_FIELDS`에 `'st': 'STATUS'` — **짧은 패턴은 exact match 전용** (`len(pattern) > 2` 가드로 서브스트링 오매칭 방지)

#### alarm 미매칭
- fault code, error code, alarm code, warning code, faultcode, warningcode 검색
- `rules.py` `_alarm_score` 제외 규칙 (addr < 2000이면 키워드 필수)
- `distribute_alarms` 동작 확인
- **SunSpec 텍스트 약어**: `StVnd` → `SUNSPEC_ALARM_FIELDS`에 `'stvnd': 'ALARM'`
  - ⚠️ `sunspec.py`에서 **ALARM 체크를 STATUS보다 먼저 실행** 필수
    — `StVnd`는 `'st'`를 부분 포함하므로 순서가 바뀌면 STATUS로 오분류됨

#### 제어/설정 레지스터 STATUS/ALARM 오분류
- `_is_control_reg(reg)`: 정의에 reactive, power factor, active power, voltage/current/frequency limit, setting, control, threshold, setpoint, permanen, fixed, droop 포함 시 True
- STATUS/ALARM 정의 적용(`_apply_saved_definitions`, `_link_definitions_to_registers`) 시 이 레지스터는 제외

#### INFO(Model/SN) 미매칭 또는 제안 부족
- `_suggest_info_field()`: 키워드 매칭 후 후보 < 2개이면 보조 키워드로 보충
- `_group_consecutive_regs()`: 연속 주소 레지스터(SN[1]~SN[12] 등)를 단일 그룹으로 묶음
  - 표시: `0x0000~0x000B (×12regs)`, 점수 +5 보너스

#### MPPT/String 미매칭
- Vpv{N}, PV{N}Curr, Ppv{N}, PV{N}Watt, MPPT zone, DC{N}, Input {N} 패턴 검색
- SunSpec DCA/DCV/DCW 반복 블록 → `detect_sunspec_mppt` 자동 처리
- Central type (번호 없는 DC/PV/Input) → MPPT=1
- MPPT current 없으면: voltage + power → handler 계산

---

### 3단계: FC(Function Code) 구분 확인

PDF에 Input/Holding Register 섹션 구분 있는지 확인:
- Input(FC04) 키워드: "input register", "input reg", "read only register"
- Holding(FC03) 키워드: "holding register", "hold register", "parameter setting"
- 같은 주소가 FC에 따라 다른 레지스터 → FC 태깅 필요

---

### 4단계: SunSpec 감지

#### 표준 SunSpec (테이블 형식 — SolarEdge, Fronius 등)

C_SunSpec_ID, C_Manufacturer, I_Status 등 패턴 3개 이상:
- `is_sunspec_pdf(registers, manufacturer)` 자동 감지
- `apply_sunspec_definitions(categorized)` 적용:
  - I_STATUS_OFF~STANDBY → EXCLUDE (값 정의, 레지스터 아님)
  - I_Status_Vendor → ALARM + `solaredge_definitions.json` 에러코드
  - DCA/DCV/DCW 반복 블록 → MPPT별 h01_field 매핑

#### SunSpec 텍스트 형식 (SMA 등 — 테이블 없는 PDF)

표준 테이블 파싱 0개 → `_parse_sunspec_text_registers(pages)` 자동 폴백:

**형식 예시:**
```
40200 Active power (W), in WW_SF (40201): sum of all inverters...
1
int16
RO
40225
Manufacturer-specific status code (StVnd): highest error code
```

**파서 규칙:**
- `^(4[0-9]{4})\s+(.+)` 패턴으로 주소 추출
- Modbus 주소 = reg_no - 1 (1-based → 0-indexed)
- 첫 번째 영문 괄호 약어 추출: `Active power (W)` → `abbrev='W'`, `full='Active power'`
- 독립 주소 행 + 다음 줄 텍스트 자동 병합 (StVnd 형식)
- `acc32`/`uint32`/`int32` → regs=2, `acc64` → regs=4
- `pad`/`sunssf` 타입 → SKIP
- 주소 중복 제거 (Gateway/Inverter 섹션 반복 처리)

**주요 약어 분류:**

| 약어 | 설명 | 분류 | H01 필드 |
|------|------|------|---------|
| `Mn` | Manufacturer | INFO | - |
| `Md` | Model | INFO | - |
| `SN` | Serial Number | INFO | - |
| `Vr` | Version | INFO | - |
| `St` | Operating status | STATUS | inverter_status |
| `StVnd` | Manufacturer status | ALARM | alarm1 |
| `WH` | Total yield | MONITORING | cumulative_energy |
| `DCA` | DC current | MONITORING | mppt{N}_current |
| `DCV` | DC voltage | MONITORING | mppt{N}_voltage |
| `DCW` | DC power | MONITORING | (pv_power handler) |

**`is_sunspec_pdf()` 제조사명 처리:**
- `SMA-PV` → 접두사 `SMA` 추출 → `SUNSPEC_MANUFACTURERS` 검색
- 코드: `mfr_lower = manufacturer.lower().split('-')[0].split(' ')[0]`

**정의 파일:** `definitions/sma_definitions.json` — StVnd 상태 코드 13개
```json
{"alarm_codes": {"0":"Defective","10":"Boot loading","60":"Grid feed-in","80":"Standby",...}}
```

---

### 5단계: STATUS/ALARM 정의 추출

PDF에서 정의 테이블 수동 검색:

| 형식 | 예시 |
|------|------|
| hex mode | `0x00 \| Standby`, `0x01 \| Fault` |
| num mode | `0: waiting, 1: normal, 3: fault` |
| NN---mode | `00---No response mode` |
| bitfield | `Bit0 \| Grid Volt Low`, `BIT00 \| Backup overvoltage fault` |
| Value/Description 테이블 | ABB 형식 |
| Enum 인라인 | `Enum • 0x0002 Reconnecting` |
| Appendix fault codes | `002 \| 0x0002 \| Grid overvoltage \| Fault` |

발견 시 `definitions/{manufacturer}_definitions.json` 생성/업데이트:
```json
{
  "status_definitions": {"0": "Waiting", "1": "Normal", "5": "Fault"},
  "alarm_codes": {"1": "Grid Over Voltage", "2": "Grid Under Voltage"}
}
```

---

### 6단계: 결과 전달 방법 선택

| 방법 | 언제 사용 | 효과 |
|------|-----------|------|
| **코드 수정** (7단계) | 동일 브랜드 PDF가 앞으로도 계속 쓰일 때 | 영구 수정 — 이후 해당 브랜드 PDF 자동 파싱 |
| **Stage 1 Excel 전달** | 긴급하거나 특수 포맷이라 코드화 어려울 때 | 해당 고객 1회성 — 사용자가 Stage 2에서 직접 임포트 |

#### Stage 1 Excel 직접 생성 방법

PDF 텍스트를 읽어 Stage 1 Excel 형식으로 직접 작성:

```python
import tempfile, sys; sys.path.insert(0, '.')
from model_maker_web_v2.backend.pipeline.stage1 import run_stage1

# 1) 코드 수정 후 정상 파싱되면 Excel 생성
result = run_stage1(pdf_path, tempfile.mkdtemp())
print('Excel 경로:', result['output_path'])
```

또는 레지스터를 수동으로 구성하여 Stage 1 Excel 형식으로 직접 저장 (stage1.py `_write_stage1_excel` 활용).

#### 사용자 임포트 방법

```
모델메이커 Stage 2 카드
  → [📂 Stage 1 Excel 불러오기] 버튼 클릭
  → .xlsx 파일 선택
  → Stage 2 자동 활성화 → Stage 2/3 정상 진행
```

---

### 7단계: 모델 메이커 프로그램 수정

AI 분석으로 발견한 패턴이 기존 코드에 없으면:

1. `__init__.py` `detect_channel_number()`: 새 MPPT/String 패턴 추가
2. `stage1.py` `assign_h01_field()`: 새 키워드 추가
3. `stage1.py` `scan_definition_tables()`: 새 정의 테이블 패턴 추가
4. `rules.py` `classify_register_with_rules()`: 새 분류 규칙 추가
5. `rules.py` `_alarm_score()`: alarm 제외 규칙 수정
6. **SunSpec 텍스트 형식 새 약어 대응:**
   - `sunspec.py` `SUNSPEC_STATUS_FIELDS`: STATUS 약어 추가 (짧은 패턴 → exact match 전용)
   - `sunspec.py` `SUNSPEC_ALARM_FIELDS`: ALARM 약어 추가
   - `sunspec.py` `classify_sunspec_register()` INFO 텍스트 블록: 새 INFO 약어 추가
   - `stage1.py` `_parse_sunspec_text_registers()`: 파서 예외 처리 추가
   - `definitions/{manufacturer}_definitions.json`: 상태/알람 코드 추가

---

### 7단계: 검증

수정 후 회귀 테스트:

```bash
python -c "
import sys, tempfile, os; sys.path.insert(0, '.')
from model_maker_web_v2.backend.pipeline.stage1 import run_stage1
pdf_dir = 'C:/Users/kyuch/Solarize/CM4_4RS485/RTU_UDP/V2_0_0/TEST_MODBUS_PDF'
tmpdir = tempfile.mkdtemp()
for f in sorted(os.listdir(pdf_dir)):
    if not f.endswith('.pdf') or f.startswith('NONE-'): continue
    try:
        r = run_stage1(os.path.join(pdf_dir, f), tmpdir)
        h = r['h01_match']
        ok = 'OK' if h['matched']==h['total'] else f'PARTIAL {h[\"matched\"]}/{h[\"total\"]}'
        print(f'{ok:<12} {f[:55]}')
    except Exception as e:
        print(f'ERROR        {f[:55]}: {e}')
"
```

**현재 TEST_MODBUS_PDF 목록 (NONE- 제외):**
- ABB-PV, CPS-PV, EG4-HYB, ESINV-HYB, Goodwe-PV
- Growatt-HYB, Growatt-PV (×2), MUST-PV
- SMA-PV (SunSpec 텍스트), SolarEdge-PV (SunSpec 테이블)
- SchneiderElectric-PV (×2)
- SRNE-HYB (한글 파일명 인코딩 이슈 — 기존 버그)

---

### 8단계: 결과 보고

```
=== {인버터명} Stage 1 결과 ===
H01: NN/NN (100%)
INFO: N개 (Model=OK, SN=OK)
MPPT: N (V=N, I=N)
String: N (모니터링: Yes/No)
Status: {레지스터명} @ 0x{addr} (StDef=N개)
Alarm:  {레지스터명} @ 0x{addr} (AlDef=N개)
IV Scan: Yes/No
FC: 03/04 구분 Yes/No
SunSpec: Yes/No (텍스트 형식: Yes/No)
```
