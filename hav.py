import re, csv, json, sys
from pathlib import Path

VT_BANNER = re.compile(r'v-?t\s+curve\s+simulations', re.I)
IV_BANNER = re.compile(r'i-?v\s+curve\s+simulations', re.I)
NUM_ROW   = re.compile(r'^\s*[-+0-9Ee\.]+(?:\s+[-+0-9Ee\.]+){1,}\s*$')
VPOWER_RE = re.compile(r'vpower\s*=\s*([+-]?[0-9.]+[Ee]?[+-]?\d*)', re.I)
REF_RE    = re.compile(r'(\w+_ref)\s*=\s*([+-]?[0-9.]+[Ee]?[+-]?\d*)', re.I)
KV_LINE   = re.compile(r'^\s*([A-Za-z0-9_]+)\s*=\s*([^\s].*?)\s*$')

def parse_blocks(lines):
    # Find all table starts
    x_idx = [i for i,l in enumerate(lines) if l.strip() == 'x']
    blocks = []
    for idx in x_idx:
        # Backtrack phase from nearest banner above this table
        phase = None
        for k in range(idx, -1, -1):
            if VT_BANNER.search(lines[k]): phase = 'vt'; break
            if IV_BANNER.search(lines[k]): phase = 'iv'; break

        # Header (next nonblank)
        j = idx + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines): continue
        header = lines[j].strip().split()
        j += 1

        # Rows until 'y'
        rows = []
        while j < len(lines):
            s = lines[j].strip()
            if s == 'y': break
            if s and NUM_ROW.match(s):
                rows.append(s.split())
            j += 1

        # Gather metadata from ~80 lines above the table
        meta = {}
        scan_from = max(0, idx - 80)
        for k in range(scan_from, idx):
            m = VPOWER_RE.search(lines[k])
            if m:
                try: meta['vpower'] = float(m.group(1))
                except: pass
            for m2 in REF_RE.finditer(lines[k]):
                key, val = m2.group(1), m2.group(2)
                try: meta[key] = float(val)
                except: pass
            m3 = KV_LINE.match(lines[k])
            if m3 and m3.group(1).lower().endswith('_ref'):
                try: meta[m3.group(1)] = float(m3.group(2))
                except: pass

        blocks.append({
            'phase': phase or 'unknown',
            'header': header,
            'rows': rows,
            'meta': meta,
            'xindex': idx,
        })
    return blocks

def assign_corners(blocks):
    # Determine supply per block, per phase
    per_phase_vals = {'vt': set(), 'iv': set()}
    for b in blocks:
        ph = b['phase']
        if ph not in ('vt', 'iv'): continue
        if ph == 'vt':
            sup = b['meta'].get('vpower')
        else:
            sup = (b['meta'].get('pullup_ref') or
                   b['meta'].get('power_cl_ref') or
                   b['meta'].get('vpower'))
        b['supply'] = sup
        if sup is not None:
            per_phase_vals[ph].add(sup)

    # Rank supplies → corner map
    corner_map = {}
    for ph in ('vt', 'iv'):
        vals = sorted(per_phase_vals[ph])
        if len(vals) == 1:
            corner_map[ph] = {vals[0]: 'typ'}
        elif len(vals) == 2:
            corner_map[ph] = {vals[0]: 'min', vals[1]: 'max'}
        elif len(vals) >= 3:
            # use smallest=min, middle=typ, largest=max
            corner_map[ph] = {vals[0]: 'min', vals[1]: 'typ', vals[-1]: 'max'}

    # Apply labels (fallback to typ if unknown)
    for b in blocks:
        ph = b['phase']
        sup = b.get('supply')
        b['corner'] = (corner_map.get(ph, {}).get(sup)) or 'typ'
        b['corner_source'] = 'supply_rank' if sup is not None else 'fallback_typ'

def write_outputs(blocks, outdir):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for b in blocks:
        phase, corner = b['phase'], b['corner']
        header, rows, meta = b['header'], b['rows'], b['meta']
        if phase not in ('vt','iv'): continue
        stem = f"{phase}_{corner}"
        # CSV
        with (outdir / f"{stem}.csv").open('w', newline='') as f:
            w = csv.writer(f)
            w.writerow(header)
            for r in rows:
                w.writerow(r)
        # META
        meta_out = {
            'phase': phase,
            'corner': corner,
            'corner_source': b.get('corner_source'),
            'supply_used': b.get('supply'),
            'columns': header,
            'rows': len(rows),
            'metadata': meta
        }
        (outdir / f"{stem}.meta.json").write_text(json.dumps(meta_out, indent=2))

def main():
    if len(sys.argv) < 2:
        print("Usage: python lis2csv.py <file.lis> [outdir]")
        sys.exit(1)
    lis_path = Path(sys.argv[1])
    outdir   = sys.argv[2] if len(sys.argv) >= 3 else "lis_out"
    lines = lis_path.read_text(errors='ignore').splitlines()
    blocks = parse_blocks(lines)
    if not blocks:
        print("No tables found (looked for 'x'...'y').")
        sys.exit(2)
    assign_corners(blocks)
    write_outputs(blocks, outdir)
    print(f"Done. Wrote CSVs + meta to {outdir}")

if __name__ == "__main__":
    main()
