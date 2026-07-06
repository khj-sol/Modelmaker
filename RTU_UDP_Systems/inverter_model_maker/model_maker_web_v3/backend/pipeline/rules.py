# -*- coding: utf-8 -*-
"""
STAGE1_RULES.md + SKILL.md 규칙을 Python 로직으로 구현

규칙 원본: model_maker/STAGE1_RULES.md
각 함수에 해당 규칙 섹션 번호를 주석으로 표기
"""
import re
from typing import List, Dict, Optional, Set

from . import (
    RegisterRow, to_upper_snake, parse_address, detect_channel_number,
    match_synonym, match_synonym_fuzzy,
    get_ref_name_by_addr, get_h01_field_from_ref, detect_channel_from_ref,
)
from .sunspec import classify_sunspec_register


# ═══════════════════════════════════════════════════════════════════════════
# §1. 추출 대상 판단
# ═══════════════════════════════════════════════════════════════════════════

def is_read_register(reg: RegisterRow) -> bool:
    """§1: 읽기(Read) 레지스터 위주, Write는 DER-AVM만 포함"""
    rw = (reg.rw or '').upper()
    if rw in ('WO', 'WRITE'):
        # DER-AVM 관련이면 포함
        defn = reg.definition.lower()
        if any(k in defn for k in ['power factor', 'reactive power', 'active power',
                                    'on/off', 'on off', 'operation mode', 'action mode',
                                    'curtailment', 'derate', 'iv scan', 'iv curve',
                                    'inverter control', 'power derating',
                                    '역률', '무효전력', '유효전력', '출력제한']):
            return True
        return False
    return True  # RO, RW, R/W 등


# ═══════════════════════════════════════════════════════════════════════════
# §2-1. H01 필수 추출 항목
# ═══════════════════════════════════════════════════════════════════════════

H01_REQUIRED_FIELDS = {
    'ac_voltage':     {'keywords': ['voltage', 'grid voltage', '전압', '계통전압'],
                       'unit': 'V', 'category': 'MONITORING'},
    'ac_current':     {'keywords': ['current', 'grid current', '전류', '계통전류'],
                       'unit': 'A', 'category': 'MONITORING'},
    'ac_power':       {'keywords': ['active power', 'output power', 'grid power',
                                    '유효전력', '출력전력', 'ac power'],
                       'unit': 'W', 'category': 'MONITORING'},
    'frequency':      {'keywords': ['frequency', '주파수', '계통주파수'],
                       'unit': 'Hz', 'category': 'MONITORING'},
    'power_factor':   {'keywords': ['power factor', 'pf', '역률', 'cos phi'],
                       'category': 'MONITORING'},
    'reactive_power': {'keywords': ['reactive power', 'q power', '무효전력'],
                       'unit': 'VAr', 'category': 'MONITORING'},
    'apparent_power': {'keywords': ['apparent power', 'va power', '피상전력'],
                       'unit': 'VA', 'category': 'MONITORING'},
    'daily_energy':   {'keywords': ['daily energy', 'today energy', '일일발전량',
                                    '금일발전량', 'daily yield', 'daily generation'],
                       'unit': 'kWh', 'category': 'MONITORING'},
    'total_energy':   {'keywords': ['total energy', 'cumulative energy', '누적발전량',
                                    '총발전량', 'lifetime energy', 'accumulated energy'],
                       'unit': 'kWh', 'category': 'MONITORING'},
    'inverter_status': {'keywords': ['inverter mode', 'running status', 'operating state',
                                     'inverter state', 'work mode', '인버터상태', '운전상태'],
                        'category': 'STATUS'},
    'temperature':    {'keywords': ['temperature', 'inner temp', 'heatsink temp',
                                    '온도', '방열판온도', '내부온도', 'module temp'],
                       'unit': '°C', 'category': 'MONITORING'},
    'alarm':          {'keywords': ['error code', 'fault code', 'alarm code', 'warning code',
                                    '오류코드', '고장코드', '알람', 'protection flag'],
                       'category': 'ALARM'},
}


# ═══════════════════════════════════════════════════════════════════════════
# §2-2. 알람 배분 규칙 (H01 alarm1/2/3)
# ═══════════════════════════════════════════════════════════════════════════

