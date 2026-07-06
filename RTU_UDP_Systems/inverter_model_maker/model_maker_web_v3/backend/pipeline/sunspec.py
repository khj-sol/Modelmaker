# -*- coding: utf-8 -*-
"""
SunSpec 표준 인버터 프로토콜 처리 모듈

SunSpec Alliance Interoperability Specification을 따르는 인버터
(SolarEdge, Fronius, SMA SunSpec 등)에 대한 하드코딩된 처리 규칙.

SunSpec 표준 구조:
- Common Model (ID 1): C_Manufacturer, C_Model, C_Version, C_SerialNumber
- Inverter Model (ID 101/102/103): I_AC_*, I_DC_*, I_Status, I_Status_Vendor
- Meter Model (ID 201~204): M_AC_*, M_Events
"""
import os
import json
import re

# ═══════════════════════════════════════════════════════════════════════════
# SunSpec 감지
# ═══════════════════════════════════════════════════════════════════════════

# SunSpec 인버터 제조사 (확인된 것)
SUNSPEC_MANUFACTURERS = {'solaredge', 'fronius', 'sma', 'abb sunspec', 'sunspec'}

# SunSpec 레지스터 이름 패턴 (PDF에서 추출된 이름으로 감지)
SUNSPEC_REGISTER_PATTERNS = [
    'c_sunspec', 'c_manufacturer', 'c_model', 'c_version', 'c_serialnumber',
    'c_deviceaddress', 'i_ac_current', 'i_ac_voltage', 'i_dc_current',
    'i_dc_voltage', 'i_dc_power', 'i_status', 'i_ac_energy', 'i_ac_power',
    'm_ac_current', 'm_ac_voltage', 'm_ac_power', 'm_events',
]


def is_sunspec_pdf(registers: list, manufacturer: str = '') -> bool:
    """PDF에서 추출된 레지스터가 SunSpec 형식인지 감지"""
    # 1) 제조사명으로 감지 (접두사 포함: "SMA-PV" → "sma" 추출)
    mfr_lower = manufacturer.lower().split('-')[0].split(' ')[0]
    if mfr_lower in SUNSPEC_MANUFACTURERS or manufacturer.lower() in SUNSPEC_MANUFACTURERS:
        return True

    # 2) SunSpec 특유의 레지스터 이름 패턴으로 감지
    sunspec_count = 0
    for reg in registers:
        dl = reg.definition.lower().replace(' ', '_')
        if any(p in dl for p in SUNSPEC_REGISTER_PATTERNS):
            sunspec_count += 1
    # C_SunSpec_ID + C_Model + I_Status 등 3개 이상이면 SunSpec
    return sunspec_count >= 3


# ═══════════════════════════════════════════════════════════════════════════
# SunSpec INFO 레지스터 (Common Model, ID=1)
# ═══════════════════════════════════════════════════════════════════════════

# SunSpec Common Model 표준 INFO 필드
SUNSPEC_INFO_FIELDS = {
    'c_manufacturer':   'INFO',     # 제조사명 (String32)
    'c_model':          'INFO',     # 모델명 (String32)
    'c_version':        'INFO',     # 펌웨어 버전 (String16)
    'c_serialnumber':   'INFO',     # 시리얼번호 (String32)
    'c_deviceaddress':  'INFO',     # Modbus Unit ID
    'c_sunspec_id':     'EXCLUDE',  # 고정값 "SunS" — 불필요
    'c_sunspec_did':    'EXCLUDE',  # 모델 ID — 불필요
    'c_sunspec_length': 'EXCLUDE',  # 블록 길이 — 불필요
    'c_sunspec_lengt':  'EXCLUDE',  # PDF 줄바꿈으로 잘린 형태
}


# ═══════════════════════════════════════════════════════════════════════════
# SunSpec STATUS 레지스터 (Inverter Model)
# ═══════════════════════════════════════════════════════════════════════════

# I_Status 값 정의 (SunSpec Inverter Model 표준)
SUNSPEC_STATUS_DEFINITIONS = {
    '1': 'Off',
    '2': 'Sleeping (auto-shutdown)',
    '3': 'Grid Monitoring/wake-up',
    '4': 'MPPT - Producing power',
    '5': 'Throttled (curtailed)',
    '6': 'Shutting down',
    '7': 'Fault',
    '8': 'Standby/Maintenance',
}

