"""Offline tests for Phase A (no network access)."""

import json
from datetime import datetime, timezone

import pytest

from satnogs_api import SatnogsClient, build_url, load_token, next_link
from select_satellites import (burst_stats, classify_mode, counter_fields, demod_filenames,
                               frame_time, grsat_supports, load_grsat_modes, prefilter)

CLASSES = {
    "narrow_1k2": {"baud": 1200, "match": "exact", "modes": ["AFSK", "BPSK"],
                   "grsat_modulations": ["AFSK", "BPSK", "DBPSK"]},
    "fsk_9k6": {"baud": 9600, "match": "prefix", "modes": ["FSK", "GMSK"],
                "grsat_modulations": ["FSK"]},
}
API_CFG = {"min_request_interval_s": 0, "max_retries": 2, "backoff_base_s": 0, "backoff_max_s": 0,
           "timeout_s": 5, "user_agent": "test"}


def test_classify_mode():
    assert classify_mode("AFSK", 1200.0, CLASSES) == "narrow_1k2"
    assert classify_mode("BPSK", 1200.0, CLASSES) == "narrow_1k2"
    assert classify_mode("BPSK PMT-A3", 1200.0, CLASSES) is None   # exact match for 1k2
    assert classify_mode("FSK AX.100 Mode 5", 9600.0, CLASSES) == "fsk_9k6"
    assert classify_mode("GMSK USP", 9600.0, CLASSES) == "fsk_9k6"
    assert classify_mode("FSK", 4800.0, CLASSES) is None
    assert classify_mode("FM", None, CLASSES) is None
    assert classify_mode("FSKX", 9600.0, CLASSES) is None           # prefix needs a word boundary
    assert classify_mode("GFSK", 9600.0, CLASSES) is None           # not in the allowed list
    assert classify_mode("MSK AX.100 Mode 5", 9600.0, CLASSES) is None


def test_frame_time_and_filenames():
    obs = {"demoddata": [{"payload_demod": "https://x/y/data_1_2026-10-26T01-02-05_1"},
                         {"payload_demod": "https://x/y/data_1_2026-10-26T01-02-05"}]}
    names = demod_filenames(obs)
    assert names == ["data_1_2026-10-26T01-02-05_1", "data_1_2026-10-26T01-02-05"]
    t = datetime(2026, 10, 26, 1, 2, 5, tzinfo=timezone.utc)
    assert all(frame_time(n) == t for n in names)
    assert frame_time("waterfall.png") is None
    for suffix in ("_g0", "_0015", ""):
        assert frame_time(f"data_1_2026-10-26T01-02-05{suffix}") == t


def test_burst_stats_regular_with_missed_burst():
    # bursts every 10 s with 3 frames each; burst at t=30 missed (interval 20 = 2 periods)
    times = [t + d for t in (0, 10, 20, 40, 50) for d in (0, 0, 1)]
    s = burst_stats(times, gap_s=2.0, tol_s=1.5)
    assert s["n_bursts"] == 5 and s["frames_per_burst_median"] == 3
    assert s["period_s"] == 10
    assert s["frac_regular"] == 1.0


def test_burst_stats_single_burst():
    s = burst_stats([5, 5, 6], gap_s=2.0, tol_s=1.5)
    assert s["n_bursts"] == 1 and s["period_s"] is None


def test_counter_fields():
    ksy = "seq:\n  - id: callsign\n  - id: uptime_ms\n  - id: frame_cnt\ntypes:\n  - id: eps_boot\n"
    assert counter_fields(ksy, r"(counter|cnt|uptime|seq)") == ["frame_cnt", "uptime_ms"]


def test_next_link_and_build_url():
    hdr = '<https://a/api/obs/?cursor=abc&x=1>; rel="next", <https://a/api/obs/?cursor=z>; rel="prev"'
    assert next_link(hdr) == "https://a/api/obs/?cursor=abc&x=1"
    assert next_link(None) is None
    assert build_url("https://a/", {"b": 2, "a": 1}) == "https://a/?a=1&b=2"


def test_load_token(tmp_path, monkeypatch):
    monkeypatch.delenv("SATNOGS_DB_TOKEN", raising=False)
    assert load_token(tmp_path / "missing.env", "SATNOGS_DB_TOKEN") == ""
    env = tmp_path / ".env"
    env.write_text("# comment\nSATNOGS_DB_TOKEN='abc123'\n", encoding="utf-8")
    assert load_token(env, "SATNOGS_DB_TOKEN") == "abc123"
    monkeypatch.setenv("SATNOGS_DB_TOKEN", "fromenv")
    assert load_token(env, "SATNOGS_DB_TOKEN") == "fromenv"


class FakeResp:
    def __init__(self, status, body, link=None):
        self.status_code, self.text = status, body
        self.ok = status < 400
        self.headers = {"Link": link} if link else {}


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls, self.headers = list(responses), [], {}

    def get(self, url, headers=None, timeout=None, stream=False):
        self.calls.append((url, headers))
        return self.responses.pop(0)