ALARM_PRIORITY = [
    # 1순위: 계통 보호
    {'keywords': ['grid over', 'grid under', 'over voltage', 'under voltage',
                  'over frequency', 'under frequency', 'over current',
                  '계통과전압', '계통저전압', '계통과주파수'],
     'priority': 1},
    # 2순위: 인버터 하드웨어 보호
    {'keywords': ['igbt', 'over temperature', 'short circuit', '과온', '단락', 'boost'],
     'priority': 2},
    # 3순위: PV 입력 보호
    {'keywords': ['pv over', 'pv under', 'dc over', 'pv insulation', 'string abnormal'],
     'priority': 3},
    # 4순위: 통신/기타
    {'keywords': ['communication', 'comm', 'eeprom', 'internal', 'logger', 'meter'],
     'priority': 4},
]


def _alarm_score(reg: RegisterRow) -> int:
    """알람 레지스터 우선순위 — 상태정의(Appendix 비트필드)가 있는 레지스터 우선
    PDF 정의 순서대로 alarm1/2/3 매칭. 같은 score면 주소순.
    """
    # 정규화: 소문자 + underscore→space (enrichment 후 FAULT_ALARM_TIME_YEAR 같은
    # 형태도 'fault alarm time year' 로 매칭되도록)
    defn_lower = (reg.definition or '').lower().replace('_', ' ')
    addr = reg.address if isinstance(reg.address, int) else 0

    # ── 제외 (score 99) ──
    if addr > 0xFFFF:
        return 99
    # Appendix Fault Code 값이 레지스터로 잘못 추출 — 주소가 비정상적으로 작음
    # Goodwe는 주소 500+ 유효, EKOS SPD는 0x000B=11
    # Sungrow Appendix: 주소 0~600 범위의 fault code 값
    if 0 < addr < 2000 and not any(k in defn_lower for k in [
            'error code', 'error message',
            'fault code', 'faultcode', 'fault/alarm code', 'alarm code',
            'fault status', 'hw fault', 'sw fault',
            'dsp alarm', 'dsp error', 'arm alarm',
            'warning code', 'warningcode',
            'ground fault', 'leak fault', 'oth fault',
            'internal error', 'arc fault',
            # CPS-Comm: Fault0/1/2/3/4/PFault/Warning Alarm Register (저주소)
            'alarm register', 'fault register',
            'pfault', 'fault0', 'fault1', 'fault2', 'fault3', 'fault4',
            'fault word', 'fault flag']):
        return 99
    # Work State 값 테이블 항목 (레지스터가 아닌 상태 값)
    if any(k in defn_lower for k in ['communicate fault', 'alarm run', 'derating run',
                                      'dispatch run', 'initial standby', 'key stop',
                                      'emergency stop']) or \
       (defn_lower.strip() in ('fault', 'stop', 'run', 'standby') and addr < 2000):
        return 99
    # Fault/Alarm time — 시간 정보, 알람 레지스터 아님
    # underscore 정규화 덕분에 FAULT_ALARM_TIME_YEAR 도 매칭됨
    if any(k in defn_lower for k in ['fault/alarm time', 'alarm time', 'fault time',
                                      'fault alarm time']):
        return 99
    # External/Power/Monitoring/Auxiliary 알람 — 인버터 자체 fault 가 아님 (Huawei)
    # [Remotesignal]Alarm 1/2/3 만 가져가도록 차단
    if any(k in defn_lower for k in ['external] poweralarm', '[external]poweralarm',
                                      'external poweralarm', 'external power alarm',
                                      'monitoringalarm', 'monitoring alarm',
                                      'monitoralarm', 'monitor alarm',
                                      'optimizer fault', 'optimizerfault',
                                      'collector fault', 'collectorfault',
                                      'numberof', 'number of critical', 'number of major',
                                      'number of minor', 'number of warning',
                                      'alarm clearance', 'historicalalarm',
                                      'latest active alarm', 'historical alarm']):
        return 99

    # ── 우선순위 ──
    # 0: 명확한 에러/폴트 코드 (정의 테이블 있음)
    if any(k in defn_lower for k in ['error code', 'fault code', 'faultcode',
                                      'fault/alarm code', 'alarm code',
                                      'sw fault', 'dsp error', 'dsp alarm',
                                      'error message', 'warningcode', 'warning code',
                                      'inverter fault code', 'inverter warning code']):
        return 0
    # 1: 번호 붙은 [Remotesignal]Alarm N — Huawei
    # (외부/모니터링 alarm 은 위에서 99 로 차단됨)
    if 'remotesignal]alarm' in defn_lower or '[remotesignal]alarm' in defn_lower:
        return 1
    # 1: Fault0/1/2 Alarm Register — CPS-Comm
    import re as _re
    if _re.search(r'\bfault\d\b.*alarm.*register', defn_lower) or \
       _re.search(r'pfault.*alarm.*register', defn_lower) or \
       _re.search(r'warning.*alarm.*register', defn_lower):
        return 1
    # 1: HW Fault / PID 알람
    if any(k in defn_lower for k in ['hw fault', 'hardware fault']):
        return 1
    if 'pid' in defn_lower and 'alarm' in defn_lower:
        return 1
    # 1: 일반 번호 붙은 Alarm (Alarm 1, Alarm 2)
    if _re.search(r'\balarm\s*\d', defn_lower):
        return 1
    # 2: Grid Status / ARM alarm
    if any(k in defn_lower for k in ['grid status', 'grid fault', 'arm alarm', 'arm error']):
        return 2
    # 3: 일반 알람/폴트/워닝
    if any(k in defn_lower for k in ['alarm', 'fault', 'error', 'warning']):
        return 3
    # 9: 기타
    return 9


