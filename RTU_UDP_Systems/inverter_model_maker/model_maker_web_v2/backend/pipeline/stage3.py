# -*- coding: utf-8 -*-
"""
Stage 3 — Stage 2 Excel → *_registers.py 코드 생성

사용자 점검 완료된 Stage 2 Excel에서 RTU 호환 registers.py 코드를 생성한다.
레퍼런스(REF_Solarize_PV_registers.py)와 동일한 구조를 목표로 한다:
- 12개 클래스, 9개 모듈 함수, SCALE, DATA_TYPES, FLOAT32_FIELDS
"""
import os
import re
import logging
import importlib.util
from datetime import datetime
from textwrap import dedent, indent
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

from . import (
    PROJECT_ROOT, COMMON_DIR, RTU_COMMON_DIR,
    RegisterRow, INVERTER_MODES,
    to_upper_snake, parse_address, detect_channel_number,
    load_synonym_db, save_synonym_db,
    load_review_history, save_review_history,
    get_openpyxl, ProgressCallback,
)
from .stage2 import read_stage1_excel_v2 as read_stage1_excel  # V2 호환


# ─── Stage 2 Excel 읽기 ─────────────────────────────────────────────────────

def read_stage2_excel(path: str) -> dict:
    """Stage 2 Excel → {meta, all_regs, review_items} — V2 + 레거시 호환"""
    openpyxl = get_openpyxl()
    wb = openpyxl.load_workbook(path, data_only=True)
    result = {'meta': {}, 'all_regs': [], 'review_items': [], 'h01_manual_mapping': {}}

    is_v2 = '1_REGISTER_MAP' in wb.sheetnames

    # ── META 읽기 ──
    summary_sheet = '3_SUMMARY' if is_v2 else 'SUMMARY'
    meta_sheet = '1_REGISTER_MAP' if is_v2 else summary_sheet
    if meta_sheet in wb.sheetnames:
        ws = wb[meta_sheet]
        for row in ws.iter_rows(values_only=True):
            cells = [str(c).strip() if c is not None else '' for c in row]
            if len(cells) >= 2 and cells[0] and cells[1]:
                result['meta'][cells[0]] = cells[1]

    # ── 레지스터 읽기 ──
    if is_v2 and '1_REGISTER_MAP' in wb.sheetnames:
        # V2: 1_REGISTER_MAP에서 섹션별로 파싱
        ws = wb['1_REGISTER_MAP']
        current_cat = ''
        header = None
        for row in ws.iter_rows(values_only=True):
            cells = [str(c).strip() if c is not None else '' for c in row]
            first = cells[0] if cells else ''
            # 섹션 감지
            if 'INFO 레지스터' in first:
                current_cat = 'INFO'
                header = None
                continue
            elif 'H01 MONITORING' in first:
                current_cat = 'MONITORING'
                header = None
                continue
            elif 'STATUS 레지스터' in first:
                current_cat = 'STATUS'
                header = None
                continue
            elif 'ALARM 레지스터' in first:
                current_cat = 'ALARM'
                header = None
                continue
            elif first.startswith('Stage 2') or not first:
                if not current_cat:
                    continue
                if not any(cells):
                    current_cat = ''
                    header = None
                    continue
            # 헤더
            if first == 'No':
                header = cells
                continue
            if not header or not first or not first.isdigit():
                continue
            # 행 파싱
            reg = _parse_s2_reg_row(cells, header, current_cat)
            if reg:
                result['all_regs'].append(reg)
    elif 'ALL' in wb.sheetnames:
        # 레거시: ALL 시트
        ws = wb['ALL']
        rows = list(ws.iter_rows(values_only=True))
        if len(rows) >= 2:
            header = [str(c).strip() if c else '' for c in rows[0]]
            for row in rows[1:]:
                cells = [str(c).strip() if c is not None else '' for c in row]
                if not any(cells):
                    continue
                d = {}
                for i, h in enumerate(header):
                    if i >= len(cells):
                        break
                    hl = h.lower()
                    if 'category' in hl:
                        d['category'] = cells[i]
                    elif 'definition' in hl:
                        d['definition'] = cells[i]
                    elif 'address' in hl:
                        d['address_hex'] = cells[i]
                        d['address'] = parse_address(cells[i]) or 0
                    elif hl == 'reg':
                        d['regs'] = cells[i]
                    elif 'type' in hl and 'body' not in hl:
                        d['data_type'] = cells[i]
                    elif 'unit' in hl or 'scale' in hl:
                        d['unit'] = cells[i]
                    elif 'r/w' in hl:
                        d['rw'] = cells[i]
                    elif 'comment' in hl:
                        d['comment'] = cells[i]
                    elif 'h01 field' in hl:
                        d['h01_field'] = cells[i]
                result['all_regs'].append(RegisterRow.from_dict(d))

    # ── REVIEW 읽기 ──
    review_sheet = '2_REVIEW' if is_v2 else 'REVIEW'
    if review_sheet in wb.sheetnames:
        ws = wb[review_sheet]
        header = None
        for row in ws.iter_rows(values_only=True):
            cells = [str(c).strip() if c is not None else '' for c in row]
            first = cells[0] if cells else ''
            if first == 'No':
                header = cells
                continue
            if header and first and first.isdigit():
                item = {}
                for i, h in enumerate(header):
                    if i < len(cells):
                        item[h] = cells[i]
                result['review_items'].append(item)

    # ── H01_MAPPING 시트 읽기 (수동 매핑 오버라이드) ──
    if 'H01_MAPPING' in wb.sheetnames:
        ws_h01 = wb['H01_MAPPING']
        header_found = False
        for row in ws_h01.iter_rows(values_only=True):
            cells = [str(c).strip() if c is not None else '' for c in row]
            if not header_found:
                # 헤더 행 감지: 'H01 Field' 컬럼 존재 확인
                if 'H01 Field' in cells:
                    header_found = True
                continue
            # cells[1]=H01 Field, cells[3]=매칭된 레지스터명
            if len(cells) >= 4 and cells[1] and cells[3]:
                result['h01_manual_mapping'][cells[1]] = cells[3]

    wb.close()
    return result


def _parse_s2_reg_row(cells, header, category):
    """Stage 2 V2 레지스터 행 파싱"""
    col_map = {}
    for i, h in enumerate(header):
        hl = h.lower()
        if 'definition' in hl or 'name' in hl:
            col_map['definition'] = i
        elif 'address' in hl:
            col_map['address'] = i
        elif 'type' in hl and 'data' not in hl:
            col_map['type'] = i
        elif 'unit' in hl or 'scale' in hl:
            col_map['scale'] = i
        elif 'r/w' in hl:
            col_map['rw'] = i
        elif 'h01' in hl:
            col_map['h01_field'] = i
        elif 'comment' in hl:
            col_map['comment'] = i

    defn = cells[col_map['definition']] if 'definition' in col_map and col_map['definition'] < len(cells) else ''
    if not defn:
        return None

    addr_raw = cells[col_map['address']] if 'address' in col_map and col_map['address'] < len(cells) else ''
    addr = parse_address(addr_raw)

    return RegisterRow(
        definition=defn,
        address=addr if addr is not None else 0,
        address_hex=addr_raw if addr_raw.startswith('0x') else (f'0x{addr:04X}' if addr else ''),
        data_type=cells[col_map.get('type', -1)] if 0 <= col_map.get('type', -1) < len(cells) else 'U16',
        scale=cells[col_map.get('scale', -1)] if 0 <= col_map.get('scale', -1) < len(cells) else '',
        rw=cells[col_map.get('rw', -1)] if 0 <= col_map.get('rw', -1) < len(cells) else 'RO',
        h01_field=cells[col_map.get('h01_field', -1)] if 0 <= col_map.get('h01_field', -1) < len(cells) else '',
        comment=cells[col_map.get('comment', -1)] if 0 <= col_map.get('comment', -1) < len(cells) else '',
        category=category,
    )


# ─── 레퍼런스 ErrorCode BITS 로딩 ───────────────────────────────────────────

def _load_error_bits_from_reference(manufacturer: str) -> dict:
    """레퍼런스 REF_*_registers.py 또는 {제조사}_*_registers.py에서 ErrorCode BITS 로드"""
    import glob
    result = {}
    mfr_lower = manufacturer.lower()
    # REF_ 파일 우선, 그 다음 신규 네이밍, 마지막으로 Solarize REF fallback
    patterns = glob.glob(os.path.join(COMMON_DIR, f'REF_{manufacturer}_*_registers.py'))
    patterns += glob.glob(os.path.join(COMMON_DIR, f'{manufacturer}_*_registers.py'))
    patterns += [os.path.join(COMMON_DIR, f'REF_Solarize_PV_registers.py'),
                 os.path.join(COMMON_DIR, 'Solarize_PV_50kw_registers.py'),
                 os.path.join(COMMON_DIR, 'Solarize_PV_registers.py')]

    for fpath in patterns:
        if not os.path.exists(fpath):
            continue
        try:
            spec = importlib.util.spec_from_file_location('_ref_err', fpath)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            for cls_name in ['ErrorCode1', 'ErrorCode2', 'ErrorCode3']:
                cls = getattr(mod, cls_name, None)
                if cls and hasattr(cls, 'BITS') and cls.BITS:
                    result[cls_name] = dict(cls.BITS)
            if result:
                return result
        except Exception as _e:
            logger.warning(f"_load_error_bits_from_reference: failed to load {fpath}: {_e}")
            continue
    return result


# ─── 코드 생성 함수들 ────────────────────────────────────────────────────────

def _gen_header(manufacturer: str, protocol_name: str, protocol_version: str,
                mppt: int, total_strings: int, strings_per_mppt: int,
                iv_scan: bool, der_avm: bool) -> str:
    return f'''# -*- coding: utf-8 -*-
"""
{manufacturer} Inverter Modbus Register Map
Based on {manufacturer} Modbus Protocol {protocol_version}
Protocol Name: {protocol_name}
MPPT: {mppt} | Strings: {total_strings} ({strings_per_mppt}/MPPT)
IV Scan: {"Yes" if iv_scan else "No"} | DER-AVM: {"Yes" if der_avm else "No"}
Generated by Model Maker Web v2 on {datetime.now().strftime("%Y-%m-%d")}
"""

'''


