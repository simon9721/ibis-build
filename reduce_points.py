#!/usr/bin/env python3
# reduce_points.py
import argparse, csv, sys, math, shutil
from pathlib import Path
from typing import List, Dict, Tuple, Sequence, Optional

# --------------------- CSV IO ---------------------
def load_csv_skip_hashes(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    lines = [ln for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    rdr = csv.DictReader(lines)
    rows = list(rdr)
    if not rows:
        raise ValueError(f"No data rows in {path}")
    header = [h.strip() for h in (rdr.fieldnames or [])]
    return header, rows

def write_csv_with_backup(path: Path, header: List[str], rows: List[Dict[str, str]], backup: bool=True):
    if backup:
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        w.writerows(rows)

# --------------------- helpers ---------------------
def fnum(s: str) -> float:
    return float((s or "0").replace("D","E"))

def resolve(inv: Dict[str,str], names: Sequence[str]) -> Optional[str]:
    for name in names:
        k = name.lower()
        if k in inv:
            return inv[k]
    return None

ALIASES = {
    "time":        ["time","t"],
    "v_sweep":     ["v_sweep","v","V"],
    "vt_cols":     ["pulldown_on","pulldown_off","pullup_on","pullup_off"],
    "iv_cols":     ["i_pulldown","i_gndclamp","i_pullup","i_powerclamp"],
}

def collect_x_y_agg(file_kind: str, header: List[str], rows: List[Dict[str,str]]) -> Tuple[List[float], List[float]]:
    """Return x and an aggregate y signal to drive point selection.
       - I-V:  x = v_sweep; y_agg = sqrt(sum(currents^2))
       - V-T:  x = time;    y_agg = sqrt(sum(voltages^2))
    """
    inv = {h.lower(): h for h in header}
    if file_kind == "iv":
        xcol = resolve(inv, ALIASES["v_sweep"])
        if not xcol:
            # fallback to first column
            xcol = header[0]
        ycols = [c for c in ALIASES["iv_cols"] if c.lower() in inv]
        if not ycols:
            raise KeyError("No I-V current columns found (expected e.g. i_pulldown, i_pullup, ...)")
        ycols = [inv[c.lower()] for c in ycols]
    else:
        xcol = resolve(inv, ALIASES["time"]) or header[0]
        ycols = [c for c in ALIASES["vt_cols"] if c.lower() in inv]
        if not ycols:
            # if raw merged V-T not present, try common alt names (V_typ etc.)
            y_guess = [c for c in header if c.lower() in ("v_typ","v_min","v_max")]
            if not y_guess:
                raise KeyError("No V-T waveform columns found (expected pulldown_on/off, pullup_on/off)")
            ycols = y_guess
        else:
            ycols = [inv[c.lower()] for c in ycols]
    xs, ys = [], []
    for r in rows:
        xs.append(fnum(r[xcol]))
        acc = 0.0
        for c in ycols:
            try:
                acc += (fnum(r[c]))**2
            except Exception:
                # non-numeric (rare); ignore this column for aggregation
                pass
        ys.append(acc**0.5)
    return xs, ys

# ------------------ selection methods ------------------
def regular_interval_indices(n: int, k: int) -> List[int]:
    if k >= n or k <= 0:
        return list(range(n))
    if k == 1:
        return [0]  # degenerate; caller will usually use >=2
    step = (n - 1) / (k - 1)
    idxs = sorted({int(round(i * step)) for i in range(k)})
    # guarantee endpoints
    idxs[0] = 0
    idxs[-1] = n - 1
    # if rounding collided and reduced count, pad by nearest free indices
    while len(idxs) < k:
        for j in range(n):
            if j not in idxs:
                idxs.append(j)
                if len(idxs) == k:
                    break
    return sorted(idxs)

def _segment_max_error(xs: List[float], ys: List[float], i1: int, i2: int) -> Tuple[float, int]:
    """Return (max_error, index) of the point with the largest vertical deviation
       from the line (xs[i1],ys[i1]) -> (xs[i2],ys[i2]) among (i1, i2) open interval.
       If no interior point, returns (0, -1).
    """
    if i2 <= i1 + 1:
        return 0.0, -1
    x1, y1 = xs[i1], ys[i1]
    x2, y2 = xs[i2], ys[i2]
    dx = (x2 - x1)
    best_err, best_idx = 0.0, -1
    if dx == 0:
        # vertical segment: use absolute diff to y1
        for i in range(i1+1, i2):
            err = abs(ys[i] - y1)
            if err > best_err:
                best_err, best_idx = err, i
        return best_err, best_idx
    for i in range(i1+1, i2):
        # linear interpolation estimate at xs[i]
        t = (xs[i] - x1) / dx
        y_est = y1 + t * (y2 - y1)
        err = abs(ys[i] - y_est)
        if err > best_err:
            best_err, best_idx = err, i
    return best_err, best_idx

def greatest_change_indices(xs: List[float], ys: List[float], k: int) -> List[int]:
    """Greedy RDP-like: start with endpoints, iteratively add the point with the largest deviation."""
    n = len(xs)
    if k >= n or k <= 2:
        # keep all (or just endpoints if exactly 2 requested)
        return list(range(n)) if k >= n else [0, n-1]
    selected = [0, n-1]
    selected.sort()
    while len(selected) < k:
        best_err, best_idx, best_pos = -1.0, -1, None
        # evaluate all current segments
        for s in range(len(selected)-1):
            i1, i2 = selected[s], selected[s+1]
            err, idx = _segment_max_error(xs, ys, i1, i2)
            if idx != -1 and err > best_err:
                best_err, best_idx, best_pos = err, idx, s+1
        if best_idx == -1:
            break  # no interior points left
        selected.insert(best_pos, best_idx)
    return sorted(selected[:k])

# ------------------ per-file reducer ------------------
def reduce_file_inplace(path: Path, file_kind: str, max_points: int, method: str, backup: bool=True) -> int:
    """Reduce a single CSV in place. Returns number of rows kept."""
    header, rows = load_csv_skip_hashes(path)
    xs, yagg = collect_x_y_agg(file_kind, header, rows)
    n = len(xs)
    k = max(2, min(max_points, n))  # at least 2 points, at most n

    if method == "regular-interval":
        idxs = regular_interval_indices(n, k)
    elif method == "greatest-change":
        idxs = greatest_change_indices(xs, yagg, k)
    else:
        raise ValueError("Unknown method: " + method)

    # Apply selection to all columns (dict rows)
    idxset = set(idxs)
    reduced = [rows[i] for i in range(n) if i in idxset]
    # Keep original order of idxs (they're sorted)
    reduced.sort(key=lambda r: xs[rows.index(r)])

    write_csv_with_backup(path, header, reduced, backup=backup)
    return len(reduced)

# ------------------ version → defaults ------------------
def ibis_defaults(version: str) -> Tuple[int, int]:
    """Return (iv_max, vt_max) defaults based on IBIS version."""
    # Normalize version like "7.2" → (7, 2)
    try:
        major = int(version.split(".")[0])
        minor = int(version.split(".")[1]) if "." in version else 0
    except Exception:
        major, minor = 7, 2
    # IBIS 3.2: 100 points cap; IBIS 4.0+: 1000 cap (we apply same default to IV for consistency)
    if major < 4 and not (major == 3 and minor >= 2):
        # older than 3.2 → be conservative
        return 100, 100
    if major == 3 and minor == 2:
        return 100, 100
    # 4.0+ (through 7.2) -> 1000 default
    return 1000, 1000

# ------------------ main ------------------
def main():
    ap = argparse.ArgumentParser(description="Reduce I-V and V-T CSV point count to IBIS limits.")
    ap.add_argument("-d","--dir", required=True, help="Model directory containing iv_*.csv and/or vt_*.csv")
    ap.add_argument("--tables", default="iv,vt", help="Which table families to process: iv, vt, or iv,vt")
    ap.add_argument("--method", choices=["regular-interval","greatest-change"], default="greatest-change",
                    help="Down-selection method")
    ap.add_argument("--ibis", default="7.2", help="Target IBIS version (e.g., 3.2, 4.0, 7.2) for default caps")
    ap.add_argument("--iv-max", type=int, help="Override max points for I-V")
    ap.add_argument("--vt-max", type=int, help="Override max points for V-T")
    ap.add_argument("--no-backup", action="store_true", help="Do not write .bak files")
    args = ap.parse_args()

    root = Path(args.dir)
    if not root.exists():
        print(f"[ERROR] directory not found: {root}", file=sys.stderr)
        sys.exit(1)

    # Defaults by version
    iv_def, vt_def = ibis_defaults(args.ibis)
    iv_max = args.iv_max if args.iv_max is not None else iv_def
    vt_max = args.vt_max if args.vt_max is not None else vt_def

    tables = set([t.strip().lower() for t in args.tables.split(",") if t.strip()])

    plan = []
    if "iv" in tables:
        plan += [("iv", root / "iv_min.csv", iv_max),
                 ("iv", root / "iv_typ.csv", iv_max),
                 ("iv", root / "iv_max.csv", iv_max)]
    if "vt" in tables:
        plan += [("vt", root / "vt_min.csv", vt_max),
                 ("vt", root / "vt_typ.csv", vt_max),
                 ("vt", root / "vt_max.csv", vt_max)]

    any_done = False
    for kind, path, cap in plan:
        if not path.exists():
            print(f"[WARN] missing {path.name}, skipping")
            continue
        kept = reduce_file_inplace(path, kind, max_points=cap, method=args.method, backup=not args.no_backup)
        print(f"[OK] {path.name}: kept {kept} points (cap {cap}, method {args.method})")
        any_done = True

    if not any_done:
        print("[WARN] No files processed. Check --tables or directory content.", file=sys.stderr)

if __name__ == "__main__":
    main()
