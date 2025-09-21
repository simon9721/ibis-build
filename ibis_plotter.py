# ibis_plotter.py
import argparse, re, math, numpy as np
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
import matplotlib.pyplot as plt

@dataclass
class TableBlock:
    index: int
    section_raw: str
    section_norm: str
    model: Optional[str]
    params: Dict[str, str] = field(default_factory=dict)
    ncols: int = 0
    data: Optional[np.ndarray] = None
    source_range: Tuple[int, int] = (0, 0)

def normalize_section(sec: str) -> str:
    return sec.strip().lower().replace(" ", "_").replace("-", "_")

PLOTTABLE_SECTIONS = {
    "pulldown","pullup",
    "gnd_clamp","ground_clamp","power_clamp",
    "rising_waveform","falling_waveform","waveform",
    "composite_current",
}

ENG = {
    'f': 1e-15, 'p': 1e-12, 'n': 1e-9, 'u': 1e-6, 'm': 1e-3,
    'k': 1e3, 'K': 1e3, 'M': 1e6, 'G': 1e9, 'T': 1e12
}

def parse_number(tok: str) -> float:
    t = tok.strip().replace(",", "")
    if t.upper() == "NA":
        return math.nan
    try:
        return float(t)             # plain / E-notation
    except ValueError:
        pass
    m = re.match(r'^([+-]?\d*\.?\d+)([fpnumkKMGTP])$', t)
    if m:
        return float(m.group(1)) * ENG[m.group(2)]
    m2 = re.match(r'^([+-]?\d*\.?\d+(?:[eE][+-]?\d+)?)([fpnumkKMGTP])$', t)
    if m2:
        return float(m2.group(1)) * ENG[m2.group(2)]
    return float(t)  # will raise if invalid

def is_num_like(token: str) -> bool:
    try:
        parse_number(token); return True
    except Exception:
        return False

def is_numeric_row(line: str) -> bool:
    toks = line.strip().split()
    if len(toks) < 2 or len(toks) > 8:
        return False
    return all(is_num_like(t) for t in toks)

def parse_header_params(line: str) -> Dict[str, str]:
    params = {}
    for tok in line.strip().split():
        if '=' in tok:
            k, v = tok.split('=', 1)
            params[k.strip()] = v.strip()
        else:
            params[tok.strip()] = "true"
    return params

def parse_ibis_tables(path: str) -> List[TableBlock]:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    blocks: List[TableBlock] = []
    section_header_re = re.compile(r'^\s*\[(.+?)\]\s*(.*)$')
    current_model: Optional[str] = None
    idx = 0; i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or line.startswith("|"):
            i += 1; continue
        m = section_header_re.match(line)
        if m:
            sec_name = m.group(1).strip()
            tail = (m.group(2) or "").strip()
            sec_norm = normalize_section(sec_name)
            if sec_norm == "model":
                current_model = tail if tail else None
                i += 1; continue
            if sec_norm in PLOTTABLE_SECTIONS:
                params = {}
                if tail: params.update(parse_header_params(tail))
                i += 1
                # read parameter lines (until numeric or new section)
                while i < len(lines):
                    peek = lines[i].strip()
                    if not peek or peek.startswith("|"):
                        i += 1; continue
                    if section_header_re.match(peek) or is_numeric_row(peek):
                        break
                    params.update(parse_header_params(peek)); i += 1
                # collect numeric chunks
                while i < len(lines):
                    st = lines[i].strip()
                    if not st or st.startswith("|"):
                        i += 1; continue
                    if section_header_re.match(st): break
                    if not is_numeric_row(st):
                        i += 1; continue
                    start = i; rows = []; ncols_expected = None
                    while i < len(lines) and is_numeric_row(lines[i]):
                        parts = lines[i].strip().split()
                        if ncols_expected is None: ncols_expected = len(parts)
                        if len(parts) == ncols_expected:
                            rows.append(parts)
                        i += 1
                    if rows:
                        arr = np.array([[parse_number(x) for x in row] for row in rows], dtype=float)
                        idx += 1
                        blocks.append(TableBlock(
                            index=idx, section_raw=sec_name, section_norm=sec_norm,
                            model=current_model, params=params.copy(),
                            ncols=arr.shape[1], data=arr, source_range=(start+1, i)
                        ))
                continue
        i += 1
    return blocks

