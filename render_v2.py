from pathlib import Path
import csv, re, sys, math
import yaml
from typing import List, Dict, Tuple, Optional
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).parent

# ---------- formatting ----------
def fmt_num(x):
    """Numeric formatter with uppercase scientific 'E'."""
    if isinstance(x, int):
        return str(x)
    try:
        v = float(x)
    except Exception:
        return str(x)
    s = f"{v:.12g}"
    return s.replace('e', 'E')

def fmt_volt(x):
    """Voltage formatter: compact with 'V' suffix."""
    try:
        v = float(x)
    except Exception:
        return str(x)
    if 1e-3 <= abs(v) < 1e6:
        return f"{v:.2f}V"
    return f"{v:.6G}".replace('e', 'E') + "V"

def fmt_ohm(x):
    try:
        v = float(x)
    except Exception:
        return str(x)
    return f"{v:.2f}Ohm"

def fmt_mv_ns_pair(dv_volts: float, dt_seconds: float) -> str:
    """Format like '670.918mV/1.773ns'."""
    return f"{dv_volts*1e3:.3f}mV/{dt_seconds*1e9:.3f}ns"

# ---------- helpers / primitives ----------
def _read_csv_as_dicts(path: Path) -> List[Dict[str, str]]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        rdr = csv.DictReader([l for l in f.read().splitlines() if l.strip()])
        for r in rdr:
            rows.append({(k or "").strip(): (v or "").strip() for k, v in r.items()})
    return rows

def _must_float(v: str) -> float:
    # tolerate Fortran 'D' exponents if they appear
    return float(v.replace("D", "E"))

# Linear interpolation helper to find time when value crosses threshold
def _cross_time(ts: List[float], vs: List[float], thr: float) -> Optional[float]:
    """
    Return interpolated time where v crosses thr.
    Works for rising or falling waveforms; requires a crossing to exist.
    """
    for i in range(len(ts) - 1):
        v1, v2 = vs[i], vs[i+1]
        if (v1 - thr) == 0:
            return ts[i]
        # detect bracket (inclusive on lower side)
        if (v1 - thr) * (v2 - thr) <= 0 and v1 != v2:
            t1, t2 = ts[i], ts[i+1]
            # linear interp
            frac = (thr - v1) / (v2 - v1)
            return t1 + frac * (t2 - t1)
    return None

def is_monotonic(xs):
    return all(xs[i] < xs[i+1] for i in range(len(xs)-1))