def distribute_alarms(alarm_regs: List[RegisterRow]) -> Dict[str, List[RegisterRow]]:
    """
    §2-2: 알람 레지스터를 H01 alarm1/2/3에 배분
    V2: score 99(제외) 레지스터는 alarm 슬롯에 넣지 않음 → N/A
    """
    import re as _re
    # 우선순위 점수로 정렬 (같으면 주소순)
    scored = sorted(alarm_regs,
                    key=lambda r: (_alarm_score(r),
                                   r.address if isinstance(r.address, int) else 0))
    # score 99 제외 (Appendix 코드값, Work State 값 등)
    valid = [r for r in scored if _alarm_score(r) < 99]
    result = {'alarm1': [], 'alarm2': [], 'alarm3': [], 'dropped': []}
    slots = ['alarm1', 'alarm2', 'alarm3']

    if not valid:
        return result

    # ── 1) 번호 붙은 시퀀스 그룹 식별 ──
    # 시퀀스 우선순위 (높을수록 선호):
    #   1. Huawei [Remotesignal]Alarm 1/2/3
    #   2. CPS-Comm FaultN Alarm Register
    #   3. Growatt Inverter fault code + Inverter Warning code (+ System fault word 0 보충)
    #   4. Growatt System fault word 0/1/2
    SEQ_PRIORITY = ['remotesignal_alarm', 'faultN_alarm_register',
                    'inverter_code', 'system_fault_word']

    def _seq_key(reg):
        d = (reg.definition or '').lower().replace('_', ' ')
        m = _re.search(r'remotesignal\]?\s*alarm\s*(\d+)', d)
        if m:
            return ('remotesignal_alarm', int(m.group(1)))
        m = _re.search(r'\bfault(\d)\b.*alarm.*register', d)
        if m:
            return ('faultN_alarm_register', int(m.group(1)))
        if 'inverter fault code' in d:
            return ('inverter_code', 1)
        if 'inverter warning code' in d:
            return ('inverter_code', 2)
        m = _re.search(r'system\s*fault\s*word\s*(\d+)', d)
        if m:
            return ('system_fault_word', int(m.group(1)))
        return None

    seq_groups = {}
    for r in valid:
        sk = _seq_key(r)
        if sk:
            key, idx = sk
            seq_groups.setdefault(key, []).append((idx, r))

    chosen_seq = None
    chosen_key = None
    for key in SEQ_PRIORITY:
        if key in seq_groups:
            items = sorted(seq_groups[key], key=lambda t: t[0])
            chosen_seq = [t[1] for t in items]
            chosen_key = key
            break

    if chosen_seq:
        # 시퀀스 채우기
        for i in range(min(3, len(chosen_seq))):
            result[slots[i]].append(chosen_seq[i])
        # 부족하면 보충 — inverter_code(2개)는 system_fault_word 첫 항목으로 alarm3 보충
        if len(chosen_seq) < 3 and chosen_key == 'inverter_code':
            sfw = sorted(seq_groups.get('system_fault_word', []), key=lambda t: t[0])
            if sfw:
                result['alarm3'].append(sfw[0][1])
        # 그래도 부족하면 alarm1 으로 alias
        for s in slots:
            if not result[s] and result['alarm1']:
                result[s].append(result['alarm1'][0])
        used = {id(r) for r in chosen_seq}
        for r in valid:
            if id(r) not in used:
                result['dropped'].append(r)
        return result

    # ── 2) 시퀀스 없음 — score 0 우선 + alias ──
    score0 = [r for r in valid if _alarm_score(r) == 0]
    if score0:
        for i in range(3):
            if i < len(score0):
                result[slots[i]].append(score0[i])
            else:
                # 부족 시 alarm1 으로 alias
                result[slots[i]].append(score0[0])
        for r in valid:
            if r not in score0[:3]:
                result['dropped'].append(r)
        return result

    # ── 3) score 0 도 없음 — 일반 정렬, 부족 시 alias ──
    for i, reg in enumerate(valid):
        if i < 3:
            result[slots[i]].append(reg)
        else:
            result['dropped'].append(reg)
    if result['alarm1'] and not result['alarm2']:
        result['alarm2'].append(result['alarm1'][0])
    if result['alarm1'] and not result['alarm3']:
        result['alarm3'].append(result['alarm1'][0])

    return result


