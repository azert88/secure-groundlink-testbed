"""Phase M: the per-frame envelope rejects replay, reorder, deletion, modification and forgery deterministically."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from trust import envelope as E  # noqa: E402

SID, KEY = bytes(range(16)), bytes(range(32))
FRAMES = [bytes([i]) * 44 for i in range(6)]


def stream():
    s = E.Sealer(SID, KEY)
    return [s.seal(f, 1000 * i) for i, f in enumerate(FRAMES)]


def run(envs):
    v = E.Verifier(SID, KEY)
    return [v.verify(e)[1] for e in envs]


def test_clean_stream_accepted_and_payload_returned():
    envs = stream()
    v = E.Verifier(SID, KEY)
    assert [v.verify(e)[2] for e in envs] == FRAMES
    assert all(len(e) == len(f) + E.OVERHEAD for e, f in zip(envs, FRAMES)) and E.OVERHEAD == 102


def test_replay_rejected():
    e = stream()
    assert run(e[:4] + [e[1]] + e[4:]) == ["ok"] * 4 + ["replay", "ok", "ok"]


def test_reorder_rejected():
    e = stream()
    # the early frame is rejected and not remembered: the stream stays broken until it is sent again in order
    assert run([e[0], e[2], e[1], e[3]]) == ["ok", "gap", "ok", "gap"]
    assert run([e[0], e[2], e[1], e[2], e[3]]) == ["ok", "gap", "ok", "ok", "ok"]


def test_deletion_rejected():
    e = stream()
    assert run(e[:2] + e[3:]) == ["ok", "ok", "gap", "gap", "gap"]


def test_modification_rejected():
    e = stream()
    bad = bytearray(e[2])
    bad[E.HEADER.size + 5] ^= 1                      # one payload bit
    hdr = bytearray(e[3])
    hdr[25] ^= 1                                     # counter field
    assert run(e[:2] + [bytes(bad), e[2], bytes(hdr), e[3]]) == ["ok", "ok", "bad_tag", "ok", "bad_tag", "ok"]


def test_forged_frame_without_key_rejected():
    e = stream()
    forger = E.Sealer(SID, b"\x00" * 32, counter=2, prev=E.hashlib.sha256(e[1]).digest())
    assert run(e[:2] + [forger.seal(b"x" * 44, 2000)]) == ["ok", "ok", "bad_tag"]


def test_other_session_and_chain_splice_rejected():
    other = E.Sealer(bytes(16), KEY).seal(FRAMES[0], 0)
    assert run([other]) == ["wrong_session"]
    e = stream()
    splice = E.Sealer(SID, KEY, counter=2, prev=bytes(32)).seal(b"y" * 44, 2000)   # valid tag, wrong chain
    assert run(e[:2] + [splice]) == ["ok", "ok", "chain"]


def test_stream_file_roundtrip(tmp_path):
    e = stream()
    E.write_stream(tmp_path / "s.bin", e)
    assert E.read_stream(tmp_path / "s.bin") == e


def test_bench_small():
    r = E.bench(n=200)
    assert r["overhead_bytes_per_frame"] == 102 and all(v["all_ok"] for v in r["per_frame_size"].values())
