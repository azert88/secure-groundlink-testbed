"""Phase A: rank SatNOGS satellites as dataset sources (docs/DATASET_SPEC.md section 1).

Every number comes from the SatNOGS DB / Network APIs (cached, rate-limited). Nothing is guessed:
when a count is capped by max_pages_per_transmitter it is reported as a lower bound (exact=false).

Candidate unit = (satellite, mode class), because one satellite can transmit both 1k2 and 9k6.

Outputs (JSON + a Markdown table) in paths.selection_dir:
  candidates.json  every candidate with its observation counts
  ranking.json     shortlist with burst period and counter fields, plus recommendations
  ranking.md       human-readable top-k tables
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import sys
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from satnogs_api import SatnogsClient, load_token  # noqa: E402

# Observed suffixes after the timestamp: none, _1, _0015, _g0 / _g1.
FRAME_TIME_RE = re.compile(r"_(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})(?:_([A-Za-z]*\d+))?$")
KSY_ID_RE = re.compile(r"^\s*-?\s*id:\s*([A-Za-z0-9_]+)\s*$", re.MULTILINE)


# ---- pure helpers (unit tested) ------------------------------------------------------------

def classify_mode(mode: str | None, baud: float | None, classes: dict) -> str | None:
    """Return the mode class name for a DB transmitter, or None if it is not a wanted mode."""
    if not mode or baud is None:
        return None
    for name, spec in classes.items():
        if float(baud) != float(spec["baud"]):
            continue
        for m in spec["modes"]:
            if spec["match"] == "exact" and mode == m:
                return name
            if spec["match"] == "prefix" and re.match(rf"^{re.escape(m)}\b", mode):
                return name
    return None


def demod_filenames(obs: dict) -> list[str]:
    """Basenames of the demodulated-data files of a Network observation."""
    names = []
    for d in obs.get("demoddata") or []:
        url = d.get("payload_demod") if isinstance(d, dict) else d
        if url:
            names.append(url.rstrip("/").rsplit("/", 1)[-1])
    return names


def frame_time(filename: str) -> datetime | None:
    """UTC time (1 s resolution) encoded in a demoddata filename, e.g. data_<id>_2026-10-26T01-02-05_1."""
    m = FRAME_TIME_RE.search(filename)
    if not m:
        return None
    return datetime.strptime(m.group(1), "%Y-%m-%dT%H-%M-%S").replace(tzinfo=timezone.utc)


def burst_stats(times_s: list[float], gap_s: float, tol_s: float) -> dict:
    """Group sorted frame times into bursts and measure the burst period.

    period_s     = median interval between consecutive burst starts
    mad_s        = median absolute deviation of those intervals
    frac_regular = fraction of intervals within tol_s of an integer multiple (>= 1) of period_s
                   (missed bursts give multiples, which are still regular)
    """
    times_s = sorted(times_s)
    starts, sizes = [], []
    for i, t in enumerate(times_s):
        if i == 0 or t - times_s[i - 1] > gap_s:
            starts.append(t)
            sizes.append(1)
        else:
            sizes[-1] += 1
    out = {"n_frames": len(times_s), "n_bursts": len(starts),
           "frames_per_burst_median": statistics.median(sizes) if sizes else None,
           "period_s": None, "mad_s": None, "frac_regular": None}
    intervals = [b - a for a, b in zip(starts, starts[1:])]
    if not intervals:
        return out
    period = statistics.median(intervals)
    mad = statistics.median(abs(d - period) for d in intervals)
    regular = 0
    for d in intervals:
        k = max(1, round(d / period))
        if abs(d - k * period) <= tol_s:
            regular += 1
    out.update(period_s=period, mad_s=mad, frac_regular=regular / len(intervals))
    return out


def counter_fields(ksy_text: str, pattern: str) -> list[str]:
    """Field ids in a Kaitai .ksy file whose name looks like a counter or uptime."""
    rx = re.compile(pattern, re.IGNORECASE)
    return sorted({f for f in KSY_ID_RE.findall(ksy_text) if rx.search(f)})


def grsat_supports(grsat_modes: set, spec: dict) -> bool:
    """True if gr-satellites lists a transmitter of a compatible modulation and the same baud."""
    return any(mod in spec["grsat_modulations"] and float(baud) == float(spec["baud"])
               for mod, baud in grsat_modes)


def load_grsat_modes(satyaml_dir: Path) -> dict[int, dict]:
    """{norad: {"name", "modes": {(modulation, baud)}}} from gr-satellites satyaml files."""
    out = {}
    for path in sorted(satyaml_dir.glob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        modes = {(t.get("modulation"), t.get("baudrate"))
                 for t in (doc.get("transmitters") or {}).values() if t.get("baudrate")}
        entry = out.setdefault(int(doc["norad"]), {"name": doc["name"], "modes": set()})
        entry["modes"] |= modes
    return out


def grsat_satyaml_dir(cfg: dict) -> tuple[Path, str]:
    """Locate satyaml files: installed gr-satellites first, else the pinned public release."""
    try:
        import satellites  # gr-satellites Python package
        installed = Path(satellites.__file__).parent / "satyaml"
        if installed.is_dir():
            return installed, "installed gr-satellites"
    except ImportError:
        pass
    g = cfg["grsat"]
    root = Path(g["extract_dir"])
    dirs = sorted(root.glob("*/python/satyaml"))
    if not dirs:
        root.mkdir(parents=True, exist_ok=True)
        tarball = root / "src.tar.gz"
        if not tarball.exists():
            resp = requests.get(g["tarball_url"], timeout=120)
            resp.raise_for_status()
            tarball.write_bytes(resp.content)
        digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
        if digest != g["tarball_sha256"]:
            raise RuntimeError(f"gr-satellites tarball SHA-256 mismatch: {digest}")
        with tarfile.open(tarball) as tar:
            members = [m for m in tar.getmembers() if m.isfile()
                       and re.fullmatch(r"[^/]+/python/satyaml/[^/]+\.yml", m.name)]
            tar.extractall(root, members=members, filter="data")
        dirs = sorted(root.glob("*/python/satyaml"))
    return dirs[0], f"public satyaml {g['version']}"


def prefilter(cands: dict, grsat: dict, classes: dict) -> tuple[list, dict]:
    """Keep (norad, class) candidates that gr-satellites can decode in that class (offline)."""
    kept, counts = [], {}
    for (norad, cls), cand in sorted(cands.items()):
        c = counts.setdefault(cls, {"mode_ok": 0, "not_in_grsat": 0, "grsat_mode_mismatch": 0,
                                    "kept": 0})
        c["mode_ok"] += 1
        if norad not in grsat:
            c["not_in_grsat"] += 1
        elif not grsat_supports(grsat[norad]["modes"], classes[cls]):
            c["grsat_mode_mismatch"] += 1
        else:
            c["kept"] += 1
            kept.append((norad, cls))
    return kept, counts


def rank_key(row: dict) -> tuple:
    return (row["meets_all"], row["n_stations"], row["n_good_with_demod"])


# ---- API steps -----------------------------------------------------------------------------

def candidate_transmitters(client: SatnogsClient, cfg: dict) -> tuple[dict, dict]:
    """Return ({(norad, class): {name, transmitters[...]}}, {norad: satellite record})."""
    sel, api = cfg["selection"], cfg["satnogs"]
    sats = client.get_json(f"{api['db_api']}/satellites/", {"format": "json"})
    txs = client.get_json(f"{api['db_api']}/transmitters/", {"format": "json"})
    in_orbit = {s["sat_id"]: s for s in sats if s.get("status") == sel["satellite_status"]}
    cands: dict = {}
    sat_by_norad: dict = {}
    for t in txs:
        sat = in_orbit.get(t.get("sat_id"))
        norad = t.get("norad_cat_id")
        if not (t.get("alive") and sat and norad and norad < sel["max_catalog_norad"]):
            continue
        cls = classify_mode(t.get("mode"), t.get("baud"), sel["mode_classes"])
        if cls is None:
            continue
        sat_by_norad[norad] = sat
        entry = cands.setdefault((norad, cls), {"name": sat["name"], "transmitters": []})
        entry["transmitters"].append({"uuid": t["uuid"], "mode": t["mode"], "baud": t["baud"],
                                      "description": t.get("description")})
    return cands, sat_by_norad


def meets_thresholds(obs: list[dict], sel: dict) -> bool:
    """True when obs with demoddata reach both the observation and the station thresholds."""
    with_demod = [o for o in obs if demod_filenames(o)]
    return (len(with_demod) >= sel["min_good_obs_with_demod"]
            and len({o["ground_station"] for o in with_demod}) >= sel["min_stations"])


def scan_candidate(client: SatnogsClient, cfg: dict, norad: int, cand: dict,
                   window: tuple[str, str], max_pages: int | None = None,
                   early_stop: bool = False) -> tuple[dict, list[dict]]:
    """Count good observations with demoddata for one (satellite, class). Returns (row, obs).

    early_stop: stop paging a transmitter as soon as its own observations meet the thresholds
    (counts are then lower bounds, flagged by stopped_early).
    """
    api, sel = cfg["satnogs"], cfg["selection"]
    max_pages = max_pages or sel["max_pages_per_transmitter"]
    obs_by_id: dict = {}
    exact, stopped_early = True, False
    for tx in cand["transmitters"]:
        params = {"format": "json", "norad_cat_id": norad, "transmitter_uuid": tx["uuid"],
                  "status": "good", "start": window[0], "end": window[1]}
        tx_obs = []
        for items, has_more in client.iter_pages(f"{api['network_api']}/observations/", params,
                                                 max_pages):
            for o in items:
                # Guard against silently ignored filters (seen with satellite__norad_cat_id).
                if (o["norad_cat_id"] != norad or o["transmitter_uuid"] != tx["uuid"]
                        or o["status"] != "good"):
                    raise RuntimeError(f"API filter not applied: obs {o['id']} for {norad}/{tx['uuid']}")
                obs_by_id[o["id"]] = o
                tx_obs.append(o)
            last_has_more = has_more
            if early_stop and has_more and meets_thresholds(tx_obs, sel):
                stopped_early = True
                break
        if last_has_more:
            exact = False
    with_demod = [o for o in obs_by_id.values() if demod_filenames(o)]
    stations = {o["ground_station"] for o in with_demod}
    row = {"norad": norad, "name": cand["name"],
           "modes": sorted({t["mode"] for t in cand["transmitters"]}),
           "transmitters": [t["uuid"] for t in cand["transmitters"]],
           "n_good": len(obs_by_id), "n_good_with_demod": len(with_demod),
           "n_stations": len(stations),
           "n_frames_total": sum(len(demod_filenames(o)) for o in with_demod),
           "exact": exact, "stopped_early": stopped_early}
    row["meets_all"] = (row["n_good_with_demod"] >= sel["min_good_obs_with_demod"]
                        and row["n_stations"] >= sel["min_stations"])
    return row, with_demod


def measure_bursts(obs_list: list[dict], sel: dict) -> dict:
    """Burst statistics on the n_burst_obs observations with the most frames (ties: lowest id)."""
    chosen = sorted(obs_list, key=lambda o: (-len(demod_filenames(o)), o["id"]))[: sel["n_burst_obs"]]
    per_obs = []
    for o in chosen:
        times = [frame_time(f) for f in demod_filenames(o)]
        t0 = datetime.strptime(o["start"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        rel = [(t - t0).total_seconds() for t in times if t is not None]
        stats = burst_stats(rel, sel["burst_gap_s"], sel["burst_multiple_tol_s"])
        per_obs.append({"obs_id": o["id"], "ground_station": o["ground_station"], **stats})
    periods = [p["period_s"] for p in per_obs if p["period_s"] is not None]
    regs = [p["frac_regular"] for p in per_obs if p["frac_regular"] is not None]
    return {"burst_period_s": statistics.median(periods) if periods else None,
            "burst_frac_regular": statistics.median(regs) if regs else None,
            "burst_obs": per_obs}


def find_counters(client: SatnogsClient, cfg: dict, sat: dict) -> dict:
    """Counter / uptime field names in the satnogs-decoders structs listed for this satellite."""
    decoders = [t["decoder"] for t in sat.get("telemetries") or [] if t.get("decoder")]
    fields = {}
    for dec in decoders:
        entry = client.get(cfg["satnogs"]["decoders_ksy_url"].format(decoder=dec))
        if entry["status"] == 200:
            fields[dec] = counter_fields(entry["text"], cfg["selection"]["counter_field_regex"])
        else:
            fields[dec] = None  # struct not found under that name
    return {"decoders": decoders, "counter_fields": fields}


def to_markdown(rows: list[dict], title: str) -> str:
    lines = [f"### {title}", "",
             "| # | name | NORAD | mode | n_good_obs (demod) | n_stations | burst period s (regular) | counter fields |",
             "|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        n = f"{r['n_good_with_demod']}" + ("" if r["exact"] else "+")
        st = f"{r['n_stations']}" + ("" if r["exact"] else "+")
        per = "n/a" if r.get("burst_period_s") is None else \
            f"{r['burst_period_s']:.0f} ({r['burst_frac_regular']:.0%})"
        cf = [f"{d}:{f}" for d, fs in (r.get("counter_fields") or {}).items() for f in (fs or [])]
        lines.append(f"| {i} | {r['name']} | {r['norad']} | {', '.join(r['modes'])} | {n} | {st} | "
                     f"{per} | {', '.join(cf[:4]) + (' ...' if len(cf) > 4 else '') or 'none'} |")
    return "\n".join(lines) + "\n"


def run_focus(client: SatnogsClient, cfg: dict, cands: dict, kept: list, sat_by_norad: dict,
              grsat: dict, window: tuple[str, str], out_dir: Path) -> int:
    """Focused Phase A: fixed candidates + the best probe_class candidates from a one-page probe."""
    sel, focus = cfg["selection"], cfg["selection"]["focus"]
    fixed = [(f["norad"], f["class"]) for f in focus["fixed"]]
    missing = [k for k in fixed if k not in kept]
    if missing:
        raise RuntimeError(f"fixed candidates not in the prefilter output: {missing}")

    probe = []
    for norad, cls in kept:
        if cls != focus["probe_class"] or (norad, cls) in fixed:
            continue
        row, _ = scan_candidate(client, cfg, norad, cands[(norad, cls)], window, max_pages=1)
        row["class"] = cls
        probe.append(row)
        print(f"[A] probe {row['name']} ({norad}): page-1 good+demod={row['n_good_with_demod']} "
              f"stations={row['n_stations']}", file=sys.stderr)
    probe.sort(key=lambda r: (r["n_good_with_demod"], r["n_stations"], -r["norad"]), reverse=True)
    picks = [(r["norad"], r["class"]) for r in probe[: focus["n_probe_pick"]]]

    results = []
    for norad, cls in fixed + picks:
        row, obs = scan_candidate(client, cfg, norad, cands[(norad, cls)], window,
                                  early_stop=cls in focus["early_stop_classes"])
        row["class"] = cls
        row["grsat_name"] = grsat[norad]["name"]
        row.update(measure_bursts(obs, sel))
        row.update(find_counters(client, cfg, sat_by_norad[norad]))
        results.append(row)
        print(f"[A] {row['name']} ({norad}, {cls}): good+demod={row['n_good_with_demod']} "
              f"stations={row['n_stations']} meets_all={row['meets_all']} "
              f"period={row['burst_period_s']} (net={client.n_network_requests}, "
              f"cache={client.n_cache_hits})", file=sys.stderr)

    (out_dir / "focus.json").write_text(json.dumps(
        {"window": window, "probe": probe, "picks": picks, "results": results,
         "requests": {"network": client.n_network_requests, "cache_hits": client.n_cache_hits}},
        indent=1), encoding="utf-8")

    lines = ["# Phase A focused selection", "",
             f"Window {window[0]} .. {window[1]}. Counts: 'exact' = all pages read; "
             "'>=' = paging stopped early once thresholds were met.", "",
             "| name | NORAD | class | DB modes | gr-satellites name | good obs (demod) | stations | "
             "burst period s | regular | frames/burst | counter / uptime fields |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        bound = "" if r["exact"] else ">="
        fpbs = [b["frames_per_burst_median"] for b in r["burst_obs"] if b["frames_per_burst_median"]]
        fpb = statistics.median(fpbs) if fpbs else "n/a"
        cf = [f"{d}:{f}" for d, fs in r["counter_fields"].items() for f in (fs or [])]
        nf = [d for d, fs in r["counter_fields"].items() if fs is None]
        cf_txt = ", ".join(cf) or "none"
        if nf:
            cf_txt += f" (struct not found: {', '.join(nf)})"
        per = "n/a" if r["burst_period_s"] is None else f"{r['burst_period_s']:.0f}"
        reg = "n/a" if r["burst_frac_regular"] is None else f"{r['burst_frac_regular']:.0%}"
        lines.append(f"| {r['name']} | {r['norad']} | {r['class']} | {', '.join(r['modes'])} | "
                     f"{r['grsat_name']} | {bound}{r['n_good_with_demod']} | {bound}{r['n_stations']} | "
                     f"{per} | {reg} | {fpb} | {cf_txt} |")
    lines += ["", "Burst observations (3 per satellite, most frames):", ""]
    for r in results:
        for b in r["burst_obs"]:
            lines.append(f"- {r['name']} obs {b['obs_id']} (station {b['ground_station']}): "
                         f"{b['n_frames']} frames, {b['n_bursts']} bursts, period {b['period_s']} s, "
                         f"MAD {b['mad_s']} s, regular {b['frac_regular']}")
    md = "\n".join(lines) + "\n"
    (out_dir / "focus.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--limit", type=int, default=None, help="scan only the first N candidates (debug)")
    ap.add_argument("--prefilter-only", action="store_true",
                    help="offline: apply mode + gr-satellites filter from cached DB lists, no API calls")
    ap.add_argument("--focus", action="store_true",
                    help="scan only selection.focus candidates (fixed + best probe picks)")
    args = ap.parse_args(argv)
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    sel, api = cfg["selection"], cfg["satnogs"]
    out_dir = Path(cfg["paths"]["selection_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    client = SatnogsClient(api, cfg["paths"]["api_cache_dir"],
                           token=load_token(api["env_file"], api["token_env_var"]),
                           offline=args.prefilter_only)
    end = datetime.strptime(sel["window_end_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    start = end - timedelta(days=sel["window_days"])
    window = (start.strftime("%Y-%m-%dT%H:%M:%SZ"), sel["window_end_utc"])

    cands, sat_by_norad = candidate_transmitters(client, cfg)
    satyaml_dir, grsat_source = grsat_satyaml_dir(cfg)
    grsat = load_grsat_modes(satyaml_dir)
    kept, counts = prefilter(cands, grsat, sel["mode_classes"])
    (out_dir / "prefilter.json").write_text(json.dumps({
        "grsat_source": grsat_source, "n_grsat_satellites": len(grsat), "counts": counts,
        "kept": [{"norad": n, "class": c, "name": cands[(n, c)]["name"],
                  "db_modes": sorted({t["mode"] for t in cands[(n, c)]["transmitters"]}),
                  "n_transmitters": len(cands[(n, c)]["transmitters"]),
                  "grsat_name": grsat[n]["name"]} for n, c in kept]}, indent=1), encoding="utf-8")
    n_tx = sum(len(cands[k]["transmitters"]) for k in kept)
    print(f"[A] prefilter ({grsat_source}, {len(grsat)} satellites): {json.dumps(counts)}", file=sys.stderr)
    print(f"[A] kept {len(kept)} candidates ({len({n for n, _ in kept})} satellites, "
          f"{n_tx} transmitters); window {window[0]} .. {window[1]}", file=sys.stderr)
    if args.prefilter_only:
        return 0
    if args.focus:
        return run_focus(client, cfg, cands, kept, sat_by_norad, grsat, window, out_dir)
    keys = kept[: args.limit] if args.limit else kept

    rows, obs_cache = [], {}
    for i, (norad, cls) in enumerate(keys, 1):
        row, obs = scan_candidate(client, cfg, norad, cands[(norad, cls)], window)
        row["class"] = cls
        rows.append(row)
        obs_cache[(norad, cls)] = obs
        print(f"[A] {i}/{len(keys)} {row['name']} ({norad}, {cls}): good+demod={row['n_good_with_demod']}"
              f" stations={row['n_stations']} exact={row['exact']} "
              f"(net={client.n_network_requests}, cache={client.n_cache_hits})", file=sys.stderr)
    rows.sort(key=lambda r: (r["class"],) + tuple(-x for x in rank_key(r)) + (r["norad"],))
    (out_dir / "candidates.json").write_text(json.dumps(
        {"window": window, "criteria": {k: sel[k] for k in ("min_good_obs_with_demod", "min_stations",
                                                              "max_catalog_norad", "mode_classes")},
         "rows": rows}, indent=1), encoding="utf-8")

    shortlist = {}
    for cls in sel["mode_classes"]:
        ranked = sorted((r for r in rows if r["class"] == cls), key=rank_key, reverse=True)
        top = []
        for r in ranked[: sel["n_shortlist"]]:
            r = dict(r)
            r.update(measure_bursts(obs_cache[(r["norad"], cls)], sel))
            r.update(find_counters(client, cfg, sat_by_norad[r["norad"]]))
            top.append(r)
        shortlist[cls] = top

    def has_counter(r):
        return any(r["counter_fields"].get(d) for d in r["decoders"])

    recommendations = {}
    for cls, top in shortlist.items():
        ok = [r for r in top if r["meets_all"]]
        # Prefer: meets all criteria, a counter field, regular rhythm, then most stations.
        ok.sort(key=lambda r: (has_counter(r), (r["burst_frac_regular"] or 0) >= 0.8,
                               r["n_stations"], r["n_good_with_demod"]), reverse=True)
        recommendations[cls] = ok[0]["norad"] if ok else None

    result = {"window": window, "shortlist": shortlist, "recommendations": recommendations,
              "n_candidates": len(rows), "n_meeting_all": sum(r["meets_all"] for r in rows),
              "requests": {"network": client.n_network_requests, "cache_hits": client.n_cache_hits}}
    (out_dir / "ranking.json").write_text(json.dumps(result, indent=1), encoding="utf-8")

    md = [f"# Phase A satellite ranking\n\nWindow {window[0]} .. {window[1]}. "
          f"'+' = lower bound (page cap reached). Regular = share of burst intervals within "
          f"{sel['burst_multiple_tol_s']} s of a multiple of the period.\n"]
    for cls, top in shortlist.items():
        md.append(to_markdown(top[: sel["top_k"]], f"{cls} (recommended NORAD: {recommendations[cls]})"))
    (out_dir / "ranking.md").write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    sys.exit(main())