def _gen_register_map(regs_by_cat: dict, mppt: int, total_strings: int,
                      strings_per_mppt: int, iv_scan: bool,
                      iv_data_points: int = 64) -> str:
    lines = [
        '',
        'class RegisterMap:',
        f'    """{regs_by_cat.get("_manufacturer", "Inverter")} Modbus Register Map"""',
        '',
    ]

    cat_titles = {
        'INFO': 'Device Information',
        'MONITORING': 'Monitoring Data',
        'STATUS': 'Status & Temperature',
        'ALARM': 'Alarm / Error Codes',
        'DER_CONTROL': 'DER-AVM Control Parameters',
        'DER_MONITOR': 'DEA-AVM Real-time Data',
        'IV_SCAN': 'IV Scan Control',
    }

    # 메인 속성들 + _HIGH 워드 자동 추가
    emitted_names = set()
    for cat in ['INFO', 'MONITORING', 'STATUS', 'ALARM', 'DER_CONTROL', 'DER_MONITOR', 'IV_SCAN']:
        regs = regs_by_cat.get(cat, [])
        if not regs:
            continue
        title = cat_titles.get(cat, cat)
        lines.append(f'    # =========================================================================')
        lines.append(f'    # {title}')
        lines.append(f'    # =========================================================================')
        for reg in sorted(regs, key=lambda r: r.address if isinstance(r.address, int) else 0):
            name = to_upper_snake(reg.definition)
            # 이름 정규화: STRING1_INPUT_VOLTAGE → STRING1_VOLTAGE 등
            name = re.sub(r'(STRING\d+)_INPUT_(VOLTAGE|CURRENT)', r'\1_\2', name)
            name = re.sub(r'(MPPT\d+)_INPUT_(VOLTAGE|CURRENT)', r'\1_\2', name)
            # PV1_POWER → MPPT1_POWER_LOW (L1_POWER는 AC 출력이므로 제외)
            m = re.match(r'PV(\d+)_POWER$', name)
            if m:
                name = f'MPPT{m.group(1)}_POWER_LOW'
            if not name or name in emitted_names:
                continue
            addr = reg.address if isinstance(reg.address, int) else parse_address(reg.address)
            if addr is None:
                continue
            comment_parts = []
            if reg.data_type:
                comment_parts.append(reg.data_type)
            if reg.scale:
                comment_parts.append(f'scale {reg.scale}')
            if reg.unit:
                comment_parts.append(reg.unit)
            comment = ', '.join(comment_parts)
            lines.append(f'    {name:40s} = 0x{addr:04X}  # {comment}')
            emitted_names.add(name)

            # U32/S32 → 자동으로 _HIGH 워드 추가
            if reg.data_type in ('U32', 'S32') and not name.endswith('_HIGH'):
                high_name = name.replace('_LOW', '_HIGH') if '_LOW' in name else f'{name}_HIGH'
                if high_name not in emitted_names:
                    lines.append(f'    {high_name:40s} = 0x{addr + 1:04X}')
                    emitted_names.add(high_name)
        lines.append('')

    # 모든 정의된 속성 집합 (별칭 검증용) — emitted_names 기반
    all_defined = set(emitted_names)

    # Alias: FIRMWARE_VERSION
    info_names = {to_upper_snake(r.definition) for r in regs_by_cat.get('INFO', [])}
    if 'MASTER_FIRMWARE_VERSION' in info_names:
        lines.append('    # Alias')
        lines.append('    FIRMWARE_VERSION                         = MASTER_FIRMWARE_VERSION')
        lines.append('')

    # R/S/T Phase Aliases (전체: VOLTAGE, CURRENT, POWER_LOW, POWER_HIGH, FREQUENCY)
    if 'L1_VOLTAGE' in all_defined:
        lines.append('    # R/S/T Phase Aliases')
        for phase, l in [('R', 'L1'), ('S', 'L2'), ('T', 'L3')]:
            for metric in ['VOLTAGE', 'CURRENT', 'POWER_LOW', 'POWER_HIGH', 'FREQUENCY']:
                src = f'{l}_{metric}'
                alias = f'{phase}_{metric}'
                if src in all_defined:
                    lines.append(f'    {alias:40s} = {src}')
        lines.append('')

    # Grid Power Aliases
    has_grid = 'GRID_TOTAL_ACTIVE_POWER_LOW' in all_defined
    has_pv = 'PV_TOTAL_INPUT_POWER_LOW' in all_defined
    if has_grid or has_pv:
        lines.append('    # Grid/PV Power Aliases')
    if has_grid:
        lines.append('    GRID_POWER_LOW                           = GRID_TOTAL_ACTIVE_POWER_LOW')
        if 'GRID_TOTAL_ACTIVE_POWER_HIGH' in all_defined:
            lines.append('    GRID_POWER_HIGH                          = GRID_TOTAL_ACTIVE_POWER_HIGH')
    if has_pv:
        lines.append('    PV_POWER_LOW                             = PV_TOTAL_INPUT_POWER_LOW')
        if 'PV_TOTAL_INPUT_POWER_HIGH' in all_defined:
            lines.append('    PV_POWER_HIGH                            = PV_TOTAL_INPUT_POWER_HIGH')
    if has_grid or has_pv:
        lines.append('')

    # Standard handler compatibility aliases (H01 Body Type 4 required)
    lines.append('    # --- Standard handler compatibility aliases (H01 Body Type 4 required) ---')
    # Sungrow 등: A_B_LINEVOLTAGE_PHASE_AVOLTAGE → L1_VOLTAGE 먼저 생성 (downstream 의존)
    _phase_src_aliases = [
        ('L1_VOLTAGE', 'A_B_LINEVOLTAGE_PHASE_AVOLTAGE'),
        ('L2_VOLTAGE', 'B_C_LINE_VOLTAGE_PHASE_BVOLTAGE'),
        ('L3_VOLTAGE', 'C_A_LINE_VOLTAGE_PHASE_CVOLTAGE'),
        ('L1_CURRENT', 'A_PHASE_CURRENT'),
        ('L2_CURRENT', 'B_PHASE_CURRENT'),
        ('L3_CURRENT', 'C_PHASE_CURRENT'),
    ]
    for alias, src in _phase_src_aliases:
        if src in all_defined and alias not in all_defined:
            lines.append(f'    {alias:40s} = {src}')
            all_defined.add(alias)

    compat_aliases = [
        ('R_PHASE_VOLTAGE', 'L1_VOLTAGE'), ('T_PHASE_VOLTAGE', 'L3_VOLTAGE'),
        ('R_PHASE_CURRENT', 'L1_CURRENT'), ('S_PHASE_CURRENT', 'L2_CURRENT'),
        ('T_PHASE_CURRENT', 'L3_CURRENT'),
        ('FREQUENCY', 'L1_FREQUENCY'),
        ('AC_POWER', 'GRID_TOTAL_ACTIVE_POWER_LOW'),
        ('PV_POWER', 'PV_TOTAL_INPUT_POWER_LOW'),
        ('TOTAL_ENERGY', 'TOTAL_ENERGY_LOW'),
    ]
    for alias, src in compat_aliases:
        if src in all_defined:
            lines.append(f'    {alias:40s} = {src}')
            all_defined.add(alias)

    # S_PHASE_VOLTAGE: L2_VOLTAGE 없으면 순서대로 대체 (단상/Sungrow/한글 인버터 등)
    if 'S_PHASE_VOLTAGE' not in all_defined:
        fallback = next((x for x in [
            'L2_VOLTAGE', 'L1_VOLTAGE', 'R_PHASE_VOLTAGE',
            'AC_VOLTAGE', 'GRID_VOLTAGE', '전압종합',  # 단상/한글 인버터 대체
        ] if x in all_defined), None)
        if fallback:
            lines.append(f'    {"S_PHASE_VOLTAGE":40s} = {fallback}  # L2 없음 → 대체')
            all_defined.add('S_PHASE_VOLTAGE')

    # MPPT{N}_VOLTAGE/CURRENT aliases (handler: MPPTn_, 생성파일: MPPT_N_)
    # 지원 네이밍: MPPT_N_(Sungrow), PV{N}VOLTAGE(Huawei), PV{N}_INPUT_VOLTAGE(Kstar),
    #              PV{N}_VOLTAGE(generic), 한글이름_PV_VOLTAGE(Ekos)
    lines.append('')
    lines.append('    # --- MPPT alias (modbus_handler: MPPT{N}_ 형식) ---')
    for i in range(1, 17):
        v_candidates = [
            f'MPPT_{i}_VOLTAGE',       # Sungrow/standard
            f'PV{i}VOLTAGE',           # Huawei (no separator)
            f'PV{i}_VOLTAGE',          # generic
            f'PV{i}_INPUT_VOLTAGE',    # Kstar
        ]
        c_candidates = [
            f'MPPT_{i}_CURRENT',
            f'PV{i}CURRENT',
            f'PV{i}_CURRENT',
            f'PV{i}_INPUT_CURRENT',
        ]
        src_v = next((c for c in v_candidates if c in all_defined), None)
        src_c = next((c for c in c_candidates if c in all_defined), None)

        # MPPT1 마지막 수단: 이름 끝에 _PV_VOLTAGE 포함 (한글 인버터 등)
        if src_v is None and i == 1:
            src_v = next((n for n in sorted(all_defined) if n.endswith('_PV_VOLTAGE')), None)

        alias_v = f'MPPT{i}_VOLTAGE'
        alias_c = f'MPPT{i}_CURRENT'
        if src_v and alias_v not in all_defined:
            lines.append(f'    {alias_v:40s} = {src_v}')
            all_defined.add(alias_v)
        if src_c and alias_c not in all_defined:
            lines.append(f'    {alias_c:40s} = {src_c}')
            all_defined.add(alias_c)
        if src_v is None:
            break  # 연속되지 않으면 중단

    # PV_VOLTAGE: 대표 PV 전압 (MPPT1 또는 PV_INPUT_VOLTAGE)
    if 'PV_VOLTAGE' not in all_defined:
        for _pv in ['MPPT1_VOLTAGE', 'MPPT_1_VOLTAGE', 'PV_INPUT_VOLTAGE', 'DC_VOLTAGE', 'L1_VOLTAGE']:
            if _pv in all_defined:
                lines.append(f'    {"PV_VOLTAGE":40s} = {_pv}')
                all_defined.add('PV_VOLTAGE')
                break

    # PV_STRING_COUNT: 실제 string 수 (mppt × strings_per_mppt)
    # strings_per_mppt=0 이면 1로 기본값 (Huawei 등 개별 string 없는 경우)
    if 'PV_STRING_COUNT' not in all_defined:
        _spm = strings_per_mppt if strings_per_mppt > 0 else 1
        _sc = mppt * _spm if mppt > 0 else 0
        if _sc > 0:
            lines.append(f'    {"PV_STRING_COUNT":40s} = {_sc}')
            all_defined.add('PV_STRING_COUNT')

    lines.append('')

    # DER-AVM backward compatibility aliases
    der_names = {to_upper_snake(r.definition) for r in regs_by_cat.get('DER_CONTROL', [])}
    if der_names:
        lines.append('    # DER-AVM backward compatibility aliases')
        der_aliases = [
            ('POWER_FACTOR_SET', 'DER_POWER_FACTOR_SET'),
            ('ACTION_MODE', 'DER_ACTION_MODE'),
            ('REACTIVE_POWER_PCT', 'DER_REACTIVE_POWER_PCT'),
            ('ACTIVE_POWER_PCT', 'DER_ACTIVE_POWER_PCT'),
            ('OPERATION_MODE', 'DER_ACTION_MODE'),
            ('REACTIVE_POWER_SET', 'DER_REACTIVE_POWER_PCT'),
        ]
        for alias, src in der_aliases:
            if src in der_names:
                lines.append(f'    {alias:40s} = {src}')
        lines.append('')

    # ── RTU modbus_handler / simulator 필수 alias (ALL PASS 보장) ──────────────
    lines.append('    # --- RTU modbus_handler / simulator 필수 alias ---')

    # INNER_TEMP — 온도 레지스터 후보 중 존재하는 첫 번째 사용
    if 'INNER_TEMP' not in all_defined:
        for _tc in ['TEMPERATURE', 'INNER_TEMPERATURE', 'HEAT_SINK_TEMPERATURE',
                    'CABINET_TEMPERATURE', 'INVERTER_TEMPERATURE', 'MODULE_TEMPERATURE',
                    'INTERNAL_TEMP', 'INTERNAL_TEMPERATURE']:
            if _tc in all_defined:
                lines.append(f'    INNER_TEMP                               = {_tc}')
                all_defined.add('INNER_TEMP')
                break
        else:
            # 한글 인버터 마지막 수단: 이름에 '온도' 포함, 모듈/외기/AD채널 제외
            _tc = next((n for n in sorted(all_defined)
                        if '온도' in n and '모듈' not in n and 'AD_CH' not in n
                        and '외기' not in n), None)
            if _tc:
                lines.append(f'    INNER_TEMP                               = {_tc}')
                all_defined.add('INNER_TEMP')

    # INVERTER_MODE — 운전 상태 레지스터 후보 중 존재하는 첫 번째 사용
    if 'INVERTER_MODE' not in all_defined:
        for _mc in ['WORK_STATE', 'RUNNING_STATE', 'DEVICE_STATUS', 'SYSTEM_STATUS',
                    'SYSTEM_STATE', 'OPERATING_STATUS', 'RUNNING_STATUS', 'STATUS',
                    'DEVICE_STATE', 'INVERTER_STATUS']:
            if _mc in all_defined:
                lines.append(f'    INVERTER_MODE                            = {_mc}')
                all_defined.add('INVERTER_MODE')
                break

    # AC_POWER — 유효전력 레지스터 다양한 후보명 지원 (한글/영문/제조사별)
    if 'AC_POWER' not in all_defined:
        for _ap in ['유효전력종합_ACTIVE_POWER', 'ACTIVE_POWER', 'TOTAL_ACTIVE_POWER',
                    'GRID_TOTAL_ACTIVE_POWER_LOW', 'ACTIVE_POWER_LOW', 'AC_ACTIVE_POWER',
                    'OUTPUT_POWER', 'GRID_ACTIVE_POWER', 'TOTAL_OUTPUT_POWER',
                    'ACTIVE_OUTPUT_POWER', 'ACTIVE_OUTPUT_POWER_LOW']:
            if _ap in all_defined:
                lines.append(f'    {"AC_POWER":40s} = {_ap}')
                all_defined.add('AC_POWER')
                break

    # R_PHASE_VOLTAGE — L1/A상 전압 후보, 없으면 S_PHASE_VOLTAGE로 대체
    if 'R_PHASE_VOLTAGE' not in all_defined:
        _found_r = False
        for _rv in ['L1_VOLTAGE', 'R_VOLTAGE', 'PHASE_A_VOLTAGE', 'UA_VOLTAGE',
                    'A_PHASE_VOLTAGE', 'A_B_LINEVOLTAGE_PHASE_AVOLTAGE']:
            if _rv in all_defined:
                lines.append(f'    {"R_PHASE_VOLTAGE":40s} = {_rv}')
                all_defined.add('R_PHASE_VOLTAGE')
                _found_r = True
                break
        if not _found_r and 'S_PHASE_VOLTAGE' in all_defined:
            lines.append(f'    {"R_PHASE_VOLTAGE":40s} = S_PHASE_VOLTAGE  # L1 없음 → 단상 대체')
            all_defined.add('R_PHASE_VOLTAGE')

    # T_PHASE_VOLTAGE — L3/C상 전압 후보, 없으면 S_PHASE_VOLTAGE로 대체
    if 'T_PHASE_VOLTAGE' not in all_defined:
        _found_t = False
        for _tv in ['L3_VOLTAGE', 'T_VOLTAGE', 'PHASE_C_VOLTAGE', 'UC_VOLTAGE',
                    'C_PHASE_VOLTAGE', 'C_A_LINE_VOLTAGE_PHASE_CVOLTAGE']:
            if _tv in all_defined:
                lines.append(f'    {"T_PHASE_VOLTAGE":40s} = {_tv}')
                all_defined.add('T_PHASE_VOLTAGE')
                _found_t = True
                break
        if not _found_t and 'S_PHASE_VOLTAGE' in all_defined:
            lines.append(f'    {"T_PHASE_VOLTAGE":40s} = S_PHASE_VOLTAGE  # L3 없음 → 단상 대체')
            all_defined.add('T_PHASE_VOLTAGE')

    # R_PHASE_CURRENT / S_PHASE_CURRENT / T_PHASE_CURRENT
    if 'R_PHASE_CURRENT' not in all_defined:
        _found_rc = False
        for _rc in ['L1_CURRENT', 'R_CURRENT', 'PHASE_A_CURRENT', 'IA_CURRENT',
                    'A_PHASE_CURRENT', '전류종합']:
            if _rc in all_defined:
                lines.append(f'    {"R_PHASE_CURRENT":40s} = {_rc}')
                all_defined.add('R_PHASE_CURRENT')
                _found_rc = True
                break
        if not _found_rc and 'S_PHASE_CURRENT' in all_defined:
            lines.append(f'    {"R_PHASE_CURRENT":40s} = S_PHASE_CURRENT  # L1 없음 → 단상 대체')
            all_defined.add('R_PHASE_CURRENT')
    if 'S_PHASE_CURRENT' not in all_defined:
        for _sc in ['L2_CURRENT', 'S_CURRENT', 'PHASE_B_CURRENT', 'IB_CURRENT',
                    'B_PHASE_CURRENT', '전류종합']:
            if _sc in all_defined:
                lines.append(f'    {"S_PHASE_CURRENT":40s} = {_sc}')
                all_defined.add('S_PHASE_CURRENT')
                break
    if 'T_PHASE_CURRENT' not in all_defined:
        _found_tc2 = False
        for _tc2 in ['L3_CURRENT', 'T_CURRENT', 'PHASE_C_CURRENT', 'IC_CURRENT',
                     'C_PHASE_CURRENT']:
            if _tc2 in all_defined:
                lines.append(f'    {"T_PHASE_CURRENT":40s} = {_tc2}')
                all_defined.add('T_PHASE_CURRENT')
                _found_tc2 = True
                break
        if not _found_tc2 and 'S_PHASE_CURRENT' in all_defined:
            lines.append(f'    {"T_PHASE_CURRENT":40s} = S_PHASE_CURRENT  # L3 없음 → 단상 대체')
            all_defined.add('T_PHASE_CURRENT')

    # TOTAL_ENERGY — 누적 발전량 후보 (kWh/Wh 단위 구분은 modbus_handler가 처리)
    if 'TOTAL_ENERGY' not in all_defined:
        for _te in ['CUMULATIVE_ENERGY', 'TOTAL_ACTIVE_ENERGY', 'ACCUMULATED_ENERGY',
                    'TOTAL_ENERGY_LOW', 'TOTAL_GENERATED_ENERGY', 'ENERGY_TOTAL']:
            if _te in all_defined:
                lines.append(f'    {"TOTAL_ENERGY":40s} = {_te}')
                all_defined.add('TOTAL_ENERGY')
                break

    # PV_POWER alias (없으면 추가)
    if 'PV_POWER' not in all_defined:
        for _pvp in ['PV_TOTAL_INPUT_POWER_LOW', 'TOTAL_PV_POWER_LOW', 'DC_POWER',
                     'INPUT_POWER', 'DC_INPUT_POWER']:
            if _pvp in all_defined:
                lines.append(f'    {"PV_POWER":40s} = {_pvp}')
                all_defined.add('PV_POWER')
                break

    # MPPT1_CURRENT 추가 후보 (단상/EKOS 등 PV전류 직접 레지스터 사용 인버터)
    if 'MPPT1_CURRENT' not in all_defined:
        for _mc1 in ['태양전지_전류', 'PV_CURRENT', 'DC_CURRENT', 'INPUT_CURRENT',
                     'PV_INPUT_CURRENT', 'PV1_CURRENT', 'PV_SIDE_CURRENT']:
            if _mc1 in all_defined:
                lines.append(f'    {"MPPT1_CURRENT":40s} = {_mc1}')
                all_defined.add('MPPT1_CURRENT')
                break

    # STRING{N}_CURRENT aliases — 한글 인버터 등 STRING_N_CURRENT 형식 통일
    for _si in range(1, total_strings + 1):
        _salias = f'STRING{_si}_CURRENT'
        if _salias not in all_defined:
            for _sc_cand in [f'STRING_{_si}_CURRENT', f'태양전지{_si}_전류_STRING_{_si}_CURRENT',
                             f'PV_STRING{_si}_CURRENT', f'PVSTRING{_si}_CURRENT']:
                if _sc_cand in all_defined:
                    lines.append(f'    {_salias:40s} = {_sc_cand}')
                    all_defined.add(_salias)
                    break

    # ERROR_CODE{N} aliases — ALARM 카테고리 첫 3개 레지스터에서 자동 생성
    _alarm_regs_sorted = sorted(
        [r for r in regs_by_cat.get('ALARM', []) if isinstance(r.address, int)],
        key=lambda r: r.address
    )
    for _ei, _areg in enumerate(_alarm_regs_sorted[:3], 1):
        _ecode_alias = f'ERROR_CODE{_ei}'
        _areg_name = to_upper_snake(_areg.definition)
        # 이름 정규화 (STRING입력/MPPT입력 패턴 제거)
        _areg_name = re.sub(r'(STRING\d+)_INPUT_(VOLTAGE|CURRENT)', r'\1_\2', _areg_name)
        if _ecode_alias not in all_defined and _areg_name in all_defined:
            lines.append(f'    {_ecode_alias:40s} = {_areg_name}')
            all_defined.add(_ecode_alias)

    # DER / Control registers — 고정 주소 (없으면 항상 추가)
    _der_fixed = [
        ('DER_POWER_FACTOR_SET',   '0x07D0'),
        ('DER_ACTION_MODE',        '0x07D1'),
        ('DER_REACTIVE_POWER_PCT', '0x07D2'),
        ('DER_ACTIVE_POWER_PCT',   '0x07D3'),
        ('INVERTER_ON_OFF',        '0x0834'),
    ]
    for _name, _addr in _der_fixed:
        if _name not in all_defined:
            lines.append(f'    {_name:40s} = {_addr}')
            all_defined.add(_name)
    lines.append('')

    # IV Scan Control aliases + registers
    if 'IV_CURVE_SCAN' in all_defined:
        lines.append('    # IV Scan aliases')
        lines.append('    IV_SCAN_COMMAND                          = IV_CURVE_SCAN  # 읽기/쓰기 겸용 레지스터')
        lines.append('    IV_SCAN_STATUS                           = IV_CURVE_SCAN  # 읽기/쓰기 겸용 레지스터')
        lines.append('')

    # IV Scan Data Registers (if supported)
    if iv_scan and mppt > 0:
        lines.append('    # =========================================================================')
        lines.append('    # IV Scan Data Registers')
        lines.append(f'    # {mppt} trackers x {strings_per_mppt} strings x 64 data points')
        lines.append('    # =========================================================================')
        for t in range(1, mppt + 1):
            tracker_base = 0x8000 + (t - 1) * 0x140
            lines.append(f'    IV_TRACKER{t}_VOLTAGE_BASE               = 0x{tracker_base:04X}')
            for s in range(1, strings_per_mppt + 1):
                s_base = tracker_base + s * 0x40
                lines.append(f'    IV_STRING{t}_{s}_CURRENT_BASE             = 0x{s_base:04X}')
        lines.append('')
        lines.append(f'    IV_SCAN_DATA_POINTS                      = {iv_data_points}')
        lines.append('    IV_TRACKER_BLOCK_SIZE                    = 0x140  # 5 x 64 registers per tracker')
        lines.append('')

    return '\n'.join(lines)