def test_client_retries_caches_and_paginates(tmp_path):
    page2 = "https://a/obs/?cursor=2"
    sess = FakeSession([FakeResp(429, ""), FakeResp(200, json.dumps([1, 2]), f'<{page2}>; rel="next"'),
                        FakeResp(200, json.dumps([3]))])
    c = SatnogsClient(API_CFG, tmp_path, token="secret", session=sess)
    pages = list(c.iter_pages("https://a/obs/", {"q": 1}, max_pages=5))
    assert pages == [([1, 2], True), ([3], False)]
    assert len(sess.calls) == 3                              # one retry after 429
    assert all(h == {} for _, h in sess.calls)               # no token on public endpoints
    # second pass is served from the cache only, and the token never reaches the cache
    assert list(c.iter_pages("https://a/obs/", {"q": 1}, max_pages=5)) == pages
    assert len(sess.calls) == 3
    assert not any("secret" in p.read_text() for p in tmp_path.iterdir())


def test_client_sends_token_only_when_auth(tmp_path):
    sess = FakeSession([FakeResp(200, "[]")])
    c = SatnogsClient(API_CFG, tmp_path, token="secret", session=sess)
    c.get_json("https://db/artifacts/", {"network_obs_id": 1}, auth=True)
    assert sess.calls[0][1] == {"Authorization": "Token secret"}


def test_client_raises_on_http_error(tmp_path):
    sess = FakeSession([FakeResp(401, '{"detail": "no"}')])
    c = SatnogsClient(API_CFG, tmp_path, session=sess)
    with pytest.raises(RuntimeError):
        c.get_json("https://db/artifacts/")


def test_grsat_supports():
    assert grsat_supports({("FSK", 9600)}, CLASSES["fsk_9k6"])          # GMSK decoded as FSK
    assert grsat_supports({("DBPSK", 1200)}, CLASSES["narrow_1k2"])
    assert not grsat_supports({("FSK", 4800)}, CLASSES["fsk_9k6"])
    assert not grsat_supports({("BPSK", 9600)}, CLASSES["fsk_9k6"])
    assert not grsat_supports({("FSK", 1200)}, CLASSES["narrow_1k2"])


def test_load_grsat_and_prefilter(tmp_path):
    for name, norad, baud in (("a", 1, 9600), ("b", 2, 4800)):
        (tmp_path / f"{name}.yml").write_text(
            f"name: {name}\nnorad: {norad}\ntransmitters:\n  t:\n    modulation: FSK\n"
            f"    baudrate: {baud}\n", encoding="utf-8")
    grsat = load_grsat_modes(tmp_path)
    assert grsat[1]["modes"] == {("FSK", 9600)}
    cands = {(1, "fsk_9k6"): {}, (2, "fsk_9k6"): {}, (3, "fsk_9k6"): {}, (1, "narrow_1k2"): {}}
    kept, counts = prefilter(cands, grsat, CLASSES)
    assert kept == [(1, "fsk_9k6")]
    assert counts["fsk_9k6"] == {"mode_ok": 3, "not_in_grsat": 1, "grsat_mode_mismatch": 1, "kept": 1}
    assert counts["narrow_1k2"]["grsat_mode_mismatch"] == 1


class FakePager:
    """Serves pages of observations; counts how many pages were requested."""

    def __init__(self, pages):
        self.pages, self.served = pages, 0

    def iter_pages(self, url, params, max_pages=1, auth=False):
        for i, page in enumerate(self.pages[:max_pages]):
            self.served += 1
            yield page, i < len(self.pages) - 1


def _obs(i, station, uuid="tx1", norad=7):
    return {"id": i, "norad_cat_id": norad, "transmitter_uuid": uuid, "status": "good",
            "ground_station": station, "demoddata": [{"payload_demod": f"u/data_{i}_2026-10-01T00-00-0{i % 10}"}]}


def test_scan_candidate_early_stop():
    from select_satellites import scan_candidate
    cfg = {"satnogs": {"network_api": "n"},
           "selection": {"max_pages_per_transmitter": 10, "min_good_obs_with_demod": 50,
                         "min_stations": 10}}
    pages = [[_obs(p * 25 + k, (p * 25 + k) % 12) for k in range(25)] for p in range(4)]
    cand = {"name": "X", "transmitters": [{"uuid": "tx1", "mode": "AFSK"}]}
    pager = FakePager(pages)
    row, _ = scan_candidate(pager, cfg, 7, cand, ("a", "b"), early_stop=True)
    assert pager.served == 2 and row["stopped_early"] and not row["exact"] and row["meets_all"]
    pager = FakePager(pages)
    row, _ = scan_candidate(pager, cfg, 7, cand, ("a", "b"))
    assert pager.served == 4 and row["exact"] and row["n_good_with_demod"] == 100


def test_scan_candidate_rejects_ignored_filter():
    from select_satellites import scan_candidate
    cfg = {"satnogs": {"network_api": "n"}, "selection": {"max_pages_per_transmitter": 1,
           "min_good_obs_with_demod": 1, "min_stations": 1}}
    cand = {"name": "X", "transmitters": [{"uuid": "tx1", "mode": "AFSK"}]}
    with pytest.raises(RuntimeError):
        scan_candidate(FakePager([[_obs(1, 1, norad=99)]]), cfg, 7, cand, ("a", "b"))
