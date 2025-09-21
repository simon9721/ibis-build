#!/usr/bin/env python3
"""
harvest.py — Extract tabular data from HSPICE .lis files into CSVs.

Features
- Scans the entire .lis, extracting each data table to its own CSV.
- Recognizes analysis blocks (e.g., "transient analysis") to name files.
- Parses columnar tables with headers (e.g., 'time  v_sweep  i_pulldown ...').
- Parses unlabeled numeric blocks (e.g., bare numeric matrices, lines may be inside parentheses).
- Parses "operating point information" node-voltage listings into a CSV (Node,Value).
- Tolerates continuation lines and ragged rows (pads or trims to header length).
- No plotting, no JSON — just CSV files.

Usage
------
python harvest.py INPUT.lis -o out_dir
"""
import argparse
import csv
import os
import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

# ---------- Utilities ----------

def slugify(text: str, max_len: int = 64) -> str:
    text = text.strip().lower()
    text = re.sub(r'[^a-z0-9._-]+', '-', text)
    text = re.sub(r'-{2,}', '-', text).strip('-')
    return text[:max_len] if text else 'data'

def is_number_token(tok: str) -> bool:
    tok = tok.strip()
    if not tok:
        return False
    # Accept Fortran 'D' exponents by converting to E
    t = tok.replace('D', 'E').replace('d', 'E')
    # lone '.' or '-' etc. shouldn't pass
    try:
        float(t)
        return True
    except ValueError:
        return False

def split_cols(line: str) -> List[str]:
    # Split on 2+ spaces or tabs to keep aligned columns
    return re.split(r'[ \t]{2,}', line.strip())

def looks_like_header(line: str) -> bool:
    # Heuristic: at least 2 non-numeric tokens separated by 2+ spaces, and no '=' (to exclude op-point lines)
    if '=' in line:
        return False
    cols = split_cols(line)
    if len(cols) < 2:
        return False
    # Header tokens should not be purely numeric; allow underscores and letters
    score = sum(1 for c in cols if not is_number_token(c))
    return score >= 2