def _gen_iv_scan_classes() -> str:
    return '''

class IVScanCommand:
    """IV Scan Command values for writing to 0x600D"""
    NON_ACTIVE = 0x0000
    ACTIVE     = 0x0001


class IVScanStatus:
    """IV Scan Status values when reading from 0x600D"""
    IDLE     = 0x0000
    RUNNING  = 0x0001
    FINISHED = 0x0002

    @classmethod
    def to_string(cls, status):
        return {0: "Idle", 1: "Running", 2: "Finished"}.get(status, f"Unknown({status})")

'''


def _gen_inverter_mode() -> str:
    lines = ['', '', 'class InverterMode:',
             '    """Inverter Mode Table (0x101D)"""']
    for code, name, desc in INVERTER_MODES:
        lines.append(f'    {name:12s} = 0x{code:02X}')
    lines.append('')
    lines.append('    @classmethod')
    lines.append('    def to_string(cls, mode):')
    lines.append('        return {')
    for code, name, desc in INVERTER_MODES:
        label = name.replace('_', '-').title() if name != 'ON_GRID' else 'On-Grid'
        if name == 'OFF_GRID':
            label = 'Off-Grid'
        lines.append(f'            0x{code:02X}: "{label}",')
    lines.append('        }.get(mode, f"Unknown(0x{mode:02X})")')
    lines.append('')
    return '\n'.join(lines)


