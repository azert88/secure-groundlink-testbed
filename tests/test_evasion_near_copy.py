"""Near-copy replay evasion (attacks.replay.near_copy, eval/evasion_near_copy.py)."""

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from attacks.replay import near_copy, protected_bytes  # noqa: E402
from detect.counters import counter_codec  # noqa: E402
from detect.hash_store import qc_view  # noqa: E402
from eval.evasion_near_copy import make_near_copy  # noqa: E402

QC = qc_view(yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")))


def _funcube_frame(value: int) -> bytes:
    _, enc = counter_codec(39444, QC)
    b = bytearray(256)
    b[0] = QC["funcube"]["satid"] << 6
    return enc(bytes(b), value)


def _techsat_frame(value: int) -> bytes:
    _, enc = counter_codec(25397, QC)
    b = bytearray(b"\x01" * 44)
    b[20:27] = b"TechSat"
    return enc(bytes(b), value)


def test_one_byte_changed_counter_kept():
    for norad, frame in ((39444, _funcube_frame(1234567)), (25397, _techsat_frame(987654))):
        dec, _ = counter_codec(norad, QC)
        for seed in range(50):
            h, i = near_copy(frame.hex(), norad, QC, seed)
            new = bytes.fromhex(h)
            diff = [k for k in range(len(frame)) if new[k] != frame[k]]
            assert diff == [i] and i not in protected_bytes(norad, QC, frame)
            assert dec(new) == dec(frame) is not None


def test_deterministic_and_only_replayed_frames():
    f = _funcube_frame(100).hex()
    assert near_copy(f, 39444, QC, 7) == near_copy(f, 39444, QC, 7)
    res = {"frames": [{"hex": f, "attack_type": ""}, {"hex": f, "attack_type": "replay"}]}
    assert make_near_copy(res, 39444, QC, 1, "v") == 1
    assert res["frames"][0]["hex"] == f and res["frames"][1]["hex"] != f