# ═══════════════════════════════════════════════════════════════════════════
# §2-3. PV 입력 (MPPT/String) 규칙
# ═══════════════════════════════════════════════════════════════════════════

def get_pv_voltage_rule() -> str:
    """§2-3: PV 전압 계산 규칙"""
    return "pv_voltage = average(MPPTn_voltage for n if MPPTn_voltage > 100V)"


def get_pv_current_rule() -> str:
    """§2-3: PV 전류 계산 규칙 (String 우선)"""
    return ("1순위: sum(STRINGn_current for all n) [String 있는 경우]\n"
            "2순위: sum(MPPTn_current for all n) [String 없는 경우]")


def get_string_voltage_rule() -> str:
    """§2-3: String 전압 없으면 해당 MPPT 전압 사용"""
    return "STRING{n}_voltage = MPPT{ceil(n/strings_per_mppt)}_voltage (String 전압 레지스터 없는 경우)"


# ═══════════════════════════════════════════════════════════════════════════
# §3. 인버터 정보 필수 추출 항목
# ═══════════════════════════════════════════════════════════════════════════

INFO_REQUIRED = {
    'DEVICE_MODEL':       ['model', 'device model', '모델', 'product model',
                           'device type code', 'type code', 'device type'],
    'SERIAL_NUMBER':      ['serial', 'sn', '시리얼', '제품번호', 'serial number'],
    'MANUFACTURER':       ['manufacturer', 'c_manufacturer'],
    'FIRMWARE_VERSION':   ['firmware', 'fw version', 'software version', '펌웨어',
                           'protocol version', 'communication version',
                           'c_version'],
    'MPPT_COUNT':         ['mppt count', 'number of mppt', 'mppt tracker', 'mppt수'],
    'NOMINAL_POWER':      ['nominal power', 'rated power', '정격출력', 'max output',
                           'nominal active power', 'rated active power',
                           'nominal reactive power', 'rated reactive power'],
    'NOMINAL_VOLTAGE':    ['nominal voltage', 'rated voltage', '정격전압'],
    'NOMINAL_FREQUENCY':  ['nominal frequency', 'rated frequency', '정격주파수'],
    'GRID_PHASE_NUMBER':  ['phase number', 'phase count', '상수', 'phase type',
                           'output type'],
    'TOTAL_RUNNING_TIME': ['total running time', 'running time', '총 가동시간',
                           'daily running time', '가동시간'],
    'TOTAL_ENERGY':       ['total power yields', 'total energy', '누적 발전량',
                           'total generation', 'cumulative energy',
                           'monthly power yields'],
    'DAILY_ENERGY':       ['daily power yields', 'daily energy', '일일 발전량',
                           'today energy', 'daily generation'],
    'INNER_TEMPERATURE':  ['internal temperature', 'inner temperature', 'module temperature',
                           '내부 온도', 'inverter temperature', 'cabinet temperature',
                           'inner_temp'],
    'APPARENT_POWER':     ['apparent power', '피상전력', 'total apparent power'],
    'COUNTRY_CODE':       ['present country', 'country code', 'country id', '국가코드'],
    'INSULATION':         ['insulation resistance', 'array insulation', '절연저항'],
    'BUS_VOLTAGE':        ['bus voltage', 'dc bus', 'bus volt', '버스전압'],
}


def is_info_register(definition: str) -> bool:
    """§3: 인버터 정보 레지스터 여부"""
    defn_lower = definition.lower()
    # PDF 줄바꿈 제거 시 공백이 사라질 수 있으므로 공백 제거 버전도 체크
    defn_nospace = defn_lower.replace(' ', '')
    for field, keywords in INFO_REQUIRED.items():
        for k in keywords:
            if k in defn_lower or k.replace(' ', '') in defn_nospace:
                return True
    return False


# ═══════════════════════════════════════════════════════════════════════════
# §4. DER-AVM 제어/모니터링 규칙
# ═══════════════════════════════════════════════════════════════════════════

