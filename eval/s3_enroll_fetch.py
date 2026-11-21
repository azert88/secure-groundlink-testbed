"""S3 enrollment passes: list (--plan) and fetch (--full) the earlier passes that build the station templates.

Protocol: docs/GATES_PHASE2_S3.md (signed, models/gates_p2_s3/lock.json), decision P2-17.
For every scored dev pass p (train + calib, selected_audio = 1) of station s and satellite n, the enrollment set is the
up to K = 3 most recent passes of (s, n) with start strictly before p's start that pass Phase 1 audio QC (readable,
48 kHz, mono, |audio duration - schedule| <= max_duration_diff_s). Chronological, independent of file location:
local data (data/raw/) first, else fetched. Candidates: SatNOGS Network observations, status good, audio present,
transmitter of the satellite's Phase 1 mode class, start < 2026-11-01 (never in the Phase 2 held-out window).

Listing: the Phase 1 observation pages (data/cache/api, exact for 2026-08-30 .. 2026-10-29, newest first) are read
from the cache; history before that window is listed per station with ground_station filter (same cached client,
1 request / 3 s). Fetched passes: metadata.json + audio.ogg only, into data/raw_enroll/<obs_id>/, written once
(.part + rename), SHA-256 in data/raw_enroll/manifest.csv; never scored; holdout files are never read.

Modes:
  --plan   list the enrollment set, assuming every not-yet-fetched candidate passes QC; writes
           data/raw_enroll/plan.json (passes local vs to fetch, estimated MB and minutes). Only listing requests.
  --full   resumable: walk back per scored pass, download missing candidates, QC them, take the next earlier one
           when QC fails, until K pass or the list (lookback) ends; writes data/raw_enroll/{qc.csv,enrollment.json}.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data"))
from satnogs_api import SatnogsClient, load_token  # noqa: E402
from select_satellites import candidate_transmitters  # noqa: E402

K = 3
SPARE = 2          # extra earlier candidates listed in case some fail audio QC
HOLDOUT_START = "2026-11-01T00:00:00Z"
OUT = Path("data/raw_enroll")
MANIFEST_FIELDS = ["obs_id", "file", "url", "download_utc", "size_bytes", "sha256"]
FMT = "%Y-%m-%dT%H:%M:%SZ"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime(FMT)


def shift(ts: str, days: float) -> str:
    return (datetime.strptime(ts, FMT) + timedelta(days=days)).strftime(FMT)


# ---- pure selection (unit tested) -------------------------------------------------------------------------------
def pick_enrollment(scored_start: str, candidates: list[dict], qc: dict[int, bool | None], k: int = K) -> list[dict]:
    """Up to k most recent candidates strictly before scored_start whose QC is True or unknown (None).

    candidates: dicts with id and start (any order). qc: obs id -> True / False / None (not yet known)."""
    out = []
    for c in sorted(candidates, key=lambda c: (c["start"], c["id"]), reverse=True):
        if c["start"] >= scored_start or c["start"] >= HOLDOUT_START:
            continue
        if qc.get(c["id"]) is False:
            continue
        out.append(c)
        if len(out) == k:
            break
    return out


# ---- inputs -----------------------------------------------------------------------------------------------------
def dev_passes() -> pd.DataFrame:
    o = pd.read_csv("data/tables/observations.csv")
    o = o[o["split"].isin(["train", "calib"]) & (o["selected_audio"] == 1)]
    return o[["obs_id", "norad", "station_id", "start_utc"]].sort_values("start_utc").reset_index(drop=True)


def local_qc() -> dict[int, bool]:
    """Audio QC of local passes (Phase 1 report for data/raw, data/raw_enroll/qc.csv for fetched ones)."""
    out = {}
    q = pd.read_csv("data/qc_report.csv", usecols=["obs_id", "audio_qc"])
    for i, a in zip(q["obs_id"], q["audio_qc"]):
        if (Path("data/raw") / str(i) / "audio.ogg").exists():
            out[int(i)] = bool(a)
    if (OUT / "qc.csv").exists():
        for r in csv.DictReader(open(OUT / "qc.csv", newline="", encoding="utf-8")):
            out[int(r["obs_id"])] = r["audio_qc"] == "True"
    return out


def is_candidate(o: dict, station: int, norad: int, tx: set[str]) -> bool:
    return (o["ground_station"] == station and o["norad_cat_id"] == norad and o["status"] == "good"
            and bool(o.get("payload")) and o["transmitter_uuid"] in tx and o["start"] < HOLDOUT_START)


def window_pages(client: SatnogsClient, cfg: dict, norad: int, tx: list[str]) -> list[dict]:
    """All good observations of the Phase 1 window, from the cached pages (same URLs as data/select_satellites.py)."""
    sel, api = cfg["selection"], cfg["satnogs"]
    end = datetime.strptime(sel["window_end_utc"], FMT).replace(tzinfo=timezone.utc)
    start = (end - timedelta(days=sel["window_days"])).strftime(FMT)
    obs = []
    for uuid in tx:
        params = {"format": "json", "norad_cat_id": norad, "transmitter_uuid": uuid, "status": "good",
                  "start": start, "end": sel["window_end_utc"]}
        for items, _ in client.iter_pages(f"{api['network_api']}/observations/", params,
                                          sel["max_pages_per_transmitter"]):
            obs += items
    return obs


def pre_window(client: SatnogsClient, cfg: dict, station: int, norad: int, tx: list[str], end: str,
               lookback_days: float, max_pages: int) -> list[dict]:
    """Good observations of (station, norad) in [end - lookback, end), newest first, via the ground_station filter."""
    api, obs = cfg["satnogs"], []
    for uuid in tx:
        params = {"format": "json", "norad_cat_id": norad, "transmitter_uuid": uuid, "status": "good",
                  "ground_station": station, "start": shift(end, -lookback_days), "end": end}
        for items, _ in client.iter_pages(f"{api['network_api']}/observations/", params, max_pages):
            for o in items:
                if o["ground_station"] != station or o["norad_cat_id"] != norad:
                    raise RuntimeError(f"API filter not applied: obs {o['id']} for station {station}")
            obs += items
    return obs


# ---- download + QC (full mode) ----------------------------------------------------------------------------------
def load_manifest() -> dict:
    p = OUT / "manifest.csv"
    if not p.exists():
        return {}
    return {(r["obs_id"], r["file"]): r for r in csv.DictReader(open(p, newline="", encoding="utf-8"))}


def append_rows(path: Path, fields: list[str], rows: list[dict]) -> None:
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerows(rows)


def fetch_and_qc(client: SatnogsClient, o: dict, cfg: dict, manifest: dict) -> bool:
    from download_satnogs import sha256_file  # noqa: E402  (data/, same downloader helpers)
    from qc import audio_checks, scheduled_s  # noqa: E402
    oid, d = str(o["id"]), OUT / str(o["id"])
    d.mkdir(parents=True, exist_ok=True)
    rows = []
    meta = d / "metadata.json"
    if not meta.exists():
        meta.write_text(json.dumps(o, indent=1, sort_keys=True), encoding="utf-8")
    if (oid, "metadata.json") not in manifest:
        rows.append({"obs_id": oid, "file": "metadata.json", "url": f"{cfg['satnogs']['network_api']}/observations/?id={oid}",
                     "download_utc": utc_now(), "size_bytes": meta.stat().st_size, "sha256": sha256_file(meta)})
    audio, known = d / "audio.ogg", manifest.get((oid, "audio.ogg"))
    if not (audio.exists() and known and sha256_file(audio) == known["sha256"]):
        if not audio.exists():
            client.download(o["payload"], audio)
        rows.append({"obs_id": oid, "file": "audio.ogg", "url": o["payload"], "download_utc": utc_now(),
                     "size_bytes": audio.stat().st_size, "sha256": sha256_file(audio)})
    append_rows(OUT / "manifest.csv", MANIFEST_FIELDS, rows)
    for r in rows:
        manifest[(r["obs_id"], r["file"])] = r
    q = cfg["qc"]
    a = audio_checks(audio)
    ok = bool(a["audio_ok"] and a["sample_rate"] == q["sample_rate"] and a["channels"] == q["channels"]
              and abs(a["audio_duration_s"] - scheduled_s(o)) <= q["max_duration_diff_s"])
    append_rows(OUT / "qc.csv", ["obs_id", "norad", "station_id", "start_utc", "audio_qc", "checked_utc"],
                [{"obs_id": oid, "norad": o["norad_cat_id"], "station_id": o["ground_station"], "start_utc": o["start"],
                  "audio_qc": ok, "checked_utc": utc_now()}])
    return ok


# ---- main -------------------------------------------------------------------------------------------------------
def rates(cfg: dict) -> tuple[dict, float]:
    """Audio bytes per second per satellite and seconds per file request, from the Phase 1 full download report."""
    rep = json.loads(Path(cfg["download"]["report_dir"], "full_report.json").read_text(encoding="utf-8"))["observations"]
    bps = {}
    for n in {r["norad"] for r in rep}:
        bps[n] = statistics.median(r["audio_bytes"] / r["duration_s"] for r in rep if r["norad"] == n and r["duration_s"])
    spr = [r["seconds"] / r["n_requests"] for r in rep if r["n_requests"]]
    return bps, max(float(cfg["satnogs"]["min_request_interval_s"]), statistics.median(spr) if spr else 0.0)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--full", action="store_true")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--lookback-days", type=float, default=90.0, help="pre-window listing depth per station")
    ap.add_argument("--max-pages", type=int, default=1, help="pre-window pages per station and transmitter (25 each)")
    ap.add_argument("--offline", action="store_true", help="cache only, no network request at all")
    a = ap.parse_args(argv)
    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    api = cfg["satnogs"]
    client = SatnogsClient(api, cfg["paths"]["api_cache_dir"], token=load_token(api["env_file"], api["token_env_var"]),
                           offline=a.offline)
    client.network_api = api["network_api"]
    OUT.mkdir(parents=True, exist_ok=True)
    cands, _ = candidate_transmitters(client, cfg)
    tx = {s["norad"]: [t["uuid"] for t in cands[(s["norad"], s["class"])]["transmitters"]]
          for s in cfg["download"]["satellites"]}
    dev = dev_passes()
    win_start = shift(cfg["selection"]["window_end_utc"], -cfg["selection"]["window_days"])
    in_window = {n: window_pages(client, cfg, n, tx[n]) for n in tx}
    qc = local_qc()
    manifest = load_manifest() if a.full else {}
    bps, s_per_req = rates(cfg)

    groups, listing = [], {"pre_window_requests": 0}
    for (st, n), g in dev.groupby(["station_id", "norad"], sort=True):
        st, n = int(st), int(n)
        pool = {o["id"]: o for o in in_window[n] if is_candidate(o, st, n, set(tx[n]))}
        first = g["start_utc"].min()
        if len(pick_enrollment(first, list(pool.values()), qc, K + SPARE)) < K + SPARE:   # history before the window
            before = client.n_network_requests
            try:
                for o in pre_window(client, cfg, st, n, tx[n], min(first, win_start), a.lookback_days, a.max_pages):
                    if is_candidate(o, st, n, set(tx[n])):
                        pool[o["id"]] = o
            except RuntimeError as exc:
                if not a.offline:
                    raise
                listing.setdefault("unlisted_pairs", []).append(f"{st}/{n}: {str(exc)[:40]}")
            listing["pre_window_requests"] += client.n_network_requests - before
        rows = []
        for _, p in g.iterrows():
            while True:
                pick = pick_enrollment(p["start_utc"], list(pool.values()), qc)
                todo = [c for c in pick if qc.get(c["id"]) is None]
                if not a.full or not todo:
                    break
                c = todo[0]                                  # newest unknown first; QC decides, then re-pick
                qc[c["id"]] = fetch_and_qc(client, c, cfg, manifest)
                print(f"[S3] {st}/{n}: fetched {c['id']} {c['start']} audio_qc={qc[c['id']]}", file=sys.stderr)
            rows.append({"obs_id": int(p["obs_id"]), "start": p["start_utc"], "k": len(pick),
                         "enroll": [{"obs_id": c["id"], "start": c["start"],
                                     "source": "local" if (Path("data/raw") / str(c["id"]) / "audio.ogg").exists()
                                     else "fetched" if qc.get(c["id"]) is not None else "to_fetch",
                                     "duration_s": (datetime.strptime(c["end"], FMT)
                                                    - datetime.strptime(c["start"], FMT)).total_seconds()}
                                    for c in pick]})
        groups.append({"station": st, "norad": n, "n_scored": len(rows), "n_candidates": len(pool), "passes": rows})

    need = {}
    for gr in groups:
        for r in gr["passes"]:
            for e in r["enroll"]:
                need[e["obs_id"]] = (gr["norad"], gr["station"], e["source"], e["duration_s"])
    to_fetch = {i: v for i, v in need.items() if v[2] == "to_fetch"}
    mb = sum(bps.get(n, 0) * d for n, _, _, d in to_fetch.values()) / 1e6
    k_hist = {str(k): sum(r["k"] == k for gr in groups for r in gr["passes"]) for k in range(K + 1)}
    summary = {"created_utc": utc_now(), "mode": "full" if a.full else "plan", "K": K, "lookback_days": a.lookback_days,
               "stations": len({g["station"] for g in groups}), "station_satellite_pairs": len(groups),
               "scored_passes": sum(g["n_scored"] for g in groups), "scored_passes_by_k": k_hist,
               "enroll_passes": len(need), "local": sum(v[2] == "local" for v in need.values()),
               "fetched": sum(v[2] == "fetched" for v in need.values()), "to_fetch": len(to_fetch),
               "to_fetch_by_satellite": {str(n): sum(v[0] == n for v in to_fetch.values()) for n in tx},
               "estimated_mb": round(mb, 1), "seconds_per_request": round(s_per_req, 2),
               "estimated_minutes": round(len(to_fetch) * s_per_req / 60, 1),
               "listing_requests": listing["pre_window_requests"],
               "unlisted_pairs_offline": len(listing.get("unlisted_pairs", [])), "cache_hits": client.n_cache_hits,
               "note": "plan assumes every to_fetch candidate passes audio QC; --full walks further back when one fails"}
    name = "enrollment.json" if a.full else "plan.json"
    (OUT / name).write_text(json.dumps({"summary": summary, "groups": groups}, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
