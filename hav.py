import re, csv, json, sys
from pathlib import Path

# Phase banners (can repeat; only used for phase transitions)
VT_BANNER = re.compile(r'v-?t\s+curve\s+simulations', re.I)
IV_BANNER = re.compile(r'i-?v\s+curve\s+simulations', re.I)

# Optional: capture .ALTER cues for meta (not used to label)
ALTER_LINE = re.compile(r'^\s*\.alter\b', re.I)
CUE_MIN = re.compile(r'\b(min(?:imum)?|slow|ss)\b', re.I)
CUE_MAX = re.compile(r'\b(max(?:imum)?|fast|ff)\b', re.I)

# Data rows (at least 2 numeric columns)
NUM_ROW = re.compile(r'^\s*[-+0-9Ee\.]+(?:\s+[-+0-9Ee\.]+){1,}\s*$')

def cue_from_text(s: str):
    if CUE_MIN.search(s): return 'min'
    if CUE_MAX.search(s): return 'max'
    return None

def parse_lis(lines):
    """
    For each phase independently:
      1st table -> min
      2nd table -> max
      3rd+ table(s) -> typ (keeps the most recent for that corner)
    """
    current_phase = None         # 'vt' | 'iv'
    seen_tables = {'vt': 0, 'iv': 0}

    # Only keep the latest table for each (phase, corner)
    latest = {}  # (phase, corner) -> {'header','rows','meta'}

    # Capture any .ALTER cue we see before the next table (for meta only)
    pending_cue = {'vt': None, 'iv': None}
    pending_cue_line = {'vt': None, 'iv': None}

    i, n = 0, len(lines)
    while i < n:
        line = lines[i]

        # Detect phase transitions (only when it actually changes)
        if VT_BANNER.search(line):
            if current_phase != 'vt':
                current_phase = 'vt'
        elif IV_BANNER.search(line):
            if current_phase != 'iv':
                current_phase = 'iv'

        # Track upcoming .ALTER cue (meta only)
        if ALTER_LINE.search(line) and current_phase in ('vt','iv'):
            cue = None
            parts = line.split('$', 1)
            if len(parts) == 2:
                cue = cue_from_text(parts[1])
            if cue is None:
                # look ahead a few comment-only lines for the cue
                k, hops = i + 1, 6
                while k < n and hops > 0:
                    s = lines[k].strip()
                    if s.startswith('$'):
                        cue = cue_from_text(s)
                        if cue: break
                    elif s and not s.startswith('$'):
                        break
                    k += 1
                    hops -= 1
            pending_cue[current_phase] = cue
            pending_cue_line[current_phase] = i + 1

        # Table start
        if line.strip() == 'x':
            if current_phase not in ('vt','iv'):
                i += 1
                continue

            # Header: next nonblank
            j = i + 1
            while j < n and not lines[j].strip():
                j += 1
            if j >= n: break
            header = lines[j].strip().split()
            j += 1

            # Rows until 'y'
            rows = []
            while j < n:
                s = lines[j].strip()
                if s == 'y': break
                if s and NUM_ROW.match(s):
                    rows.append(s.split())
                j += 1

            # Increment per-phase table counter and map by order
            seen_tables[current_phase] += 1
            idx = seen_tables[current_phase]
            if idx == 1:
                corner = 'min'
            elif idx == 2:
                corner = 'max'
            else:
                corner = 'typ'

            # Meta (diagnostics)
            meta = {
                'label_policy': 'by_sequence_min_max_typ',
                'phase_table_index': idx,
                'pre_table_alter_cue': pending_cue[current_phase],
                'pre_table_alter_cue_line': pending_cue_line[current_phase],
            }
            # consume the pending cue after recording
            pending_cue[current_phase] = None
            pending_cue_line[current_phase] = None

            latest[(current_phase, corner)] = {
                'header': header,
                'rows': rows,
                'meta': meta
            }

            i = j  # jump to 'y'
        i += 1

    return latest

def write_outputs(latest, outdir):
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)

    # exactly six outputs
    targets = [
        ('vt','min'), ('vt','max'), ('vt','typ'),
        ('iv','min'), ('iv','max'), ('iv','typ'),
    ]
    for phase, corner in targets:
        csv_path = out / f"{phase}_{corner}.csv"
        meta_path = out / f"{phase}_{corner}.meta.json"
        block = latest.get((phase, corner))
        if not block:
            # stub to keep the set complete if something is missing in the .lis
            with csv_path.open('w', newline='') as f:
                csv.writer(f).writerow(['NO_DATA_FOUND'])
            meta = {
                'phase': phase, 'corner': corner,
                'columns': ['NO_DATA_FOUND'],
                'rows': 0,
                'metadata': {'label_policy': 'by_sequence_min_max_typ', 'missing': True}
            }
            meta_path.write_text(json.dumps(meta, indent=2))
            print(f"Wrote {csv_path}  (0 rows) [missing]")
            continue

        header, rows, meta_info = block['header'], block['rows'], block['meta']
        with csv_path.open('w', newline='') as f:
            w = csv.writer(f)
            w.writerow(header)
            for r in rows:
                w.writerow(r)

        meta_blob = {
            'phase': phase,
            'corner': corner,
            'columns': header,
            'rows': len(rows),
            'metadata': meta_info
        }
        meta_path.write_text(json.dumps(meta_blob, indent=2))
        print(f"Wrote {csv_path}  ({len(rows)} rows)")

def main():
    if len(sys.argv) < 2:
        print("Usage: python lis2csv.py <file.lis> [outdir]")
        sys.exit(1)
    lis_path = Path(sys.argv[1])
    outdir = sys.argv[2] if len(sys.argv) >= 3 else "io33v_bk"
    lines = lis_path.read_text(errors='ignore').splitlines()
    latest = parse_lis(lines)
    write_outputs(latest, outdir)

if __name__ == '__main__':
    main()

