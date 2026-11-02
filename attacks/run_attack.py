"""Apply ONE attack to a raw SatNOGS observation folder (audio.ogg, demoddata/, metadata.json).

Writes, without touching the input folder:
  <out>/                     the attacked recording as a raw SatNOGS-like folder (audio.ogg, demoddata/, metadata.json
                             with satellite, pass start and id only), ready for detect/run.py
  <out>.truth.json           the ground truth (operations, attacked frames, audio events), kept OUTSIDE the folder
  <out>.spectrogram.png      spectrogram before / after, attack segments shaded

Commands (the same generators as the frozen dataset, attacks/ and data/make_recipes.py):
  replay        replay of a valid 5-20-frame subsequence of the same pass (--length to fix L)
  sequence      one sequence anomaly: --subtype deletion|swap|duplicate|forged|timing|counter_jump
  interference  narrowband interference: --shape tone|noise --f0 HZ --snr-db DB [--bw HZ] [--t0 S --t1 S]
                (snr_db = 10 log10(P_signal / P_interference), P_signal over the in-band power of the pass)
  standard      the challenge scenario: replay + one sequence anomaly + interference at a random snr_db in [-10, +30]

Example:  python attacks/run_attack.py standard --obs-dir data/sample/15123656 --out out/attacked --seed 7
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import soundfile as sf
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "data"))
from attacks.apply import apply_recipe  # noqa: E402
from attacks.common import burst_intervals  # noqa: E402
from attacks.interference import reference_powers  # noqa: E402
from detect.counters import counter_codec  # noqa: E402

FMT = "%Y-%m-%dT%H:%M:%SZ"


def load_folder(obs_dir: Path, cfg: dict) -> dict:
    """Clean observation dict for attacks.apply, from a raw folder only (no dataset tables needed)."""
    from build_tables import frame_rows, load_clean_frames
    from detect import line_screen as LS
    from detect.audio_front import window_psds
    from qc import load_frames, timing_stats
    qc, tb = {**cfg["qc"], **cfg["canonical"]}, cfg["tables"]
    meta = json.loads((obs_dir / "metadata.json").read_text(encoding="utf-8"))
    norad, t0 = int(meta["norad_cat_id"]), datetime.strptime(meta["start"], FMT).replace(tzinfo=timezone.utc)
    dec, enc = counter_codec(norad, qc)
    is_fc = norad == qc["funcube"]["norad"]
    frames, reliable = [], True
    if (obs_dir / "demoddata").is_dir():
        frames = load_clean_frames(obs_dir, norad, qc, t0)
        ts = timing_stats(load_frames(obs_dir, norad, qc), qc["max_frames_per_second"][norad],
                          qc["funcube"] if is_fc else None, qc["timing_max_overfull_frac"], counter_rule=is_fc)
        reliable = bool(ts["timing_reliable"])
    c = {"obs_id": int(meta["id"]), "norad": norad, "start": t0, "frames": frames, "timing_reliable": reliable,
         "order_by_counter": is_fc, "f_low": qc["psd"]["f_low_hz"], "encode_counter": enc, "decode_counter": dec,
         "audio": None, "sr": None, "ref": None, "f_edge": None, "f_drop": None, "band_hi": None, "duration_s": None}
    if (obs_dir / "audio.ogg").exists():
        x, sr = sf.read(str(obs_dir / "audio.ogg"), dtype="float32")
        x = x.mean(axis=1) if x.ndim > 1 else x
        freqs, pxx = window_psds(x, sr, qc["psd"])
        f_edge, f_drop = LS.estimate_f_edge(freqs, 10 * np.log10(pxx + 1e-30), qc["psd"])
        band_hi = f_drop - tb["feature_band_top_margin_hz"]
        rows = frame_rows(frames, t0, reliable, is_fc, tb["burst_gap_s"], dec) if frames else []
        intervals = burst_intervals(rows, tb["burst_margin_s"]) if (reliable and rows) else None
        p = {**qc["psd"], "chunk_windows": tb["features_chunk_windows"]}
        c.update(audio=x, sr=sr, f_edge=float(f_edge), f_drop=float(f_drop), band_hi=float(band_hi),
                 duration_s=len(x) / sr, ref=reference_powers(x, sr, p, c["f_low"], f_edge, band_hi, intervals))
    elif frames:
        c["duration_s"] = max(f["t"] for f in frames) + 10.0
    c["frames_df"] = (pd.DataFrame(frame_rows(frames, t0, reliable, is_fc, tb["burst_gap_s"], dec))
                      if frames else pd.DataFrame())
    return c


def plan(cmd: str, a: argparse.Namespace, clean: dict, cfg: dict, rng) -> list[dict]:
    """Operations of the requested attack (data/make_recipes.py generators)."""
    import make_recipes as MR
    ac = cfg["attacks"]
    fr, dur, norad = clean["frames_df"], clean["duration_s"], clean["norad"]
    replay_audio = clean["audio"] is not None and clean["timing_reliable"]
    if cmd in ("replay", "sequence", "standard") and fr.empty:
        raise SystemExit("this attack needs decoded frames (demoddata/)")
    if cmd in ("interference", "standard") and clean["audio"] is None:
        raise SystemExit("this attack needs audio.ogg")
    if cmd == "replay":
        acr = {**ac, "replay": {**ac["replay"], "length": [a.length, a.length]}} if a.length else ac
        return [MR.plan_replay(rng, fr, fr, dur, acr, ac["replay"]["airtime_s"][norad], replay_audio, "intra_pass", None)]
    if cmd == "sequence":
        acs = {**ac, "sequence": {**ac["sequence"], "subtypes": [a.subtype]}}
        return [MR.plan_sequence(rng, fr, acs, clean["timing_reliable"], dur)]
    if cmd == "interference":
        lo, hi = ac["interference"]["f0_low_hz"], clean["band_hi"]
        bw = (a.bw if a.bw is not None else float(rng.uniform(*ac["interference"]["bw_hz"]))) if a.shape == "noise" else None
        f0 = a.f0 if a.f0 is not None else float(rng.uniform(lo + (bw or 0) / 2, hi - (bw or 0) / 2))
        if not lo <= f0 <= hi:
            raise SystemExit(f"--f0 must be inside the recording band [{lo:.0f}, {hi:.0f}] Hz")
        snr = a.snr_db if a.snr_db is not None else float(rng.uniform(*ac["interference"]["snr_random_db"]))
        t0, t1 = (a.t0 or 0.0), (a.t1 if a.t1 is not None else dur)
        return [{"type": "interference", "shape": a.shape, "f0_hz": float(f0), "bw_hz": bw, "snr_db": float(snr),
                 "phase": float(rng.uniform(0, 2 * np.pi)), "segments": [[float(t0), float(min(t1, dur))]],
                 "drift_hz_s": 0.0, "subtype": a.shape, "evasion": 0}]
    # standard: same as the dataset's combo recipes (replay leaves >= 3 frames for the sequence anomaly)
    rp = MR.plan_replay(rng, fr, fr, dur, ac, ac["replay"]["airtime_s"][norad], replay_audio, "intra_pass", None,
                        max_len=len(fr) - 3)
    sq = MR.plan_sequence(rng, fr, ac, clean["timing_reliable"], dur, exclude=set(rp["source_filenames"]))
    return [rp, sq, MR.plan_interference(rng, dur, clean["band_hi"], ac)]


def spectrogram_png(before, after, sr: int, events: list[dict], path: Path, title: str, f_max: float | None) -> None:
    """Before / after spectrogram in dB on ONE colour scale (from the clean recording), attack segments outlined."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from scipy.signal import spectrogram
    fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True, sharey=True, constrained_layout=True)
    specs = [spectrogram(x, sr, nperseg=1024, noverlap=0) for x in (before, after)]   # ~47 Hz x 21 ms cells
    band = specs[0][0] <= (f_max or sr / 2)
    vmin, vmax = np.percentile(10 * np.log10(specs[0][2][band] + 1e-20), [20, 99.9])
    for ax, (f, t, sxx), name in zip(axes, specs, ("before", "after")):
        im = ax.pcolormesh(t, f, 10 * np.log10(sxx + 1e-20), vmin=vmin, vmax=vmax, cmap="magma", shading="auto")
        ax.set_ylabel("audio frequency (Hz)")
        ax.set_title(f"{title}: {name}")
        if f_max:
            ax.set_ylim(0, f_max)
    for e in events:
        for t0, t1 in e["segments"]:
            axes[1].axvspan(t0, t1, fill=False, edgecolor="cyan" if e["attack_type"] == "interference" else "lime", lw=1)
    axes[1].set_xlabel("time from pass start (s); outlined = attacked audio (cyan interference, green replay)")
    fig.colorbar(im, ax=axes, label="dB")
    fig.savefig(path, dpi=90)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Apply one attack to a raw SatNOGS observation folder")
    ap.add_argument("attack", choices=["replay", "sequence", "interference", "standard"])
    ap.add_argument("--obs-dir", required=True, help="raw observation folder (audio.ogg, demoddata/, metadata.json)")
    ap.add_argument("--out", required=True, help="output folder for the attacked recording")
    ap.add_argument("--seed", type=int, default=None, help="random seed (default: config.yaml seed)")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--length", type=int, default=None, help="replay: number of frames (5-20)")
    ap.add_argument("--subtype", default="swap", choices=["deletion", "swap", "duplicate", "forged", "timing", "counter_jump"])
    ap.add_argument("--shape", default="tone", choices=["tone", "noise"])
    ap.add_argument("--f0", type=float, default=None, help="interference: audio frequency in Hz (default random)")
    ap.add_argument("--bw", type=float, default=None, help="interference: noise band width in Hz (default 20-200)")
    ap.add_argument("--snr-db", type=float, default=None, help="interference: snr_db (default random in [-10, 30])")
    ap.add_argument("--t0", type=float, default=None, help="interference: start (s), default pass start")
    ap.add_argument("--t1", type=float, default=None, help="interference: end (s), default pass end")
    ap.add_argument("--no-png", action="store_true", help="skip the spectrogram")
    a = ap.parse_args(argv)
    from eval.run_standard_test import write_folder
    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    seed = cfg["seed"] if a.seed is None else a.seed
    obs_dir, out = Path(a.obs_dir), Path(a.out)
    if out.resolve() == obs_dir.resolve():
        raise SystemExit("--out must differ from --obs-dir (the input is never modified)")
    clean = load_folder(obs_dir, cfg)
    import make_recipes as MR
    rng = np.random.default_rng(seed)
    for attempt in range(50):            # an infeasible draw (e.g. replay too long for the free time) is redrawn
        try:
            ops = plan(a.attack, a, clean, cfg, rng)
            break
        except MR.Infeasible as e:
            last = e
    else:
        raise SystemExit(f"no feasible {a.attack} attack on this recording after 50 draws: {last}")
    recipe = {"obs_id": clean["obs_id"], "variant_id": f"{a.attack}_cli", "seed": seed, "operations": ops}
    res = apply_recipe(recipe, clean)
    out.mkdir(parents=True, exist_ok=True)
    write_folder(out, res, clean)
    truth = {"attack": a.attack, "source_obs_dir": obs_dir.name, "seed": seed, "timing_reliable": clean["timing_reliable"],
             "operations": recipe["operations"],
             "attacked_frames": [{"filename": f["filename"], "attack_type": f["attack_type"], "subtype": f["subtype"]}
                                 for f in res["frames"] if f.get("is_attack")],
             "audio_events": [{k: v for k, v in e.items() if k != "p_interf"} for e in res["events"]]}
    tpath = out.parent / f"{out.name}.truth.json"
    tpath.write_text(json.dumps(truth, indent=1, default=float) + "\n", encoding="utf-8")
    outputs = {"attacked_dir": out.as_posix(), "truth": tpath.as_posix()}
    if clean["audio"] is not None and not a.no_png:
        png = out.parent / f"{out.name}.spectrogram.png"
        spectrogram_png(clean["audio"], res["audio"], clean["sr"], res["events"], png, f"{a.attack} on {obs_dir.name}",
                        clean["f_drop"])
        outputs["spectrogram"] = png.as_posix()
    print(json.dumps({**outputs, "operations": [{k: v for k, v in o.items() if k in
                     ("type", "subtype", "length", "delta_s", "shape", "f0_hz", "snr_db", "segments")} for o in recipe["operations"]]},
                     indent=1, default=float))
    return 0


if __name__ == "__main__":
    sys.exit(main())
