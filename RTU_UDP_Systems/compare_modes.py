# -*- coding: utf-8 -*-
"""
Offline Pipeline vs Reference comparison for all 6 built-in brands.
Runs Stage1->Stage2->Stage3 in OFFLINE mode via the web API, then compares
results against reference *_mm_registers.py files in common/.

Usage: python compare_modes.py
Requires the web server running on http://localhost:8181
"""

import io
import sys
import json
import os
import re
import time
import requests

# Force UTF-8 output on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_URL = "http://localhost:8181"
PROJECT   = "C:/CM4_4rs485/RTU_UDP_System_V1_1_0"
PDF_DIR   = os.path.join(PROJECT, "INVERTER_MODBUS_PDF_EXCEL")
COMMON    = os.path.join(PROJECT, "common")
OUT_DIR   = os.path.join(PROJECT, "_comparison_output")
os.makedirs(OUT_DIR, exist_ok=True)

BRANDS = {
    "solarize": {
        "pdf":      "Solarize Modbus Protocol-Korea-V1.2.4.pdf",
        "mppt": 9,  "strings": 24,  "fc": "FC03",
        "protocol": "solarize", "iv_scan": True,  "der_avm": True,
        "ref":      "solarize_mm_registers.py",
    },
    "huawei": {
        "pdf":      "SUN2000MC V200R023C00 Modbus Interface Definitions.pdf",
        "mppt": 6,  "strings": 12,  "fc": "FC03",
        "protocol": "huawei",   "iv_scan": False, "der_avm": False,
        "ref":      "huawei_mm_registers.py",
    },
    "kstar": {
        "pdf":      "1_KSG1.250K.Inverter.Modbus.Communication.Protocol.3.5.pdf",
        "mppt": 4,  "strings": 8,   "fc": "FC03",
        "protocol": "kstar",    "iv_scan": True,  "der_avm": False,
        "ref":      "kstar_mm_registers.py",
    },
    "sungrow": {
        "pdf":      "Communication Protocol of PV Grid-Connected String Inverters_V1.1.37_EN.pdf",
        "mppt": 4,  "strings": 8,   "fc": "FC03",
        "protocol": "sungrow",  "iv_scan": False, "der_avm": False,
        "ref":      "sungrow_mm_registers.py",
    },
    "ekos": {
        "pdf":      None,   # Excel-only source
        "mppt": 2,  "strings": 4,   "fc": "FC03",
        "protocol": "ekos",     "iv_scan": False, "der_avm": False,
        "ref":      "ekos_mm_registers.py",
    },
    "goodwe": {
        "pdf":      "goodwe_modbus_protocol.pdf",
        "mppt": 4,  "strings": 8,   "fc": "FC03",
        "protocol": "goodwe",   "iv_scan": False, "der_avm": True,
        "ref":      "goodwe_mm_registers.py",
    },
}

POLL_INTERVAL = 3      # seconds between polls
POLL_TIMEOUT  = 300    # max seconds per stage


# ─────────────────────── API helpers ────────────────────────────

def api(method, path, **kwargs):
    url = BASE_URL + path
    resp = getattr(requests, method)(url, timeout=120, **kwargs)
    try:
        return resp.status_code, resp.json()
    except Exception:
        return resp.status_code, resp.text


def poll_until_ready(path_fn, stage_name, sid, timeout=POLL_TIMEOUT):
    """
    Poll GET endpoint every POLL_INTERVAL seconds until 200 or timeout.
    Returns (status_code, data).
    """
    deadline = time.time() + timeout
    last_sc = None
    while time.time() < deadline:
        sc, data = api("get", path_fn(sid))
        last_sc = sc
        if sc == 200:
            return sc, data
        if sc not in (404, 409):
            # Unexpected error
            print(f"    [{stage_name}] unexpected status {sc}: {data}")
        time.sleep(POLL_INTERVAL)
    return last_sc, {"error": f"Timeout after {timeout}s"}


# ─────────────────────── code analysis ──────────────────────────

def count_registermap_attrs(code):
    """Count attribute assignments in RegisterMap class body."""
    in_class = False
    count = 0
    for line in code.splitlines():
        if re.match(r'^class RegisterMap', line):
            in_class = True
            continue
        if in_class:
            if re.match(r'^class ', line):
                break
            if re.match(r'^\s+\w+\s*=\s*', line):
                count += 1
    return count


def get_class_names(code):
    return re.findall(r'^class\s+(\w+)', code, re.MULTILINE)


def get_scale_keys(code):
    m = re.search(r'SCALE\s*=\s*\{([^}]+)\}', code, re.DOTALL)
    if not m:
        return []
    return re.findall(r"'(\w+)'\s*:", m.group(1))


def read_ref(brand_cfg):
    path = os.path.join(COMMON, brand_cfg["ref"])
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


# ─────────────────────── pipeline ───────────────────────────────

