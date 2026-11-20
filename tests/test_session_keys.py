"""S8: per-pass session keys (HKDF) and anti-rollback, end to end with the Phase 1 envelope."""

from __future__ import annotations

import pytest

from trust.envelope import Sealer, Verifier
from trust.session_keys import (EpochState, RollbackError, derive_session_key, epoch_of, make_session_id,
                                new_master_key)


def test_both_sides_derive_the_same_key_and_keys_differ_per_session():
    m = new_master_key()
    s1, s2 = make_session_id(1), make_session_id(2)
    assert derive_session_key(m, s1, 39444) == derive_session_key(m, s1, 39444)
    assert derive_session_key(m, s1, 39444) != derive_session_key(m, s2, 39444)
    assert derive_session_key(m, s1, 39444) != derive_session_key(m, s1, 25397)
    assert derive_session_key(m, s1, 39444) != derive_session_key(new_master_key(), s1, 39444)


def test_epoch_encoding():
    assert epoch_of(make_session_id(7)) == 7
    with pytest.raises(ValueError):
        epoch_of(b"short")


def test_sender_epochs_increase_and_persist(tmp_path):
    st = EpochState(tmp_path / "tx.json")
    m = new_master_key()
    a, _ = st.next_session(m, 39444)
    b, _ = EpochState(tmp_path / "tx.json").next_session(m, 39444)      # new process, same state file
    assert epoch_of(b) == epoch_of(a) + 1


def test_station_rejects_replayed_session(tmp_path):
    m = new_master_key()
    sid, key = EpochState(tmp_path / "tx.json").next_session(m, 39444)
    env = Sealer(sid, key).seal(b"frame-0")
    rx = EpochState(tmp_path / "rx.json")
    k_rx = rx.accept(m, sid, 39444)
    ok, why, body = Verifier(sid, k_rx).verify(env)
    assert ok and why == "ok" and body == b"frame-0"
    rx.commit(sid)
    with pytest.raises(RollbackError):                     # the whole recorded pass replayed later
        rx.accept(m, sid, 39444)


def test_wrong_master_key_gives_bad_tag(tmp_path):
    sid, key = EpochState(tmp_path / "tx.json").next_session(new_master_key(), 39444)
    env = Sealer(sid, key).seal(b"frame")
    k_other = derive_session_key(new_master_key(), sid, 39444)
    assert Verifier(sid, k_other).verify(env)[1] == "bad_tag"


def test_holdout_rule_is_deterministic_and_overlay_merges():
    import importlib.util
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "data" / "holdout_p2" / "fetch_holdout.py"
    spec = importlib.util.spec_from_file_location("fetch_holdout", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    cfg = m.merged_config()
    assert cfg["selection"]["window_end_utc"] == "2026-12-01T00:00:00Z" and cfg["seed"] == 20261103
    assert cfg["download"]["raw_dir"] == "data/holdout_p2/raw"
    sets = [m.assign(i, "tsyp14-p2-holdout/", 0.75) for i in range(15000000, 15002000)]
    assert sets == [m.assign(i, "tsyp14-p2-holdout/", 0.75) for i in range(15000000, 15002000)]
    assert 0.70 < sets.count("holdout") / len(sets) < 0.80
