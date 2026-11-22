import math

import numpy as np

from detect import jam_v1 as J

P = J.load_params()


def blocks(levels_db, gain_db=None, dt=1.0):
    n = len(levels_db)
    return {"t0": np.arange(n) * dt, "t1": (np.arange(n) + 1) * dt, "inband": 10 ** (np.asarray(levels_db) / 10),
            "gain_db": np.zeros(n) if gain_db is None else np.asarray(gain_db, float)}


def test_params_match_gate():
    assert (P["window_frames"], P["loss_threshold"], P["consecutive"], P["p0_quantile"]) == (10, 0.3, 2, 99.0)
    assert P["inband_hz"] == [98000.0, 166000.0]


def test_windows():
    assert J.windows(60) == [(i, i + 10) for i in range(0, 60, 10)]
    assert J.windows(25) == [(0, 10), (10, 20), (20, 25)]
    assert J.windows(24) == [(0, 10), (10, 20)]


def test_span_power_weights_and_gain():
    b = blocks([-40, -30], gain_db=[0, 10])
    assert math.isclose(J.span_power_db(0, 1, b), -40)
    assert math.isclose(J.span_power_db(1, 2, b), -40)                          # gain removed
    assert math.isclose(J.span_power_db(1, 2, b, remove_gain=False), -30)
    assert math.isclose(J.span_power_db(0.5, 1.5, blocks([-40, -40])), -40)
    assert math.isnan(J.span_power_db(5, 6, b))


def test_window_rows_and_consecutive_rule():
    frames = [{"t0": i, "t1": i + 1, "lost": (10 <= i < 30) or i == 45} for i in range(60)]
    lv = [-40] * 10 + [-20] * 20 + [-40] * 30
    rows = J.window_rows(frames, blocks(lv), P)
    assert [r["lost"] for r in rows] == [0, 10, 10, 0, 1, 0]
    out = J.apply(rows, -30.0, P)
    assert [r["jamming_suspected"] for r in out] == [False, True, True, False, False, False]
    assert out[1]["alert_text"] == "jamming or strong interference suspected" and out[0]["alert_text"] == ""
    assert all(r["jamming_or_strong_interference_suspected"] == r["jamming_suspected"] for r in out)
    assert "attack" not in J.ALERT_TEXT
    single = J.apply(rows[:2], -30.0, P)                                         # one candidate alone: no alert
    assert single[1]["candidate"] and not single[1]["jamming_suspected"]
    assert not J.pass_alert(J.apply(rows, -10.0, P))


def test_fit_p0_rules():
    assert J.fit_p0([], P) == math.inf
    assert J.fit_p0([-3.0, -1.0, float("nan")], P) == -1.0                       # < 20 windows: maximum
    v = list(np.linspace(0, 99, 100))
    assert math.isclose(J.fit_p0(v, P), np.percentile(v, 99))


def test_lost_frame_power_ablation():
    frames = [{"t0": i, "t1": i + 1, "b0": i, "b1": i + 0.5, "lost": i == 3} for i in range(10)]
    r = J.window_rows(frames, blocks([-40] * 3 + [-10] + [-40] * 6, dt=0.5), P)
    assert math.isclose(r[0]["P_lost"], -40.0)                                  # frame 3 burst = block 6
