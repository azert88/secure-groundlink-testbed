"""Bench rehearsal with NO hardware: the real bench scripts, end to end, with "fake:" device URIs (bench/fake_pluto.py).

Nothing is transmitted: every device is a FakePluto whose TX only appends to an in-memory cable read by its RX.
Mode A uses a 60 s cut of data/sample/14932721 (memory: ~0.4 GB peak for IQ files and cable); mode B needs the
git-ignored data/tables/frames.csv. Runtime about 1 min.
"""

from __future__ import annotations

import csv
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
import yaml

from bench import fake_pluto, iqfile, iq_to_satnogs, link_signed, make_attack_iq, prepare_tx, probe, rx_capture, tx_play
from bench.safety import DEFAULT_CONFIG, SafetyError

SAMPLE = Path("data/sample/14932721")
CUT_S = (132.0, 192.0)                      # 06:02:06-06:03:06: 8 decoded frames of the sample pass


def run(mod, argv: list[str], monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", [mod.__name__] + [str(a) for a in argv])
    rc = mod.main()
    assert rc in (None, 0)


def write_config(path: Path, attenuation_db: float) -> Path:
    cfg = yaml.safe_load(Path(DEFAULT_CONFIG).read_text(encoding="utf-8"))
    assert all(not str(u).startswith("fake:") for u in cfg["devices"].values())   # shipped config: real devices
    cfg["devices"] = {r: f"fake:{r}" for r in ("satellite", "attacker", "station")}
    cfg["safety"]["declared_attenuation_db"] = attenuation_db
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return path


def cut_sample(out: Path) -> str:
    """60 s of the sample pass as a SatNOGS folder (audio, metadata, the frames inside the cut). Returns its start."""
    meta = json.loads((SAMPLE / "metadata.json").read_text(encoding="utf-8"))
    x, sr = sf.read(str(SAMPLE / "audio.ogg"), dtype="float32")
    start = datetime.fromisoformat(meta["start"].replace("Z", "+00:00")) + timedelta(seconds=CUT_S[0])
    end = start + timedelta(seconds=CUT_S[1] - CUT_S[0])
    (out / "demoddata").mkdir(parents=True)
    iq_to_satnogs.write_ogg(out / "audio.ogg", x[int(CUT_S[0] * sr):int(CUT_S[1] * sr)], sr)   # blocks: libsndfile
    for p in sorted((SAMPLE / "demoddata").iterdir()):
        t = datetime.strptime(p.name.split("_")[2], "%Y-%m-%dT%H-%M-%S").replace(tzinfo=timezone.utc)
        if start <= t < end:
            shutil.copy(p, out / "demoddata" / p.name)
    iso = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    (out / "metadata.json").write_text(json.dumps({**meta, "start": iso, "end": end.strftime("%Y-%m-%dT%H:%M:%SZ")}),
                                       encoding="utf-8")
    return iso


def test_fake_selected_only_by_uri():
    from bench.pluto import open_pluto
    assert isinstance(open_pluto("fake:station"), fake_pluto.FakePluto)
    with pytest.raises(ValueError):
        fake_pluto.FakePluto("usb:")


def test_dry_run_mode_a(tmp_path, monkeypatch, capsys):
    cfg0 = write_config(tmp_path / "bench0.yaml", 0)
    cfg = write_config(tmp_path / "bench30.yaml", 30)
    run(probe, ["--config", cfg], monkeypatch)
    assert capsys.readouterr().out.count("FAKE (no hardware)") == 3

    cab = fake_pluto.reset_cable()
    run(make_attack_iq, ["--kind", "tone", "--out", tmp_path / "atk_tone", "--config", cfg], monkeypatch)
    with pytest.raises(SafetyError, match="declared fixed attenuation 0"):
        run(tx_play, ["--device", "attacker", "--iq", tmp_path / "atk_tone", "--config", cfg0], monkeypatch)
    assert cab.pending_lead and cab.pending_samples() == 0                        # refused before opening a device
    with pytest.raises(SafetyError, match="above the bench limit"):
        run(tx_play, ["--device", "attacker", "--iq", tmp_path / "atk_tone", "--config", cfg, "--tx-gain-db", -5],
            monkeypatch)

    src = tmp_path / "src"
    src_start = cut_sample(src)
    n_src = len(list((src / "demoddata").iterdir()))
    run(prepare_tx, ["--obs-dir", src, "--out", tmp_path / "tx", "--config", cfg], monkeypatch)
    run(tx_play, ["--device", "satellite", "--iq", tmp_path / "tx", "--config", cfg], monkeypatch)
    assert "CONDUCTED BENCH" in capsys.readouterr().out                           # accepted with 30 dB declared
    run(rx_capture, ["--device", "station", "--seconds", 66, "--out", tmp_path / "rx", "--config", cfg], monkeypatch)
    g = iqfile.read_meta(tmp_path / "rx")["global"]
    assert g["bench:full_scale"] == 2048 and g["bench:rx_gain_db"] == 30
    raw = iqfile.open_cs16(tmp_path / "rx")
    assert len(raw) == 2 * 66 * 528000 and np.abs(raw).max() <= 2047

    obs = tmp_path / "obs"
    run(iq_to_satnogs, ["--iq", tmp_path / "rx", "--norad", 39444, "--obs-id", 900000001, "--out", obs, "--config", cfg,
                        "--frames-from", src / "demoddata", "--frames-src-start", src_start], monkeypatch)
    assert len(list((obs / "demoddata").iterdir())) == n_src == 8
    with open(obs / "bench_features.csv", newline="", encoding="utf-8") as fh:
        feats = list(csv.DictReader(fh))
    mid = feats[3:-3]                                                             # signal on (lead / tail are idle)
    assert abs(np.median([float(f["cfo_hz"]) for f in mid]) - cab.cfo_hz) < 50
    assert np.median([float(f["pilot_snr_db"]) for f in mid]) > 20

    from detect import run as detect_run
    assert detect_run.main(["--obs-dir", str(obs), "--out", str(tmp_path / "verdict.json")]) == 0
    res = json.loads((tmp_path / "verdict.json").read_text(encoding="utf-8"))
    assert res["unavailable"] == {} and res["detectors"]["interference"]["n_alert_windows"] == 0
    # a Phase 1 pass retransmitted is a cross-pass replay (bench/README.md mode A)
    assert res["verdict"] == "quarantine" and res["fusion"]["alert_names"] == ["replay_cross_pass"]
    print("mode A verdict:", res["verdict"], json.dumps(res["detectors"], default=str))


@pytest.mark.skipif(not Path("data/tables/frames.csv").exists(), reason="data/tables/frames.csv is git-ignored")
def test_dry_run_mode_b(tmp_path, monkeypatch, capsys):
    cfg = write_config(tmp_path / "bench30.yaml", 30)
    keys = ["--master-key", tmp_path / "master.key", "--state-dir", tmp_path / "state", "--config", cfg]
    fake_pluto.reset_cable()
    assert link_signed.main([str(a) for a in ["--write-tx-iq", tmp_path / "link_tx", *keys]]) == 0
    sent = json.loads(capsys.readouterr().out)
    run(tx_play, ["--device", "satellite", "--iq", tmp_path / "link_tx", "--config", cfg], monkeypatch)
    seconds = int(np.ceil(fake_pluto.CABLE.lead_s + sent["seconds"])) + 1
    run(rx_capture, ["--device", "station", "--seconds", seconds, "--out", tmp_path / "link_rx", "--config", cfg],
        monkeypatch)
    capsys.readouterr()
    assert link_signed.main([str(a) for a in ["--read-rx-iq", tmp_path / "link_rx", *keys]]) == 0
    out = capsys.readouterr().out
    summary = json.loads(out[out.index("\n{") + 1:])
    print("mode B:", summary)
    assert summary["received"] == summary["accepted"] == sent["frames"] and summary["rejected"] == {}