DER_CONTROL_KEYWORDS = [
    'power limit', 'output regulation', 'curtailment', 'derate',
    'power factor set', 'pf set', 'cos phi set', 'pf control',
    'reactive power set', 'q set', 'var set', 'q control',
    'on/off', 'on off', 'start/stop', 'remote on',
    'operation mode', 'running mode set', 'control mode', 'power control mode',
    'active power limit', 'active power curtailment',
    '출력제한', '역률설정', '무효전력설정', '운전모드설정', '기동정지',
]


def is_der_control(reg: RegisterRow) -> bool:
    """§4: DER-AVM 제어 레지스터 여부 — Write 가능 + 키워드 매칭"""
    rw = (reg.rw or '').upper()
    if rw not in ('RW', 'WO', 'R/W'):
        return False
    defn_lower = reg.definition.lower()
    return any(k in defn_lower for k in DER_CONTROL_KEYWORDS)


# ═══════════════════════════════════════════════════════════════════════════
# §5. IV 스캔 지원 판단
# ═══════════════════════════════════════════════════════════════════════════

IV_SCAN_KEYWORDS = [
    'iv scan', 'iv curve', 'i-v scan', 'iv test',
    'iv start', 'start iv', 'iv trigger',
    'iv status', 'iv state', 'iv result',
    'iv point', 'iv data', 'string iv',
]

# 확정 IV 스캔 지원 제조사
IV_SCAN_CONFIRMED = {'solarize', 'kstar', 'senergy'}


def detect_iv_scan_support(registers: List[RegisterRow], manufacturer: str) -> bool:
    """§5: IV 스캔 지원 여부 판단"""
    # 확정 제조사
    if manufacturer.lower() in IV_SCAN_CONFIRMED:
        return True
    # 레지스터 키워드 탐색
    for reg in registers:
        defn_lower = reg.definition.lower()
        if any(k in defn_lower for k in IV_SCAN_KEYWORDS):
            return True
    return False


# ═══════════════════════════════════════════════════════════════════════════
# §6. 제외 항목
# ═══════════════════════════════════════════════════════════════════════════

EXCLUDE_KEYWORDS = [
    # 통신 설정
    'baud rate', 'baudrate', 'slave id', 'slave address', 'communication setting',
    'comm setting', 'rs485', 'modbus addr', 'protocol version', 'comm port',
    'wifi', 'ssid', 'password', 'digital meter modbus',
    # 보호 파라미터 설정 (§6)
    'over voltage protection', 'under voltage protection',
    'over frequency protection', 'under frequency protection',
    'over current protection', 'protection setting', 'protection value',
    'trip point', 'trip time', 'trip_time', 'reconnect time', 'anti-islanding',
    'loss level', 'loss_level',
    'triggering voltage', 'triggering threshold',
    'lvrt', 'hvrt', 'frt mode', 'frt_mode',
    'fault ride', 'ride through',
    'detection',  # insulation/ground detection settings
    # 날짜/시간 레지스터
    'dateyear', 'datemonth', 'datesecond',
    # 기타 설정
    'first connect', 'soft start', 'start time',
    # 'device type' — 제거 (INFO의 Device type code와 충돌)
    # REMS (§6)
    'rems', 'remote monitoring system',
    # V2: 제어 관련 레지스터 제외 (DER-AVM으로만 제어)
    'start/stop', 'remote start', 'remote stop', 'emergency stop',
    'power on command', 'shut down command',
    # V2: Q(P)/Q(U) 커브, 모델명 테이블
    'q(p)', 'q(u)', 'q(v)', 'qu curve', 'qp curve',
    'uin', 'uout', 'u1 limit', 'u2 limit', 'ulimit',
    'hysteresis', 'curve(italy)', 'v1i(', 'v2i(', 'v1s(', 'v2s',
    'qmax(', 'pin(italy)', 'pout(italy)',
    'enablemode', 'lower q/sn', 'upper q/sn',
    # V2: 시스템 클럭/예약
    'system clock', 'reserved',
    # V2: PID 관련 설정
    'anti-pid', 'pid suppression', 'pid impedance', 'pid function',
    # V2: 국가코드/지역코드 값
    'great britain', 'us-ne', 'us-sa', 'us-',
    # V2: 기타 설정값/커브
    'upper u limit', 'lower u limit',
    'key stop', 'dispatch run', 'derating run',
    # V2: Fault 코드 테이블 엔트리 (이름이 짧은 상태 설명)
    'stop bit', 'data bit', 'output overload',
    'pv input configuration',
    # V2: Fuse 관련 (H01/INFO 불필요)
    'fuse open', 'fuse check', 'fuse data',
]

# 보호 파라미터 주소 범위 (§6: 0x5000~ 보호설정 블록)
EXCLUDE_ADDR_RANGES = [
    (0x5000, 0x51FF),   # 보호 파라미터 설정 블록
    (0x3000, 0x3005),   # 날짜/시간 설정
    (0x3060, 0x307F),   # WiFi 설정
    (0x30B0, 0x30B1),   # Digital meter/device type 설정
]

