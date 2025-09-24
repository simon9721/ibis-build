# iv_subtract.py
import csv
import argparse
from pathlib import Path

def fnum(s: str) -> float:
    # tolerate Fortran 'D' exponents
    return float(s.replace('D', 'E'))

def find_col(name_map, candidates):
    # case-insensitive lookup with aliases
    for c in candidates:
        if c is None:
            continue
        k = c.strip().lower()
        if k in name_map:
            return name_map[k]
    return None

def main():
    ap = argparse.ArgumentParser(description="Point-by-point current subtraction on IV CSVs.")
    ap.add_argument("--file", "-f", required=True, help="Input IV CSV (e.g., iv_typ.csv)")
    ap.add_argument("--minuend", "-a", required=True, help="Column to subtract FROM (e.g., i_pulldown)")
    ap.add_argument("--subtrahend", "-b", required=True, help="Column to subtract (e.g., i_gndclamp)")
    ap.add_argument("--out", "-o", help="Output CSV path (default: <input>_<minuend>-<subtrahend>.csv)")
    ap.add_argument("--iname", default="i_new", help="Output current column name (default: i_new)")
    args = ap.parse_args()

    inp = Path(args.file)
    if not inp.exists():
        raise FileNotFoundError(inp)

    # Load CSV
    lines = [ln for ln in inp.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    rdr = csv.DictReader(lines)
    rows = list(rdr)
    if not rows:
        raise ValueError("No data rows found.")

    # Build a case-insensitive column map -> original header
    header = rdr.fieldnames or []
    inv = {h.lower(): h for h in header}

    # Resolve time and voltage columns
    t_col = find_col(inv, ["time", "t"])
    v_col = find_col(inv, ["v_sweep", "V", "v"])
    if t_col is None:
        raise KeyError(f"Could not find a time column among {header}")
    if v_col is None:
        raise KeyError(f"Could not find a voltage column among {header}")

    # Resolve current columns (allow common aliases)
    aliases = {
        "i_pulldown": ["i_pulldown","pulldown","i_pd","ipulldown"],
        "i_gndclamp": ["i_gndclamp","i_groundclamp","ground_clamp","gndclamp","igndclamp"],
        "i_pullup": ["i_pullup","pullup","i_pu","ipullup"],
        "i_powerclamp": ["i_powerclamp","power_clamp","ipowerclamp"],
    }
    def resolve(name: str) -> str:
        # exact first
        if name.lower() in inv:
            return inv[name.lower()]
        # try aliases
        for k, al in aliases.items():
            if name.lower() == k or name.lower() in [a.lower() for a in al]:
                # return the first that exists in header
                for cand in [k] + al:
                    if cand.lower() in inv:
                        return inv[cand.lower()]
        # fallback: direct match if present
        raise KeyError(f"Column '{name}' not found. Available: {header}")

    a_col = resolve(args.minuend)
    b_col = resolve(args.subtrahend)

    # Compute point-by-point subtraction
    out_rows = []
    for r in rows:
        try:
            t = fnum(r[t_col])
            v = fnum(r[v_col])
            a = fnum(r[a_col])
            b = fnum(r[b_col])
            out_rows.append({"t": t, "v": v, args.iname: a - b})
        except Exception as e:
            raise ValueError(f"Failed to parse row: {r}\nError: {e}")

    # Output path
    outp = Path(args.out) if args.out else inp.with_name(f"{inp.stem}_{args.minuend}-{args.subtrahend}.csv")

    # Write CSV with guaranteed header
    outp.parent.mkdir(parents=True, exist_ok=True)
    with outp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["t","v",args.iname])
        w.writeheader()
        w.writerows(out_rows)

    print(f"[OK] Wrote {outp} ({len(out_rows)} rows)")

if __name__ == "__main__":
    main()
