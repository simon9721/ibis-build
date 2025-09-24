#!/usr/bin/env python3
import csv
import argparse
from pathlib import Path
import shutil

def fnum(s: str) -> float:
    # tolerate Fortran 'D' exponents
    return float((s or "0").replace('D', 'E'))

def ffmt(x: float) -> str:
    # compact, uppercase exponent; similar to what you use elsewhere
    return f"{x:.12g}".replace('e', 'E')

ALIASES = {
    "time":        ["t"],
    "v_sweep":     ["v","V"],
    "i_pulldown":  ["pulldown","i_pd","ipulldown"],
    "i_gndclamp":  ["i_groundclamp","ground_clamp","gndclamp","igndclamp"],
    "i_pullup":    ["pullup","i_pu","ipullup"],
    "i_powerclamp":["power_clamp","ipowerclamp"],
}

def resolve_col(inv_map, name: str):
    """Resolve a column name case-insensitively with simple aliases."""
    key = name.lower()
    if key in inv_map:
        return inv_map[key]
    # try aliases by canonical key if provided
    for canon, alist in ALIASES.items():
        if key == canon or key in [a.lower() for a in alist]:
            for cand in [canon] + alist:
                k = cand.lower()
                if k in inv_map:
                    return inv_map[k]
    raise KeyError(f"Column '{name}' not found. Available: {list(inv_map.values())}")

def load_csv_skip_comments(path: Path):
    # Keep header + data, skip lines starting with '#'
    raw = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    lines = [ln for ln in raw if ln.strip() and not ln.lstrip().startswith("#")]
    rdr = csv.DictReader(lines)
    rows = list(rdr)
    if not rows:
        raise ValueError(f"No data rows found in {path}")
    header = rdr.fieldnames or []
    return header, rows

def inplace_subtract(
    csv_path: Path,
    target_col: str,  # will be overwritten with result
    sub_col: str,     # value to subtract
    backup: bool = True
):
    if not csv_path.exists():
        raise FileNotFoundError(csv_path)

    header, rows = load_csv_skip_comments(csv_path)
    inv = {h.lower(): h for h in header}

    # resolve target and subtrahend columns from header
    target_name = resolve_col(inv, target_col)
    sub_name    = resolve_col(inv, sub_col)

    # compute in-place: target = target - sub
    for r in rows:
        a = fnum(r[target_name])
        b = fnum(r[sub_name])
        r[target_name] = ffmt(a - b)

    # Back up original if requested
    if backup:
        shutil.copy2(csv_path, csv_path.with_suffix(csv_path.suffix + ".bak"))

    # Write back with the same header order, comma-delimited
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        w.writerows(rows)

    print(f"[OK] Updated {csv_path.name}: '{target_name}' := '{target_name}' - '{sub_name}' "
          f"({len(rows)} rows). Backup: {csv_path.with_suffix(csv_path.suffix + '.bak').name if backup else 'none'}")

def main():
    ap = argparse.ArgumentParser(
        description="Overwrite a column in-place with point-by-point subtraction on an I-V CSV."
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("-f", "--file", help="Single CSV file to modify (e.g., models/io/iv_typ.csv)")
    g.add_argument("-d", "--triplet-dir", help="Directory containing iv_typ.csv, iv_min.csv, iv_max.csv")
    ap.add_argument("-t", "--target", required=True,
                    help="Column to overwrite (minuend), e.g., i_pulldown")
    ap.add_argument("-s", "--subtract", required=True,
                    help="Column to subtract (subtrahend), e.g., i_gndclamp")
    ap.add_argument("--no-backup", action="store_true", help="Do not create .bak backup file")
    args = ap.parse_args()

    if args.file:
        inplace_subtract(Path(args.file), args.target, args.subtract, backup=not args.no_backup)
        return

    # Triplet mode
    root = Path(args.triplet_dir)
    if not root.exists():
        raise FileNotFoundError(root)
    for corner in ("typ","min","max"):
        p = root / f"iv_{corner}.csv"
        if p.exists():
            inplace_subtract(p, args.target, args.subtract, backup=not args.no_backup)
        else:
            print(f"[WARN] missing {p}, skipping")

if __name__ == "__main__":
    main()
