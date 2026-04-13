# -*- coding: utf-8 -*-
"""
AI Register File Generator — Model Maker v1.3.0

Uses Claude API (Anthropic) to analyze an inverter Modbus protocol PDF and
generate a production-ready *_registers.py compatible with the RTU UDP System.

Features:
  - PDF text extraction via PyMuPDF (fitz) or pdfminer.six
  - Structured Claude API prompt with full register file spec
  - 12-item validation test suite
  - Auto-retry up to 3 times on validation failures
  - API key + model stored in config/ai_settings.ini
"""

import os
import re
import configparser


# ---------------------------------------------------------------------------
# API Key / Settings Management
# ---------------------------------------------------------------------------

_SETTINGS_PATH = None


def _get_settings_path():
    global _SETTINGS_PATH
    if _SETTINGS_PATH is None:
        base = os.path.dirname(os.path.abspath(__file__))
        _SETTINGS_PATH = os.path.normpath(
            os.path.join(base, '..', 'config', 'ai_settings.ini'))
    return _SETTINGS_PATH


def load_api_key():
    """Load Anthropic API key from config/ai_settings.ini."""
    path = _get_settings_path()
    if not os.path.isfile(path):
        return ''
    cfg = configparser.ConfigParser()
    cfg.read(path, encoding='utf-8')
    return cfg.get('claude_api', 'api_key', fallback='').strip()


def save_api_key(api_key):
    """Save Anthropic API key to config/ai_settings.ini."""
    path = _get_settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cfg = configparser.ConfigParser()
    if os.path.isfile(path):
        cfg.read(path, encoding='utf-8')
    if 'claude_api' not in cfg:
        cfg['claude_api'] = {}
    cfg['claude_api']['api_key'] = api_key
    with open(path, 'w', encoding='utf-8') as f:
        cfg.write(f)


def load_model_name():
    """Load Claude model name from config/ai_settings.ini."""
    path = _get_settings_path()
    if not os.path.isfile(path):
        return 'claude-opus-4-6'
    cfg = configparser.ConfigParser()
    cfg.read(path, encoding='utf-8')
    return cfg.get('claude_api', 'model', fallback='claude-opus-4-6').strip()


def save_model_name(model_name):
    """Save Claude model name to config/ai_settings.ini."""
    path = _get_settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cfg = configparser.ConfigParser()
    if os.path.isfile(path):
        cfg.read(path, encoding='utf-8')
    if 'claude_api' not in cfg:
        cfg['claude_api'] = {}
    cfg['claude_api']['model'] = model_name
    with open(path, 'w', encoding='utf-8') as f:
        cfg.write(f)


# ---------------------------------------------------------------------------
# PDF Text Extraction
# ---------------------------------------------------------------------------

def extract_pdf_text(pdf_path, max_chars=90000):
    """Extract text from PDF. Uses PyMuPDF if available, else pdfminer.six."""
    full_text = ''
    try:
        import fitz
        doc = fitz.open(pdf_path)
        try:
            chunks = [page.get_text() for page in doc]
        finally:
            doc.close()
        full_text = '\n'.join(chunks)
    except ImportError:
        try:
            from pdfminer.high_level import extract_text
            full_text = extract_text(pdf_path)
        except ImportError:
            raise ImportError(
                "PDF extraction requires PyMuPDF or pdfminer.six.\n"
                "Run: pip install PyMuPDF")
        except Exception as e:
            raise RuntimeError(f"PDF parse error: {e}") from e

    if not full_text:
        raise RuntimeError("Could not extract text from PDF.")

    if len(full_text) > max_chars:
        full_text = full_text[:max_chars] + '\n... [truncated]'
    return full_text


# ---------------------------------------------------------------------------
# Prompt Builder
# ---------------------------------------------------------------------------