# ---------- readers: 'already-merged' path ----------
def read_iv(path: Path):
    """
    Reads an already-merged IV CSV with schema:
      # ref: VCC|GND
      V, I_typ, I_min, I_max
    """
    txt = path.read_text(encoding="utf-8")
    m = re.search(r"#\s*ref:\s*(\w+)", txt, re.I)
    ref = m.group(1).upper() if m else None
    rows = []
    rdr = csv.DictReader(
        [l for l in txt.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    )
    for r in rdr:
        rows.append({
            "V": float(r["V"]),
            "typ": float(r["I_typ"]),
            "min": float(r["I_min"]),
            "max": float(r["I_max"]),
        })
    return ref, rows

def read_vt(path: Path):
    """
    Reads an already-merged VT CSV with schema:
      # kind: rising|falling
      # R_fixture: <ohms>
      # V_fixture: <volts>
      t, V_typ, V_min, V_max
    """
    txt = path.read_text(encoding="utf-8")
    kind = re.search(r"#\s*kind:\s*(\w+)", txt, re.I).group(1).lower()
    Rf  = float(re.search(r"#\s*R_fixture:\s*([\d.eE+-]+)", txt).group(1))
    Vf  = float(re.search(r"#\s*V_fixture:\s*([\d.eE+-]+)", txt).group(1))
    rows = []
    rdr = csv.DictReader(
        [l for l in txt.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    )
    for r in rdr:
        rows.append({
            "t": float(r["t"]),
            "typ": float(r["V_typ"]),
            "min": float(r["V_min"]),
            "max": float(r["V_max"]),
        })
    return {"kind": kind, "R": Rf, "V": Vf, "points": rows}

def read_ramp_yaml(path: Path):
    """Read ramp.yml; return dict with dvdt_r/dvdt_f in V/s."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "dvdt_r" not in data or "dvdt_f" not in data:
        raise ValueError(f"Invalid ramp.yml format in {path}")
    return data

def to_v_per_ns(val_in_v_per_s):
    return float(val_in_v_per_s) / 1e9

# ---------- readers: NEW 'triplet' auto-merge path ----------
def read_iv_triplet(model_path: Path) -> Dict[str, Tuple[str, List[Dict[str, float]]]]:
    """
    Auto-merge iv_typ/max/min.csv → 4 IV blocks with refs:
      pullup (VCC), pulldown (GND), power_clamp (VCC), ground_clamp (GND)
    Assumes HSPICE headings:
      time, v_sweep, i_pulldown, i_gndclamp, i_pullup, i_powerclamp
    """
    bases = {c: _read_csv_as_dicts(model_path / f"iv_{c}.csv") for c in ("typ", "min", "max")}

    def pack(col_name: str) -> List[Dict[str, float]]:
        out = []
        for i in range(len(bases["typ"])):
            V = _must_float(bases["typ"][i]["v_sweep"])
            out.append({
                "V": V,
                "typ": _must_float(bases["typ"][i][col_name]),
                "min": _must_float(bases["min"][i][col_name]),
                "max": _must_float(bases["max"][i][col_name]),
            })
        out.sort(key=lambda r: r["V"])
        return out

    return {
        "pullup":      ("VCC", pack("i_pullup")),
        "pulldown":    ("GND", pack("i_pulldown")),
        "power_clamp": ("VCC", pack("i_powerclamp")),
        "ground_clamp":("GND", pack("i_gndclamp")),
    }

def read_vt_triplet(model_path: Path, v_fixture: float, r_fixture: float) -> Tuple[List[Dict], List[Dict], Dict]:
    """
    Auto-merge vt_typ/max/min.csv → 2 waveform sets (rising/falling) and *computed* ramp.
    Default mapping (adjust VT_MAP if your columns differ):
      Rising  := pullup_on
      Falling := pulldown_on
    Assumes HSPICE headings:
      time, pulldown_on, pulldown_off, pullup_on, pullup_off
    """
    VT_MAP = {
        "rising_col":  "pullup_on",
        "falling_col": "pulldown_on",
    }
    bases = {c: _read_csv_as_dicts(model_path / f"vt_{c}.csv") for c in ("typ", "min", "max")}

    # Build waveform points for template
    def build_points(col: str) -> List[Dict[str, float]]:
        pts = []
        for i in range(len(bases["typ"])):
            pts.append({
                "t":   _must_float(bases["typ"][i]["time"]),
                "typ": _must_float(bases["typ"][i][col]),
                "min": _must_float(bases["min"][i][col]),
                "max": _must_float(bases["max"][i][col]),
            })
        pts.sort(key=lambda p: p["t"])
        return pts

    rising_points = build_points(VT_MAP["rising_col"])
    falling_points = build_points(VT_MAP["falling_col"])

    rising = [{
        "R": r_fixture,
        "V": v_fixture,
        "points": rising_points,
    }]
    falling = [{
        "R": r_fixture,
        "V": v_fixture,
        "points": falling_points,
    }]

    # ---- Compute Ramp (20–80%) from the first rising/falling set ----
    ramp = compute_ramp_from_points(rising_points, falling_points, v_fixture, r_fixture)

    return rising, falling, ramp

# ---------- ramp from waveforms (20–80%) ----------
def compute_ramp_from_points(
    rising_pts: List[Dict[str, float]],
    falling_pts: List[Dict[str, float]],
    v_fixture: float,
    r_fixture: float
) -> Dict:
    """
    Compute dv/dt using 20%–80% of V_fixture for typ/min/max on both rising & falling.
    Returns structure ready for template consumption, including pretty strings.
    """
    def extract_series(points: List[Dict[str, float]], key: str) -> Tuple[List[float], List[float]]:
        ts = [p["t"] for p in points]
        vs = [p[key] for p in points]
        return ts, vs

    v20 = 0.2 * v_fixture
    v80 = 0.8 * v_fixture
    dV = v80 - v20  # same for rise/fall, typ/min/max

    dvdt = {"r": {}, "f": {}}
    # For each corner, interpolate crossing times
    for corner in ("typ", "min", "max"):
        # Rising
        tr_ts, tr_vs = extract_series(rising_pts, corner)
        t20_r = _cross_time(tr_ts, tr_vs, v20)
        t80_r = _cross_time(tr_ts, tr_vs, v80)
        if t20_r is None or t80_r is None or t80_r <= t20_r:
            # fallback robustly if needed
            dvdt["r"][corner] = {"dv": dV, "dt": float("nan"), "v_per_ns": float("nan")}
        else:
            dt = t80_r - t20_r
            dvdt["r"][corner] = {"dv": dV, "dt": dt, "v_per_ns": dV / dt / 1e9}

        # Falling (80% -> 20%)
        tf_ts, tf_vs = extract_series(falling_pts, corner)
        t80_f = _cross_time(tf_ts, tf_vs, v80)
        t20_f = _cross_time(tf_ts, tf_vs, v20)
        # For a falling edge v decreases, the 80% crossing should come before 20%
        if t20_f is None or t80_f is None or t20_f <= t80_f:
            dvdt["f"][corner] = {"dv": dV, "dt": float("nan"), "v_per_ns": float("nan")}
        else:
            dt = t20_f - t80_f
            dvdt["f"][corner] = {"dv": dV, "dt": dt, "v_per_ns": dV / dt / 1e9}

    # Build template-friendly structure
    def pack_row(d):
        return {
            "typ_v_per_ns": d["typ"]["v_per_ns"],
            "min_v_per_ns": d["min"]["v_per_ns"],
            "max_v_per_ns": d["max"]["v_per_ns"],
            # pretty strings like '670.918mV/1.773ns'
            "typ_str": fmt_mv_ns_pair(d["typ"]["dv"], d["typ"]["dt"]),
            "min_str": fmt_mv_ns_pair(d["min"]["dv"], d["min"]["dt"]),
            "max_str": fmt_mv_ns_pair(d["max"]["dv"], d["max"]["dt"]),
        }

    ramp = {
        "r_load_ohm": r_fixture,
        "dvdt_r": pack_row(dvdt["r"]),
        "dvdt_f": pack_row(dvdt["f"]),
    }
    return ramp

# ---------- pins ----------
def read_pins_three_or_six(root: Path):
    """Read pins.csv that may contain 3 or 6 columns; normalize to 6 in-memory."""
    pins = []
    with open(root / "pins.csv", "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            parts = [x.strip() for x in s.split(",")]
            if len(parts) < 3:
                raise ValueError(f"pins.csv line needs at least 3 columns: {s}")
            pin, signal, model = parts[:3]
            r_pin = float(parts[3]) if len(parts) > 3 and parts[3] != "" else 0.0
            l_pin = float(parts[4]) if len(parts) > 4 and parts[4] != "" else 0.0
            c_pin = float(parts[5]) if len(parts) > 5 and parts[5] != "" else 0.0
            pins.append({
                "pin": pin,
                "signal": signal,
                "model": model,
                "r_pin": r_pin,
                "l_pin": l_pin,
                "c_pin": c_pin,
            })
    return pins

# ---------- top-level model/component context ----------
def read_component(root: Path):
    cfg = yaml.safe_load((root / "component.yml").read_text(encoding="utf-8"))

    # Ensure voltage range exists
    for k in ("v_min", "v_typ", "v_max"):
        if k not in cfg.get("meta", {}):
            raise ValueError(f"meta.{k} missing in component.yml")

    pins = read_pins_three_or_six(root)

    # VT fixture defaults (override in component.yml -> component.vt_defaults)
    vt_defaults = (cfg.get("component", {}).get("vt_defaults") or {})
    vt_R = float(vt_defaults.get("R_fixture", 50.0))
    vt_V = float(vt_defaults.get("V_fixture", cfg["meta"]["v_typ"]))


    models_ctx = []
    for m in cfg["models"]:
        mp = root / "models" / m["name"]

        # Per-model on/off switch for emitting V-T waveform tables (default: ON)
        include_vt = bool(m.get("include_vt", True))

        # ---- Path 1: already-merged CSVs present (pullup.csv, pulldown.csv, ...) ----
        merged_present = (mp / "pullup.csv").exists()
        if merged_present:
            pu_ref, pu = read_iv(mp / "pullup.csv")
            pd_ref, pd = read_iv(mp / "pulldown.csv")
            pc_ref, pc = read_iv(mp / "power_clamp.csv")
            gc_ref, gc = read_iv(mp / "ground_clamp.csv")

            # V-T waveforms listed explicitly (only load if include_vt)
            rising, falling = [], []
            if include_vt:
                for fn in m.get("vt_sets", {}).get("rising", []):
                    rising.append(read_vt(mp / fn))
                for fn in m.get("vt_sets", {}).get("falling", []):
                    falling.append(read_vt(mp / fn))

            # Ramp: from first waveform pair if present; else ramp.yml; else zeros
            if include_vt and rising and falling:
                ramp = compute_ramp_from_points(
                    rising[0]["points"], falling[0]["points"], rising[0]["V"], rising[0]["R"]
                )
            else:
                try:
                    ramp_raw = read_ramp_yaml(mp / "ramp.yml")
                    ramp = {
                        "r_load_ohm": vt_R,
                        "dvdt_r": {
                            "typ_v_per_ns": to_v_per_ns(ramp_raw["dvdt_r"]["typ"]),
                            "min_v_per_ns": to_v_per_ns(ramp_raw["dvdt_r"]["min"]),
                            "max_v_per_ns": to_v_per_ns(ramp_raw["dvdt_r"]["max"]),
                            "typ_str": fmt_mv_ns_pair(0.6*vt_V, 0.6*vt_V / float(ramp_raw["dvdt_r"]["typ"])),
                            "min_str": fmt_mv_ns_pair(0.6*vt_V, 0.6*vt_V / float(ramp_raw["dvdt_r"]["min"])),
                            "max_str": fmt_mv_ns_pair(0.6*vt_V, 0.6*vt_V / float(ramp_raw["dvdt_r"]["max"])),
                        },
                        "dvdt_f": {
                            "typ_v_per_ns": to_v_per_ns(ramp_raw["dvdt_f"]["typ"]),
                            "min_v_per_ns": to_v_per_ns(ramp_raw["dvdt_f"]["min"]),
                            "max_v_per_ns": to_v_per_ns(ramp_raw["dvdt_f"]["max"]),
                            "typ_str": fmt_mv_ns_pair(0.6*vt_V, 0.6*vt_V / float(ramp_raw["dvdt_f"]["typ"])),
                            "min_str": fmt_mv_ns_pair(0.6*vt_V, 0.6*vt_V / float(ramp_raw["dvdt_f"]["min"])),
                            "max_str": fmt_mv_ns_pair(0.6*vt_V, 0.6*vt_V / float(ramp_raw["dvdt_f"]["max"])),
                        },
                    }
                except Exception:
                    ramp = {
                        "r_load_ohm": vt_R,
                        "dvdt_r": {"typ_v_per_ns": 0.0, "min_v_per_ns": 0.0, "max_v_per_ns": 0.0,
                                   "typ_str": "0.000mV/0.000ns", "min_str": "0.000mV/0.000ns", "max_str": "0.000mV/0.000ns"},
                        "dvdt_f": {"typ_v_per_ns": 0.0, "min_v_per_ns": 0.0, "max_v_per_ns": 0.0,
                                   "typ_str": "0.000mV/0.000ns", "min_str": "0.000mV/0.000ns", "max_str": "0.000mV/0.000ns"},
                    }

        else:
            # ---- Path 2: triplet auto-merge (iv_*.csv + vt_*.csv) ----
            iv_all = read_iv_triplet(mp)
            pu_ref, pu = iv_all["pullup"]
            pd_ref, pd = iv_all["pulldown"]
            pc_ref, pc = iv_all["power_clamp"]
            gc_ref, gc = iv_all["ground_clamp"]

            if include_vt:
                # Auto-derive V-T waveforms and computed ramp
                rising, falling, ramp = read_vt_triplet(mp, v_fixture=vt_V, r_fixture=vt_R)
            else:
                # Suppress V-T; keep only Ramp (from ramp.yml if available)
                rising, falling = [], []
                try:
                    ramp_raw = read_ramp_yaml(mp / "ramp.yml")
                    ramp = {
                        "r_load_ohm": vt_R,
                        "dvdt_r": {
                            "typ_v_per_ns": to_v_per_ns(ramp_raw["dvdt_r"]["typ"]),
                            "min_v_per_ns": to_v_per_ns(ramp_raw["dvdt_r"]["min"]),
                            "max_v_per_ns": to_v_per_ns(ramp_raw["dvdt_r"]["max"]),
                            "typ_str": fmt_mv_ns_pair(0.6*vt_V, 0.6*vt_V / float(ramp_raw["dvdt_r"]["typ"])),
                            "min_str": fmt_mv_ns_pair(0.6*vt_V, 0.6*vt_V / float(ramp_raw["dvdt_r"]["min"])),
                            "max_str": fmt_mv_ns_pair(0.6*vt_V, 0.6*vt_V / float(ramp_raw["dvdt_r"]["max"])),
                        },
                        "dvdt_f": {
                            "typ_v_per_ns": to_v_per_ns(ramp_raw["dvdt_f"]["typ"]),
                            "min_v_per_ns": to_v_per_ns(ramp_raw["dvdt_f"]["min"]),
                            "max_v_per_ns": to_v_per_ns(ramp_raw["dvdt_f"]["max"]),
                            "typ_str": fmt_mv_ns_pair(0.6*vt_V, 0.6*vt_V / float(ramp_raw["dvdt_f"]["typ"])),
                            "min_str": fmt_mv_ns_pair(0.6*vt_V, 0.6*vt_V / float(ramp_raw["dvdt_f"]["min"])),
                            "max_str": fmt_mv_ns_pair(0.6*vt_V, 0.6*vt_V / float(ramp_raw["dvdt_f"]["max"])),
                        },
                    }
                except Exception:
                    ramp = {
                        "r_load_ohm": vt_R,
                        "dvdt_r": {"typ_v_per_ns": 0.0, "min_v_per_ns": 0.0, "max_v_per_ns": 0.0,
                                   "typ_str": "0.000mV/0.000ns", "min_str": "0.000mV/0.000ns", "max_str": "0.000mV/0.000ns"},
                        "dvdt_f": {"typ_v_per_ns": 0.0, "min_v_per_ns": 0.0, "max_v_per_ns": 0.0,
                                   "typ_str": "0.000mV/0.000ns", "min_str": "0.000mV/0.000ns", "max_str": "0.000mV/0.000ns"},
                    }

        # C_comp: inherit component default unless overridden per model
        c_comp = m.get("c_comp", cfg["component"]["c_comp"])

        models_ctx.append({
            "name": m["name"],
            "type": m["type"],
            "polarity": m.get("polarity", "Non-Inverting"),
            "enable": m.get("enable", "NA"),
            "c_comp": c_comp,
            "pullup": pu, "pulldown": pd,
            "power_clamp": pc, "ground_clamp": gc,
            "ramp": ramp, "rising": rising, "falling": falling,
            "_refs": {"pu": pu_ref, "pd": pd_ref, "pc": pc_ref, "gc": gc_ref},
        })


    # External package text (only R_pkg / L_pkg / C_pkg lines)
    package_text = ""
    comp_cfg = cfg["component"]
    if comp_cfg.get("use_external_package_text", False) and (root / "package.pkg").exists():
        package_text = (root / "package.pkg").read_text(encoding="utf-8").strip()

    return {
        "meta": cfg["meta"],
        "component": {**comp_cfg, "package_text": package_text},
        "pins": pins,
        "models": models_ctx,
    }

# ---------- validators ----------
def validate(ctx):
    ok = True
    for m in ctx["models"]:
        # reference checks
        if not (m["_refs"]["pu"] == "VCC" and m["_refs"]["pc"] == "VCC" and
                m["_refs"]["pd"] == "GND" and m["_refs"]["gc"] == "GND"):
            print(f"[ERROR] {m['name']}: IV reference mismatch (PU/PC must be VCC; PD/GC must be GND)")
            ok = False
        # monotonic voltage axis checks
        for blk, rows in [("pullup", m["pullup"]), ("pulldown", m["pulldown"]),
                          ("power_clamp", m["power_clamp"]), ("ground_clamp", m["ground_clamp"])]:
            Vs = [r["V"] for r in rows]
            if not is_monotonic(Vs):
                print(f"[ERROR] {m['name']}: {blk} V-axis not strictly increasing")
                ok = False
        # VT time axis checks
        for wfset, label in [(m["rising"], "rising"), (m["falling"], "falling")]:
            for wf in wfset:
                ts = [p["t"] for p in wf["points"]]
                if not is_monotonic(ts):
                    print(f"[ERROR] {m['name']}: {label} waveform has non-monotonic time axis")
                    ok = False
    return ok

# ---------- render ----------
def main():
    env = Environment(
        loader=FileSystemLoader(str(ROOT / "templates")),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True
    )
    env.filters["fmt_num"] = fmt_num
    env.filters["fmt_volt"] = fmt_volt
    env.filters["fmt_ohm"] = fmt_ohm  # NEW

    ctx = read_component(ROOT)

    if not validate(ctx):
        print("Validation failed. Fix issues above.", file=sys.stderr)
        sys.exit(1)

    tpl = env.get_template("ibis_file.ibs.j2")
    rendered = tpl.render(**ctx)

    out_path = ROOT / ctx["meta"]["file_name"]
    out_path.write_text(rendered, encoding="utf-8")
    print(f"[OK] Wrote {out_path}")

    # Debug head
    print("--- HEAD of rendered file ---")
    for i, line in enumerate(rendered.splitlines()[:40], 1):
        print(f"{i:2d} {line}")

if __name__ == "__main__":
    main()
