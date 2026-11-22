from pathlib import Path

import numpy as np
import pytest

from bench import link_jam_sim as S
from detect import jam_v1

HAVE_FRAMES = Path("data/tables/frames.csv").exists()


def test_conditions_and_seeds():
    assert len(S.CONDITIONS) == 7 + 3 * 6 and S.jam_name("pulsed", -5) == "jam_pulsed_-5"
    assert S.parse_jam("jam_fullband_+20") == ("fullband", 20) and S.parse_jam("fading") is None
    a = S.schedule("fading", S.rng_for("calib", "fading", 3))
    b = S.schedule("fading", S.rng_for("calib", "fading", 3))
    c = S.schedule("fading", S.rng_for("train", "fading", 3))
    assert np.array_equal(a["snr_db"], b["snr_db"]) and not np.array_equal(a["snr_db"], c["snr_db"])


def test_schedules_follow_gate():
    rng = np.random.default_rng(0)
    g = S.schedule("gain_step", rng)
    assert np.all(g["gain_db"][:30] == 0) and np.all(g["gain_db"][30:] == 10)
    r = S.schedule("noise_rise", rng)["rise_db"]
    assert np.all(r[:20] == 0) and np.all(r[40:] == 0) and 1 <= r[20] <= 6
    lo = S.schedule("low_snr", rng)["snr_db"]
    assert 2 <= lo[0] <= 8 and np.all(lo == lo[0])
    j = S.schedule("jam_pulsed_+5", rng)["jam"]
    assert j["kind"] == "pulsed" and j["sir_db"] == 5 and abs(j["offset_hz"]) <= 3000


def test_split_sources_are_disjoint():
    assert not set(S.source_obs("train")) & set(S.source_obs("calib"))


@pytest.mark.skipif(not HAVE_FRAMES, reason="data/tables/frames.csv is git-ignored")
def test_one_window_nominal_vs_fullband_jammer():
    p = jam_v1.load_params()
    clean = S.simulate_run("smoke", "nominal", 0, n_frames=10)
    jam = S.simulate_run("smoke", "jam_fullband_-10", 0, n_frames=10)
    assert clean["n_lost"] == 0 and jam["n_lost"] >= 8
    pc, pj = (jam_v1.window_rows(r["frames"], r["blocks"], p)[0]["P"] for r in (clean, jam))
    assert pj > pc + 10                                         # jammer raises in-band power
    assert jam["pilot_snr_db_windows"][0] < clean["pilot_snr_db_windows"][0]   # full band also hits the pilot
