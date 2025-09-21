#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Harvest HSPICE .lis → CSV for IBEX (v2)
- Handles multiple VT/IV tables per file (typ/min/max blocks without explicit .ALTER markers).
- Associates each table to a corner by looking back for measured reference values (e.g., POWER_cl_ref or Vpower).
- Accepts optional "Index" column in headers.
- Case/whitespace tolerant; ignores non-numeric separators between header and data.

Outputs per .lis file into <outdir>/<lis_basename>/ :
  VT:
    vt_fall_pd_on_{corner}.csv    (t_s, v_pad_V)
    vt_fall_pd_off_{corner}.csv
    vt_rise_pu_on_{corner}.csv
    vt_rise_pu_off_{corner}.csv
  IV:
    pulldown_{corner}.csv         (v_gndref_V_raw, i_into_pad_A_raw)
    gnd_clamp_{corner}.csv        (v_gndref_V_raw, i_into_pad_A_raw)
    pullup_{corner}.csv           (v_vccref_V_raw, i_into_pad_A_raw)
    power_clamp_{corner}.csv      (v_vccref_V_raw, i_into_pad_A_raw)
"""
import re, csv, sys
from pathlib import Path
from typing import List, Tuple, Optional, Dict

_NUM = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
def is_numeric_row(s: str) -> bool:
    s = s.strip()
    if not s or set(s)==set('-'):
        return False
    toks = s.split()
    return all(re.fullmatch(_NUM, t) for t in toks)

def find_all_headers(lines: List[str], kind: str) -> List[int]:
    idxs = []
    if kind == "VT":
        pat = re.compile(r"\b(?:index\s+)?time\s+pulldown_on\s+pulldown_off\s+pullup_on\s+pullup_off\b", re.IGNORECASE)
    else:
        pat = re.compile(r"\b(?:index\s+)?time\s+v_sweep\s+i_pulldown\s+i_gndclamp\s+i_pullup\s+i_powerclamp\b", re.IGNORECASE)
    for i, l in enumerate(lines):
        if pat.search(l):
            idxs.append(i)
    return idxs

def parse_table(lines: List[str], hdr_idx: int) -> Tuple[List[str], List[List[str]], int]:
    """Return (header_tokens, rows, next_index)"""
    header_line = lines[hdr_idx].strip()
    header = header_line.split()
    # consume until first numeric line
    j = hdr_idx + 1
    while j < len(lines) and not is_numeric_row(lines[j]):
        j += 1
    rows = []
    while j < len(lines) and is_numeric_row(lines[j]):
        toks = lines[j].split()
        rows.append(toks)
        j += 1
    return header, rows, j

def to_lower_map(header: List[str]) -> Dict[str,int]:
    return {h.lower(): i for i,h in enumerate(header)}

def window_text(lines: List[str], idx: int, before: int = 60) -> str:
    a = max(0, idx-before); b = idx+1
    return "\n".join(lines[a:b])

def extract_nearby_ref(lines: List[str], hdr_idx: int) -> Optional[float]:
    """Search back a window for POWER_cl_ref or Vpower; return its float value if found."""
    win = window_text(lines, hdr_idx, before=80)
    m = re.search(r"\bPOWER?_?cl?_?ref\s*=\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)", win, re.IGNORECASE)
    if m: 
        try: return float(m.group(1))
        except: pass
    m = re.search(r"\bVpower\s*=\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)", win, re.IGNORECASE)
    if m:
        try: return float(m.group(1))
        except: pass
    return None

def map_ref_to_corner(refs: List[Optional[float]]) -> List[str]:
    """Given a list of reference voltages (possibly None), assign 'typ','min','max' in a stable way.
       Strategy: collect unique finite refs, sort ascending; label [min, mid, max].
       For None, fall back to first=typ, second=min, third=max order."""
    finite = sorted({r for r in refs if isinstance(r, (int,float))})
    labels = []
    def label_for(r):
        if not finite:
            return None
        if len(finite) == 1:
            return "typ"
        if len(finite) == 2:
            return "min" if r == finite[0] else "max"
        # len>=3
        # choose closest
        diffs = [abs(r - v) for v in finite]
        idx = diffs.index(min(diffs))
        return ["min","typ","max"][idx if len(finite)>=3 else idx]
    prelim = [label_for(r) if r is not None else None for r in refs]
    # Fill Nones by position heuristic
    order = ["typ","min","max"]
    for i, lab in enumerate(prelim):
        if lab is None:
            prelim[i] = order[i] if i < len(order) else "typ"
    return prelim

def write_csv(path: Path, header: List[str], rows: List[List[str]]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)

def harvest_v2(lis_path: Path, out_root: Path):
    text = lis_path.read_text(errors="ignore")
    lines = text.splitlines()
    out_dir = out_root / lis_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- VT ---
    vt_idxs = find_all_headers(lines, "VT")
    vt_refs = [extract_nearby_ref(lines, idx) for idx in vt_idxs]
    vt_tags = map_ref_to_corner(vt_refs)
    for idx, tag in zip(vt_idxs, vt_tags):
        header, rows, _ = parse_table(lines, idx)
        hmap = to_lower_map(header)
        # drop optional Index
        t_idx = hmap.get("time") or hmap.get("index time")  # robust
        pd_on = hmap.get("pulldown_on"); pd_off = hmap.get("pulldown_off")
        pu_on = hmap.get("pullup_on");   pu_off = hmap.get("pullup_off")
        if t_idx is None or None in (pd_on, pd_off, pu_on, pu_off):
            continue
        # If an "Index" column exists, shift indices by 1
        if header[0].lower() == "index" and header[1].lower() == "time":
            # indices already point to names; data row includes Index as col0
            pass
        # Emit four files
        def emit(col_idx, name):
            out = out_dir / f"{name}_{tag}.csv"
            data = []
            for r in rows:
                if len(r) > col_idx:
                    # time is always column for 'time' (assumed at index 0 or 1)
                    # find its position in header
                    tpos = hmap.get("time")
                    data.append([r[tpos], r[col_idx]])
            write_csv(out, ["t_s","v_pad_V"], data)
        emit(pd_on, "vt_fall_pd_on")
        emit(pd_off,"vt_fall_pd_off")
        emit(pu_on, "vt_rise_pu_on")
        emit(pu_off,"vt_rise_pu_off")

    # --- IV ---
    iv_idxs = find_all_headers(lines, "IV")
    iv_refs = [extract_nearby_ref(lines, idx) for idx in iv_idxs]
    iv_tags = map_ref_to_corner(iv_refs)
    for idx, tag in zip(iv_idxs, iv_tags):
        header, rows, _ = parse_table(lines, idx)
        hmap = to_lower_map(header)
        # handle optional 'index' leading column
        vs = hmap.get("v_sweep")
        ipd = hmap.get("i_pulldown")
        igc = hmap.get("i_gndclamp")
        ipu = hmap.get("i_pullup")
        ipc = hmap.get("i_powerclamp")
        # allow for 'index' as col0 by not assuming time at 0
        if vs is None:
            continue
        def dump(col_idx, name, xlab):
            if col_idx is None: return
            out = out_dir / f"{name}_{tag}.csv"
            data = []
            for r in rows:
                if len(r) > max(vs, col_idx):
                    data.append([r[vs], r[col_idx]])
            write_csv(out, [xlab, "i_into_pad_A_raw"], data)
        dump(ipd, "pulldown",    "v_gndref_V_raw")
        dump(igc, "gnd_clamp",   "v_gndref_V_raw")
        dump(ipu, "pullup",      "v_vccref_V_raw")
        dump(ipc, "power_clamp", "v_vccref_V_raw")

def main():
    import argparse
    ap = argparse.ArgumentParser(description="Harvest HSPICE .lis → CSV for IBEX (v2)")
    ap.add_argument("inputs", nargs="+", help=".lis files or directories")
    ap.add_argument("--outdir", required=True, help="Output directory for CSVs")
    args = ap.parse_args()
    out_root = Path(args.outdir); out_root.mkdir(parents=True, exist_ok=True)
    # collect paths
    paths = []
    for item in args.inputs:
        p = Path(item)
        if p.is_dir():
            paths += list(p.rglob("*.lis"))
        elif p.suffix.lower()==".lis":
            paths.append(p)
    if not paths:
        print("[err] No .lis files found."); sys.exit(2)
    for p in sorted(paths):
        print(f"[harvest] {p}")
        harvest_v2(p, out_root)

if __name__ == "__main__":
    main()
