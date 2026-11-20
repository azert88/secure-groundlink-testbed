"""eval/s1_natural_rates.py helpers on synthetic frames (masking, within / across / concurrent, same counter)."""

import numpy as np

from eval.s1_natural_rates import counter_pairs, cum, masked_min_dist


def fr(obs, data, t, mask_pos=(), counter=None, ctol=60.0):
    m = np.zeros(len(data), bool)
    m[list(mask_pos)] = True
    return {"obs": obs, "data": bytes(data), "t": t, "mask": m, "counter": counter, "ctol": ctol}


def test_masked_distance_within_across_and_concurrent():
    a = bytes(16)
    b = bytearray(a)
    b[3] = 1                                   # masked position: does not count
    b[9] = 1                                   # counts: distance 1
    c = bytearray(a)
    c[9], c[10] = 1, 1                         # distance 2 to a, 1 to b
    frames = [fr(1, a, 0.0, (3,)), fr(1, b, 30.0, (3,)),          # same pass
              fr(2, c, 10_000.0, (3,)),                          # other pass, far in time
              fr(3, a, 5.0, (3,))]                               # other station, same pass (concurrent)
    d = masked_min_dist(frames)
    assert d["within"][0] == 1 and d["within"][1] == 1
    assert d["across"][2] == 1                                   # to b (frame 3 is not concurrent with frame 2)
    assert d["concurrent"][0] == 0 and d["concurrent"][3] == 0
    assert d["across"][0] == 2                                   # concurrent copy excluded from 'across'
    assert np.isinf(d["within"][2])


def test_counter_pairs_and_cumulative_counts():
    frames = [fr(1, b"\x01\x02", 0.0, counter=7), fr(1, b"\x01\x03", 30.0, counter=7),   # within, different bytes
              fr(2, b"\x01\x04", 9_000.0, counter=7),                                     # across
              fr(3, b"\x01\x02", 10.0, counter=7)]                                        # concurrent, same bytes as 0
    assert counter_pairs(frames) == {"within_pass": 1, "across_passes": 3, "concurrent": 1}
    c = cum([0.0, 1.0, 3.0, np.inf])
    assert c["0"] == 1 and c["2"] == 2 and c["4"] == 3 and c["none"] == 1