# 이름 길이 제한
MAX_NAME_LENGTH = 60


def should_exclude(reg: RegisterRow) -> bool:
    """§6: 제외 항목 판단"""
    defn_lower = reg.definition.lower()
    defn_upper = to_upper_snake(reg.definition)

    # 키워드 제외
    for kw in EXCLUDE_KEYWORDS:
        if kw in defn_lower:
            return True

    # 주소 범위 제외
    addr = reg.address if isinstance(reg.address, int) else parse_address(str(reg.address))
    if addr is not None:
        for lo, hi in EXCLUDE_ADDR_RANGES:
            if lo <= addr <= hi:
                return True

    # 이름이 너무 길면 제외 (PDF에서 설명문이 이름으로 추출된 경우)
    # 단, INFO 필수 키워드(model, serial 등) 포함 시 제외하지 않음
    if len(defn_upper) > MAX_NAME_LENGTH:
        if not is_info_register(reg.definition):
            return True

    return False


# ═══════════════════════════════════════════════════════════════════════════
# §7. 카테고리 분류 (REVIEW 기준 포함)
# ═══════════════════════════════════════════════════════════════════════════

REVIEW_REASONS = {
    'ambiguous_h01':      'H01 필드 대응이 애매 — 동일 물리량에 여러 레지스터',
    'unit_unusual':       '단위/스케일이 특이 — 변환 방법 불확실',
    'der_unclear':        'DER-AVM 해당 여부 불분명 — Write 레지스터',
    'alarm_undefined':    '알람 비트 정의 미확인',
    'duplicate_quantity': '같은 물리량에 여러 레지스터가 존재',
    'unclassifiable':     '자동 분류 불가 — 레퍼런스/동의어 매칭 없음',
}


