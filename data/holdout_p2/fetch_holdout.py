"""Download the Phase 2 held-out set with the Phase 1 downloader (docs/HOLDOUT_P2_SPEC.md). Run by the user, not before 2026-12-01.

    python data/holdout_p2/fetch_holdout.py --plan     # selection only, writes data/holdout_p2/download/plan.json
    python data/holdout_p2/fetch_holdout.py --full     # resumable download, 1 request every 3 s
    python data/holdout_p2/fetch_holdout.py --split    # writes data/holdout_p2/split.json (ids only, no file opened)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = deep_merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def merged_config() -> dict:
    base = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    over = yaml.safe_load((HERE / "config_overlay.yaml").read_text(encoding="utf-8"))
    return deep_merge(base, over)


def assign(obs_id: int, salt: str, frac: float) -> str:
    u = int(hashlib.sha256(f"{salt}{obs_id}".encode()).hexdigest()[:8], 16) / 2 ** 32
    return "holdout" if u < frac else "bench_pool"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true")
    g.add_argument("--full", action="store_true")
    g.add_argument("--split", action="store_true")
    args = ap.parse_args()
    cfg = merged_config()
    if args.split:
        plan = json.loads((ROOT / cfg["download"]["report_dir"] / "plan.json").read_text(encoding="utf-8"))
        sp = cfg["split"]
        out = {n: {o["obs_id"]: {"set": assign(o["obs_id"], sp["salt"], sp["holdout_fraction"]),
                                 "station": o["station"]} for o in obs} for n, obs in plan.items()}
        (HERE / "split.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
        for n, d in out.items():
            print(n, {s: sum(v["set"] == s for v in d.values()) for s in ("holdout", "bench_pool")})
        return 0
    merged = HERE / "config_merged.yaml"
    merged.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    sys.path.insert(0, str(ROOT / "data"))
    import download_satnogs                                   # noqa: E402  (Phase 1 downloader, unchanged)
    return download_satnogs.main(["--config", str(merged), "--plan" if args.plan else "--full"])


if __name__ == "__main__":
    sys.exit(main())
