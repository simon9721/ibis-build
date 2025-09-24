#!/usr/bin/env python3
# iv_postproc.py
import argparse, csv, shutil, sys
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import yaml

# ---------- numeric helpers ----------
def fnum(s: str) -> float:
    return float((s or "0").replace("D", "E"))

def ffmt(x: float) -> str:
    return f"{x:.12g}".replace("e", "E")

# ---------- column resolution ----------
ALIASES = {
    "time":        ["t"],
    "v_sweep":     ["v","V"],
    "i_pulldown":  ["pulldown","i_pd","ipulldown"],
    "i_gndclamp":  ["i_groundclamp","ground_clamp","gndclamp","igndclamp"],
    "i_pullup":    ["pullup","i_pu","ipullup"],
    "i_powerclamp":["power_clamp","ipowerclamp"],
}

def resolve(inv: Dict[str,str], name: str) -> str:
    key = name.lower()
    if key in inv:
        return inv[key]
    for canon, alist in ALIASES.items():
        if key == canon or key in [a.lower() for a in alist]:
            for cand in [canon] + alist:
                if cand.lower() in inv:
                    return inv[cand.lower()]
    raise KeyError(f"Column '{name}' not found. Available: {list(inv.values())}")

def load_csv_skip_hashes(path: Path) -> Tuple[List[str], List[Dict[str,str]]]:
    lines = [ln for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    rdr = csv.DictReader(lines)
    rows = list(rdr)
    if not rows:
        raise ValueError(f"No data rows in {path}")
    header = [h.strip() for h in (rdr.fieldnames or [])]
    return header, rows

# ---------- per-file processing ----------
def process_file(csv_path: Path, vcc_for_corner: float, backup: bool=True) -> None:
    header, rows = load_csv_skip_hashes(csv_path)
    inv = {h.lower(): h for h in header}

    # required columns
    vcol = resolve(inv, "v_sweep")
    pu   = resolve(inv, "i_pullup")
    pd   = resolve(inv, "i_pulldown")
    pc   = resolve(inv, "i_powerclamp")
    gc   = resolve(inv, "i_gndclamp")

    # 1) device-minus-clamp (in place)
    for r in rows:
        r[pu] = ffmt(fnum(r[pu]) - fnum(r[pc]))
        r[pd] = ffmt(fnum(r[pd]) - fnum(r[gc]))

    # 2) clamp cleanup (keep rows; zero values beyond limits)
    z_gc = z_pc = 0
    for r in rows:
        V = fnum(r[vcol])
        if V >= vcc_for_corner:     # ground clamp cut at Vcc (per corner)
            if r[gc] != "0" and r[gc] != "0.0":
                z_gc += 1
            r[gc] = "0"
        if V >= 0.0:                # power clamp cut at 0 V (ground-relative; NO conversion)
            if r[pc] != "0" and r[pc] != "0.0":
                z_pc += 1
            r[pc] = "0"

    # backup & save
    if backup:
        shutil.copy2(csv_path, csv_path.with_suffix(csv_path.suffix + ".bak"))
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        w.writerows(rows)

    print(f"[OK] {csv_path.name}: "
          f"pullup:=pullup-powerclamp, pulldown:=pulldown-gndclamp; "
          f"zeroed gndclamp@V>=Vcc ({z_gc} rows), powerclamp@V>=0 ({z_pc} rows)")

# ---------- component.yml V triplet ----------
def load_vtriplet_from_yaml(path: Path) -> Tuple[float,float,float]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    try:
        meta = data["meta"]
        return float(meta["v_min"]), float(meta["v_typ"]), float(meta["v_max"])
    except Exception as e:
        raise ValueError(f"component.yml missing meta.v_min/typ/max: {e}")

# ---------- main ----------
def main():
    ap = argparse.ArgumentParser(description="Automate I-V post-processing for IBIS IO buffers.")
    ap.add_argument("-d","--dir", required=True, help="Model directory containing iv_typ.csv, iv_min.csv, iv_max.csv")
    ap.add_argument("--component", help="Path to component.yml (to read v_min/typ/max).")
    ap.add_argument("--vmin", type=float, help="Override Vmin for iv_min.csv")
    ap.add_argument("--vtyp", type=float, help="Override Vtyp for iv_typ.csv")
    ap.add_argument("--vmax", type=float, help="Override Vmax for iv_max.csv")
    ap.add_argument("--no-backup", action="store_true", help="Do not write .bak backups")
    args = ap.parse_args()

    root = Path(args.dir)
    if not root.exists():
        print(f"[ERROR] directory not found: {root}", file=sys.stderr)
        sys.exit(1)

    # Determine V triplet (prefer component.yml; allow CLI overrides)
    vmin=vtyp=vmax=None
    if args.component:
        vmin, vtyp, vmax = load_vtriplet_from_yaml(Path(args.component))
    if args.vmin is not None: vmin = args.vmin
    if args.vtyp is not None: vtyp = args.vtyp
    if args.vmax is not None: vmax = args.vmax
    if None in (vmin, vtyp, vmax):
        print("[ERROR] Need v_min/typ/max via --component or --vmin/--vtyp/--vmax", file=sys.stderr)
        sys.exit(1)

    plan = [
        ("iv_min.csv", vmin),
        ("iv_typ.csv", vtyp),
        ("iv_max.csv", vmax),
    ]
    any_done = False
    for fname, vcc in plan:
        p = root / fname
        if not p.exists():
            print(f"[WARN] missing {p}, skipping")
            continue
        process_file(p, vcc_for_corner=vcc, backup=not args.no_backup)
        any_done = True

    if not any_done:
        print("[WARN] No iv_*.csv files processed.", file=sys.stderr)

if __name__ == "__main__":
    main()
