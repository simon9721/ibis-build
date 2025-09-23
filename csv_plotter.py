#!/usr/bin/env python3
"""
csv_plotter.py — Plot CSV data into separate figures (one figure per file).

Usage examples:
  # Basic: first column is X, others are Y series (auto legend from headers)
  python csv_plotter.py --files iv_max.csv vt_typ.csv

  # Specify columns explicitly (by name or 0-based index)
  python csv_plotter.py --files iv_max.csv --xcol V --ycols I_typ I_min I_max

  # Overlay multiple files in one figure (matched columns by name or position)
  python csv_plotter.py --files vt_typ.csv iv_max.csv --overlay

  # Set log scales
  python csv_plotter.py --files iv_max.csv --logy

  # Keep the app open for more plots interactively
  python csv_plotter.py --files iv_max.csv --interactive

Notes:
- Lines starting with '#' are treated as comments and ignored.
- Whitespace after commas is fine; we also handle CSVs with spaces-only delimiters.
- If the CSV has no header row, columns will be named C0, C1, C2, ...
"""

import argparse
import os
import sys
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

def guess_delimiter(path):
    # Try comma, then whitespace
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        head = ''.join([next(f, '') for _ in range(10)])
    # Heuristic: if any line contains a comma-separated pattern, choose comma
    if any(',' in line for line in head.splitlines() if line.strip() and not line.strip().startswith('#')):
        return ','
    return None  # None -> pandas will infer whitespace with delim_whitespace=True

def read_csv_flexible(path, has_header=True, comment='#', skiprows=0):
    delim = guess_delimiter(path)
    if has_header:
        if delim == ',':
            df = pd.read_csv(path, comment=comment, skiprows=skiprows, skipinitialspace=True, engine='python')
        else:
            df = pd.read_csv(path, comment=comment, skiprows=skiprows, skipinitialspace=True,
                             delim_whitespace=True, engine='python')
    else:
        if delim == ',':
            df = pd.read_csv(path, header=None, comment=comment, skiprows=skiprows, skipinitialspace=True, engine='python')
        else:
            df = pd.read_csv(path, header=None, comment=comment, skiprows=skiprows, skipinitialspace=True,
                             delim_whitespace=True, engine='python')
        df.columns = [f"C{i}" for i in range(len(df.columns))]
    # Strip column names
    df.columns = [str(c).strip() for c in df.columns]
    return df

def select_columns(df, xcol=None, ycols=None):
    cols = list(df.columns)
    # Convert potential numeric indices to names
    def col_to_name(c):
        if c is None: return None
        if isinstance(c, int): return cols[c]
        # try int-like string
        try:
            idx = int(c)
            return cols[idx]
        except Exception:
            pass
        # match case-insensitively
        lc = [cc for cc in cols if str(cc).strip().lower() == str(c).strip().lower()]
        return lc[0] if lc else c  # may raise later if missing
    xname = col_to_name(xcol) if xcol is not None else cols[0]
    if xname not in df.columns:
        raise ValueError(f"X column '{xname}' not found. Available: {cols}")
    if ycols:
        ynames = [col_to_name(y) for y in ycols]
        for y in ynames:
            if y not in df.columns:
                raise ValueError(f"Y column '{y}' not found. Available: {cols}")
    else:
        # default: all numeric except x
        numeric_cols = [c for c in cols if pd.api.types.is_numeric_dtype(df[c])]
        ynames = [c for c in numeric_cols if c != xname]
        # Fallback: if none numeric recognized (e.g., strings with 'e' parsed), try to coerce
        if not ynames:
            for c in cols:
                if c == xname: continue
                try:
                    pd.to_numeric(df[c])
                    ynames.append(c)
                except Exception:
                    pass
    return xname, ynames

def axis_labels_from_names(xname, ynames):
    def pretty(name):
        s = str(name)
        s = s.replace('_', ' ')
        return s
    # Guess axis units
    xl = pretty(xname)
    yl = "Value"
    lower = ' '.join([n.lower() for n in ynames])
    if 'current' in lower or '(a' in lower or lower.startswith('i(') or lower.startswith('i_') or lower.startswith('i '):
        yl = "Current (A)"
    elif 'voltage' in lower or '(v' in lower or lower.startswith('v(') or lower.startswith('v_') or lower.startswith('v '):
        yl = "Voltage (V)"
    return xl, yl

