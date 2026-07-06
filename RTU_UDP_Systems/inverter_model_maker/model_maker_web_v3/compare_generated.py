# -*- coding: utf-8 -*-
"""
Compare a MM2-generated *_registers.py against the hand-written "correct" file.

Usage:
    python compare_generated.py <correct_file.py> <generated_file.py>

Prints a structured report with:
- Address constant match (name -> int)
- DATA_TYPES match
- SCALE match
- Top-level constants (MPPT_CHANNELS, RTU_FC_CODE, etc.)
- InverterMode values
- Overall %
"""
import ast
import sys
from typing import Any, Dict, Set, Tuple


def _load_module_ast(path: str) -> ast.Module:
    with open(path, 'r', encoding='utf-8') as f:
        return ast.parse(f.read(), filename=path)


def _eval_literal(node):
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def extract_spec(path: str) -> Dict[str, Any]:
    """Return a normalized spec dict for comparison."""
    tree = _load_module_ast(path)
    spec: Dict[str, Any] = {
        'register_map': {},      # name -> int
        'data_types': {},        # name -> str
        'scale': {},             # key -> float/str
        'top_consts': {},        # CONST_NAME -> value
        'inverter_modes': {},    # 'INITIAL' -> int
        'error_bits': {},        # 'ErrorCode1': {bit: name}
        'h01_field_map': {},     # field -> addr/list
        'read_blocks': [],       # list of tuples
    }

    top_const_names = {
        'MPPT_CHANNELS', 'STRING_CHANNELS', 'STRINGS_PER_MPPT',
        'RTU_FC_CODE', 'U32_WORD_ORDER', 'RTU_SLAVE_ID',
        'RTU_BAUDRATE', 'RTU_PARITY', 'RTU_STOPBITS', 'RTU_BYTESIZE',
        'DEFAULT_TIMEOUT', 'DEFAULT_RETRIES',
    }

    for node in tree.body:
        # Top-level assignments
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            val = _eval_literal(node.value)
            if name in top_const_names:
                spec['top_consts'][name] = val
            elif name == 'DATA_TYPES' and isinstance(val, dict):
                spec['data_types'] = val
            elif name == 'SCALE' and isinstance(val, dict):
                spec['scale'] = val
            elif name == 'H01_FIELD_MAP' and isinstance(val, dict):
                spec['h01_field_map'] = val
            elif name == 'READ_BLOCKS':
                spec['read_blocks'] = val if val is not None else []

        # Class definitions: RegisterMap, InverterMode, ErrorCode1/2/3
        elif isinstance(node, ast.ClassDef):
            cls = node.name
            if cls == 'RegisterMap':
                for stmt in node.body:
                    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                        cname = stmt.targets[0].id
                        cval = _eval_literal(stmt.value)
                        if isinstance(cval, int):
                            spec['register_map'][cname] = cval
            elif cls == 'InverterMode':
                for stmt in node.body:
                    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                        cname = stmt.targets[0].id
                        cval = _eval_literal(stmt.value)
                        if isinstance(cval, int):
                            spec['inverter_modes'][cname] = cval
            elif cls.startswith('ErrorCode'):
                for stmt in node.body:
                    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                        if stmt.targets[0].id == 'BITS':
                            bits = _eval_literal(stmt.value)
                            if isinstance(bits, dict):
                                spec['error_bits'][cls] = bits

    return spec


def _dict_diff(name: str, a: Dict, b: Dict) -> Tuple[int, int, list]:
    """Return (matched, total_expected, issues)."""
    issues = []
    keys_a = set(a.keys())
    keys_b = set(b.keys())
    missing = keys_a - keys_b
    extra = keys_b - keys_a
    common = keys_a & keys_b
    mismatched = 0
    for k in sorted(common):
        if a[k] != b[k]:
            mismatched += 1
            issues.append(f'  MISMATCH {name}[{k}]: correct={a[k]!r} vs generated={b[k]!r}')
    for k in sorted(missing):
        issues.append(f'  MISSING  {name}[{k}] (correct={a[k]!r})')
    for k in sorted(extra):
        issues.append(f'  EXTRA    {name}[{k}] (generated={b[k]!r})')
    matched = len(common) - mismatched
    total = len(keys_a)
    return matched, total, issues


def compare(correct_path: str, generated_path: str) -> None:
    correct = extract_spec(correct_path)
    generated = extract_spec(generated_path)

    print(f'=== Comparison ===')
    print(f'  correct:   {correct_path}')
    print(f'  generated: {generated_path}')
    print()

    total_match = 0
    total_count = 0

    for key, label in [
        ('register_map', 'RegisterMap addresses'),
        ('data_types', 'DATA_TYPES'),
        ('scale', 'SCALE'),
        ('top_consts', 'Top constants'),
        ('inverter_modes', 'InverterMode'),
        ('h01_field_map', 'H01_FIELD_MAP'),
    ]:
        m, t, issues = _dict_diff(label, correct[key], generated[key])
        total_match += m
        total_count += t
        pct = (m / t * 100) if t > 0 else 100.0
        print(f'[{label}] {m}/{t} ({pct:.1f}%)')
        for line in issues[:15]:
            print(line)
        if len(issues) > 15:
            print(f'  ... and {len(issues)-15} more issues')
        print()

    # Error bits (per class)
    for cls in sorted(set(correct['error_bits'].keys()) | set(generated['error_bits'].keys())):
        a = correct['error_bits'].get(cls, {})
        b = generated['error_bits'].get(cls, {})
        m, t, issues = _dict_diff(cls, a, b)
        total_match += m
        total_count += t
        pct = (m / t * 100) if t > 0 else 100.0
        print(f'[{cls}.BITS] {m}/{t} ({pct:.1f}%)')
        for line in issues[:10]:
            print(line)
        print()

    overall = (total_match / total_count * 100) if total_count > 0 else 0.0
    print(f'=== OVERALL: {total_match}/{total_count} = {overall:.1f}% ===')


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print('Usage: python compare_generated.py <correct> <generated>')
        sys.exit(1)
    compare(sys.argv[1], sys.argv[2])
