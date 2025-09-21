from pathlib import Path
import csv, re, sys
import yaml
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
    """Voltage formatter: compact with 'V' suffix on a single line."""
    try:
        v = float(x)
    except Exception:
        return str(x)
    # Use fixed with 2 decimals for normal ranges; otherwise scientific with uppercase E
    if 1e-3 <= abs(v) < 1e6:
        return f"{v:.2f}V"
    return f"{v:.6G}".replace('e', 'E') + "V"

# ---------- readers ----------
def read_iv(path: Path):
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
            # Optional numeric R/L/C pins (columns 4–6)
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

def read_component(root: Path):
    cfg = yaml.safe_load((root / "component.yml").read_text(encoding="utf-8"))

    # Ensure voltage range exists (prevents empty [Voltage Range] lines)
    for k in ("v_min", "v_typ", "v_max"):
        if k not in cfg.get("meta", {}):
            raise ValueError(f"meta.{k} missing in component.yml")

    # pins (accept 3 or 6 columns; we always render 6)
    pins = read_pins_three_or_six(root)

    models_ctx = []
    for m in cfg["models"]:
        mp = root / "models" / m["name"]

        # IV tables
        pu_ref, pu = read_iv(mp / "pullup.csv")
        pd_ref, pd = read_iv(mp / "pulldown.csv")
        pc_ref, pc = read_iv(mp / "power_clamp.csv")
        gc_ref, gc = read_iv(mp / "ground_clamp.csv")

        # Ramp (YAML in V/s → convert to V/ns; falling uses positive magnitude)
        ramp_raw = read_ramp_yaml(mp / "ramp.yml")
        ramp = {
            "dvdt_r": {
                "typ_v_per_ns": to_v_per_ns(ramp_raw["dvdt_r"]["typ"]),
                "min_v_per_ns": to_v_per_ns(ramp_raw["dvdt_r"]["min"]),
                "max_v_per_ns": to_v_per_ns(ramp_raw["dvdt_r"]["max"]),
            },
            "dvdt_f": {
                "typ_v_per_ns": abs(to_v_per_ns(ramp_raw["dvdt_f"]["typ"])),
                "min_v_per_ns": abs(to_v_per_ns(ramp_raw["dvdt_f"]["min"])),
                "max_v_per_ns": abs(to_v_per_ns(ramp_raw["dvdt_f"]["max"])),
            },
        }

        # VT waveforms (you can list multiple in component.yml)
        rising, falling = [], []
        for fn in m.get("vt_sets", {}).get("rising", []):
            rising.append(read_vt(mp / fn))
        for fn in m.get("vt_sets", {}).get("falling", []):
            falling.append(read_vt(mp / fn))

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
    if cfg["component"].get("use_external_package_text", False) and (root / "package.pkg").exists():
        package_text = (root / "package.pkg").read_text(encoding="utf-8").strip()

    return {
        "meta": cfg["meta"],
        "component": {**cfg["component"], "package_text": package_text},
        "pins": pins,
        "models": models_ctx,
    }

# ---------- validators (light but useful) ----------
def is_monotonic(xs):
    return all(xs[i] < xs[i+1] for i in range(len(xs)-1))

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

    ctx = read_component(ROOT)

    if not validate(ctx):
        print("Validation failed. Fix issues above.", file=sys.stderr)
        sys.exit(1)

    tpl = env.get_template("ibis_file.ibs.j2")
    rendered = tpl.render(**ctx)

    out_path = ROOT / ctx["meta"]["file_name"]
    out_path.write_text(rendered, encoding="utf-8")
    print(f"[OK] Wrote {out_path}")

    # Debug head for quick eyeball of structure
    print("--- HEAD of rendered file ---")
    for i, line in enumerate(rendered.splitlines()[:40], 1):
        print(f"{i:2d} {line}")

if __name__ == "__main__":
    main()