def classify_register_with_rules(
    reg: RegisterRow,
    synonym_db: dict,
    review_history: dict,
    ref_patterns: dict,
    device_type: str = 'inverter',
    all_regs: List[RegisterRow] = None,
) -> tuple:
    """
    §7: STAGE1_RULES에 따른 카테고리 분류
    Returns: (category, review_reason)
    """
    defn = reg.definition
    defn_lower = defn.lower()
    defn_upper = to_upper_snake(defn)
    addr = reg.address if isinstance(reg.address, int) else parse_address(reg.address)

    # §6: 제외 항목
    if should_exclude(reg):
        return ('EXCLUDE', '')

    # §SunSpec: SunSpec 표준 레지스터 분류 (C_*, I_*, M_* 패턴)
    sunspec_cat = classify_sunspec_register(defn, addr)
    if sunspec_cat:
        return (sunspec_cat, '')

    # 0) INFO 키워드 최우선 — PDF에서 추출한 이름 기반
    # (레퍼런스 주소 매칭보다 먼저 체크하여 다른 제조사 레퍼런스와 주소 충돌 방지)
    if is_info_register(defn):
        return ('INFO', '')

    # 1) 레퍼런스 패턴 기반 (주소 매칭)
    if addr is not None:
        for proto, addr_map in ref_patterns.items():
            if addr in addr_map:
                ref_name = addr_map[addr].upper()
                if 'DEA_' in ref_name:
                    return ('DER_MONITOR' if device_type == 'inverter' else 'EXCLUDE', '')
                if any(k in ref_name for k in ['DER_', 'INVERTER_ON_OFF', 'INVERTER_CONTROL',
                                                'POWER_FACTOR_DYNAMIC', 'REACTIVE_POWER_DYNAMIC',
                                                'POWER_DERATING', 'CLEAR_PV', 'EEPROM']):
                    return ('DER_CONTROL' if device_type == 'inverter' else 'EXCLUDE', '')
                if 'IV_' in ref_name:
                    return ('IV_SCAN' if device_type == 'inverter' else 'EXCLUDE', '')
                if any(k in ref_name for k in ['ERROR_CODE', 'ALARM', 'FAULT', 'WARNING']):
                    return ('ALARM', '')
                if any(k in ref_name for k in ['INVERTER_MODE', 'RUNNING_STATUS', 'STATUS']):
                    return ('STATUS', '')
                if any(k in ref_name for k in ['MODEL', 'SERIAL', 'FIRMWARE', 'NOMINAL',
                                                'MPPT_COUNT', 'PHASE', 'EMS_', 'LCD_',
                                                'DEVICE_TYPE', 'OUTPUT_TYPE',
                                                'TOTAL_RUNNING', 'DAILY_RUNNING',
                                                'INNER_TEMP', 'MODULE_TEMP',
                                                'APPARENT_POWER', 'COUNTRY',
                                                'INSULATION', 'BUS_VOLTAGE',
                                                'TOTAL_ENERGY', 'DAILY_ENERGY',
                                                'TODAY_ENERGY', 'MONTHLY_ENERGY']):
                    return ('INFO', '')
                return ('MONITORING', '')

    # 2) §3: INFO 키워드 (synonym_db보다 먼저 — INFO가 MONITORING으로 잘못 분류되는 것 방지)
    if is_info_register(defn):
        return ('INFO', '')

    # §2-2: STATUS 키워드 — synonym_db보다 우선 (학습 데이터 충돌 방지)
    if any(k in defn_lower for k in ['fault status', 'alarm status', 'fault state',
                                      'grid status']):
        return ('ALARM', '')
    if any(k in defn_lower for k in ['status', 'state', 'mode', 'running',
                                      '상태', '운전', '동작']):
        # §7: 동일 물리량 중복 체크
        if all_regs:
            similar = [r for r in all_regs if r is not reg and
                       any(k in r.definition.lower() for k in ['status', 'state', 'mode'])]
            if len(similar) > 2:
                return ('STATUS', '')  # 여러 개여도 STATUS
        return ('STATUS', '')

    # 5) §2-2: ALARM 키워드 — synonym_db보다 우선
    if any(k in defn_lower for k in ['alarm', 'fault', 'error', 'warning', 'protection flag',
                                      '알람', '고장', '경보', '오류']):
        return ('ALARM', '')

    # 5b) synonym_db 매칭 — STATUS/ALARM 키워드 이후 (학습 데이터 충돌 방지)
    syn = match_synonym(defn, synonym_db)
    if syn:
        cat = syn['category']
        if cat in ('DER_CONTROL', 'DER_MONITOR', 'IV_SCAN') and device_type != 'inverter':
            return ('EXCLUDE', '')
        return (cat, '')

    # 6) §5: IV Scan 키워드 (인버터만)
    if device_type == 'inverter':
        if any(k in defn_lower for k in IV_SCAN_KEYWORDS):
            return ('IV_SCAN', '')

    # 7) §4: DER 키워드 (인버터만)
    if device_type == 'inverter' and is_der_control(reg):
        return ('DER_CONTROL', '')

    # 8) MPPT/String → MONITORING
    ch = detect_channel_number(defn)
    if ch:
        return ('MONITORING', '')

    # 9) §2-1: H01 필수 항목 키워드
    for field_name, field_info in H01_REQUIRED_FIELDS.items():
        if any(k in defn_lower for k in field_info['keywords']):
            return (field_info['category'], '')

    # 10) 일반 모니터링 키워드
    if any(k in defn_lower for k in ['voltage', 'current', 'power', 'energy', 'frequency',
                                      'temperature', 'factor', '전압', '전류', '전력',
                                      '발전량', '온도', '주파수', '역률']):
        return ('MONITORING', '')

    # 11) 퍼지 매칭
    fuzzy = match_synonym_fuzzy(defn, synonym_db, threshold=0.6)
    if fuzzy:
        cat = fuzzy['category']
        if cat in ('DER_CONTROL', 'DER_MONITOR', 'IV_SCAN') and device_type != 'inverter':
            return ('EXCLUDE', '')
        return (cat, '')

    # 12) review_history 동일 패턴
    for item in review_history.get('approved', []):
        if item.get('definition', '').upper() == defn_upper:
            verdict = item.get('verdict', '')
            if verdict == 'DELETE':
                return ('EXCLUDE', '')
            if verdict.startswith('MOVE:'):
                return (verdict.replace('MOVE:', ''), '')

    # V2: 분류 불가 → 자동 분류 (REVIEW 최소화)
    # REVIEW는 H01/IV 매칭에서 진짜 헷갈리는 경우만

    # "abnormal" → ALARM
    if 'abnormal' in defn_lower:
        return ('ALARM', '')

    # Write 레지스터인데 제어도 모니터링도 아닌 경우 → 제외
    if reg.rw in ('RW', 'WO', 'R/W') and device_type == 'inverter':
        return ('EXCLUDE', '')

    # 설명문/디버깅/내부 사용 → 제외
    if any(k in defn_lower for k in ['debugging', 'internal use', 'register for',
                                      'tens place', 'read keep', 'read input',
                                      'single write', 'rotational speed',
                                      'coefficient']):
        return ('EXCLUDE', '')

    # 나머지 → MONITORING으로 자동 포함
    return ('MONITORING', '')