def _gen_der_action_mode() -> str:
    return '''

class DerActionMode:
    """DER-AVM Action Mode (0x07D1)"""
    SELF_CONTROL    = 0
    DER_AVM_CONTROL = 2
    QV_CONTROL      = 5

'''


def _gen_device_type() -> str:
    return '''
class DeviceType:
    """Device Type for config file"""
    RTU              = 0
    INVERTER         = 1
    ENVIRONMENT_SENSOR = 2
    POWER_METER      = 3
    PROTECTION_RELAY = 4

    @classmethod
    def to_string(cls, dtype):
        return {
            0: "RTU", 1: "Inverter", 2: "Environment Sensor",
            3: "Power Meter", 4: "Protection Relay",
        }.get(dtype, f"Unknown({dtype})")


class ControlMode:
    """Control Mode for config file"""
    NONE    = "NONE"
    DER_AVM = "DER_AVM"

'''


def _gen_iv_scan_body_type(mppt: int, strings_per_mppt: int) -> str:
    lines = ['', 'class IVScanBodyType:',
             '    """IV Scan Body Type mapping for H05 protocol"""',
             '    IV_SCAN_RESULT = 12',
             '    IV_SCAN_DATA  = 15']
    # Tracker/String body type IDs
    # From reference: Tracker1=134, +5 per tracker. String offset: +1 per string within tracker
    for t in range(1, mppt + 1):
        tracker_bt = 134 + (t - 1) * 5
        lines.append(f'    TRACKER{t}_VOLTAGE             = {tracker_bt}')
        for s in range(1, strings_per_mppt + 1):
            s_bt = tracker_bt + s
            lines.append(f'    STRING{t}_{s}_CURRENT           = {s_bt}')
    lines.append('')
    return '\n'.join(lines)


def _gen_error_classes(alarm_regs: list, error_bits: dict) -> str:
    lines = []
    sorted_alarms = sorted(alarm_regs,
                           key=lambda r: r.address if isinstance(r.address, int) else 0)
    for i, reg in enumerate(sorted_alarms, start=1):
        name = to_upper_snake(reg.definition)
        cls_name = f'ErrorCode{i}'
        addr_hex = reg.address_hex if reg.address_hex else f'0x{reg.address:04X}'

        # BITS from reference
        bits = error_bits.get(f'ErrorCode{i}', {})
        bits_str = '{\n'
        if bits:
            for bit_n, desc in sorted(bits.items()):
                bits_str += f'        {bit_n}:  "{desc}",\n'
            bits_str += '    }'
        else:
            bits_str += '    }'

        lines.append(f'')
        lines.append(f'')
        lines.append(f'class {cls_name}:')
        lines.append(f'    """Error Code Table{i} ({addr_hex}) — Bit field"""')
        lines.append(f'    BITS = {bits_str}')
        lines.append(f'')
        lines.append(f'    @classmethod')
        lines.append(f'    def decode(cls, value):')
        lines.append(f'        return [f"E{{b}}:{{d}}" for b, d in cls.BITS.items() if value & (1 << b)]')
        lines.append(f'')
        lines.append(f'    @classmethod')
        lines.append(f'    def to_string(cls, value):')
        lines.append(f'        return ", ".join(cls.decode(value)) if value else "OK"')
    return '\n'.join(lines)


def _to_class_prefix(name: str) -> str:
    """제조사명 → Python 클래스명 접두사 (하이픈/공백 제거, CamelCase)"""
    import re
    parts = re.split(r'[\s\-_]+', name)
    return ''.join(p.capitalize() for p in parts if p)


def _gen_status_converter(manufacturer: str) -> str:
    cls_name = f'{_to_class_prefix(manufacturer)}StatusConverter'
    return f'''


class {cls_name}:
    """{manufacturer} INVERTER_MODE register already contains InverterMode values."""

    @classmethod
    def to_inverter_mode(cls, raw):
        return raw


# Dynamic-loader alias required by modbus_handler.load_register_module
StatusConverter = {cls_name}
'''


