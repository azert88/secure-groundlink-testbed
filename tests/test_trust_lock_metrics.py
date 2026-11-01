"""Ed25519 manifests (trust/manifest.py), the test lock (eval/lock.py) and shared metrics (eval/metrics.py)."""

import json

import numpy as np
import pytest

from eval import metrics as M
from eval.lock import LockError, freeze, require_lock
from trust import manifest as tm


@pytest.fixture
def keys(tmp_path):
    priv, pub = tmp_path / "k" / "priv.pem", tmp_path / "k" / "pub.pem"
    tm.keygen(priv, pub)
    return priv, pub


def test_sign_verify_and_tamper(keys, tmp_path):
    priv, pub = keys
    f = tmp_path / "model.json"
    f.write_text("{}")
    m = tm.sign(tm.make_manifest([f], "x"), priv)
    assert tm.verify(m, pub)
    assert not tm.verify({**m, "label": "y"}, pub)                   # any change breaks the signature
    with pytest.raises(FileExistsError):
        tm.keygen(priv, pub)


def test_lock_freeze_check_and_change(keys, tmp_path, monkeypatch):
    priv, pub = keys
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models" / "r").mkdir(parents=True)
    f = tmp_path / "models" / "r" / "threshold.json"
    f.write_text(json.dumps({"tau": 0.5}))
    freeze("r", [f.relative_to(tmp_path)], "models", priv)
    assert require_lock("r", "models", pub)["files"]
    with pytest.raises(LockError):
        freeze("r", [f.relative_to(tmp_path)], "models", priv)          # never re-frozen
    f.write_text(json.dumps({"tau": 0.4}))                             # retuned after freezing
    with pytest.raises(LockError):
        require_lock("r", "models", pub)
    with pytest.raises(LockError):
        require_lock("missing", "models", pub)


def test_threshold_for_fpr_pooled_and_per_group():
    neg = np.r_[np.zeros(990), np.full(10, 0.5)]
    assert M.threshold_for_fpr(neg, 0.01) == 0.5                      # 10 / 1000 = 1 % allowed
    g = np.r_[np.zeros(990), np.ones(10)]                             # all hits in one small group
    assert M.threshold_for_fpr(neg, 0.01, g) > 0.5


def test_binary_eer_bootstrap():
    b = M.binary([1, 1, 0, 0], [1, 0, 1, 0])
    assert b["precision"] == 0.5 and b["recall"] == 0.5 and b["fpr"] == 0.5
    assert M.eer([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) == 0.0
    assert abs(M.eer([0, 1, 0, 1], [0.5, 0.5, 0.5, 0.5]) - 0.5) < 1e-9
    assert np.isfinite(M.auc([0, 1, 1], [np.nan, 1.0, np.inf]))
    v, lo, hi = M.bootstrap_ci(np.array([1.0, 1.0, 1.0, np.nan]))
    assert v == lo == hi == 1.0