_STRUCTURE_REFERENCE = """\
## Required Python File Structure

```python
# -*- coding: utf-8 -*-
\"\"\"
<Manufacturer> Inverter Modbus Register Map
Protocol: <protocol_name>
Function Code: FC03 (or FC04)
\"\"\"


class RegisterMap:
    \"\"\"Inverter Modbus register addresses (0-based).\"\"\"

    # --- Device Info ---
    MODEL_NAME_BASE    = 0x????  # ASCII, N regs
    SERIAL_NUMBER_BASE = 0x????

    # --- AC Output Phase R/L1 ---
    R_PHASE_VOLTAGE    = 0x????  # U16, x0.1 V
    R_PHASE_CURRENT    = 0x????  # U16, x0.01 A
    R_PHASE_POWER_L    = 0x????  # S32 low, x0.1 W
    R_PHASE_POWER_H    = 0x????

    # --- AC Output Phase S/L2 ---
    S_PHASE_VOLTAGE    = 0x????
    S_PHASE_CURRENT    = 0x????
    S_PHASE_POWER_L    = 0x????
    S_PHASE_POWER_H    = 0x????

    # --- AC Output Phase T/L3 ---
    T_PHASE_VOLTAGE    = 0x????
    T_PHASE_CURRENT    = 0x????
    T_PHASE_POWER_L    = 0x????
    T_PHASE_POWER_H    = 0x????

    FREQUENCY          = 0x????  # U16, x0.01 Hz

    # --- MPPT Data ---
    MPPT1_VOLTAGE      = 0x????
    MPPT1_CURRENT      = 0x????
    MPPT1_POWER_L      = 0x????
    MPPT1_POWER_H      = 0x????
    # MPPT2...N follow same pattern

    # --- PV String Data ---
    STRING1_VOLTAGE    = 0x????
    STRING1_CURRENT    = 0x????
    # STRING2...N follow same pattern

    # --- Status ---
    INVERTER_MODE      = 0x????  # U16, see InverterMode
    ERROR_CODE1        = 0x????  # U16, bit-field
    INNER_TEMP         = 0x????  # S16, 1 C

    # --- Energy / Power ---
    TOTAL_ENERGY       = 0x????  # U32 low word
    TOTAL_ENERGY_H     = 0x????  # U32 high word
    TODAY_ENERGY_L     = 0x????  # U32 low
    TODAY_ENERGY_H     = 0x????
    AC_POWER           = 0x????  # S32 low, grid total active power
    AC_POWER_H         = 0x????
    PV_TOTAL_POWER_L   = 0x????  # S32 low, total PV input power
    PV_TOTAL_POWER_H   = 0x????
    POWER_FACTOR       = 0x????  # S16, x0.001

    # --- Required Aliases (MUST be present) ---
    L1_VOLTAGE                  = R_PHASE_VOLTAGE
    L1_CURRENT                  = R_PHASE_CURRENT
    L2_VOLTAGE                  = S_PHASE_VOLTAGE
    L3_VOLTAGE                  = T_PHASE_VOLTAGE
    TOTAL_ENERGY_LOW            = TOTAL_ENERGY
    TODAY_ENERGY_LOW            = TODAY_ENERGY_L
    PV_TOTAL_INPUT_POWER_LOW    = PV_TOTAL_POWER_L
    GRID_TOTAL_ACTIVE_POWER_LOW = AC_POWER

    # --- DER-AVM Control (if supported) ---
    DER_POWER_FACTOR_SET    = 0x????
    DER_ACTION_MODE         = 0x????
    DER_REACTIVE_POWER_PCT  = 0x????
    DER_ACTIVE_POWER_PCT    = 0x????
    INVERTER_ON_OFF         = 0x????
    POWER_FACTOR_SET        = DER_POWER_FACTOR_SET   # alias
    OPERATION_MODE          = DER_ACTION_MODE          # alias


SCALE = {
    'voltage':      0.1,
    'current':      0.01,
    'power':        0.1,
    'frequency':    0.01,
    'power_factor': 0.001,
}

DATA_TYPES = {
    'R_PHASE_VOLTAGE':  ('U16', 'V'),
    'R_PHASE_CURRENT':  ('U16', 'A'),
    'R_PHASE_POWER_L':  ('S32', 'W'),
    'TOTAL_ENERGY':     ('U32', 'kWh'),
    # ... all significant registers
}


class InverterMode:
    INITIAL  = 0
    STANDBY  = 1
    ON_GRID  = 2
    FAULT    = 3
    SHUTDOWN = 4

    _MAP = {0: 'Initial', 1: 'Standby', 2: 'On-Grid', 3: 'Fault', 4: 'Shutdown'}

    @classmethod
    def to_string(cls, value):
        return cls._MAP.get(value, f'Unknown({value})')


class StatusConverter:
    # Map manufacturer raw status codes to InverterMode values
    _CONVERSION_MAP = {
        # raw: InverterMode.STATE  (fill from PDF)
    }

    @classmethod
    def to_inverter_mode(cls, raw_status):
        return cls._CONVERSION_MAP.get(raw_status, InverterMode.STANDBY)


def registers_to_u32(low, high):
    \"\"\"Combine two 16-bit registers into unsigned 32-bit (little-endian).\"\"\"
    return ((high & 0xFFFF) << 16) | (low & 0xFFFF)


def registers_to_s32(low, high):
    \"\"\"Combine two 16-bit registers into signed 32-bit (little-endian).\"\"\"
    val = registers_to_u32(low, high)
    return val if val < 0x80000000 else val - 0x100000000


def get_mppt_registers(mppt_index):
    \"\"\"Return (v_addr, i_addr, p_low_addr, p_high_addr) for MPPT channel.\"\"\"
    _MAP = {
        1: (RegisterMap.MPPT1_VOLTAGE, RegisterMap.MPPT1_CURRENT,
            RegisterMap.MPPT1_POWER_L, RegisterMap.MPPT1_POWER_H),
        # 2..N entries
    }
    return _MAP.get(mppt_index)


def get_string_registers(string_index):
    \"\"\"Return (v_addr, i_addr) for PV string channel.\"\"\"
    _MAP = {
        1: (RegisterMap.STRING1_VOLTAGE, RegisterMap.STRING1_CURRENT),
        # 2..N entries
    }
    return _MAP.get(string_index)
```
"""


