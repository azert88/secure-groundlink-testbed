"""Tests for the within-observation, benign-matched shortcut test (data/diag_shortcut.py) on synthetic variants."""

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import roc_auc_score

from diag_shortcut import assemble, balanced_weights, shortcut_markdown, shortcut_report

VC = {"shortcut_auc_dev_max": 0.1, "shortcut_level_min_snr_db": 15.0, "snr_bin_db": 5.0, "shortcut_forest_trees": 20}


def synthetic(seq_shift_db=0.0, seq_benign_shift_db=None, n_obs=20, seed=0):
    """Composition confound built in: test observations are longer and carry more attacked variants.

    Pure sequence variants = own clean + seq_shift_db. Sequence variants with a gain condition get a gain drawn
    like the benign variants', or seq_benign_shift_db if given (a benign-stratum mismatch)."""
    rng = np.random.default_rng(seed)
    rows, recipes = [], {}

    def add(i, vid, group, split, rms, dur, ops):
        rows.append({"obs_id": i, "variant_id": vid, "duration_s": dur, "peak_abs": 4 * 10 ** (rms / 20),
                     "rms_db": rms, "recipes_sha256": "x"})
        recipes[(i, vid)] = {"group": group, "split": split, "norad": 1, "operations": ops}
        cue_windows.append({"obs_id": i, "variant_id": vid, "n_windows": int(dur), "mean_power_db": rms + 3})

    cue_windows = []
    for i in range(n_obs):
        test = i >= n_obs * 2 // 3
        split, dur = ("test", 800.0 + 10 * i) if test else ("train", 400.0 + 10 * i)
        base = rng.normal(-45, 5)
        add(i, "clean", "clean", split, base, dur, [])
        for k in range(2):
            add(i, f"benign_{k}", "benign", split, base + rng.uniform(0, 6), dur, [{"type": "gain"}])
        for k in range(8 if test else 2):
            add(i, f"sequence_{k}", "sequence", split, base + seq_shift_db, dur, [{"type": "sequence"}])
        g = rng.uniform(0, 6) if seq_benign_shift_db is None else seq_benign_shift_db
        add(i, "sequence_b", "sequence", split, base + g, dur, [{"type": "gain"}, {"type": "sequence"}])
    wagg = pd.DataFrame(cue_windows).set_index(["obs_id", "variant_id"])
    return assemble(pd.DataFrame(rows), wagg, recipes)


def test_balanced_weights_half_per_class_per_stratum():
    w = balanced_weights([1, 1, 1, 2, 2, 3], [1, 1, 0, 1, 0, 1])
    assert w[:3] == pytest.approx([0.25, 0.25, 0.5]) and w[3:5] == pytest.approx([0.5, 0.5])
    assert w[5] == 0                                                   # stratum with one class only


def test_composition_confound_is_not_a_shortcut():
    sc = synthetic()
    assert abs(roc_auc_score(sc.y, sc.duration_s) - 0.5) > 0.1        # the pooled test is fooled
    rep = shortcut_report(sc, VC, 1)
    assert rep["ok"] and rep["n_chain_trace_failures"] == 0
    assert rep["n_structure_differs_from_own_clean"] == {"duration_s": 0, "n_windows": 0}
    row = next(r for r in rep["comparisons"] if r["comparison"] == "sequence vs not attacked")
    assert row["duration_s"] == 0.5 and row["rms_db"] == pytest.approx(0.5, abs=0.1)


def test_level_trace_on_power_neutral_attack_fails():
    rep = shortcut_report(synthetic(seq_shift_db=3.0), VC, 1)
    assert not rep["ok"] and rep["n_chain_trace_failures"] > 0
    assert "FAIL" in shortcut_markdown(rep)[0]


def test_benign_stratum_mismatch_is_a_documented_limitation():
    rep = shortcut_report(synthetic(seq_benign_shift_db=-6.0), VC, 1)
    assert rep["ok"] and rep["n_rule_failures"] > 0 and rep["n_chain_trace_failures"] == 0
    assert all(f["auc_pure_stratum"] == pytest.approx(0.5, abs=0.1) for f in rep["failures"])
    assert "documented limitations" in shortcut_markdown(rep)[0]