# ═══════════════════════════════════════════════════════════════════════════
# §8. Stage 1/2 역할 분리
# ═══════════════════════════════════════════════════════════════════════════

def filter_channels_stage2(
    registers: List[RegisterRow],
    mppt_count: int,
    total_strings: int,
    ref_patterns: dict = None,
) -> List[RegisterRow]:
    """
    §8: Stage 2 필터링
    - MPPT{n} → n <= mppt_count
    - STRING{n} → n <= total_strings
    - IV Scan 트래커 → tracker <= mppt_count
    - INFO, MONITORING(비채널), STATUS, ALARM, DER → 전부 포함
    """
    filtered = []
    for reg in registers:
        # 이름에서 채널 감지
        ch = detect_channel_number(reg.definition)
        if not ch:
            # 주소에서 레퍼런스 채널 감지
            addr = reg.address if isinstance(reg.address, int) else parse_address(reg.address)
            if addr is not None and ref_patterns:
                ch = detect_channel_from_ref(addr, ref_patterns)
        if ch:
            prefix, n = ch
            if prefix == 'MPPT' and n > mppt_count:
                continue
            if prefix == 'STRING' and n > total_strings:
                continue
        filtered.append(reg)
    return filtered


# ═══════════════════════════════════════════════════════════════════════════
# §9. Stage 2 인계 원칙
# ═══════════════════════════════════════════════════════════════════════════

def should_include_ambiguous(reg: RegisterRow) -> bool:
    """§9: 애매한 항목은 제외하지 않고 포함"""
    return True  # 항상 포함, REVIEW로 분류하여 Stage 2에서 처리


# ═══════════════════════════════════════════════════════════════════════════
# 설비 타입별 카테고리 필터
# ═══════════════════════════════════════════════════════════════════════════

DEVICE_CATEGORIES = {
    'inverter': ['INFO', 'MONITORING', 'STATUS', 'ALARM',
                 'DER_CONTROL', 'DER_MONITOR', 'IV_SCAN', 'REVIEW'],
    'relay':    ['INFO', 'MONITORING', 'STATUS', 'ALARM', 'REVIEW'],
    'weather':  ['INFO', 'MONITORING', 'STATUS', 'ALARM', 'REVIEW'],
}


def get_valid_categories(device_type: str) -> List[str]:
    """설비 타입별 유효 카테고리"""
    return DEVICE_CATEGORIES.get(device_type, DEVICE_CATEGORIES['inverter'])


# ═══════════════════════════════════════════════════════════════════════════
# V2: H01 DER 겹침 필드 판단
# ═══════════════════════════════════════════════════════════════════════════

# H01에서 DER 주소를 사용하는 9개 필드의 키워드
# 이 필드들은 PDF에서 매핑 불필요 (DER 고정 주소맵으로 자동 삽입)
H01_DER_OVERLAP_KEYWORDS = {
    'r_voltage':    ['phase a voltage', 'l1 voltage', 'r-phase voltage', 'phase r voltage',
                     'ua', 'grid a voltage', 'a-n voltage', 'a phase voltage'],
    's_voltage':    ['phase b voltage', 'l2 voltage', 's-phase voltage', 'phase s voltage',
                     'ub', 'grid b voltage', 'b-n voltage', 'b phase voltage'],
    't_voltage':    ['phase c voltage', 'l3 voltage', 't-phase voltage', 'phase t voltage',
                     'uc', 'grid c voltage', 'c-n voltage', 'c phase voltage'],
    'r_current':    ['phase a current', 'l1 current', 'r-phase current', 'phase r current',
                     'ia', 'grid a current', 'a phase current'],
    's_current':    ['phase b current', 'l2 current', 's-phase current', 'phase s current',
                     'ib', 'grid b current', 'b phase current'],
    't_current':    ['phase c current', 'l3 current', 't-phase current', 'phase t current',
                     'ic', 'grid c current', 'c phase current'],
    'ac_power':     ['total active power', 'total output power', 'grid active power',
                     'active power', 'output power', 'total power output'],
    'power_factor': ['power factor', 'cos phi', 'pf'],
    'frequency':    ['grid frequency', 'output frequency', 'frequency'],
}


def is_h01_der_overlap(reg: RegisterRow) -> Optional[str]:
    """
    V2: H01 DER 겹침 필드인지 판단
    Returns: 겹치는 H01 필드명 or None
    """
    defn_lower = reg.definition.lower()
    for h01_field, keywords in H01_DER_OVERLAP_KEYWORDS.items():
        if any(k in defn_lower for k in keywords):
            return h01_field
    return None
