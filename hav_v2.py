import re, csv, json, sys, os
from pathlib import Path
from typing import List, Dict, Any, Optional

# =========================
# Canonical header defaults
# =========================
IV_DEFAULT_HDR = ["time","v_sweep","i_pulldown","i_gndclamp","i_pullup","i_powerclamp"]
VT_DEFAULT_HDR = ["time","pulldown_on","pulldown_off","pullup_on","pullup_off"]

# Accepts: 0, 0., .5, 5., 1.23, -3.0E+01, etc.
NUM_TOKEN = r'[+\-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+\-]?\d+)?'

# Data row: at least two numeric columns separated by whitespace
NUM_ROW = re.compile(r'^\s*(' + NUM_TOKEN + r')(?:\s+(' + NUM_TOKEN + r'))+\s*$')

def _has_non_numeric_tokens(s: str) -> bool:
    tokens = [t.strip().strip(",") for t in s.strip().split() if t.strip(",")]
    num = re.compile(r'^' + NUM_TOKEN + r'$')
    return any(not num.match(t) for t in tokens)

def _synthesize_headers(phase: str, ncols: int) -> list:
    base = IV_DEFAULT_HDR if phase == "iv" else VT_DEFAULT_HDR
    hdr = base[:ncols]
    if len(hdr) < ncols:
        hdr += [f"col{j}" for j in range(len(hdr), ncols)]
    return hdr

# ============
# Phase banners
# ============
VT_BANNER = re.compile(r'v-?t\s+curve\s+simulations', re.I)
IV_BANNER = re.compile(r'i-?v\s+curve\s+simulations', re.I)

# ============================
# Parameter/temperature parsing
# ============================
PARAM_KV = re.compile(r'^\s*([a-zA-Z_]\w*)\s*=\s*(' + NUM_TOKEN + r')\s*$')
TEMP_LINE = re.compile(r'\btemp\s*=\s*(' + NUM_TOKEN + r')', re.I)
TNOM_LINE = re.compile(r'\btnom\s*=\s*(' + NUM_TOKEN + r')', re.I)