def build_generation_prompt(pdf_text, manufacturer, mppt_count, string_count,
                             include_iv, include_deravm, protocol_name,
                             class_name='RegisterMap'):
    """Build the Claude API prompt for register file generation."""

    deravm_section = ''
    if include_deravm:
        deravm_section = """\

### DER-AVM Control Attributes (REQUIRED — all 7 must be present in RegisterMap):
These are mandatory aliases for RTU DER-AVM integration:
  - `DER_POWER_FACTOR_SET`   — power factor setpoint register address
  - `DER_ACTION_MODE`        — action mode / operation mode register
  - `DER_REACTIVE_POWER_PCT` — reactive power percentage register
  - `DER_ACTIVE_POWER_PCT`   — active power limit percentage register
  - `INVERTER_ON_OFF`        — inverter on/off control register
  - `POWER_FACTOR_SET = DER_POWER_FACTOR_SET`  (alias)
  - `OPERATION_MODE = DER_ACTION_MODE`          (alias)
If these exact registers are not in the PDF, use the closest equivalent and
add a `# TODO: verify address` comment.
"""

    iv_section = ''
    if include_iv:
        iv_section = """\

### IV Scan Support:
Include IV scan register addresses if present in the PDF:
  - `IV_SCAN_START`   — trigger register for IV scan
  - `IV_SCAN_STATUS`  — scan status/result register
  - IV curve data registers (voltage/current point arrays per MPPT)
"""

    prompt = f"""\
You are an expert in solar inverter Modbus protocols and Python code generation.

## Task
Analyze the Modbus protocol PDF below and generate a complete, production-ready
Python register map file for the RTU UDP System.

## Output File Name
`{protocol_name}_registers.py`

## Manufacturer / Model
{manufacturer}

## Configuration
- MPPT channels: {mppt_count}
- PV string count: {string_count}
- Include IV Scan: {include_iv}
- Include DER-AVM: {include_deravm}
- Class name: MUST be exactly `{class_name}`
- Function Code: auto-detect from PDF (FC03 or FC04)

## Validation Requirements (your output will be tested against these 12 checks)
1. Valid Python syntax (no SyntaxError)
2. Class `{class_name}` exists and is importable
3. Register constants: all addresses are non-negative integers
4. Essential aliases ALL present in `{class_name}`:
   L1_VOLTAGE, L1_CURRENT, L2_VOLTAGE, L3_VOLTAGE,
   MPPT1_VOLTAGE, MPPT1_CURRENT, INVERTER_MODE, ERROR_CODE1,
   TOTAL_ENERGY_LOW, TODAY_ENERGY_LOW, PV_TOTAL_INPUT_POWER_LOW,
   GRID_TOTAL_ACTIVE_POWER_LOW, POWER_FACTOR
5. No duplicate register addresses (WARN only if present)
6. SCALE dict with all 5 keys: voltage, current, power, frequency, power_factor
7. InverterMode class with INITIAL/STANDBY/ON_GRID/FAULT/SHUTDOWN + to_string()
8. registers_to_u32(low, high) and registers_to_s32(low, high) functions
9. get_mppt_registers(mppt_index) function returning tuple of 4 addresses
10. get_string_registers(string_index) function returning tuple of 2 addresses
11. DATA_TYPES dict present with valid type codes (U16/S16/U32/S32/ASCII/FLOAT32)
12. DER-AVM attributes: DER_POWER_FACTOR_SET, DER_ACTION_MODE,
    DER_REACTIVE_POWER_PCT, DER_ACTIVE_POWER_PCT, INVERTER_ON_OFF,
    POWER_FACTOR_SET, OPERATION_MODE{' (REQUIRED)' if include_deravm else ' (not required for this file)'}
{deravm_section}{iv_section}
## Required File Structure
{_STRUCTURE_REFERENCE}

## Rules
- Output ONLY Python source code — no prose, no markdown, no explanation
- Start with: `# -*- coding: utf-8 -*-`
- Include a module docstring with manufacturer name, protocol name, and today's date
- Use the EXACT register addresses from the PDF (0-based, no +1 offset)
- Add inline comments showing data type and unit for each register
- If an address for a required alias cannot be found in the PDF, use your best
  estimate based on common inverter patterns and add `# TODO: verify` comment
- Use little-endian U32 (low word first) unless PDF explicitly states big-endian
- End with a single trailing newline

## PDF Content
---
{pdf_text}
---
"""
    return prompt