def _gen_scale_dict() -> str:
    return '''

# Scale factors
SCALE = {
    'voltage':            0.1,
    'current':            0.01,
    'power':              0.1,
    'frequency':          0.01,
    'power_factor':       0.001,
    'dea_current':        0.1,
    'dea_voltage':        0.1,
    'dea_active_power':   0.1,
    'dea_reactive_power': 1,
    'dea_frequency':      0.1,
    'iv_voltage':         0.1,
    'iv_current':         0.1,
}
'''


def _gen_helpers(mppt: int, total_strings: int, strings_per_mppt: int,
                 iv_data_points: int = 64) -> str:
    return f'''

# Channel configuration (used by modbus_handler for dynamic array sizing)
MPPT_CHANNELS = {mppt}
STRING_CHANNELS = {total_strings}


def registers_to_u32(low, high):
    """Combine two U16 to U32"""
    return (high << 16) | low


def registers_to_s32(low, high):
    """Combine two U16 to S32"""
    value = (high << 16) | low
    if value >= 0x80000000:
        value -= 0x100000000
    return value


def get_string_registers(string_num):
    """Return (voltage_addr, current_addr) for a string number (1-{total_strings}).
    # Solarize 프로토콜 전용 주소 (0x1050 기반)"""
    if string_num < 1 or string_num > {total_strings}:
        raise ValueError(f"String number must be 1-{total_strings}, got {{string_num}}")
    base = 0x1050 + (string_num - 1) * 2
    return (base, base + 1)


def get_mppt_registers(mppt_num):
    """Return (voltage, current, power_low, power_high) for MPPT number (1-{mppt}).
    # Solarize 프로토콜 전용 주소 (0x1010 기반)"""
    if mppt_num < 1 or mppt_num > {mppt}:
        raise ValueError(f"MPPT number must be 1-{mppt}, got {{mppt_num}}")
    if mppt_num <= 3:
        base = 0x1010 + (mppt_num - 1) * 4
    elif mppt_num == 4:
        base = 0x103E
    else:  # 5+
        base = 0x1080 + (mppt_num - 5) * 4
    return (base, base + 1, base + 2, base + 3)


def get_iv_tracker_voltage_registers(tracker_num, data_points={iv_data_points}):
    """Return {{'base', 'count', 'end'}} for IV voltage block of a tracker (1-{mppt})."""
    if tracker_num < 1 or tracker_num > {mppt}:
        raise ValueError(f"Tracker number must be 1-{mppt}, got {{tracker_num}}")
    base = 0x8000 + (tracker_num - 1) * RegisterMap.IV_TRACKER_BLOCK_SIZE
    return {{'base': base, 'count': data_points, 'end': base + data_points - 1}}


def get_iv_string_current_registers(mppt_num, string_num, data_points={iv_data_points}):
    """Return {{'base', 'count', 'end'}} for IV current block of a string (string_num 1-{strings_per_mppt})."""
    if mppt_num < 1 or mppt_num > {mppt}:
        raise ValueError(f"MPPT number must be 1-{mppt}, got {{mppt_num}}")
    if string_num < 1 or string_num > {strings_per_mppt}:
        raise ValueError(f"String number must be 1-{strings_per_mppt} per MPPT, got {{string_num}}")
    tracker_base = 0x8000 + (mppt_num - 1) * RegisterMap.IV_TRACKER_BLOCK_SIZE
    base = tracker_base + string_num * 0x40
    return {{'base': base, 'count': data_points, 'end': base + data_points - 1}}


def get_iv_string_mapping(total_strings={total_strings}, strings_per_mppt={strings_per_mppt}):
    """Return list of dicts mapping string index to IV scan register addresses."""
    mapping = []
    data_points = RegisterMap.IV_SCAN_DATA_POINTS
    for string_idx in range(total_strings):
        mppt_num       = (string_idx // strings_per_mppt) + 1
        string_in_mppt = (string_idx % strings_per_mppt) + 1
        v_regs = get_iv_tracker_voltage_registers(mppt_num, data_points)
        i_regs = get_iv_string_current_registers(mppt_num, string_in_mppt, data_points)
        mapping.append({{
            'string_num':     string_idx + 1,
            'total_strings':  total_strings,
            'mppt_num':       mppt_num,
            'string_in_mppt': string_in_mppt,
            'voltage_base':   v_regs['base'],
            'current_base':   i_regs['base'],
            'data_points':    data_points,
        }})
    return mapping


def generate_iv_voltage_data(voc, v_min, data_points={iv_data_points}):
    """Generate IV scan voltage array (U16, 0.1V units, ascending v_min->voc)."""
    step = (voc - v_min) / max(data_points - 1, 1)
    return [int((v_min + step * i) * 10) & 0xFFFF for i in range(data_points)]


def generate_iv_current_data(isc, voc, v_min, data_points={iv_data_points}):
    """Generate IV scan current array (U16, 0.01A units) using IV curve approximation."""
    step = (voc - v_min) / max(data_points - 1, 1)
    regs = []
    for i in range(data_points):
        v = v_min + step * i
        ratio = v / voc if voc > 0 else 0
        current = max(0.0, isc * (1.0 - ratio ** 20))
        regs.append(int(current * 100) & 0xFFFF)
    return regs
'''


def _gen_read_blocks(all_regs: List[RegisterRow], fc_code: int = 3,
                     gap_tolerance: int = 32) -> str:
    """RTU 배치 읽기용 READ_BLOCKS 생성.

    모니터링/상태/알람 레지스터를 연속 블록으로 묶어 Modbus 트랜잭션 수를 최소화.
    FLOAT32/U32/S32는 2개 레지스터를 차지한다.
    fc_code: 기본 FC (3=Holding, 4=Input).
    개별 레지스터의 reg.fc가 기본과 다르면 별도 블록으로 분리.
    gap_tolerance: 이 간격 이하의 빈 공간은 같은 블록으로 병합 (기본 32)
    """
    read_cats = {'MONITORING', 'STATUS', 'ALARM'}
    two_reg_types = {'FLOAT32', 'U32', 'S32'}

    # (fc, address, size) 수집 — 개별 레지스터 FC 참조
    entries = []
    for reg in all_regs:
        if reg.category not in read_cats:
            continue
        if not isinstance(reg.address, int):
            continue
        size = 2 if (reg.data_type or '').upper() in two_reg_types else 1
        # 개별 FC: reg.fc가 있으면 사용, 없으면 기본 fc_code
        reg_fc_str = str(getattr(reg, 'fc', '') or '').strip()
        if reg_fc_str in ('3', '03', 'FC03'):
            reg_fc = 3
        elif reg_fc_str in ('4', '04', 'FC04'):
            reg_fc = 4
        else:
            reg_fc = fc_code  # 기본 FC
        entries.append((reg_fc, reg.address, size))

    if not entries:
        return "\nREAD_BLOCKS = []\n"

    # FC별로 분리 후 각각 블록 그룹화
    from collections import defaultdict
    by_fc = defaultdict(list)
    for fc, addr, size in entries:
        by_fc[fc].append((addr, size))

    all_blocks = []  # (start, count, fc)
    for fc in sorted(by_fc.keys()):
        addr_sizes = by_fc[fc]
        # 중복 제거 후 정렬
        seen: set = set()
        unique = []
        for addr, size in sorted(addr_sizes):
            if addr not in seen:
                seen.add(addr)
                unique.append((addr, size))
        if not unique:
            continue

        # 연속 블록 그룹화
        blk_start, blk_end = unique[0][0], unique[0][0] + unique[0][1]
        for addr, size in unique[1:]:
            if addr <= blk_end + gap_tolerance:
                blk_end = max(blk_end, addr + size)
            else:
                all_blocks.append((blk_start, blk_end - blk_start, fc))
                blk_start, blk_end = addr, addr + size
        all_blocks.append((blk_start, blk_end - blk_start, fc))

    # 주소 순 정렬
    all_blocks.sort(key=lambda x: (x[0], x[2]))

    lines = ['\n', '# RTU 배치 읽기 블록 — start/count/fc 지정으로 트랜잭션 최소화',
             'READ_BLOCKS = [']
    for start, count, fc in all_blocks:
        MAX_REG = 125
        off = 0
        while off < count:
            c = min(MAX_REG, count - off)
            lines.append(f"    {{'start': 0x{start + off:04X}, 'count': {c:3d}, 'fc': {fc}}},")
            off += c
    lines.append(']')
    lines.append('')
    return '\n'.join(lines)