def parse_lis(filepath: Path):
    """
    Parse a HSPICE .lis containing VT and IV sections.

    Output:
      {
        'vt': {'tables': [ {'cols': [...], 'rows': [[...], ...], 'params': {...}}, ... ]},
        'iv': {'tables': [ ... ]},
      }
    """
    lines = filepath.read_text(errors='ignore').splitlines()
    i = 0
    phase: Optional[str] = None
    out = {'vt': {'tables': []}, 'iv': {'tables': []}}
    pending_params: Dict[str, Any] = {}
    current_cols: Optional[List[str]] = None
    current_rows: List[List[str]] = []

    def flush_table():
        nonlocal current_cols, current_rows, pending_params, phase
        if not current_rows:
            # Clear params so they don't bleed into next table
            pending_params = {}
            return
        # Guarantee header existence & length match
        ncols = len(current_rows[0])
        if not current_cols:
            current_cols = _synthesize_headers(phase or "", ncols)
        elif len(current_cols) != ncols:
            current_cols = (current_cols[:ncols]
                            if len(current_cols) > ncols
                            else current_cols + [f"col{j}" for j in range(len(current_cols), ncols)])
        tbl = {
            'cols': current_cols,
            'rows': current_rows,
            'params': pending_params if pending_params else {}
        }
        out[phase]['tables'].append(tbl)
        current_cols = None
        current_rows = []
        pending_params = {}

    while i < len(lines):
        s = lines[i]

        # Phase banners
        if VT_BANNER.search(s):
            flush_table()
            phase = 'vt'
            pending_params = {}
            current_cols = None
            current_rows = []

            # consume following parameter block lines (until a blank line or a numeric row)
            j = i + 1
            while j < len(lines):
                t = lines[j]
                if '******' in t or VT_BANNER.search(t):
                    j += 1
                    continue
                mtemp = TEMP_LINE.search(t)
                if mtemp:
                    pending_params['temp'] = float(mtemp.group(1).replace('D','E'))
                mtnom = TNOM_LINE.search(t)
                if mtnom:
                    pending_params['tnom'] = float(mtnom.group(1).replace('D','E'))
                mkv = PARAM_KV.match(t)
                if mkv:
                    k, v = mkv.group(1), float(mkv.group(2).replace('D','E'))
                    pending_params[k.lower()] = v
                    j += 1
                    continue
                # stop params when we hit a numeric row or blank separating block
                if NUM_ROW.match(t) or (t.strip() == ''):
                    break
                j += 1
            i = j
            continue

        if IV_BANNER.search(s):
            flush_table()
            phase = 'iv'
            current_cols = None
            current_rows = []
            pending_params = {}
            # capture temp/tnom similar to VT so we can validate/reorder corners
            j = i + 1
            while j < len(lines):
                t = lines[j]
                if '******' in t or IV_BANNER.search(t):
                    j += 1
                    continue
                mtemp = TEMP_LINE.search(t)
                if mtemp:
                    pending_params['temp'] = float(mtemp.group(1).replace('D','E'))
                mtnom = TNOM_LINE.search(t)
                if mtnom:
                    pending_params['tnom'] = float(mtnom.group(1).replace('D','E'))
                if NUM_ROW.match(t) or (t.strip() == ''):
                    break
                j += 1
            i = j
            continue

        # Skip the sentinel 'x' line that precedes headers
        if phase in ('vt','iv') and s.strip().lower() == 'x':
            i += 1
            continue

        # Within a phase, try to pick up a header line
        if phase in ('vt', 'iv') and s.strip() and not NUM_ROW.match(s):
            # Accept as header if it contains any non-numeric tokens
            if _has_non_numeric_tokens(s):
                tokens = [t.strip().strip(',') for t in re.split(r'[,\s]+', s.strip()) if t.strip(',')]
                current_cols = tokens
                i += 1
                continue

        # Data rows
        if phase in ('vt', 'iv') and NUM_ROW.match(s):
            parts = [p for p in re.split(r'[,\s]+', s.strip()) if p != '']
            current_rows.append(parts)
            i += 1
            # lookahead: if next line is not numeric, flush
            if i >= len(lines) or not NUM_ROW.match(lines[i]):
                flush_table()
            continue

        # outside interest
        i += 1

    # final
    flush_table()
    return out