def tokens_from_numeric_line(line: str) -> Optional[List[str]]:
    if not line.strip():
        return None
    # Allow lines that begin with a number (possibly signed, decimal, or scientific), then columns
    # Strip leading '+' used by HSPICE for continuation of different sections.
    if line.lstrip().startswith('+'):
        return None
    # Remove enclosing parentheses that may prefix a numeric block
    s = line.strip()
    if s == '(' or s == ')':
        return None
    # Single 'x' or 'y' or other one-letter markers should not be parsed
    if re.fullmatch(r'[A-Za-z]$', s):
        return None
    # Split and check if majority are numbers
    cols = re.split(r'[ \t]+', s)
    if not cols or not is_number_token(cols[0]):
        return None
    # If at least half tokens look numeric, accept row
    num_count = sum(1 for t in cols if is_number_token(t))
    if num_count >= max(1, len(cols) // 2):
        return cols
    return None

def parse_number(tok: str) -> str:
    # Normalize Fortran 'D' to 'E' but return as the original string normalized
    return tok.replace('D', 'E').replace('d', 'E')

# ---------- Parsers ----------

class TableBlock:
    def __init__(self, headers: List[str], context: str, index: int):
        self.headers = headers[:]  # list of strings
        self.rows: List[List[str]] = []
        self.context = context
        self.index = index

    def add_row(self, row: List[str]):
        # Normalize row to header length (pad or trim)
        if len(row) < len(self.headers):
            row = row + [''] * (len(self.headers) - len(row))
        elif len(row) > len(self.headers):
            row = row[:len(self.headers)]
        # Normalize numbers
        row = [parse_number(c) for c in row]
        self.rows.append(row)

    def filename(self) -> str:
        base = ''
        if self.context:
            base = slugify(self.context, max_len=48)
        # Use first few headers to differentiate
        hdr_id = slugify('-'.join(self.headers[:3]), max_len=48)
        stem = f'{self.index:03d}_{base}_{hdr_id}' if base else f'{self.index:03d}_{hdr_id}'
        return f'{stem}.csv'

    def write_csv(self, outdir: Path):
        outdir.mkdir(parents=True, exist_ok=True)
        path = outdir / self.filename()
        with path.open('w', newline='') as f:
            w = csv.writer(f)
            w.writerow(self.headers)
            for r in self.rows:
                w.writerow(r)
        return path

class NumericMatrixBlock:
    """For unlabeled numeric blocks (no headers)."""
    def __init__(self, context: str, index: int):
        self.rows: List[List[str]] = []
        self.context = context
        self.index = index

    def add_row(self, cols: List[str]):
        row = [parse_number(c) for c in cols]
        self.rows.append(row)

    def filename(self) -> str:
        base = slugify(self.context, max_len=48) if self.context else 'matrix'
        return f'{self.index:03d}_{base}_matrix.csv'

    def write_csv(self, outdir: Path):
        outdir.mkdir(parents=True, exist_ok=True)
        # Determine max column count to pad rows
        ncols = max((len(r) for r in self.rows), default=0)
        headers = [f'col{i+1}' for i in range(ncols)]
        path = outdir / self.filename()
        with path.open('w', newline='') as f:
            w = csv.writer(f)
            w.writerow(headers)
            for r in self.rows:
                if len(r) < ncols:
                    r = r + [''] * (ncols - len(r))
                w.writerow(r)
        return path

def parse_operating_point(lines: List[str], start_idx: int) -> Tuple[dict, int]:
    """
    Parses node=voltage listing after 'operating point information'.
    Returns (dict of node->value, next_index_after_section)
    """
    values = {}
    i = start_idx
    # Skip lines until we encounter lines containing one or more "name = value" pairs.
    pair_re = re.compile(r'([0-9A-Za-z_:\.\+\-]+)\s*=\s*([\-+0-9\.EeDd]+)')
    while i < len(lines):
        s = lines[i].strip()
        if not s:
            break
        if s.startswith('******') or s.startswith('*****') or 'analysis' in s.lower():
            break
        # Continuation lines that begin with '+' still contain pairs
        if s.startswith('+'):
            s = s[1:].strip()
        # Collect all pairs on the line
        found = list(pair_re.finditer(s))
        if found:
            for m in found:
                name = m.group(1)
                val = parse_number(m.group(2))
                values[name] = val
            i += 1
            continue
        # If the line doesn't contain pairs and doesn't look like a header, end
        # Stop at first non-pair line
        break
        i += 1
    return values, i

# ---------- Main scan ----------

def harvest(lis_path: Path, outdir: Path) -> List[Path]:
    with lis_path.open('r', errors='ignore') as f:
        lines = f.read().splitlines()

    written: List[Path] = []

    current_context = ''  # e.g., 'transient analysis', 'dc analysis', etc.
    i = 0
    table_index = 1

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Track analysis/context
        if 'analysis' in stripped.lower():
            # Capture like "transient analysis               tnom= ..."
            m = re.search(r'([A-Za-z ]+analysis)', stripped, flags=re.IGNORECASE)
            if m:
                current_context = m.group(1).strip()
        elif 'operating point information' in stripped.lower():
            current_context = 'operating point information'
            # Try to parse op-point block starting from next line(s)
            # Skip potential headings lines following
            j = i + 1
            # Skip status line: "***** operating point status is voltage   simulation time is     0."
            while j < len(lines) and ('=' not in lines[j] and not lines[j].lstrip().startswith('+') and lines[j].strip()):
                # If a line contains '=' we likely hit data, so stop skipping
                if 'node' in lines[j].lower() and '=' not in lines[j]:
                    j += 1
                    continue
                if lines[j].strip().startswith('*') or lines[j].strip().startswith('+'):
                    j += 1
                    continue
                # If it's a "node = voltage" header line, skip
                j += 1
            values, nxt = parse_operating_point(lines, j)
            if values:
                # Write a CSV
                headers = ['Node', 'Value']
                tbl = TableBlock(headers, current_context, table_index)
                for k, v in values.items():
                    tbl.add_row([k, v])
                path = tbl.write_csv(outdir)
                written.append(path)
                table_index += 1
            i = max(i + 1, nxt)
            continue

        # Detect a classic column header, followed by numeric rows
        if looks_like_header(line):
            headers = split_cols(line)
            # Look ahead for data rows; allow a blank line between header and data
            j = i + 1
            # Skip a single blank line
            if j < len(lines) and not lines[j].strip():
                j += 1
            # If next line isn't numeric-ish, this isn't a table; advance
            if j >= len(lines) or tokens_from_numeric_line(lines[j]) is None:
                i += 1
                continue
            # Collect rows until a non-numeric line or empty or next header
            tbl = TableBlock(headers, current_context, table_index)
            while j < len(lines):
                row_tokens = tokens_from_numeric_line(lines[j])
                if row_tokens is None:
                    break
                tbl.add_row(row_tokens)
                j += 1
            path = tbl.write_csv(outdir)
            written.append(path)
            table_index += 1
            i = j
            continue

        # Detect unlabeled numeric matrix blocks (e.g., inside parentheses)
        # Start at a line that is '(' or a numeric line with many numbers,
        # and proceed until a terminator.
        if stripped == '(':
            # Start matrix after '('
            j = i + 1
            block = NumericMatrixBlock(current_context or 'unlabeled', table_index)
            any_rows = False
            while j < len(lines):
                s = lines[j].strip()
                if s == ')' or re.fullmatch(r'[A-Za-z]$', s) or s.startswith('******'):
                    # end of block
                    j += 1
                    break
                toks = tokens_from_numeric_line(lines[j])
                if toks is None:
                    # if we encounter a non-numeric line, end the matrix
                    if any_rows:
                        break
                    else:
                        j += 1
                        continue
                block.add_row(toks)
                any_rows = True
                j += 1
            if any_rows:
                path = block.write_csv(outdir)
                written.append(path)
                table_index += 1
            i = j
            continue
        else:
            # Might still be a plain numeric line starting a matrix
            toks0 = tokens_from_numeric_line(line)
            if toks0 is not None and len(toks0) >= 2:
                # Verify we have consecutive numeric lines to justify a matrix block
                j = i + 1
                consecutive = 1
                while j < len(lines) and tokens_from_numeric_line(lines[j]) is not None:
                    consecutive += 1
                    if consecutive >= 2:
                        break
                    j += 1
                if consecutive >= 2:
                    block = NumericMatrixBlock(current_context or 'unlabeled', table_index)
                    block.add_row(toks0)
                    k = i + 1
                    while k < len(lines):
                        t = tokens_from_numeric_line(lines[k])
                        if t is None:
                            break
                        block.add_row(t)
                        k += 1
                    path = block.write_csv(outdir)
                    written.append(path)
                    table_index += 1
                    i = k
                    continue

        i += 1

    return written

# ---------- CLI ----------

def main():
    p = argparse.ArgumentParser(description="Extract data tables from HSPICE .lis into CSV files.")
    p.add_argument('lis_file', type=Path, help='Path to .lis file')
    p.add_argument('-o', '--outdir', type=Path, default=Path('./lis_csv'),
                   help='Directory to write CSV files (default: ./lis_csv)')
    args = p.parse_args()

    if not args.lis_file.exists():
        sys.stderr.write(f"ERROR: Input file not found: {args.lis_file}\n")
        sys.exit(1)

    written = harvest(args.lis_file, args.outdir)

    if not written:
        sys.stderr.write("No tables found.\n")
    else:
        sys.stderr.write(f"Wrote {len(written)} CSV file(s) to {args.outdir}\n")
        for pth in written:
            sys.stderr.write(f" - {pth.name}\n")

if __name__ == '__main__':
    main()