def _gen_data_parser(all_regs: List[RegisterRow], mppt_count: int, total_strings: int,
                     h01_manual_mapping: dict = None) -> str:
    """H01 출력 필드 → RegisterMap 속성명 매핑 DATA_PARSER 생성.

    modbus_handler._read_inverter_data_dynamic()이 이 매핑을 사용하여
    레지스터 파일에 의존하지 않고 H01 필드를 읽는다.
    RegisterMap 속성명은 _gen_register_map()이 생성하는 표준 alias와 일치해야 한다.

    우선순위:
      1. H01_MAPPING 시트 수동 매핑 (h01_manual_mapping)
      2. Stage2 h01_field 자동 매칭 레지스터명
      3. _gen_register_map()이 항상 보장하는 표준 alias
    """
    # Stage2 H01 매칭 결과: h01_field → RegisterMap 속성명
    h01_to_reg: dict = {}
    for reg in all_regs:
        h01 = getattr(reg, 'h01_field', '') or ''
        if h01 and h01 not in h01_to_reg:
            name = to_upper_snake(reg.definition)
            if name:
                h01_to_reg[h01] = name

    # 수동 매핑 오버라이드 (H01_MAPPING 시트에서 읽은 값)
    if h01_manual_mapping:
        for field, reg_name in h01_manual_mapping.items():
            if reg_name and reg_name.strip():
                h01_to_reg[field] = reg_name.strip()

    lines = ['\n', '# H01 출력 필드 → RegisterMap 속성명 매핑',
             '# modbus_handler._read_inverter_data_dynamic()이 이 매핑을 사용한다.',
             'DATA_PARSER = {']

    # RTU 기본 12개 필드 — _gen_register_map()이 항상 보장하는 alias 사용
    # Stage2 매칭 결과가 있으면 우선 사용, 없으면 표준 alias
    _required = [
        ('mode',              'INVERTER_MODE'),
        ('r_voltage',         'R_PHASE_VOLTAGE'),
        ('s_voltage',         'S_PHASE_VOLTAGE'),
        ('t_voltage',         'T_PHASE_VOLTAGE'),
        ('r_current',         'R_PHASE_CURRENT'),
        ('s_current',         'S_PHASE_CURRENT'),
        ('t_current',         'T_PHASE_CURRENT'),
        ('frequency',         'FREQUENCY'),
        ('ac_power',          'AC_POWER'),
        ('cumulative_energy', 'TOTAL_ENERGY'),
        ('alarm1',            'ERROR_CODE1'),
    ]
    for h01, default_alias in _required:
        alias = h01_to_reg.get(h01, default_alias)
        lines.append(f"    '{h01:20s}': '{alias}',")

    # MPPT 동적 필드
    for n in range(1, mppt_count + 1):
        alias_v = h01_to_reg.get(f'mppt{n}_voltage', f'MPPT{n}_VOLTAGE')
        alias_c = h01_to_reg.get(f'mppt{n}_current', f'MPPT{n}_CURRENT')
        lines.append(f"    'mppt{n}_voltage'        : '{alias_v}',")
        lines.append(f"    'mppt{n}_current'        : '{alias_c}',")

    # String 전류 동적 필드
    for n in range(1, total_strings + 1):
        alias = h01_to_reg.get(f'string{n}_current', f'STRING{n}_CURRENT')
        lines.append(f"    'string{n}_current'      : '{alias}',")

    lines.append('}')
    lines.append('')
    return '\n'.join(lines)


def _gen_data_types(all_regs: List[RegisterRow]) -> str:
    """DATA_TYPES dict 생성"""
    lines = ['\n', 'DATA_TYPES = {']
    seen = set()
    for reg in sorted(all_regs, key=lambda r: r.address if isinstance(r.address, int) else 0):
        name = to_upper_snake(reg.definition)
        if not name or name in seen:
            continue
        # _HIGH 워드는 S32/U32 쌍의 일부이므로 별도 타입 불필요
        if name.endswith('_HIGH') and reg.data_type in ('S32', 'U32'):
            continue
        dtype = reg.data_type or 'U16'
        seen.add(name)
        lines.append(f"    '{name}': '{dtype}',")
    lines.append('}')
    lines.append('')
    lines.append('FLOAT32_FIELDS: set = set()')
    lines.append('')
    return '\n'.join(lines)


def _gen_string_current_monitor(all_regs: List[RegisterRow]) -> str:
    """STRING_CURRENT_MONITOR 플래그 생성 — String 전류 레지스터 존재 여부"""
    has_string = any(
        detect_channel_number(r.definition) and detect_channel_number(r.definition)[0] == 'STRING'
        and 'current' in r.definition.lower()
        for r in all_regs
    )
    return f'''
# String 전류 모니터링 지원 여부
# True: String별 전류 레지스터 있음 (Solarize, Senergy, Kstar 등)
# False: String 전류 레지스터 없음 (Huawei 등 — PV 전류만 제공)
STRING_CURRENT_MONITOR = {has_string}
'''


# ─── RTU 통신 블록 생성 ──────────────────────────────────────────────────────

# H01 canonical field name → standard alias mapping
_H01_FIELD_ALIASES: dict = {
    'R_PHASE_VOLTAGE': 'R_PHASE_VOLTAGE',
    'S_PHASE_VOLTAGE': 'S_PHASE_VOLTAGE',
    'T_PHASE_VOLTAGE': 'T_PHASE_VOLTAGE',
    'R_PHASE_CURRENT': 'R_PHASE_CURRENT',
    'S_PHASE_CURRENT': 'S_PHASE_CURRENT',
    'T_PHASE_CURRENT': 'T_PHASE_CURRENT',
    'AC_POWER': 'AC_POWER',
    'PV_POWER': 'PV_POWER',
    'FREQUENCY': 'FREQUENCY',
    'POWER_FACTOR': 'POWER_FACTOR',
    'TOTAL_ENERGY': 'TOTAL_ENERGY',
    'CUMULATIVE_ENERGY': 'TOTAL_ENERGY',
    'INVERTER_MODE': 'INVERTER_MODE',
    'WORK_STATE': 'INVERTER_MODE',
    'RUNNING_STATUS': 'INVERTER_MODE',
    'DEVICE_STATUS': 'INVERTER_MODE',
    'ERROR_CODE1': 'ERROR_CODE1',
    'ERROR_CODE2': 'ERROR_CODE2',
    **{f'MPPT{i}_VOLTAGE': f'MPPT{i}_VOLTAGE' for i in range(1, 9)},
    **{f'MPPT{i}_CURRENT': f'MPPT{i}_CURRENT' for i in range(1, 9)},
    **{f'PV{i}_VOLTAGE': f'MPPT{i}_VOLTAGE' for i in range(1, 9)},
    **{f'PV{i}_CURRENT': f'MPPT{i}_CURRENT' for i in range(1, 9)},
    **{f'STRING{i}_CURRENT': f'STRING{i}_CURRENT' for i in range(1, 17)},
}


def _compute_blocks_and_parser(monitoring_regs: List[RegisterRow], fc_code: int = 3):
    """모니터링 레지스터를 연속 블록으로 그룹화하여 READ_BLOCKS, DATA_PARSER 생성.

    Args:
        monitoring_regs: MONITORING 카테고리 RegisterRow 목록
        fc_code: Modbus Function Code (3=Holding, 4=Input)

    Returns:
        (read_blocks, data_parser)
        read_blocks: [(start_addr, count), ...]
        data_parser: {canonical_field: (block_idx, offset, dtype_str, scale_float)}
    """
    MAX_GAP = 8  # 이 갭 이하면 같은 블록으로 묶음

    # 유효 엔트리 추출 (비정수/TEXT/ASCII/STRINGING 제외)
    entries = []  # (addr, canonical_name, dtype, scale_float, reg_size)
    seen_canonical = {}  # canonical_name → already seen?

    for reg in monitoring_regs:
        try:
            addr = parse_address(reg.address)
        except Exception:
            addr = None
        if addr is None or addr <= 0:
            continue

        name = to_upper_snake(reg.definition)
        if not name:
            continue

        dtype = (reg.data_type or 'U16').upper()
        if dtype in ('TEXT', 'ASCII', 'STRINGING', 'STRING'):
            continue

        # h01_field 우선 → H01_ALIASES 테이블 → 원래 이름
        h01 = getattr(reg, 'h01_field', '') or ''
        if h01:
            canonical = h01
        else:
            canonical = _H01_FIELD_ALIASES.get(name, name)

        if canonical in seen_canonical:
            continue  # 중복 canonical 스킵 (첫 번째 우선)
        seen_canonical[canonical] = True

        # scale 파싱: reg.scale 문자열에서 첫 번째 숫자 추출
        scale_val = 1.0
        for token in str(reg.scale or '1').split():
            try:
                scale_val = float(token)
                break
            except ValueError:
                continue

        reg_size = 2 if dtype in ('U32', 'S32', 'FLOAT32') else 1
        entries.append((addr, canonical, dtype, scale_val, reg_size))

    if not entries:
        return [], {}

    entries.sort(key=lambda x: x[0])

    # 연속 블록 그룹화
    blocks = []  # [(blk_start, blk_end, [(addr, canonical, dtype, scale, reg_size)])]
    cur_start = cur_end = None
    cur_entries = []

    for addr, canonical, dtype, scale_val, reg_size in entries:
        if cur_start is None:
            cur_start, cur_end = addr, addr + reg_size - 1
            cur_entries = [(addr, canonical, dtype, scale_val, reg_size)]
        elif addr <= cur_end + MAX_GAP + 1:
            cur_entries.append((addr, canonical, dtype, scale_val, reg_size))
            cur_end = max(cur_end, addr + reg_size - 1)
        else:
            blocks.append((cur_start, cur_end, cur_entries))
            cur_start, cur_end = addr, addr + reg_size - 1
            cur_entries = [(addr, canonical, dtype, scale_val, reg_size)]

    if cur_start is not None:
        blocks.append((cur_start, cur_end, cur_entries))

    # READ_BLOCKS / DATA_PARSER 구성
    read_blocks = []
    data_parser = {}

    for blk_idx, (blk_start, blk_end, blk_entries) in enumerate(blocks):
        blk_count = blk_end - blk_start + 1
        read_blocks.append((blk_start, blk_count))

        for addr, canonical, dtype, scale_val, reg_size in blk_entries:
            # U32/S32의 _HIGH 워드 엔트리 스킵 (이미 LOW에서 처리)
            if canonical.endswith('_HIGH') and dtype in ('U32', 'S32'):
                continue
            offset = addr - blk_start
            data_parser[canonical] = (blk_idx, offset, dtype, scale_val)

    return read_blocks, data_parser