# SunSpec STATUS 필드 분류
SUNSPEC_STATUS_FIELDS = {
    'i_status':   'STATUS',     # 인버터 상태 (1~8)
    'st':         'STATUS',     # SMA SunSpec 텍스트 약어
}

# SunSpec 값 정의 테이블 (레지스터가 아닌 상태값 정의)
# I_STATUS_OFF~STANDBY는 PDF에서 레지스터로 잘못 추출됨 → EXCLUDE
SUNSPEC_VALUE_DEFINITIONS = [
    'i_status_off', 'i_status_sleeping', 'i_status_starting',
    'i_status_mppt', 'i_status_throttled', 'i_status_shutting_down',
    'i_status_fault', 'i_status_standby',
]


# ═══════════════════════════════════════════════════════════════════════════
# SunSpec ALARM 레지스터
# ═══════════════════════════════════════════════════════════════════════════

# SunSpec ALARM 필드 분류
SUNSPEC_ALARM_FIELDS = {
    'i_status_vendor':  'ALARM',    # 에러코드 (제조사별)
    'i_status_vendor4': 'ALARM',    # 추가 에러코드
    'm_events':         'ALARM',    # Meter 이벤트 비트필드
    'stvnd':            'ALARM',    # SMA SunSpec 텍스트 약어 (Manufacturer status)
}

# Scale Factor 레지스터 — 독립 레지스터가 아닌 보조값 → EXCLUDE
SUNSPEC_SCALE_FACTOR_SUFFIX = '_sf'


# ═══════════════════════════════════════════════════════════════════════════
# SunSpec 분류 적용
# ═══════════════════════════════════════════════════════════════════════════

def classify_sunspec_register(definition: str, addr: int = None) -> str:
    """
    SunSpec 레지스터를 분류. 해당 없으면 '' 반환.
    Returns: 'INFO', 'STATUS', 'ALARM', 'EXCLUDE', 'MONITORING', or ''
    """
    dl = definition.lower().replace(' ', '_').replace('-', '_')
    # 정확한 이름 매칭 (C_, I_, M_ 접두사)
    dl_clean = re.sub(r'[^a-z0-9_]', '', dl)

    # 1) 값 정의 테이블 → EXCLUDE (레지스터가 아님)
    if dl_clean in SUNSPEC_VALUE_DEFINITIONS:
        return 'EXCLUDE'
    # I_STATUS_* 패턴 (addr < 20이면 값 정의)
    if dl_clean.startswith('i_status_') and addr is not None and addr < 20:
        return 'EXCLUDE'

    # 2) INFO
    for pattern, cat in SUNSPEC_INFO_FIELDS.items():
        if pattern in dl_clean:
            return cat
    # SunSpec 텍스트 형식 약어/전체이름 (SMA 등 — 접두사 없는 이름)
    if dl_clean in ('manufacturer', 'model', 'version', 'serialnumber', 'serial',
                    'mn', 'md', 'vr', 'sn', 'da', 'opt'):
        return 'INFO'
    if 'serial number' in dl.replace('_', ' '):
        return 'INFO'

    # 4) ALARM — STATUS보다 먼저 체크 (StVnd가 'st' 서브스트링에 오매칭 방지)
    for pattern, cat in SUNSPEC_ALARM_FIELDS.items():
        if pattern in dl_clean:
            return cat

    # 3) STATUS
    for pattern, cat in SUNSPEC_STATUS_FIELDS.items():
        # 짧은 패턴('st')은 완전 일치만 허용 (서브스트링 오매칭 방지)
        if dl_clean == pattern or (len(pattern) > 2 and pattern in dl_clean and 'vendor' not in dl_clean):
            return cat

    # 5) SunSpec MPPT DC 레지스터 → MONITORING
    if dl_clean in ('dca', 'dcv', 'dcw', 'dcwh', 'wh'):
        return 'MONITORING'

    # 6) Scale Factor → EXCLUDE (보조값)
    if dl_clean.endswith(SUNSPEC_SCALE_FACTOR_SUFFIX):
        return 'EXCLUDE'

    return ''


