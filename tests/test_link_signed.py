"""Mode B signed link (simulation only): 2-FSK PHY round trip, receiver verdicts, attacks (a)-(e)."""

import os

import numpy as np
import pytest

from bench import link_attacks as A
from bench import link_phy as PHY
from bench import link_signed as LS
from trust.session_keys import EpochState


@pytest.mark.parametrize("cfo", [20000.0, -20000.0])
def test_phy_round_trip_cfo_20khz_snr_12db(cfo):
    rng = np.random.default_rng(int(abs(cfo)) + (cfo < 0))
    frames = [rng.bytes(int(n)) for n in rng.integers(16, 360, 120)]
    iq = PHY.channel(PHY.modulate(frames), cfo_hz=cfo, snr_db=12.0, seed=7)
    got = PHY.demodulate(iq, return_cfo=True)
    ok = [p for _, p, c, _ in got if c]
    assert sum(1 for f in frames if f in ok) >= 0.99 * len(frames)
    assert all(abs(f - cfo) < 200 for *_, f in got)


def test_phy_sync_index_and_noise_only():
    iq = PHY.modulate([b"abc" * 30])
    (idx, payload, ok), = PHY.demodulate(PHY.channel(iq, cfo_hz=-5000.0, snr_db=30.0))
    assert ok and payload == b"abc" * 30
    assert abs(idx - (int(0.02 * PHY.FS) + 8 * len(PHY.PREAMBLE) * PHY.sps())) <= PHY.sps()
    rng = np.random.default_rng(0)
    noise = (0.01 * (rng.standard_normal(PHY.FS) + 1j * rng.standard_normal(PHY.FS))).astype(np.complex64)
    assert PHY.demodulate(noise) == []


def test_receiver_malformed_and_wrong_master(tmp_path):
    m = os.urandom(32)
    envs = LS.Sender(m, EpochState(tmp_path / "s.json")).seal_pass([(1000, b"x" * 40), (2000, b"y" * 40)])
    rx = LS.Receiver(os.urandom(32), EpochState(tmp_path / "r.json"))
    assert rx.receive(envs[0])["verdict"] == "bad_tag"
    assert rx.receive(envs[0][:50])["verdict"] == "malformed"
    rx = LS.Receiver(m, EpochState(tmp_path / "r2.json"))
    assert [rx.receive(e)["verdict"] for e in envs] == ["ok", "ok"]


@pytest.mark.parametrize("attack", A.ATTACKS)
def test_attack_gives_expected_rejection(attack):
    r = A.run(attack)
    assert r["received"] == r["sent"]                         # no PHY loss at 15 dB
    assert r["rejected"] == A.EXPECTED[attack], r["rejected"]
    if attack == "clean":
        assert r["accepted"] == r["sent"] and not r["rejected"]
    if attack == "a":
        assert r["setup"]["accepted"] == 16 and r["accepted"] == r["sent"] - 16
    if attack == "d_resync":
        assert [x["missing"] for x in r["rows"] if x["verdict"] == "ok_after_gap"] == [2]