def run_offline(brand, cfg):
    result = {
        "brand": brand,
        "stage1_ok": False, "stage2_ok": False, "stage3_ok": False,
        "stage1_rows": 0,   "stage2_rows": 0,
        "gen_code": "",     "val_results": [],
        "error": "",
    }

    pdf_path = os.path.join(PDF_DIR, cfg["pdf"]) if cfg["pdf"] else None
    if not pdf_path or not os.path.exists(pdf_path):
        result["error"] = f"No PDF: {cfg['pdf']}"
        return result

    # Create session
    sc, sess = api("post", "/api/session/create")
    if sc != 200:
        result["error"] = f"Session create failed ({sc}): {sess}"
        return result
    sid = sess["session_id"]
    print(f"  Session: {sid}")

    # Upload PDF
    with open(pdf_path, "rb") as f:
        sc, up = api("post", f"/api/upload-pdf?session_id={sid}",
                     files={"file": (os.path.basename(pdf_path), f, "application/pdf")})
    if sc != 200:
        result["error"] = f"Upload failed ({sc}): {up}"
        return result
    print(f"  PDF uploaded")

    # Stage 1 (async)
    sc, _ = api("post", "/api/stage1/run",
                json={"session_id": sid, "mode": "offline"})
    if sc != 200:
        result["error"] = f"Stage1 start failed ({sc})"
        return result
    print(f"  Stage1 started, polling...", end="", flush=True)
    sc1, s1data = poll_until_ready(lambda s: f"/api/stage1/result?session_id={s}", "stage1", sid)
    if sc1 != 200:
        result["error"] = f"Stage1 timeout/error: {s1data}"
        print(" FAIL")
        return result
    result["stage1_ok"] = True
    result["stage1_rows"] = len(s1data.get("rows", []))
    print(f" {result['stage1_rows']} rows")

    # Stage 2 (async)
    sc, _ = api("post", "/api/stage2/run",
                json={"session_id": sid, "mode": "offline",
                      "mppt_count": cfg["mppt"], "string_count": cfg["strings"]})
    if sc != 200:
        result["error"] = f"Stage2 start failed ({sc})"
        return result
    print(f"  Stage2 started, polling...", end="", flush=True)
    sc2, s2data = poll_until_ready(lambda s: f"/api/stage2/result?session_id={s}", "stage2", sid)
    if sc2 != 200:
        result["error"] = f"Stage2 timeout/error: {s2data}"
        print(" FAIL")
        return result
    result["stage2_ok"] = True
    result["stage2_rows"] = len(s2data.get("rows", []))
    print(f" {result['stage2_rows']} rows")

    # Stage 3 (async)
    sc, _ = api("post", "/api/stage3/run",
                json={
                    "session_id":   sid,
                    "mode":         "offline",
                    "protocol_name": cfg["protocol"],
                    "manufacturer": brand.capitalize(),
                    "mppt_count":   cfg["mppt"],
                    "string_count": cfg["strings"],
                    "iv_scan":      cfg["iv_scan"],
                    "der_avm":      cfg["der_avm"],
                    "class_name":   "RegisterMap",
                    "fc_code":      cfg["fc"],
                })
    if sc != 200:
        result["error"] = f"Stage3 start failed ({sc})"
        return result
    print(f"  Stage3 started, polling...", end="", flush=True)
    sc3, s3data = poll_until_ready(lambda s: f"/api/stage3/result?session_id={s}", "stage3", sid)
    if sc3 != 200:
        result["error"] = f"Stage3 timeout/error: {s3data}"
        print(" FAIL")
        return result
    result["stage3_ok"] = True
    result["gen_code"]   = s3data.get("code", "")
    # results is list of [status, name, message] or (status, name, message)
    result["val_results"] = s3data.get("results", [])
    print(f" {len(result['gen_code'])} chars, {len(result['val_results'])} checks")

    # Save generated file
    out_path = os.path.join(OUT_DIR, f"{brand}_offline_registers.py")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(result["gen_code"])
    print(f"  Saved: {out_path}")

    return result


# ─────────────────────── comparison ─────────────────────────────

def compare(offline, ref_code, brand):
    gen_code = offline.get("gen_code", "")

    gen_regs = count_registermap_attrs(gen_code) if gen_code else 0
    ref_regs = count_registermap_attrs(ref_code) if ref_code else 0

    gen_cls  = get_class_names(gen_code)
    ref_cls  = get_class_names(ref_code)
    gen_sc   = get_scale_keys(gen_code)
    ref_sc   = get_scale_keys(ref_code)

    val = offline.get("val_results", [])
    # each item is [status, name, message] or (status, name, message)
    passed = sum(1 for v in val if (v[0] if isinstance(v, (list, tuple)) else v.get("status","")) == "PASS")
    total  = len(val)

    return {
        "brand": brand,
        "ok": offline.get("stage3_ok", False),
        "error": offline.get("error", ""),
        "s1_rows": offline.get("stage1_rows", 0),
        "s2_rows": offline.get("stage2_rows", 0),
        "gen_regs": gen_regs,
        "ref_regs": ref_regs,
        "reg_delta": gen_regs - ref_regs,
        "gen_cls": gen_cls,
        "ref_cls": ref_cls,
        "miss_cls": sorted(set(ref_cls) - set(gen_cls)),
        "extra_cls": sorted(set(gen_cls) - set(ref_cls)),
        "gen_sc": gen_sc,
        "ref_sc": ref_sc,
        "miss_sc": sorted(set(ref_sc) - set(gen_sc)),
        "extra_sc": sorted(set(gen_sc) - set(ref_sc)),
        "val_pass": passed,
        "val_total": total,
        "val_items": val,
    }