def detect_sunspec_mppt(registers: list) -> dict:
    """
    SunSpec Multiple MPPT Extension (ID=160) 블록에서 MPPT별 DCA/DCV/DCW 감지

    SunSpec MPPT 구조:
    - DCA/DCV/DCW가 같은 이름으로 반복 (주소만 다름)
    - 반복 간격 = 20 words (Repeating Block Size)
    - 첫 번째 DCA 주소에서 시작, 20씩 증가

    Returns: {
        'mppt_count': N,
        'mppts': [
            {'n': 1, 'dca_addr': 40140, 'dcv_addr': 40141, 'dcw_addr': 40142},
            {'n': 2, 'dca_addr': 40160, 'dcv_addr': 40161, 'dcw_addr': 40162},
            ...
        ]
    }
    """
    # DCA/DCV/DCW 주소 수집
    dca_addrs = []
    dcv_addrs = []
    dcw_addrs = []

    for reg in registers:
        dl = reg.definition.strip()
        addr = reg.address if isinstance(reg.address, int) else 0
        if dl == 'DCA':
            dca_addrs.append(addr)
        elif dl == 'DCV':
            dcv_addrs.append(addr)
        elif dl == 'DCW':
            dcw_addrs.append(addr)

    if not dca_addrs:
        return {'mppt_count': 0, 'mppts': []}

    # 주소순 정렬
    dca_addrs.sort()
    dcv_addrs.sort()
    dcw_addrs.sort()

    mppts = []
    for i, dca in enumerate(dca_addrs):
        n = i + 1
        dcv = dcv_addrs[i] if i < len(dcv_addrs) else dca + 1
        dcw = dcw_addrs[i] if i < len(dcw_addrs) else dca + 2
        mppts.append({'n': n, 'dca_addr': dca, 'dcv_addr': dcv, 'dcw_addr': dcw})

    return {'mppt_count': len(mppts), 'mppts': mppts}


def apply_sunspec_definitions(categorized: dict, log=None):
    """
    SunSpec STATUS/ALARM 레지스터에 정의 데이터 적용
    - STATUS I_Status → 8개 상태 정의
    - ALARM I_Status_Vendor → 외부 에러코드 (solaredge_definitions.json)
    """
    # STATUS 정의 적용
    for reg in categorized.get('STATUS', []):
        dl = reg.definition.lower().replace(' ', '_')
        if 'i_status' in dl and 'vendor' not in dl:
            if not getattr(reg, 'value_definitions', None):
                reg.value_definitions = dict(SUNSPEC_STATUS_DEFINITIONS)
                if log:
                    log(f'  SunSpec status 정의 적용: {reg.definition} ({len(SUNSPEC_STATUS_DEFINITIONS)}개)')

    # ALARM 정의 적용 (외부 JSON)
    defs_path = os.path.join(os.path.dirname(__file__), 'solaredge_definitions.json')
    alarm_codes = {}
    if os.path.exists(defs_path):
        try:
            with open(defs_path, encoding='utf-8') as f:
                alarm_codes = json.load(f).get('alarm_codes', {})
        except Exception:
            pass

    if alarm_codes:
        for reg in categorized.get('ALARM', []):
            dl = reg.definition.lower()
            if 'vendor' in dl:
                if not getattr(reg, 'value_definitions', None):
                    reg.value_definitions = alarm_codes
                    if log:
                        log(f'  SunSpec alarm 정의 적용: {reg.definition} ({len(alarm_codes)}개)')
                    break

    # MPPT 블록: DCA/DCV/DCW → mppt{N}_current/voltage/power 매핑
    all_regs = []
    for cat_regs in categorized.values():
        all_regs.extend(cat_regs)
    mppt_info = detect_sunspec_mppt(all_regs)
    if mppt_info['mppt_count'] > 0:
        # DCA/DCV/DCW 레지스터에 h01_field 할당
        for mppt in mppt_info['mppts']:
            n = mppt['n']
            for reg in categorized.get('MONITORING', []):
                addr = reg.address if isinstance(reg.address, int) else 0
                if addr == mppt['dcv_addr'] and reg.definition.strip() == 'DCV':
                    reg.h01_field = f'mppt{n}_voltage'
                elif addr == mppt['dca_addr'] and reg.definition.strip() == 'DCA':
                    reg.h01_field = f'mppt{n}_current'
                elif addr == mppt['dcw_addr'] and reg.definition.strip() == 'DCW':
                    reg.h01_field = f'mppt{n}_power'
        if log:
            log(f'  SunSpec MPPT {mppt_info["mppt_count"]}개 감지 (DCA/DCV/DCW 블록)')

    if log:
        log(f'  SunSpec 정의 적용 완료')