def write_csv(path: Path, cols: List[str], rows: List[List[str]], phase: str):
    """
    Always guarantee a header row in the written CSV.
    If cols is missing or wrong length, inject phase defaults.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return

    ncols = len(rows[0])

    if not cols or len(cols) != ncols:
        defaults = IV_DEFAULT_HDR if phase == "iv" else VT_DEFAULT_HDR
        cols = (defaults + [f"col{j}" for j in range(len(defaults), ncols)])[:ncols]

    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)      # header
        w.writerows(rows)

def write_params_csv(path: Path, params: Dict[str, Any]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['key','value'])
        for k,v in params.items():
            w.writerow([k, v])

def choose_corners_by_order_and_checks(phase_tables: List[Dict[str, Any]], phase: str):
    """
    File order is typ, min, max — but we *reorder by temperature* when available:
      highest temp  -> min
      lowest temp   -> max
      middle temp   -> typ
    For VT we also check Vmin < Vtyp < Vmax based on 'pulldown_on' end value.
    Returns dict: {'typ': tbl, 'min': tbl, 'max': tbl, ...warnings/info}
    """
    # Pad if fewer than 3 tables (keeps pipeline from crashing)
    if len(phase_tables) < 3:
        phase_tables = phase_tables + [{}] * (3 - len(phase_tables))

    # Initial map by declared order
    mapping = {'typ': phase_tables[0], 'min': phase_tables[1], 'max': phase_tables[2]}

    # ---- Temperature-based reorder (applies to both VT and IV) ----
    temps = {k: mapping[k].get('params', {}).get('temp') for k in ('typ','min','max')}
    if all(t is not None for t in temps.values()):
        # low..high
        corners = ['typ','min','max']
        sorted_by_temp = sorted(corners, key=lambda k: temps[k])
        low, mid, high = sorted_by_temp[0], sorted_by_temp[1], sorted_by_temp[2]
        # Enforce rule: highest->min, lowest->max, middle->typ
        mapping = {
            'max': mapping[low],
            'typ': mapping[mid],
            'min': mapping[high],
        }
        mapping['_temp_info'] = f"Reordered by temp: {temps} -> max:{low}, typ:{mid}, min:{high}"
    else:
        mapping['_temp_warning'] = "Temperature missing for one or more corners; kept file order typ,min,max."

    # ---- VT voltage relationship check ----
    if phase == 'vt':
        def get_pdon_last(tbl: Dict[str,Any]) -> Optional[float]:
            cols = tbl.get('cols') or []
            rows = tbl.get('rows') or []
            if not cols or not rows:
                return None
            # find pulldown_on column; fallback to pullup_on if needed
            cand_idx = None
            for cand in ('pulldown_on', 'pd_on', 'pull_down_on'):
                for idx, c in enumerate(cols):
                    if cand.lower() == c.lower():
                        cand_idx = idx
                        break
                if cand_idx is not None:
                    break
            if cand_idx is None:
                return None
            try:
                return float(rows[-1][cand_idx].replace('D','E'))
            except Exception:
                return None

        v = {k: get_pdon_last(mapping[k]) for k in ('min','typ','max')}
        if all(x is not None for x in v.values()):
            if not (v['min'] < v['typ'] < v['max']):
                mapping['_vt_warning'] = f"Pulldown_on end-voltage order failed: {v} (expected Vmin<Vtyp<Vmax)."
        else:
            mapping['_vt_warning'] = "Could not find 'pulldown_on' for VT voltage order check."

    return mapping

def main():
    if len(sys.argv) < 2:
        print("Usage: python hav.py <file.lis> [outdir]")
        sys.exit(1)

    infile = Path(sys.argv[1])
    outdir = Path(sys.argv[2])

    # ✅ ensure output directory exists
    outdir.mkdir(parents=True, exist_ok=True)

    lis = Path(sys.argv[1])
    outdir = Path(sys.argv[2]) if len(sys.argv) >= 3 else Path('lis_out')
    parsed = parse_lis(lis)

    # Corner mapping per phase (with temp-based reorder)
    vt_map = choose_corners_by_order_and_checks(parsed['vt']['tables'], 'vt')
    iv_map = choose_corners_by_order_and_checks(parsed['iv']['tables'], 'iv')

    # ---- Write VT CSVs + per-corner params ----
    for corner, tbl in (('typ',vt_map['typ']), ('min',vt_map['min']), ('max',vt_map['max'])):
        if tbl and tbl.get('rows'):
            write_csv(outdir / f'vt_{corner}.csv', tbl.get('cols'), tbl.get('rows'), "vt")
            if tbl.get('params'):
                write_params_csv(outdir / f'vt_params_{corner}.csv', tbl['params'])

    # ---- Write IV CSVs ----
    for corner, tbl in (('typ',iv_map['typ']), ('min',iv_map['min']), ('max',iv_map['max'])):
        if tbl and tbl.get('rows'):
            write_csv(outdir / f'iv_{corner}.csv', tbl.get('cols'), tbl.get('rows'), "iv")

    # Meta summary
    meta = {
        'vt_temp_info': vt_map.get('_temp_info'),
        'vt_temp_warning': vt_map.get('_temp_warning'),
        'vt_voltage_warning': vt_map.get('_vt_warning'),
        'iv_temp_info': iv_map.get('_temp_info'),
        'iv_temp_warning': iv_map.get('_temp_warning'),
        'source': str(lis)
    }
    (outdir / 'harvest_meta.json').write_text(json.dumps(meta, indent=2))
    print(f"[OK] wrote VT/IV CSVs to {outdir}")
    warns = {k:v for k,v in meta.items() if k.endswith('_warning') and v}
    infos = {k:v for k,v in meta.items() if k.endswith('_info') and v}
    if infos:
        print("[INFO]", infos)
    if warns:
        print("[WARN]", warns)

if __name__ == '__main__':
    main()