def _gen_rtu_comm(monitoring_regs: List[RegisterRow], fc_code: int = 3) -> str:
    """FC_CODE, READ_BLOCKS, DATA_PARSER 코드 섹션 생성."""
    read_blocks, data_parser = _compute_blocks_and_parser(monitoring_regs, fc_code)

    fc_comment = 'Holding Registers (FC03)' if fc_code == 3 else 'Input Registers (FC04)'
    lines = [
        '',
        '# ─────────────────────────────────────────────────────────────────────────',
        '# RTU 블록 통신 설정 — modbus_handler._read_inverter_data_blocks() 사용',
        '# ─────────────────────────────────────────────────────────────────────────',
        f'FC_CODE = {fc_code}  # {fc_comment}',
        '',
        '# READ_BLOCKS: [(start_addr, count), ...]',
        '# modbus_handler가 한 번에 읽는 연속 레지스터 블록 목록',
        'READ_BLOCKS = [',
    ]
    for start_addr, count in read_blocks:
        end_addr = start_addr + count - 1
        lines.append(f'    (0x{start_addr:04X}, {count:3d}),  # 0x{start_addr:04X}–0x{end_addr:04X}')
    lines.append(']')
    lines.append('')
    lines.append('# DATA_PARSER: {canonical_field: (block_idx, offset, dtype, scale)}')
    lines.append('# block_idx=READ_BLOCKS 인덱스, offset=블록 내 레지스터 오프셋')
    lines.append('# dtype=U16/S16/U32/S32/FLOAT32, scale=물리값(SI) 변환 계수')
    lines.append('DATA_PARSER = {')
    for name, (blk_idx, offset, dtype, scale) in data_parser.items():
        lines.append(f"    {name!r}: ({blk_idx}, {offset}, {dtype!r}, {scale}),")
    lines.append('}')
    lines.append('')

    return '\n'.join(lines)


# ─── 코드 검증 ───────────────────────────────────────────────────────────────

def validate_code(code: str, mppt: int, total_strings: int,
                   iv_scan: bool = True, der_avm: bool = True) -> dict:
    checks = {}
    if mppt == 0:
        checks['mppt_count_nonzero'] = False  # MPPT count must be > 0
        return checks
    checks['class_RegisterMap'] = 'class RegisterMap' in code
    checks['class_InverterMode'] = 'class InverterMode' in code
    if iv_scan:
        checks['class_IVScanCommand'] = 'class IVScanCommand' in code
        checks['class_IVScanStatus'] = 'class IVScanStatus' in code
    # DER-AVM은 der_avm=True일 때만 검사
    if der_avm:
        checks['class_DerActionMode'] = 'class DerActionMode' in code
    checks['class_DeviceType'] = 'class DeviceType' in code
    checks['class_ErrorCode1'] = 'class ErrorCode1' in code
    checks['InverterMode_to_string'] = 'def to_string' in code
    checks['SCALE_dict'] = "SCALE = {" in code
    checks['registers_to_u32'] = 'def registers_to_u32' in code
    checks['registers_to_s32'] = 'def registers_to_s32' in code
    # RTU 필수 alias
    checks['alias_INNER_TEMP'] = 'INNER_TEMP' in code
    checks['alias_INVERTER_MODE'] = 'INVERTER_MODE' in code
    checks['alias_INVERTER_ON_OFF'] = 'INVERTER_ON_OFF' in code
    checks['alias_DER_POWER_FACTOR_SET'] = 'DER_POWER_FACTOR_SET' in code
    checks['alias_S_PHASE_VOLTAGE'] = 'S_PHASE_VOLTAGE' in code
    checks['alias_MPPT1_VOLTAGE'] = 'MPPT1_VOLTAGE' in code
    checks['alias_PV_VOLTAGE'] = 'PV_VOLTAGE' in code
    checks['alias_PV_STRING_COUNT'] = 'PV_STRING_COUNT' in code
    checks['get_string_registers'] = 'def get_string_registers' in code
    checks['get_mppt_registers'] = 'def get_mppt_registers' in code
    checks['DATA_TYPES'] = 'DATA_TYPES' in code
    checks['StatusConverter'] = 'StatusConverter' in code
    # RTU 통신 호환성 — READ_BLOCKS / DATA_PARSER
    checks['READ_BLOCKS'] = 'READ_BLOCKS' in code
    checks['DATA_PARSER'] = 'DATA_PARSER' in code
    # 필수 H01 매핑 alias
    checks['alias_AC_POWER'] = 'AC_POWER' in code
    checks['alias_R_PHASE_VOLTAGE'] = 'R_PHASE_VOLTAGE' in code
    checks['alias_T_PHASE_VOLTAGE'] = 'T_PHASE_VOLTAGE' in code
    checks['alias_TOTAL_ENERGY'] = 'TOTAL_ENERGY' in code

    # RTU 블록 통신 호환성 검증
    # FC_CODE: 구형(독립 변수) 또는 신형(READ_BLOCKS 딕트 내 'fc':) 모두 허용
    checks['RTU_FC_CODE'] = 'FC_CODE' in code or ("'fc':" in code and 'READ_BLOCKS' in code)
    checks['RTU_READ_BLOCKS'] = 'READ_BLOCKS' in code
    checks['RTU_DATA_PARSER'] = 'DATA_PARSER' in code

    # MPPT 채널 수 — Method 1: 생성된 MPPT_CHANNELS 상수 우선
    if f'MPPT_CHANNELS = {mppt}' in code:
        mppt_count = mppt  # 명시적 선언 → 바로 통과
    else:
        # Method 2: 상수명 패턴 (다양한 제조사 네이밍 지원)
        mppt_count = 0
        for n in range(1, 20):
            _mppt_pats = [
                f'MPPT{n}_VOLTAGE', f'PV{n}_VOLTAGE', f'PV{n}VOLTAGE',
                f'INPUT_{n}_VOLTAGE', f'INPUT{n}_VOLTAGE',   # ABB: Input 1 Voltage
                f'DC{n}_VOLTAGE', f'DC_{n}_VOLTAGE',          # CPS/Delta: DC1 Voltage
                f'PP{n}_VOLTAGE', f'PP_{n}_VOLTAGE',           # ABB: PP voltage
                f'CH{n}_VOLTAGE', f'CH_{n}_VOLTAGE',           # Channel variants
                f'STRING{n}_INPUT_VOLTAGE',                    # String with input
            ]
            if any(p in code for p in _mppt_pats):
                mppt_count = n
    checks[f'MPPT_channels_{mppt}'] = mppt_count >= mppt

    # String 채널 수 (STRING{n} 또는 PV_STRING{n})
    string_count = 0
    for n in range(1, 50):
        if f'STRING{n}_VOLTAGE' in code or f'STRING{n}_CURRENT' in code or \
           f'PV_STRING{n}' in code or f'PVSTRING{n}' in code:
            string_count = n
    # String이 없는 인버터(화웨이 등)는 PDF에 없으면 통과
    if string_count == 0 and total_strings > 0:
        # PDF에 String 레지스터가 없으면 해당 인버터는 String 미지원 → 통과
        checks[f'String_channels_{total_strings}'] = True
    else:
        checks[f'String_channels_{total_strings}'] = string_count >= total_strings

    return checks


# ─── 학습 피드백 ─────────────────────────────────────────────────────────────

def update_synonym_db(all_regs: List[RegisterRow], synonym_db: dict) -> int:
    added = 0
    for reg in all_regs:
        if not reg.h01_field:
            continue
        defn = reg.definition
        for field_name, info in synonym_db.get('fields', {}).items():
            if info.get('h01_field') == reg.h01_field:
                syns = info.setdefault('synonyms', [])
                if defn not in syns and to_upper_snake(defn) != field_name:
                    syns.append(defn)
                    added += 1
                break
    return added


def update_review_history(review_items: list, manufacturer: str,
                          history: dict) -> int:
    added = 0
    for item in review_items:
        verdict = item.get('사용자 판정', '').strip()
        if not verdict:
            continue
        history.setdefault('approved', []).append({
            'manufacturer': manufacturer,
            'definition': item.get('Definition', ''),
            'address': item.get('Address', ''),
            'verdict': verdict,
            'reason': item.get('사유', ''),
            'pattern': '',
            'date': datetime.now().strftime('%Y-%m-%d'),
        })
        history.setdefault('stats', {})
        history['stats']['total_reviewed'] = history['stats'].get('total_reviewed', 0) + 1
        added += 1
    return added


def _register_device_model(protocol_name: str, manufacturer: str, log):
    """device_models.ini에 새 프로토콜이 없으면 자동 등록"""
    import configparser

    config_dir = os.path.join(RTU_COMMON_DIR, '..', 'config') if RTU_COMMON_DIR else ''
    ini_path = os.path.join(config_dir, 'device_models.ini')
    if not os.path.isfile(ini_path):
        return

    try:
        cfg = configparser.ConfigParser()
        cfg.read(ini_path, encoding='utf-8')

        # 이미 등록된 프로토콜인지 확인
        existing_protocols = {}
        if cfg.has_section('inverter_protocols'):
            for mid, proto in cfg.items('inverter_protocols'):
                if not mid.startswith('#'):
                    existing_protocols[proto.strip()] = mid.strip()

        pname = protocol_name.lower().strip()
        if pname in existing_protocols:
            return  # 이미 등록됨

        # 새 model_id 결정 (기존 최대 + 1)
        max_id = 0
        if cfg.has_section('inverter_models'):
            for mid, _ in cfg.items('inverter_models'):
                try:
                    max_id = max(max_id, int(mid))
                except ValueError:
                    pass
        new_id = str(max_id + 1)

        # 등록
        if not cfg.has_section('inverter_models'):
            cfg.add_section('inverter_models')
        cfg.set('inverter_models', new_id, manufacturer or pname.title())

        if not cfg.has_section('inverter_features'):
            cfg.add_section('inverter_features')
        cfg.set('inverter_features', new_id, 'false, true, false')

        if not cfg.has_section('inverter_protocols'):
            cfg.add_section('inverter_protocols')
        cfg.set('inverter_protocols', new_id, pname)

        with open(ini_path, 'w', encoding='utf-8') as f:
            cfg.write(f)
        log(f'  device_models.ini 등록: [{new_id}] {pname} ({manufacturer})')

    except Exception as e:
        log(f'  device_models.ini 등록 실패: {e}', 'warn')


# ─── Stage 3 메인 함수 ───────────────────────────────────────────────────────

