"""Packaging (Phase P): sanitized sample metadata, attack CLI on a sample recording, tracked demo inputs."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "data"))
from make_sample import sanitize  # noqa: E402
from attacks import run_attack  # noqa: E402

SAMPLE = ROOT / "data" / "sample"
PRIVATE = ("observer", "station_name", "station_lat", "station_lng", "station_alt", "vetted_user",
           "waterfall_status_user", "client_metadata")


def test_sanitize_drops_personal_fields():
    m = {"id": 1, "norad_cat_id": 39444, "start": "s", "observer": "X", "station_lat": 1.0, "station_lng": 2.0,
         "payload": "https://example.org/a.ogg"}
    out = sanitize(m)
    assert not set(PRIVATE) & set(out) and "payload" not in out
    assert {"observer", "station_lat", "payload"} <= set(out["removed_fields"])


def test_sample_metadata_is_sanitized():
    obs = [p for p in SAMPLE.iterdir() if (p / "metadata.json").exists()]
    assert len(obs) == 2
    sats = set()
    for p in obs:
        m = json.loads((p / "metadata.json").read_text(encoding="utf-8"))
        assert not set(PRIVATE) & set(m)
        assert (p / "audio.ogg").exists() and any((p / "demoddata").iterdir())
        sats.add(m["norad_cat_id"])
    assert sats == {39444, 25397}


def test_demo_inputs_are_tracked():
    """Everything the demo needs must be in git (not excluded by .gitignore)."""
    need = ["models/fusion/lock.json", "models/fusion/fusion.json", "models/interference_v2/model.pkl",
            "models/interference_v2/threshold.json", "models/interference_v2/template.json",
            "models/replay/params.json", "models/replay/threshold.json", "models/replay/hash_store.json",
            "models/sequence/profile.json", "models/sequence/threshold.json", "keys/public/ed25519_public.pem",
            "config.yaml", "requirements.txt"] + [p.relative_to(ROOT).as_posix() for p in SAMPLE.rglob("*") if p.is_file()]
    r = subprocess.run(["git", "check-ignore", *need], cwd=ROOT, capture_output=True, text=True)
    assert r.stdout.strip() == "", f"ignored by .gitignore: {r.stdout}"


def test_run_attack_sequence_and_interference(tmp_path):
    fc = SAMPLE / "14932721"
    out = tmp_path / "seq"
    assert run_attack.main(["sequence", "--subtype", "swap", "--obs-dir", str(fc), "--out", str(out), "--no-png"]) == 0
    t = json.loads((tmp_path / "seq.truth.json").read_text(encoding="utf-8"))
    assert [f["subtype"] for f in t["attacked_frames"]] == ["swap", "swap"]
    assert (out / "audio.ogg").exists() and (out / "metadata.json").exists()
    assert len(list((out / "demoddata").iterdir())) == len(list((fc / "demoddata").iterdir()))
    out2 = tmp_path / "int"
    assert run_attack.main(["interference", "--shape", "tone", "--f0", "3000", "--snr-db", "5", "--t0", "10", "--t1", "60",
                            "--obs-dir", str(fc), "--out", str(out2)]) == 0
    ev = json.loads((tmp_path / "int.truth.json").read_text(encoding="utf-8"))["audio_events"][0]
    assert ev["f0_hz"] == 3000 and ev["snr_db"] == 5 and ev["segments"] == [[10.0, 60.0]]
    assert (tmp_path / "int.spectrogram.png").stat().st_size > 10_000