# ─────────────────────── report ─────────────────────────────────

def print_report(rows):
    sep = "=" * 80
    print(f"\n{sep}")
    print("  OFFLINE PIPELINE vs REFERENCE -- COMPARISON REPORT")
    print(f"  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(sep)

    # Summary table
    hdr = f"{'Brand':<10} {'Pipeline':^10} {'S1rows':^7} {'S2rows':^7} {'GenRegs':^8} {'RefRegs':^8} {'Delta':^6} {'Val':^8}"
    print(f"\n{hdr}")
    print("-" * len(hdr))
    for r in rows:
        ok  = "OK"   if r["ok"]    else "FAIL"
        val = f"{r['val_pass']}/{r['val_total']}" if r["val_total"] else "n/a"
        dlt = f"{r['reg_delta']:+d}" if r["ok"] else "n/a"
        print(f"{r['brand']:<10} {ok:^10} {r['s1_rows']:^7} {r['s2_rows']:^7} "
              f"{r['gen_regs']:^8} {r['ref_regs']:^8} {dlt:^6} {val:^8}")

    # Per-brand detail
    for r in rows:
        print(f"\n{'─'*80}")
        print(f"  {r['brand'].upper()}")
        print(f"{'─'*80}")

        if not r["ok"]:
            err = r["error"].replace("\u2014", "--").replace("\u2013", "-")
            print(f"  ERROR: {err}")
            if r["ref_regs"]:
                print(f"  Reference has {r['ref_regs']} RegisterMap attributes")
            continue

        print(f"  Stage1 rows   : {r['s1_rows']}")
        print(f"  Stage2 rows   : {r['s2_rows']}")
        print(f"  RegisterMap   : generated={r['gen_regs']}  reference={r['ref_regs']}  delta={r['reg_delta']:+d}")

        if r["miss_cls"]:
            print(f"  Missing classes : {r['miss_cls']}")
        if r["extra_cls"]:
            print(f"  Extra classes   : {r['extra_cls']}")
        if not r["miss_cls"] and not r["extra_cls"]:
            print(f"  Classes match   : {', '.join(r['gen_cls'])}")

        if r["miss_sc"]:
            print(f"  Missing SCALE   : {r['miss_sc']}")
        if r["extra_sc"]:
            print(f"  Extra SCALE     : {r['extra_sc']}")
        if not r["miss_sc"] and not r["extra_sc"]:
            print(f"  SCALE keys match: {', '.join(r['gen_sc'])}")

        if r["val_total"]:
            print(f"\n  Validation {r['val_pass']}/{r['val_total']} passed:")
            for item in r["val_items"]:
                if isinstance(item, (list, tuple)):
                    status, name, msg = item[0], item[1] if len(item) > 1 else "", item[2] if len(item) > 2 else ""
                else:
                    status = item.get("status", "?")
                    name   = item.get("name", "?")
                    msg    = item.get("message", "")
                icon = "v" if status == "PASS" else "x"
                print(f"    [{icon}] {name:<35} {msg}")
        else:
            print("  Validation: no data")


# ─────────────────────── main ───────────────────────────────────

def main():
    print(f"Offline Pipeline Comparison -- {len(BRANDS)} brands")
    print(f"Server : {BASE_URL}")
    print(f"Output : {OUT_DIR}\n")

    all_cmp = []

    for brand, cfg in BRANDS.items():
        print(f"\n{'='*60}")
        print(f"  {brand.upper()}")
        print(f"{'='*60}")

        ref_code = read_ref(cfg)
        if not ref_code:
            print(f"  WARNING: no reference file ({cfg['ref']})")

        if cfg["pdf"] is None:
            print(f"  SKIP: No PDF (EKOS uses Excel-only source)")
            offline = {
                "brand": brand, "stage1_ok": False, "stage2_ok": False, "stage3_ok": False,
                "stage1_rows": 0, "stage2_rows": 0, "gen_code": "", "val_results": [],
                "error": "No PDF -- EKOS uses Excel source only",
            }
        else:
            offline = run_offline(brand, cfg)

        cmp = compare(offline, ref_code, brand)
        all_cmp.append(cmp)
        time.sleep(1)

    print_report(all_cmp)

    report_path = os.path.join(OUT_DIR, "comparison_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(all_cmp, f, indent=2, default=str)
    print(f"\nJSON report: {report_path}")


if __name__ == "__main__":
    main()
