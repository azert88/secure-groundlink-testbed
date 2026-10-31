"""Tests for the TRAIN-only downlink region."""

import numpy as np

from downlink_bands import region_from_bands


def test_region_needs_enough_train_observations():
    f = np.arange(0, 24000, 23.4375)
    per_obs = [[[11000, 12300]], [[11100, 12200]], [[11050, 12250], [3000, 3300]], []]
    region = region_from_bands(f, per_obs, 0.5)
    assert len(region) == 1 and 11000 <= region[0][0] <= 11150 and 12150 <= region[0][1] <= 12350
    assert region_from_bands(f, [], 0.5) == []
    assert region_from_bands(f, [[], []], 0.5) == []
