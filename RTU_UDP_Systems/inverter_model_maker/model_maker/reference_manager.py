# -*- coding: utf-8 -*-
"""
Reference Manager - Model Maker v1.3.0

Manages the reference library of successful inverter register mappings.
Used by stage_pipeline.py for Mode 1 (offline) and Mode 2 (AI) matching.

Reference directory layout:
  model_maker/reference/
  ├── index.json                  # {name: meta} index
  └── {name}/
      ├── registers.py            # Successful register .py file
      ├── mapping.xlsx            # Stage 2 mapping Excel (optional)
      └── meta.json               # {manufacturer, protocol, mppt_count, ...}

Built-in references are loaded from common/*_mm_registers.py without copying.
User-created references are saved to model_maker/reference/{name}/.
"""

import os
import re
import json
import difflib
from datetime import datetime

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_THIS_DIR  = os.path.dirname(os.path.abspath(__file__))
_REF_DIR   = os.path.join(_THIS_DIR, 'reference')
_COMMON_DIR = os.path.normpath(os.path.join(_THIS_DIR, '..', 'common'))

# ---------------------------------------------------------------------------
# Built-in references (common/*_mm_registers.py)
# These are pre-validated references that ship with the system.
# ---------------------------------------------------------------------------

_BUILTIN = {
    'solarize': {
        'manufacturer': 'Solarize',
        'protocol':     'solarize',
        'description':  'Solarize Modbus Protocol V2.0.11',
        'capacity':     '3~100kW',
        'mppt_count':   9,
        'string_count': 24,
        'fc_code':      'FC03',
        'iv_scan':      True,
        'der_avm':      True,
        'builtin':      True,
    },
    'huawei': {
        'manufacturer': 'Huawei',
        'protocol':     'huawei',
        'description':  'Huawei SUN2000 series',
        'capacity':     '2~330kW',
        'mppt_count':   6,
        'string_count': 12,
        'fc_code':      'FC03',
        'iv_scan':      False,
        'der_avm':      False,
        'builtin':      True,
    },
    'kstar': {
        'manufacturer': 'Kstar',
        'protocol':     'kstar',
        'description':  'Kstar KSG / KTL series',
        'capacity':     '3~250kW',
        'mppt_count':   12,
        'string_count': 48,
        'fc_code':      'FC04',
        'iv_scan':      True,
        'der_avm':      True,
        'builtin':      True,
    },
    'sungrow': {
        'manufacturer': 'Sungrow',
        'protocol':     'sungrow',
        'description':  'Sungrow SG series',
        'capacity':     '2~350kW',
        'mppt_count':   12,
        'string_count': 24,
        'fc_code':      'FC03',
        'iv_scan':      False,
        'der_avm':      True,
        'builtin':      True,
    },
    'ekos': {
        'manufacturer': 'EKOS',
        'protocol':     'ekos',
        'description':  'EKOS inverter',
        'capacity':     '3~50kW',
        'mppt_count':   2,
        'string_count': 4,
        'fc_code':      'FC03',
        'iv_scan':      False,
        'der_avm':      True,
        'builtin':      True,
    },
    'senergy': {
        'manufacturer': 'Senergy',
        'protocol':     'senergy',
        'description':  'Senergy Modbus Protocol V1.2.4',
        'capacity':     '3~100kW',
        'mppt_count':   4,
        'string_count': 8,
        'fc_code':      'FC03',
        'iv_scan':      True,
        'der_avm':      True,
        'builtin':      True,
    },
    'goodwe': {
        'manufacturer': 'GoodWe',
        'protocol':     'goodwe',
        'description':  'GoodWe Grid-tied series',
        'capacity':     '2~100kW',
        'mppt_count':   6,
        'string_count': 20,
        'fc_code':      'FC03',
        'iv_scan':      False,
        'der_avm':      True,
        'builtin':      True,
    },
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_upper_snake(name):
    s = re.sub(r'[()（）\[\]【】]', '', str(name))
    s = re.sub(r'[\s\-./·:,]+', '_', s)
    s = re.sub(r'([a-z])([A-Z])', r'\1_\2', s)
    s = re.sub(r'[^A-Za-z0-9_]', '', s)
    s = re.sub(r'_+', '_', s).strip('_')
    result = s.upper()
    if result and result[0].isdigit():
        result = 'REG_' + result
    return result


def _token_similarity(a, b):
    """Jaccard similarity of UPPER_SNAKE tokens between two names."""
    ta = set(_to_upper_snake(a).split('_'))
    tb = set(_to_upper_snake(b).split('_'))
    if not ta or not tb:
        return 0.0
    overlap = len(ta & tb)
    union   = len(ta | tb)
    return overlap / union


def _seq_similarity(a, b):
    """SequenceMatcher ratio on normalized strings."""
    na = _to_upper_snake(a)
    nb = _to_upper_snake(b)
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _load_py_register_names(py_path):
    """
    Execute a *_registers.py file and return {const_name: addr_int} from RegisterMap.
    Returns empty dict on failure.
    """
    if not os.path.isfile(py_path):
        return {}
    try:
        with open(py_path, 'r', encoding='utf-8') as f:
            code = f.read()
        base_dir = os.path.dirname(py_path)
        ns = {'__file__': py_path}
        exec(compile(code, py_path, 'exec'), ns)
        reg_cls = ns.get('RegisterMap')
        if reg_cls is None:
            return {}
        return {k: v for k, v in vars(reg_cls).items()
                if isinstance(v, int) and v >= 0
                and not k.startswith('_') and not callable(v)}
    except Exception:
        return {}


def _resolve_builtin_py(name):
    """Return path to common/*_mm_registers.py for a builtin reference name."""
    path = os.path.join(_COMMON_DIR, f'{name}_mm_registers.py')
    if os.path.isfile(path):
        return path
    # Fallback: non-mm version
    path2 = os.path.join(_COMMON_DIR, f'{name}_registers.py')
    if os.path.isfile(path2):
        return path2
    return None


# ---------------------------------------------------------------------------
# ReferenceManager
# ---------------------------------------------------------------------------

class ReferenceManager:
    """
    Manages reference register files for offline and AI-assisted mapping.

    Usage:
        rm = ReferenceManager()
        # Offline mapping
        match = rm.offline_match_name('Phase A Output Voltage', 'AC Output', 'U16', 'V')
        # AI few-shot
        examples = rm.get_few_shot_text(n=2)
        # Save new reference
        rm.save_reference('newbrand', py_code, meta, mapping_excel_path)
    """

    def __init__(self, ref_dir=None):
        self.ref_dir    = ref_dir or _REF_DIR
        self.index_file = os.path.join(self.ref_dir, 'index.json')
        os.makedirs(self.ref_dir, exist_ok=True)

        # Load user-created index
        self._user_index = self._load_index()

        # Merge builtin definitions (builtin always present, user index can override meta)
        self._all_index = dict(_BUILTIN)
        self._all_index.update(self._user_index)

        # Cache: {ref_name: {const_name: addr_int}}
        self._name_cache = {}

        # Aggregate standard name set (built lazily)
        self._std_names = None

    # ── Index I/O ──────────────────────────────────────────────────────────

    def _load_index(self):
        if os.path.isfile(self.index_file):
            try:
                with open(self.index_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_index(self):
        # Save only user-created (non-builtin) entries
        user_entries = {k: v for k, v in self._all_index.items()
                        if not v.get('builtin')}
        try:
            with open(self.index_file, 'w', encoding='utf-8') as f:
                json.dump(user_entries, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    # ── Public API ─────────────────────────────────────────────────────────

    def list_references(self):
        """Return list of (name, meta) tuples — all references."""
        return list(self._all_index.items())

    def count(self):
        return len(self._all_index)

    def load_register_names(self, ref_name):
        """Return {const_name: addr_int} for a reference. Cached."""
        if ref_name in self._name_cache:
            return self._name_cache[ref_name]

        meta = self._all_index.get(ref_name, {})
        names = {}

        if meta.get('builtin'):
            # Load from common/*_mm_registers.py
            py_path = _resolve_builtin_py(ref_name)
            if py_path:
                names = _load_py_register_names(py_path)
        else:
            # Load from reference/{name}/registers.py
            py_path = meta.get('py_file', '')
            if not os.path.isabs(py_path):
                py_path = os.path.join(self.ref_dir, ref_name, 'registers.py')
            names = _load_py_register_names(py_path)

        self._name_cache[ref_name] = names
        return names

    def get_all_standard_names(self):
        """Return set of all known Solarize-standard constant names across all references."""
        if self._std_names is not None:
            return self._std_names
        result = set()
        for ref_name in self._all_index:
            result.update(self.load_register_names(ref_name).keys())
        self._std_names = result
        return result

    def offline_match_name(self, source_name, source_section='',
                            source_type='', source_unit='', threshold=0.55):
        """
        Find the best Solarize standard name for a source register name (offline mode).

        Strategy (in priority order):
          1. Exact normalized match (source UPPER_SNAKE == standard name)
          2. SequenceMatcher similarity >= threshold
          3. Token (Jaccard) similarity >= threshold
          4. Section + unit heuristics (e.g., section='AC Output', unit='V' → L1_VOLTAGE)

        Returns (best_name, confidence_float) or (None, 0.0).
        """
        std_names = self.get_all_standard_names()
        if not std_names:
            return None, 0.0

        normalized = _to_upper_snake(source_name)

        # 1. Exact match
        if normalized in std_names:
            return normalized, 1.0

        # 2. Sequence similarity
        best_seq  = ''
        best_seq_s = 0.0
        for sn in std_names:
            s = _seq_similarity(source_name, sn)
            if s > best_seq_s:
                best_seq_s = s
                best_seq   = sn

        if best_seq_s >= threshold + 0.15:   # high confidence
            return best_seq, best_seq_s

        # 3. Token (Jaccard) similarity
        best_tok  = ''
        best_tok_s = 0.0
        src_tokens = set(normalized.split('_'))
        for sn in std_names:
            st = set(sn.split('_'))
            overlap = len(src_tokens & st)
            union   = len(src_tokens | st)
            s = overlap / union if union else 0.0
            if s > best_tok_s:
                best_tok_s = s
                best_tok   = sn

        if best_tok_s >= threshold:
            # Prefer seq if close
            if best_seq_s >= threshold:
                if best_seq_s >= best_tok_s:
                    return best_seq, best_seq_s
            return best_tok, best_tok_s

        if best_seq_s >= threshold:
            return best_seq, best_seq_s

        # 4. Heuristic: section + unit → likely field
        sec_low  = source_section.lower()
        unit_low = source_unit.lower()
        type_low = source_type.lower()
        hint = _heuristic_match(normalized, sec_low, unit_low, type_low)
        if hint and hint in std_names:
            return hint, 0.5

        return None, 0.0

    def get_few_shot_text(self, n=3):
        """
        Generate n reference examples as structured text for Claude few-shot prompting.
        Returns multi-line string.
        """
        lines = []
        count = 0
        for ref_name, meta in self._all_index.items():
            if count >= n:
                break
            names = self.load_register_names(ref_name)
            if not names:
                continue
            mfr = meta.get('manufacturer', ref_name)
            lines.append(f'--- Reference: {mfr} ({ref_name}) ---')
            # Show up to 40 meaningful registers
            shown = 0
            for const_name, addr in names.items():
                if shown >= 40:
                    break
                if const_name.endswith('_HIGH'):
                    continue   # skip high-word pairs for brevity
                lines.append(f'  0x{addr:04X}  {const_name}')
                shown += 1
            lines.append('')
            count += 1
        return '\n'.join(lines)

    def get_few_shot_mapping_examples(self, n=2):
        """
        Return compact mapping examples for Stage 2 AI prompt:
        List of 'source_name → solarize_name (addr=0xNNNN)' strings.
        """
        examples = []
        count = 0
        for ref_name, meta in self._all_index.items():
            if count >= n:
                break
            names = self.load_register_names(ref_name)
            if not names:
                continue
            mfr = meta.get('manufacturer', ref_name)
            examples.append(f'# {mfr}')
            for const_name, addr in list(names.items())[:30]:
                if const_name.endswith('_HIGH'):
                    continue
                examples.append(f'  addr=0x{addr:04X} → {const_name}')
            examples.append('')
            count += 1
        return '\n'.join(examples)

    def save_reference(self, name, py_code, meta, mapping_excel_path=None):
        """
        Save a successfully generated register file as a new reference.

        Args:
            name               : Reference name (e.g. 'delta', 'fronius')
            py_code            : Generated Python source code string
            meta               : dict with manufacturer, protocol, mppt_count, etc.
            mapping_excel_path : Optional path to Stage 2 mapping Excel to copy
        """
        ref_subdir = os.path.join(self.ref_dir, name)
        os.makedirs(ref_subdir, exist_ok=True)

        # Save registers.py
        py_path = os.path.join(ref_subdir, 'registers.py')
        with open(py_path, 'w', encoding='utf-8') as f:
            f.write(py_code + '\n')

        # Copy mapping Excel if provided
        if mapping_excel_path and os.path.isfile(mapping_excel_path):
            import shutil
            dst = os.path.join(ref_subdir, 'mapping.xlsx')
            shutil.copy2(mapping_excel_path, dst)
            meta['mapping_excel'] = dst

        meta['py_file']  = py_path
        meta['created']  = datetime.now().isoformat()
        meta['builtin']  = False

        # Save meta.json
        meta_path = os.path.join(ref_subdir, 'meta.json')
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

        self._all_index[name] = meta
        self._name_cache.pop(name, None)   # Invalidate cache
        self._std_names = None             # Invalidate aggregate cache
        self._save_index()

    def promote_to_builtin(self, name):
        """Promote a user-created reference to builtin status.

        Copies the .py file to common/{name}_mm_registers.py and marks
        the reference as builtin so it persists across restarts.
        """
        import shutil

        meta = self._all_index.get(name, {})
        if meta.get('builtin'):
            return False, f"'{name}' is already a builtin reference."

        # Find the user reference .py file
        ref_subdir = os.path.join(self.ref_dir, name)
        py_file = meta.get('py_file') or os.path.join(ref_subdir, 'registers.py')
        if not os.path.isfile(py_file):
            return False, f"Reference .py file not found: {py_file}"

        # Copy to common/{name}_mm_registers.py
        dest = os.path.join(_COMMON_DIR, f'{name}_mm_registers.py')
        shutil.copy2(py_file, dest)

        # Also copy to common/{name}_registers.py (for RTU runtime)
        dest_rt = os.path.join(_COMMON_DIR, f'{name}_registers.py')
        if not os.path.isfile(dest_rt):
            shutil.copy2(py_file, dest_rt)

        # Update meta to builtin
        meta['builtin'] = True
        self._all_index[name] = meta

        # Update meta.json in reference subdir
        meta_path = os.path.join(ref_subdir, 'meta.json')
        if os.path.isfile(meta_path):
            try:
                import json as _json
                with open(meta_path, 'r', encoding='utf-8') as f:
                    m = _json.load(f)
                m['builtin'] = True
                with open(meta_path, 'w', encoding='utf-8') as f:
                    _json.dump(m, f, indent=2, ensure_ascii=False)
            except Exception:
                pass

        # Remove from user index (it's now builtin)
        self._user_index.pop(name, None)
        self._name_cache.pop(name, None)
        self._std_names = None
        self._save_index()

        return True, f"'{name}' promoted to builtin. Files: {dest}"

    def delete_reference(self, name):
        """Delete a user-created reference (builtin refs cannot be deleted)."""
        meta = self._all_index.get(name, {})
        if meta.get('builtin'):
            return False, "Cannot delete built-in reference."
        ref_subdir = os.path.join(self.ref_dir, name)
        if os.path.isdir(ref_subdir):
            import shutil
            shutil.rmtree(ref_subdir, ignore_errors=True)
        self._all_index.pop(name, None)
        self._user_index.pop(name, None)
        self._name_cache.pop(name, None)
        self._std_names = None
        self._save_index()
        return True, "Deleted."


# ---------------------------------------------------------------------------
# Heuristic matching helper
# ---------------------------------------------------------------------------

_HEURISTIC_RULES = [
    # (section_keywords, unit_keywords, type_keywords, name_keywords, → solarize_name)
    (['ac', 'output', 'grid', 'phase a', 'l1'],   ['v', 'volt'],  ['u16','s16'], [],        'L1_VOLTAGE'),
    (['ac', 'output', 'grid', 'phase b', 'l2'],   ['v', 'volt'],  ['u16','s16'], [],        'L2_VOLTAGE'),
    (['ac', 'output', 'grid', 'phase c', 'l3'],   ['v', 'volt'],  ['u16','s16'], [],        'L3_VOLTAGE'),
    (['ac', 'output', 'grid', 'phase a', 'l1'],   ['a', 'amp'],   ['u16','s16'], [],        'L1_CURRENT'),
    (['ac', 'output', 'grid', 'phase b', 'l2'],   ['a', 'amp'],   ['u16','s16'], [],        'L2_CURRENT'),
    (['ac', 'output', 'grid', 'phase c', 'l3'],   ['a', 'amp'],   ['u16','s16'], [],        'L3_CURRENT'),
    (['ac', 'output', 'grid'],                     ['hz', 'freq'], [],            [],        'L1_FREQUENCY'),
    (['pv', 'mppt', 'dc', 'input'],               ['v', 'volt'],  [],            ['1'],     'MPPT1_VOLTAGE'),
    (['pv', 'mppt', 'dc', 'input'],               ['a', 'amp'],   [],            ['1'],     'MPPT1_CURRENT'),
    (['pv', 'mppt', 'dc', 'input'],               ['w', 'power'], [],            ['1'],     'MPPT1_POWER_LOW'),
    (['energy', 'kwh', 'generation', 'total'],      ['kwh', 'wh', 'mwh'],  [],    ['total', 'accumulated', 'cumulative'], 'TOTAL_ENERGY_LOW'),
    (['energy', 'kwh', 'generation', 'total'],      ['kwh', 'wh', 'mwh'],  [],    ['today', 'daily'],    'TODAY_ENERGY_LOW'),
    (['status', 'mode', 'state', 'run'],           [],             ['u16'],       [],        'INVERTER_MODE'),
    (['temperature', 'temp'],                      ['c', 'deg'],   ['s16'],       [],        'INNER_TEMP'),
    (['error', 'fault', 'alarm'],                  [],             ['u16'],       ['1'],     'ERROR_CODE1'),
    (['power factor', 'pf'],                       [],             ['s16'],       [],        'POWER_FACTOR'),
]


def _heuristic_match(normalized_name, sec_low, unit_low, type_low):
    """Return a Solarize standard name from heuristic rules, or None."""
    name_low = normalized_name.lower()

    for sec_kws, unit_kws, type_kws, name_kws, result in _HEURISTIC_RULES:
        # All specified keyword groups must have at least one match
        if sec_kws and not any(k in sec_low or k in name_low for k in sec_kws):
            continue
        if unit_kws and not any(k in unit_low for k in unit_kws):
            continue
        if type_kws and not any(k in type_low for k in type_kws):
            continue
        if name_kws and not any(k in name_low for k in name_kws):
            continue
        return result

    return None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_default_manager = None


def get_manager():
    """Return the module-level singleton ReferenceManager."""
    global _default_manager
    if _default_manager is None:
        _default_manager = ReferenceManager()
    return _default_manager


def reset_manager():
    """Reset singleton (useful for testing)."""
    global _default_manager
    _default_manager = None