def axis_hint(section_norm: str) -> str:
    if section_norm in {"pulldown","pullup","gnd_clamp","ground_clamp","power_clamp"}:
        return "V-I"
    if "waveform" in section_norm:
        return "T-V"
    if section_norm == "composite_current":
        return "I-T"
    return "data"

def plot_block(b: TableBlock):
    x = b.data[:, 0]
    plt.figure(figsize=(7,5))
    labels_guess = ["typ", "min", "max"]
    if b.ncols == 2:
        plt.plot(x, b.data[:,1], marker='o', label='Series 1')
    else:
        for idx_col in range(1, b.ncols):
            lab = labels_guess[idx_col-1] if idx_col-1 < len(labels_guess) else f"col{idx_col+1}"
            plt.plot(x, b.data[:, idx_col], marker='o', label=lab)
    if b.section_norm in {"pulldown","pullup","gnd_clamp","ground_clamp","power_clamp"}:
        plt.xlabel("Voltage (V)"); plt.ylabel("Current (A)")
    elif "waveform" in b.section_norm:
        plt.xlabel("Time (s)"); plt.ylabel("Voltage (V)")
    elif b.section_norm == "composite_current":
        plt.xlabel("Time (s)"); plt.ylabel("Current (A)")
    else:
        plt.xlabel("X"); plt.ylabel("Y")
    plt.title(f"{b.section_raw} — Model: {b.model}")
    plt.grid(True); plt.legend()

def parse_indices(text: str, max_idx: int) -> List[int]:
    """
    Accepts formats like: '2', '2,5,8', '3-7', '3-7,11,15-18'.
    Filters out-of-range indices silently; de-duplicates.
    """
    nums: List[int] = []
    parts = re.split(r'[,\s]+', text.strip())
    for p in parts:
        if not p: continue
        if '-' in p:
            lo, hi = p.split('-', 1)
            try:
                lo_i = int(lo); hi_i = int(hi)
                if lo_i > hi_i: lo_i, hi_i = hi_i, lo_i
                for k in range(lo_i, hi_i+1):
                    if 1 <= k <= max_idx: nums.append(k)
            except ValueError:
                continue
        else:
            try:
                k = int(p)
                if 1 <= k <= max_idx: nums.append(k)
            except ValueError:
                continue
    # de-duplicate preserving order
    seen = set(); out = []
    for k in nums:
        if k not in seen:
            seen.add(k); out.append(k)
    return out

def main():
    ap = argparse.ArgumentParser(description="IBIS plotter: V-I, T-V, Composite Current. Supports multiple indices and interactive loop.")
    ap.add_argument("--file","-f", required=True, help="Path to .ibs file")
    ap.add_argument("--index","-i", help="Indices to plot initially (e.g. '2,5-7'). Optional.")
    args = ap.parse_args()

    blocks = parse_ibis_tables(args.file)
    if not blocks:
        print("No plottable tables found."); return

    print("\nFound tables:\n")
    for b in blocks:
        print(f"[{b.index}] {b.section_raw} | Model={b.model or '(no model)'} | cols={b.ncols} | {axis_hint(b.section_norm)} | lines {b.source_range[0]}–{b.source_range[1]}")

    # Helper to plot a set of indices in one go (separate figures), then show
    def plot_indices(idx_list: List[int]):
        id_to_block = {b.index: b for b in blocks}
        valid = [id_to_block[i] for i in idx_list if i in id_to_block]
        if not valid:
            print("No valid indices to plot.")
            return
        for b in valid:
            plot_block(b)
        plt.show()  # blocks until figures are closed

    # First batch from CLI if provided
    if args.index:
        first = parse_indices(args.index, max_idx=len(blocks))
        if not first:
            print("No valid indices in --index.")
        else:
            plot_indices(first)

    # Interactive loop
    while True:
        inp = input("\nEnter indices to plot (e.g. 44,108 or 37-40), or 'q' to quit: ").strip()
        if inp.lower() in {"q", "quit", "exit"}:
            print("Bye."); break
        idxs = parse_indices(inp, max_idx=len(blocks))
        if not idxs:
            print("No valid indices parsed. Try again.")
            continue
        plot_indices(idxs)

if __name__ == "__main__":
    main()
