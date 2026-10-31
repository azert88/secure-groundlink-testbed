"""Phase B: download SatNOGS observations for the selected satellites (DATASET_SPEC.md section 2).

Per observation, into data/raw/<obs_id>/:
  metadata.json             the Network API observation record, as received
  audio.ogg                 the observation audio (payload)
  waterfall.png             the waterfall image
  demoddata/<original name> every demodulated frame file

Selection is deterministic: eligible observations are grouped by station, shuffled per station
with the configured seed, capped per station, then taken round-robin over stations (station
order shuffled with the same seed) until the target count. target_obs: null = all up to the cap.

Every request goes through the shared SatnogsClient (1 request every min_request_interval_s).
Resumable: a file is skipped when it exists and its SHA-256 matches data/raw/manifest.csv.
Files are written once (via .part + rename) and never modified afterwards.

Modes:
  --plan    selection only (from cached API pages), writes <report_dir>/plan.json
  --pilot   download the first `pilot` observations per satellite, project the full download
  --full    download the whole selection
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from satnogs_api import SatnogsClient, load_token  # noqa: E402
from select_satellites import candidate_transmitters, demod_filenames, scan_candidate  # noqa: E402

MANIFEST_FIELDS = ["obs_id", "file", "url", "download_utc", "size_bytes", "sha256"]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---- selection (pure, unit tested) ---------------------------------------------------------

def is_eligible(o: dict) -> bool:
    return bool(o.get("status") == "good" and demod_filenames(o) and o.get("payload")
                and o.get("waterfall"))


def select_observations(obs: list[dict], target: int | None, cap: int, seed: int) -> list[dict]:
    """Spread the selection over stations: per-station seeded shuffle + cap, then round-robin."""
    rng = np.random.default_rng(seed)
    by_station: dict[int, list[dict]] = {}
    for o in sorted(obs, key=lambda o: o["id"]):
        by_station.setdefault(o["ground_station"], []).append(o)
    stations = sorted(by_station)
    for st in stations:
        order = rng.permutation(len(by_station[st]))
        by_station[st] = [by_station[st][i] for i in order][:cap]
    stations = [stations[i] for i in rng.permutation(len(stations))]
    picked = []
    for rnd in range(cap):
        for st in stations:
            if rnd < len(by_station[st]):
                picked.append(by_station[st][rnd])
    return picked if target is None else picked[:target]


def files_for(o: dict) -> list[tuple[str, str]]:
    """(relative path inside data/raw/<obs_id>/, source URL) for every file of an observation."""
    out = [("audio.ogg", o["payload"]), ("waterfall.png", o["waterfall"])]
    for d in o["demoddata"]:
        url = d["payload_demod"]
        out.append((f"demoddata/{url.rstrip('/').rsplit('/', 1)[-1]}", url))
    return out


# ---- manifest + DATA.md --------------------------------------------------------------------

def load_manifest(path: Path) -> dict[tuple[str, str], dict]:
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as fh:
        return {(r["obs_id"], r["file"]): r for r in csv.DictReader(fh)}


def append_manifest(path: Path, rows: list[dict]) -> None:
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=MANIFEST_FIELDS)
        if new:
            w.writeheader()
        w.writerows(rows)


def append_data_md(path: Path, run_label: str, obs_rows: list[dict]) -> None:
    """Append a provenance section (one row per observation) to data/DATA.md."""
    lines = []
    if not path.exists():
        lines += ["# Dataset provenance and datasheet", "",
                  "Source: SatNOGS Network observations (https://network.satnogs.org). "
                  "Full per-file provenance (URL, UTC download time, size, SHA-256): "
                  "`data/raw/manifest.csv`.", ""]
    lines += [f"## Download run: {run_label} ({utc_now()})", "",
              "| obs_id | NORAD | station | start (UTC) | files | audio SHA-256 | audio URL |",
              "|---|---|---|---|---|---|---|"]
    for r in obs_rows:
        lines.append(f"| {r['obs_id']} | {r['norad']} | {r['station']} | {r['start']} | "
                     f"{r['n_files']} | {r['audio_sha256']} | {r['audio_url']} |")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n\n")


# ---- download --------------------------------------------------------------------------------

def download_observation(client: SatnogsClient, o: dict, raw_dir: Path,
                         manifest: dict) -> dict:
    """Download one observation; return per-observation stats."""
    obs_id = str(o["id"])
    obs_dir = raw_dir / obs_id
    obs_dir.mkdir(parents=True, exist_ok=True)
    new_rows, n_req, n_bytes, n_skipped = [], 0, 0, 0
    t0 = time.monotonic()

    meta = obs_dir / "metadata.json"
    if not meta.exists():
        meta.write_text(json.dumps(o, indent=1, sort_keys=True), encoding="utf-8")
    if (obs_id, "metadata.json") not in manifest:
        new_rows.append({"obs_id": obs_id, "file": "metadata.json",
                         "url": f"{client_network_api(client)}/observations/?id={obs_id}",
                         "download_utc": utc_now(), "size_bytes": meta.stat().st_size,
                         "sha256": sha256_file(meta)})

    for rel, url in files_for(o):
        dest = obs_dir / rel
        known = manifest.get((obs_id, rel))
        if dest.exists() and known and sha256_file(dest) == known["sha256"]:
            n_skipped += 1
            continue
        if dest.exists() and not known:
            # Completed earlier (written via .part + rename) but not yet recorded.
            when = datetime.fromtimestamp(dest.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            before = client.n_network_requests
            client.download(url, dest)
            n_req += client.n_network_requests - before
            when = utc_now()
        size = dest.stat().st_size
        n_bytes += size
        new_rows.append({"obs_id": obs_id, "file": rel, "url": url, "download_utc": when,
                         "size_bytes": size, "sha256": sha256_file(dest)})
    for r in new_rows:
        manifest[(r["obs_id"], r["file"])] = r
    sizes = {r["file"]: int(r["size_bytes"]) for r in manifest.values() if r["obs_id"] == obs_id}
    return {"obs_id": o["id"], "norad": o["norad_cat_id"], "station": o["ground_station"],
            "start": o["start"], "duration_s": duration_s(o), "n_demod": len(o["demoddata"]),
            "n_files": len(files_for(o)) + 1, "n_requests": n_req, "n_skipped": n_skipped,
            "bytes_downloaded": n_bytes, "seconds": round(time.monotonic() - t0, 1),
            "audio_bytes": sizes.get("audio.ogg", 0), "waterfall_bytes": sizes.get("waterfall.png", 0),
            "demod_bytes": sum(v for k, v in sizes.items() if k.startswith("demoddata/")),
            "audio_sha256": manifest[(obs_id, "audio.ogg")]["sha256"], "audio_url": o["payload"],
            "_new_rows": new_rows}


def client_network_api(client: SatnogsClient) -> str:
    return getattr(client, "network_api", "https://network.satnogs.org/api")


def duration_s(o: dict) -> float:
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return (datetime.strptime(o["end"], fmt) - datetime.strptime(o["start"], fmt)).total_seconds()


def project(pilot: list[dict], selection: dict[int, list[dict]], interval_s: float) -> dict:
    """Project requests, time and disk for the full selection from pilot measurements."""
    out = {"per_satellite": {}, "total": {}}
    tot_req = tot_bytes = 0
    s_per_req = [p["seconds"] / p["n_requests"] for p in pilot if p["n_requests"]]
    sec_per_request = max(interval_s, statistics.median(s_per_req)) if s_per_req else interval_s
    for norad, obs in selection.items():
        pp = [p for p in pilot if p["norad"] == norad]
        audio_bps = statistics.mean(p["audio_bytes"] / p["duration_s"] for p in pp)
        wf = statistics.mean(p["waterfall_bytes"] for p in pp)
        demod_each = (sum(p["demod_bytes"] for p in pp) / max(1, sum(p["n_demod"] for p in pp)))
        req = sum(2 + len(o["demoddata"]) for o in obs)
        size = sum(audio_bps * duration_s(o) + wf + demod_each * len(o["demoddata"]) for o in obs)
        out["per_satellite"][norad] = {"n_obs": len(obs), "requests": req,
                                       "requests_per_obs": round(req / len(obs), 1),
                                       "bytes": round(size), "mb_per_obs": round(size / len(obs) / 1e6, 2)}
        tot_req += req
        tot_bytes += size
    out["total"] = {"requests": tot_req, "bytes": round(tot_bytes),
                    "gb": round(tot_bytes / 1e9, 3), "seconds_per_request": round(sec_per_request, 2),
                    "hours": round(tot_req * sec_per_request / 3600, 2)}
    return out


def build_selection(client: SatnogsClient, cfg: dict) -> dict[int, list[dict]]:
    """Eligible observations per satellite (from the Phase A pages, cached) and the selection."""
    sel, dl = cfg["selection"], cfg["download"]
    end = datetime.strptime(sel["window_end_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    window = ((end - timedelta(days=sel["window_days"])).strftime("%Y-%m-%dT%H:%M:%SZ"),
              sel["window_end_utc"])
    cands, _ = candidate_transmitters(client, cfg)
    out = {}
    for s in dl["satellites"]:
        _, obs = scan_candidate(client, cfg, s["norad"], cands[(s["norad"], s["class"])], window)
        eligible = [o for o in obs if is_eligible(o)]
        n_st = len({o["ground_station"] for o in eligible})
        need = sel.get("min_stations_by_class", {}).get(s["class"], sel["min_stations"])
        if n_st < need:
            raise RuntimeError(f"{s['norad']}: {n_st} stations < required {need}")
        out[s["norad"]] = select_observations(eligible, s["target_obs"], s["cap_per_station"],
                                              cfg["seed"] + s["norad"])
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default="config.yaml")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--pilot", action="store_true")
    mode.add_argument("--full", action="store_true")
    args = ap.parse_args(argv)
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    api, dl = cfg["satnogs"], cfg["download"]
    client = SatnogsClient(api, cfg["paths"]["api_cache_dir"],
                           token=load_token(api["env_file"], api["token_env_var"]))
    client.network_api = api["network_api"]
    report_dir = Path(dl["report_dir"])
    report_dir.mkdir(parents=True, exist_ok=True)

    selection = build_selection(client, cfg)
    plan = {str(n): [{"obs_id": o["id"], "station": o["ground_station"], "start": o["start"],
                      "n_demod": len(o["demoddata"])} for o in obs] for n, obs in selection.items()}
    (report_dir / "plan.json").write_text(json.dumps(plan, indent=1), encoding="utf-8")
    for n, obs in selection.items():
        print(f"[B] {n}: {len(obs)} obs selected from {len({o['ground_station'] for o in obs})} stations",
              file=sys.stderr)
    if args.plan:
        return 0

    todo = []
    for n, obs in selection.items():
        k = int(dl["pilot"][n]) if args.pilot else len(obs)
        todo += obs[:k]
    raw_dir = Path(dl["raw_dir"])
    manifest_path = Path(dl["manifest"])
    manifest = load_manifest(manifest_path)
    stats = []
    for i, o in enumerate(todo, 1):
        st = download_observation(client, o, raw_dir, manifest)
        append_manifest(manifest_path, st.pop("_new_rows"))
        stats.append(st)
        print(f"[B] {i}/{len(todo)} obs {st['obs_id']} ({st['norad']}): {st['n_requests']} requests, "
              f"{st['bytes_downloaded'] / 1e6:.2f} MB, {st['seconds']} s", file=sys.stderr)
    append_data_md(Path(dl["data_md"]), "pilot" if args.pilot else "full", stats)

    report = {"mode": "pilot" if args.pilot else "full", "finished_utc": utc_now(),
              "observations": stats,
              "projection_full": project(stats, selection, float(api["min_request_interval_s"]))}
    report["projection_full"]["over_limit"] = report["projection_full"]["total"]["hours"] > dl["max_projected_hours"]
    name = "pilot_report.json" if args.pilot else "full_report.json"
    (report_dir / name).write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report["projection_full"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