def plot_file(path, args, overlay_ax=None):
    df = read_csv_flexible(path, has_header=not args.no_header, comment=args.comment, skiprows=args.skiprows)
    xname, ynames = select_columns(df, xcol=args.xcol, ycols=args.ycols)
    # Convert to numeric (coerce errors to NaN) then drop NaNs row-wise for plotting stability
    x = pd.to_numeric(df[xname], errors='coerce')
    series_list = []
    for y in ynames:
        yv = pd.to_numeric(df[y], errors='coerce')
        valid = x.notna() & yv.notna()
        series_list.append((y, x[valid].to_numpy(), yv[valid].to_numpy()))
    # Prepare axes
    if overlay_ax is None:
        fig = plt.figure(figsize=(7,5))
        ax = fig.add_subplot(111)
    else:
        ax = overlay_ax
    for label, xv, yv in series_list:
        ax.plot(xv, yv, marker='o', label=label)
    xl, yl = axis_labels_from_names(xname, ynames)
    ax.set_xlabel(xl)
    if not args.ylabel:
        ax.set_ylabel(yl)
    else:
        ax.set_ylabel(args.ylabel)
    title = os.path.basename(path)
    if args.title: title = args.title
    ax.set_title(title)
    ax.grid(True)
    ax.legend()
    if args.logx: ax.set_xscale('log')
    if args.logy: ax.set_yscale('log')
    return ax

def main():
    parser = argparse.ArgumentParser(description="Plot CSV files into separate figures (or overlay).")
    parser.add_argument("--files", "-f", nargs="+", required=True, help="CSV file paths.")
    parser.add_argument("--xcol", help="X column name or 0-based index. Default: first column.")
    parser.add_argument("--ycols", nargs="+", help="Y column names or indices. Default: all numeric except X.")
    parser.add_argument("--no-header", action="store_true", help="Treat CSV as having no header row.")
    parser.add_argument("--comment", default="#", help="Comment prefix to ignore lines. Default: '#'")
    parser.add_argument("--skiprows", type=int, default=0, help="Rows to skip at the top (in addition to comments).")
    parser.add_argument("--overlay", action="store_true", help="Overlay all files in one figure instead of separate figures.")
    parser.add_argument("--logx", action="store_true", help="Use logarithmic X scale.")
    parser.add_argument("--logy", action="store_true", help="Use logarithmic Y scale.")
    parser.add_argument("--ylabel", help="Force Y-axis label text.")
    parser.add_argument("--title", help="Override plot title (single figure or overlay).")
    parser.add_argument("--interactive", "-i", action="store_true", help="After plotting, prompt for more files to plot.")
    args = parser.parse_args()

    if not args.overlay:
        # One figure per file
        for p in args.files:
            ax = plot_file(p, args)
        plt.show()
    else:
        # Overlay all in one
        fig = plt.figure(figsize=(7,5))
        ax = fig.add_subplot(111)
        for p in args.files:
            plot_file(p, args, overlay_ax=ax)
        plt.show()

    if args.interactive:
        while True:
            try:
                user_in = input("\nEnter CSV path(s) to plot (space-separated), or 'q' to quit: ").strip()
            except EOFError:
                break
            if user_in.lower() in {"q","quit","exit"}:
                print("Bye.")
                break
            if not user_in:
                continue
            more = user_in.split()
            # Plot new batch same way user chose originally (overlay vs separate)
            if not args.overlay:
                for p in more:
                    try:
                        plot_file(p, args)
                    except Exception as e:
                        print(f"Error: {e}")
                plt.show()
            else:
                fig = plt.figure(figsize=(7,5))
                ax = fig.add_subplot(111)
                for p in more:
                    try:
                        plot_file(p, args, overlay_ax=ax)
                    except Exception as e:
                        print(f"Error: {e}")
                plt.show()

if __name__ == "__main__":
    main()