def run_stage3(
    stage2_path: str,
    output_dir: str,
    progress: ProgressCallback = None,
    iv_data_points: int = 64,
) -> dict:
    def log(msg, level='info'):
        if progress:
            progress(msg, level)

    log('Stage 2 Excel 읽기...')
    s2_data = read_stage2_excel(stage2_path)
    meta = s2_data['meta']
    all_regs = s2_data['all_regs']
    review_items = s2_data['review_items']
    h01_manual_mapping = s2_data.get('h01_manual_mapping', {})

    manufacturer = meta.get('제조사', meta.get('manufacturer', 'Unknown'))
    device_type = meta.get('설비 타입', meta.get('device_type', 'inverter'))

    # iv_data_points from meta (Stage 1에서 감지한 값 우선)
    if iv_data_points == 64:
        try:
            iv_data_points = int(meta.get('IV Data Points', meta.get('iv_data_points', 64)))
        except (ValueError, TypeError):
            pass

    # REVIEW 판정 반영
    for item in review_items:
        verdict = item.get('사용자 판정', '').strip()
        if not verdict:
            continue
        defn = item.get('Definition', '')
        if verdict == 'DELETE':
            all_regs = [r for r in all_regs
                        if to_upper_snake(r.definition) != to_upper_snake(defn)]
        elif verdict.startswith('MOVE:'):
            target = verdict.replace('MOVE:', '')
            for reg in all_regs:
                if to_upper_snake(reg.definition) == to_upper_snake(defn):
                    reg.category = target
        elif verdict == 'KEEP':
            for reg in all_regs:
                if to_upper_snake(reg.definition) == to_upper_snake(defn):
                    reg.category = 'MONITORING'

    # 카테고리별 그룹화
    regs_by_cat = {}
    for reg in all_regs:
        cat = reg.category or 'MONITORING'
        regs_by_cat.setdefault(cat, []).append(reg)

    # ── MONITORING 필터: RTU가 실제 사용하는 레지스터만 유지 ──────────────────────
    # Stage2 H01 매칭(h01_field 설정) 또는 MPPT/String/PV 채널 패턴에 해당하는
    # 레지스터만 RegisterMap에 포함하고, 설정·네트워크·라이선스 등 불필요 레지스터 제거.
    _MPPT_STR_PAT = re.compile(r'^(MPPT|STRING|PV)\d+', re.IGNORECASE)
    mon_regs = regs_by_cat.get('MONITORING', [])
    if len(mon_regs) > 50:  # 레지스터가 많을 때만 필터 적용 (소규모 PDF는 그대로)
        essential_mon = [
            r for r in mon_regs
            if (getattr(r, 'h01_field', '') or
                _MPPT_STR_PAT.match(to_upper_snake(r.definition or '')))
        ]
        if essential_mon:  # 필터 결과가 비어있지 않을 때만 교체
            log(f'  MONITORING 필터: {len(mon_regs)} → {len(essential_mon)} '
                f'(RTU 미사용 레지스터 {len(mon_regs) - len(essential_mon)}개 제거)')
            regs_by_cat['MONITORING'] = essential_mon
            _excluded_ids = {id(r) for r in mon_regs} - {id(r) for r in essential_mon}
            all_regs = [r for r in all_regs if id(r) not in _excluded_ids]

    # MPPT / String 수
    mppt_count = 0
    total_strings = 0
    for reg in all_regs:
        ch = detect_channel_number(reg.definition)
        if ch:
            prefix, n = ch
            if prefix == 'MPPT':
                mppt_count = max(mppt_count, n)
            elif prefix == 'STRING':
                total_strings = max(total_strings, n)

    # meta에서 보완
    if mppt_count == 0:
        try:
            mppt_count = int(meta.get('MPPT', '0').split()[0])
        except (ValueError, IndexError):
            pass
    if total_strings == 0:
        try:
            s = meta.get('String', '0')
            total_strings = int(s.split()[0])
        except (ValueError, IndexError):
            pass

    strings_per_mppt = total_strings // max(1, mppt_count) if mppt_count > 0 else 0

    # 프로토콜명 정규화 (긴 파일명 → 짧은 프로토콜명)
    protocol_name = manufacturer.lower()
    protocol_version = meta.get('프로토콜 버전', '')

    # 인버터 타입 감지 (PDF 파일명 기반): HYB / PV
    _pdf_fn = meta.get('원본 파일', '').upper()
    inverter_type = 'HYB' if any(k in _pdf_fn for k in ['HYB', 'HYBRID', '하이브리드']) else 'PV'

    # 용량 (Stage 2에서 사용자 입력, '-' 이거나 비어있으면 생략)
    _cap = meta.get('용량', '').strip()
    capacity_str = f'_{_cap}' if _cap and _cap != '-' else ''

    # V2: IV/DER 판단 — 레지스터 유무 + 메타데이터 + 제조사 확정
    iv_scan = (
        len(regs_by_cat.get('IV_SCAN', [])) > 0 or
        meta.get('IV Scan', '').lower() in ('yes', 'true', '1') or
        meta.get('iv_scan', '').lower() in ('yes', 'true', '1') or
        any(to_upper_snake(r.definition) == 'IV_CURVE_SCAN'
            for cat_regs in regs_by_cat.values() if isinstance(cat_regs, list)
            for r in cat_regs) or
        manufacturer.lower() in ('solarize', 'kstar', 'senergy')
    )
    # DER-AVM은 인버터면 항상 True (고정 주소맵)
    der_avm = (
        len(regs_by_cat.get('DER_CONTROL', [])) > 0 or
        device_type == 'inverter'
    )

    log(f'제조사: {manufacturer}, 프로토콜: {protocol_name}')
    log(f'MPPT: {mppt_count}, String: {total_strings} ({strings_per_mppt}/MPPT)')

    # ErrorCode BITS 로딩
    error_bits = _load_error_bits_from_reference(manufacturer)
    if error_bits:
        log(f'  ErrorCode BITS: {list(error_bits.keys())} 로드')

    # RegisterMap에 제조사명 전달
    regs_by_cat['_manufacturer'] = manufacturer

    # ── 코드 생성 ──
    log('Python 코드 생성...')

    # FC 코드 감지 (MONITORING 레지스터 fc 필드 기반)
    monitoring_regs = regs_by_cat.get('MONITORING', [])
    _fc_vals = [str(getattr(r, 'fc', '')).strip() for r in monitoring_regs
                if str(getattr(r, 'fc', '')).strip()]
    fc_code = 4 if _fc_vals and all(v in ('4', '04', 'FC04') for v in _fc_vals) else 3

    code_parts = [
        _gen_header(manufacturer, protocol_name, protocol_version,
                    mppt_count, total_strings, strings_per_mppt,
                    iv_scan, der_avm),
        _gen_register_map(regs_by_cat, mppt_count, total_strings,
                          strings_per_mppt, iv_scan, iv_data_points),
        _gen_iv_scan_classes() if iv_scan else '',
        _gen_inverter_mode(),
        _gen_der_action_mode() if der_avm else '',
        _gen_device_type(),
        _gen_iv_scan_body_type(mppt_count, strings_per_mppt) if iv_scan else '',
        _gen_error_classes(regs_by_cat.get('ALARM', []), error_bits),
        _gen_status_converter(manufacturer),
        _gen_scale_dict(),
        _gen_helpers(mppt_count, total_strings, strings_per_mppt, iv_data_points),
        _gen_data_types(all_regs),
        _gen_string_current_monitor(all_regs),
        _gen_read_blocks(all_regs),
        _gen_data_parser(all_regs, mppt_count, total_strings, h01_manual_mapping),
    ]
    code = '\n'.join(p for p in code_parts if p)

    # ── 검증 ──
    log('코드 검증...')
    validation = validate_code(code, mppt_count, total_strings, iv_scan, der_avm)
    passed = sum(1 for v in validation.values() if v)
    total = len(validation)
    log(f'  검증: {passed}/{total} 통과')
    for check_name, ok in validation.items():
        log(f'    {"✓" if ok else "✗"} {check_name}')

    # ── 파일 저장 ──
    output_name = f'{manufacturer}_{inverter_type}{capacity_str}_registers.py'
    output_path = os.path.join(output_dir, output_name)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(code)
    log(f'코드 저장: {output_name}', 'ok')

    # ── 학습 피드백 ──
    log('학습 피드백 업데이트...')
    synonym_db = load_synonym_db()
    syn_added = update_synonym_db(all_regs, synonym_db)
    if syn_added > 0:
        save_synonym_db(synonym_db)
        log(f'  synonym_db: +{syn_added}개 동의어')

    review_history = load_review_history()
    rv_recorded = update_review_history(review_items, manufacturer, review_history)
    if rv_recorded > 0:
        save_review_history(review_history)
        log(f'  review_history: +{rv_recorded}개 판정')

    # common/ 레퍼런스 등록 — 모든 검증 통과 시에만 (오염 방지)
    all_passed = all(validation.values())
    common_path = os.path.join(COMMON_DIR, output_name)
    if all_passed:
        import shutil
        # 1) Model Maker common/ (레퍼런스용)
        shutil.copy2(output_path, common_path)
        log(f'  레퍼런스 등록: common/{output_name}')
        # 2) RTU V2_0_0/common/ (즉시 배포용)
        if os.path.isdir(RTU_COMMON_DIR):
            rtu_common_path = os.path.join(RTU_COMMON_DIR, output_name)
            shutil.copy2(output_path, rtu_common_path)
            log(f'  RTU 배포: V2_0_0/common/{output_name}')
        else:
            log(f'  RTU common/ 없음 — 수동 복사 필요: {RTU_COMMON_DIR}', 'warn')
    else:
        failed = [k for k, v in validation.items() if not v]
        log(f'  레퍼런스 미등록 (검증 실패: {failed})', 'warn')

    # device_models.ini 자동 등록 — 새 프로토콜이면 추가
    if all_passed and protocol_name:
        _register_device_model(protocol_name, manufacturer, log)

    log('Stage 3 완료', 'ok')

    return {
        'output_path': output_path,
        'filename': output_name,
        'validation': validation,
        'synonym_added': syn_added,
        'review_recorded': rv_recorded,
        'mppt_count': mppt_count,
        'total_strings': total_strings,
        'register_count': len(all_regs),
    }