# ---------------------------------------------------------------------------
# Code Extraction
# ---------------------------------------------------------------------------

def extract_python_code(response_text):
    """Extract Python code block from Claude API response text."""
    # ```python ... ```
    m = re.search(r'```python\s*\n(.*?)```', response_text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # plain ``` ... ```
    m = re.search(r'```\s*\n(.*?)```', response_text, re.DOTALL)
    if m:
        candidate = m.group(1).strip()
        if 'class RegisterMap' in candidate or 'def registers_to_u32' in candidate:
            return candidate
    # Entire response is code
    stripped = response_text.strip()
    if (stripped.startswith('#') or stripped.startswith('"""') or
            stripped.startswith("'''") or 'class RegisterMap' in stripped):
        return stripped
    return stripped


# ---------------------------------------------------------------------------
# 12-Item Validation
# ---------------------------------------------------------------------------

def run_12_validation_tests(code, class_name='RegisterMap', include_deravm=True):
    """
    Run 12 validation tests on generated Python register code.

    Returns:
        List of (status, message) tuples.
        status is one of: 'PASS', 'WARN', 'FAIL'
    """
    results = []
    base_dir = os.path.dirname(os.path.abspath(__file__))
    fake_file = os.path.join(base_dir, '..', 'common', '_ai_test_registers.py')
    ns = {'__file__': fake_file}
    reg_cls = None
    regs = {}

    # ── Test 1: Syntax ───────────────────────────────────────────────────────
    try:
        compile(code, '<ai_gen>', 'exec')
        results.append(('PASS', '1. Syntax check'))
    except SyntaxError as e:
        results.append(('FAIL', f'1. Syntax error: {e}'))
        return results  # fatal — stop here

    # ── Test 2: Import & class ────────────────────────────────────────────────
    try:
        exec(code, ns)  # noqa: S102
        reg_cls = ns.get(class_name)
        if reg_cls is None:
            results.append(('FAIL', f'2. Class "{class_name}" not found in generated code'))
        else:
            results.append(('PASS', f'2. Class "{class_name}" found and importable'))
    except Exception as e:
        results.append(('FAIL', f'2. Exec error: {e}'))
        return results  # fatal

    # ── Test 3: Register constants ────────────────────────────────────────────
    if reg_cls:
        regs = {k: v for k, v in vars(reg_cls).items()
                if not k.startswith('_') and isinstance(v, int)}
        if regs:
            neg = [k for k, v in regs.items() if v < 0]
            if neg:
                results.append(('FAIL',
                    f'3. Negative register addresses: {", ".join(neg[:5])}'))
            else:
                results.append(('PASS',
                    f'3. Register constants: {len(regs)} registers, all non-negative'))
        else:
            results.append(('FAIL', '3. No integer register constants found in class'))
    else:
        results.append(('FAIL', '3. Skipped (no class)'))

    # ── Test 4: Essential aliases ─────────────────────────────────────────────
    essential_attrs = [
        'L1_VOLTAGE', 'L1_CURRENT', 'L2_VOLTAGE', 'L3_VOLTAGE',
        'MPPT1_VOLTAGE', 'MPPT1_CURRENT',
        'INVERTER_MODE', 'ERROR_CODE1',
        'TOTAL_ENERGY_LOW', 'TODAY_ENERGY_LOW',
        'POWER_FACTOR',
        'PV_TOTAL_INPUT_POWER_LOW',
        'GRID_TOTAL_ACTIVE_POWER_LOW',
    ]
    if reg_cls:
        all_attrs = {k for k in vars(reg_cls) if not k.startswith('_')}
        missing = [a for a in essential_attrs if a not in all_attrs]
        if missing:
            results.append(('FAIL',
                f'4. Missing essential aliases ({len(missing)}): {", ".join(missing)}'))
        else:
            results.append(('PASS',
                f'4. Essential aliases: all {len(essential_attrs)} present'))
    else:
        results.append(('FAIL', '4. Skipped (no class)'))

    # ── Test 5: Address uniqueness ────────────────────────────────────────────
    if regs:
        addr_map = {}
        for k, v in regs.items():
            addr_map.setdefault(v, []).append(k)
        dups = {hex(a): names for a, names in addr_map.items() if len(names) > 1}
        if dups:
            dup_strs = [f'{addr}: [{", ".join(n[:3])}]'
                        for addr, n in list(dups.items())[:3]]
            results.append(('WARN',
                f'5. Duplicate addresses ({len(dups)}): ' + '; '.join(dup_strs)))
        else:
            results.append(('PASS', '5. Address uniqueness: all unique'))
    else:
        results.append(('WARN', '5. Skipped (no registers to check)'))

    # ── Test 6: SCALE dict ────────────────────────────────────────────────────
    scale = ns.get('SCALE')
    required_scale = ['voltage', 'current', 'power', 'frequency', 'power_factor']
    if scale is None:
        results.append(('FAIL', '6. SCALE dict not found (module-level)'))
    elif not isinstance(scale, dict):
        results.append(('FAIL', '6. SCALE is not a dict'))
    else:
        missing_keys = [k for k in required_scale if k not in scale]
        bad_vals = [k for k, v in scale.items()
                    if not isinstance(v, (int, float))]
        if missing_keys:
            results.append(('FAIL',
                f'6. SCALE missing standard keys: {", ".join(missing_keys)}'))
        elif bad_vals:
            results.append(('FAIL',
                f'6. SCALE non-numeric values: {", ".join(bad_vals[:5])}'))
        else:
            results.append(('PASS',
                f'6. SCALE dict: {len(scale)} entries, all 5 standard keys OK'))

    # ── Test 7: InverterMode class ────────────────────────────────────────────
    inv_mode = ns.get('InverterMode')
    if inv_mode is None:
        results.append(('FAIL', '7. InverterMode class not found'))
    else:
        required_states = ['INITIAL', 'STANDBY', 'ON_GRID', 'FAULT', 'SHUTDOWN']
        missing_states = [a for a in required_states if not hasattr(inv_mode, a)]
        if missing_states:
            results.append(('FAIL',
                f'7. InverterMode missing states: {", ".join(missing_states)}'))
        elif not hasattr(inv_mode, 'to_string'):
            results.append(('WARN',
                '7. InverterMode: to_string() method not found'))
        else:
            results.append(('PASS',
                '7. InverterMode: all 5 states + to_string() OK'))

    # ── Test 8: registers_to_u32 / registers_to_s32 ──────────────────────────
    fn_u32 = ns.get('registers_to_u32')
    fn_s32 = ns.get('registers_to_s32')
    missing_fn = ((['registers_to_u32'] if fn_u32 is None else []) +
                  (['registers_to_s32'] if fn_s32 is None else []))
    if missing_fn:
        results.append(('FAIL',
            f'8. Missing helper functions: {", ".join(missing_fn)}'))
    else:
        try:
            u32_le = fn_u32(0x1234, 0x5678) == 0x56781234
            u32_be = fn_u32(0x1234, 0x5678) == 0x12345678
            u32_ok = u32_le or u32_be
            s32_ok = (fn_s32(0, 0x8000) < 0) or (fn_s32(0x8000, 0) < 0)
            byte_order = 'big-endian' if u32_be else 'little-endian'
            if u32_ok and s32_ok:
                results.append(('PASS',
                    f'8. registers_to_u32/s32 OK ({byte_order})'))
            else:
                fails = (['u32'] if not u32_ok else []) + (['s32'] if not s32_ok else [])
                results.append(('FAIL',
                    f'8. Incorrect result: {", ".join(fails)}'))
        except Exception as e:
            results.append(('FAIL', f'8. Function error: {e}'))

    # ── Test 9: get_mppt_registers ────────────────────────────────────────────
    fn_mppt = ns.get('get_mppt_registers')
    if fn_mppt is None:
        results.append(('FAIL', '9. get_mppt_registers() function not found'))
    else:
        try:
            r = fn_mppt(1)
            if r is not None and hasattr(r, '__len__') and len(r) >= 2:
                results.append(('PASS',
                    f'9. get_mppt_registers(1) OK → {r}'))
            else:
                results.append(('WARN',
                    f'9. get_mppt_registers(1) returned unexpected value: {r}'))
        except Exception as e:
            results.append(('FAIL', f'9. get_mppt_registers error: {e}'))

    # ── Test 10: get_string_registers ─────────────────────────────────────────
    fn_str = ns.get('get_string_registers')
    if fn_str is None:
        results.append(('FAIL', '10. get_string_registers() function not found'))
    else:
        try:
            r = fn_str(1)
            if r is not None and hasattr(r, '__len__') and len(r) >= 2:
                results.append(('PASS',
                    f'10. get_string_registers(1) OK → {r}'))
            else:
                results.append(('WARN',
                    f'10. get_string_registers(1) returned unexpected value: {r}'))
        except Exception as e:
            results.append(('FAIL', f'10. get_string_registers error: {e}'))

    # ── Test 11: DATA_TYPES dict ──────────────────────────────────────────────
    data_types = ns.get('DATA_TYPES')
    valid_dt = {'U16', 'S16', 'U32', 'S32', 'ASCII', 'FLOAT32'}
    if data_types is None:
        results.append(('WARN',
            '11. DATA_TYPES dict not found (optional but recommended)'))
    elif not isinstance(data_types, dict):
        results.append(('FAIL', '11. DATA_TYPES is not a dict'))
    else:
        bad = [f"{k}:{v[0]}" for k, v in data_types.items()
               if isinstance(v, (tuple, list)) and v and v[0] not in valid_dt]
        if bad:
            results.append(('WARN',
                f'11. DATA_TYPES unknown type codes: {", ".join(bad[:4])}'))
        else:
            results.append(('PASS',
                f'11. DATA_TYPES dict: {len(data_types)} entries, type codes OK'))

    # ── Test 12: DER-AVM attributes ───────────────────────────────────────────
    if include_deravm:
        deravm_required = [
            'DER_POWER_FACTOR_SET', 'DER_ACTION_MODE',
            'DER_REACTIVE_POWER_PCT', 'DER_ACTIVE_POWER_PCT',
            'INVERTER_ON_OFF',
            'POWER_FACTOR_SET', 'OPERATION_MODE',
        ]
        if reg_cls:
            all_attrs = {k for k in vars(reg_cls) if not k.startswith('_')}
            missing = [a for a in deravm_required if a not in all_attrs]
            if missing:
                results.append(('FAIL',
                    f'12. DER-AVM missing ({len(missing)}): {", ".join(missing)}'))
            else:
                results.append(('PASS',
                    f'12. DER-AVM: all {len(deravm_required)} control attributes present'))
        else:
            results.append(('FAIL', '12. Skipped (no class)'))
    else:
        results.append(('PASS', '12. DER-AVM: not required for this file'))

    return results


# ---------------------------------------------------------------------------
# Main Generation Function
# ---------------------------------------------------------------------------

def generate_register_file(pdf_path, manufacturer, mppt_count, string_count,
                            include_iv, include_deravm, protocol_name,
                            class_name='RegisterMap', api_key=None, model=None,
                            max_retries=3, progress_callback=None):
    """
    Generate a *_registers.py file from a Modbus PDF using Claude API.

    Args:
        pdf_path:          Path to inverter Modbus PDF
        manufacturer:      Manufacturer / model string shown in docstring
        mppt_count:        Number of MPPT channels
        string_count:      Number of PV string channels
        include_iv:        True to include IV scan support
        include_deravm:    True to include DER-AVM control
        protocol_name:     Protocol / file name (e.g. 'newbrand')
        class_name:        Python class name — should always be 'RegisterMap'
        api_key:           Anthropic API key (loads from config if None)
        model:             Claude model ID (loads from config if None)
        max_retries:       Max retry attempts on validation failure (default 3)
        progress_callback: Callable(step: str, detail: str) for progress updates

    Returns:
        dict with keys:
            'code':    Generated Python source code (str)
            'results': List of (status, message) validation tuples
            'attempt': Number of API calls made
            'success': True if no FAIL items in results
    """
    def _log(step, detail=''):
        if progress_callback:
            progress_callback(step, detail)

    # Resolve credentials
    if api_key is None:
        api_key = load_api_key()
    if model is None:
        model = load_model_name()

    if not api_key:
        raise ValueError(
            "Anthropic API key is not configured.\n"
            "Click the 'API Settings' button to enter your key.")

    try:
        import anthropic
    except ImportError:
        raise ImportError(
            "The 'anthropic' package is not installed.\n"
            "Run: pip install anthropic")

    # Extract PDF text
    _log('Extracting PDF text...')
    pdf_text = extract_pdf_text(pdf_path)
    _log('PDF extracted', f'{len(pdf_text):,} characters')

    client = anthropic.Anthropic(api_key=api_key)

    code = None
    results = []

    for attempt in range(1, max_retries + 1):
        _log(f'Calling Claude API (attempt {attempt}/{max_retries})...')

        if attempt == 1 or code is None:
            prompt = build_generation_prompt(
                pdf_text, manufacturer, mppt_count, string_count,
                include_iv, include_deravm, protocol_name, class_name)
        else:
            # Build retry prompt with failure details
            fail_items = [msg for status, msg in results if status == 'FAIL']
            warn_items = [msg for status, msg in results if status == 'WARN']

            retry_note = (
                f"\n\n## RETRY REQUIRED (attempt {attempt}/{max_retries})\n"
                f"The previous generated code failed {len(fail_items)} validation check(s):\n"
            )
            for msg in fail_items:
                retry_note += f"  FAIL: {msg}\n"
            for msg in warn_items:
                retry_note += f"  WARN: {msg}\n"
            retry_note += (
                "\nPlease regenerate the COMPLETE corrected file fixing ALL FAIL items.\n"
                "Output only the Python source code, nothing else.\n\n"
                f"Previous code for reference:\n```python\n{code}\n```\n"
            )

            prompt = build_generation_prompt(
                pdf_text, manufacturer, mppt_count, string_count,
                include_iv, include_deravm, protocol_name, class_name)
            prompt += retry_note

        # API call
        try:
            message = client.messages.create(
                model=model,
                max_tokens=8192,
                messages=[{'role': 'user', 'content': prompt}],
            )
        except Exception as e:
            raise RuntimeError(f"Claude API call failed: {e}") from e

        # Log token usage
        usage = getattr(message, 'usage', None)
        if usage:
            in_tok = getattr(usage, 'input_tokens', 0)
            out_tok = getattr(usage, 'output_tokens', 0)
            _log(f'Response received (attempt {attempt})',
                 f'input={in_tok:,} output={out_tok:,} tokens')
        else:
            _log(f'Response received (attempt {attempt})')

        # Extract code
        response_text = (message.content[0].text
                         if message.content else '')
        code = extract_python_code(response_text)

        # Validate
        _log(f'Running 12 validation tests (attempt {attempt})...')
        results = run_12_validation_tests(code, class_name, include_deravm)

        n_fail = sum(1 for s, _ in results if s == 'FAIL')
        n_warn = sum(1 for s, _ in results if s == 'WARN')
        n_pass = sum(1 for s, _ in results if s == 'PASS')
        _log(f'Attempt {attempt} result',
             f'{n_pass} PASS  {n_warn} WARN  {n_fail} FAIL')

        if n_fail == 0:
            _log('All tests passed!', f'Attempt {attempt}/{max_retries}')
            break

        if attempt < max_retries:
            _log(f'{n_fail} failure(s) — retrying...')

    success = all(s != 'FAIL' for s, _ in results)
    return {
        'code': code,
        'results': results,
        'attempt': attempt,
        'success': success,
    }
