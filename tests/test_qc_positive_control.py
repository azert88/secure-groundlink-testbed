"""Test for the line-screen positive control (synthetic audio)."""

import numpy as np

from attacks.interference import reference_powers
from qc_positive_control import control_case
from test_qc import QC


def test_control_flags_tone_and_noise_band_at_minus_10_db():
    sr, rng = 48000, np.random.default_rng(1)
    x = rng.standard_normal(30 * sr).astype(np.float32)
    ref = reference_powers(x, sr, QC["psd"], 100.0, 20000.0, 20000.0)
    for case in ({"shape": "tone", "f0_hz": 4000.0}, {"shape": "noise", "f0_hz": 7000.0, "bw_hz": 150.0}):
        rec = control_case(x, sr, QC, 20000.0, ref, case, -10.0, rng)
        assert rec["flagged"], case
        assert abs(10 * np.log10(rec["p_ref"] / rec["p_interf"]) + 10) < 1e-9
