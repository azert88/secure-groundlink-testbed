"""Offline tests for Phase B (no network access)."""

from collections import Counter

from download_satnogs import (append_manifest, download_observation, files_for, is_eligible,
                              load_manifest, project, select_observations)


def _obs(i, station, n_demod=2):
    return {"id": i, "norad_cat_id": 7, "ground_station": station, "status": "good",
            "start": "2026-10-01T00:00:00Z", "end": "2026-10-01T00:10:00Z",
            "payload": f"https://cdn/satnogs_{i}.ogg", "waterfall": f"https://s3/wf_{i}.png",
            "demoddata": [{"payload_demod": f"https://s3/data_{i}_2026-10-01T00-01-0{k}_g0"}
                          for k in range(n_demod)]}


def test_is_eligible():
    assert is_eligible(_obs(1, 1))
    assert not is_eligible(_obs(1, 1, n_demod=0))
    o = _obs(1, 1)
    o["payload"] = None
    assert not is_eligible(o)


def test_select_caps_spreads_and_is_deterministic():
    obs = [_obs(i, 1) for i in range(20)] + [_obs(100 + i, 2) for i in range(3)] + [_obs(200, 3)]
    a = select_observations(obs, target=None, cap=12, seed=5)
    assert Counter(o["ground_station"] for o in a) == {1: 12, 2: 3, 3: 1}
    assert [o["id"] for o in a] == [o["id"] for o in select_observations(obs, None, 12, 5)]
    # round-robin: the first 3 picks come from 3 different stations
    assert len({o["ground_station"] for o in a[:3]}) == 3
    b = select_observations(obs, target=5, cap=6, seed=5)
    assert len(b) == 5 and len({o["id"] for o in b}) == 5


def test_files_for_keeps_original_names():
    rel = [r for r, _ in files_for(_obs(9, 1))]
    assert rel == ["audio.ogg", "waterfall.png", "demoddata/data_9_2026-10-01T00-01-00_g0",
                   "demoddata/data_9_2026-10-01T00-01-01_g0"]


class FakeClient:
    def __init__(self):
        self.n_network_requests = 0
        self.network_api = "https://net/api"

    def download(self, url, dest):
        self.n_network_requests += 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(url.encode())
        return dest


def test_download_resumes_from_manifest(tmp_path):
    client, o = FakeClient(), _obs(9, 1)
    manifest_path = tmp_path / "manifest.csv"
    manifest = load_manifest(manifest_path)
    st = download_observation(client, o, tmp_path / "raw", manifest)
    append_manifest(manifest_path, st.pop("_new_rows"))
    assert st["n_requests"] == 4 and (tmp_path / "raw/9/metadata.json").exists()
    # second run: everything matches the manifest, no requests, no new rows
    st2 = download_observation(client, o, tmp_path / "raw", load_manifest(manifest_path))
    assert st2["n_requests"] == 0 and st2["n_skipped"] == 4 and st2["_new_rows"] == []
    # a corrupted file is downloaded again
    (tmp_path / "raw/9/audio.ogg").write_bytes(b"broken")
    st3 = download_observation(client, o, tmp_path / "raw", load_manifest(manifest_path))
    assert st3["n_requests"] == 1


def test_project_scales_with_demod_count():
    pilot = [{"norad": 7, "audio_bytes": 600, "duration_s": 600, "waterfall_bytes": 100,
              "demod_bytes": 20, "n_demod": 2, "n_requests": 4, "seconds": 12.0}]
    sel = {7: [_obs(1, 1, n_demod=2), _obs(2, 1, n_demod=8)]}
    p = project(pilot, sel, interval_s=3.0)
    assert p["per_satellite"][7]["requests"] == (2 + 2) + (2 + 8)
    assert p["total"]["hours"] == round(14 * 3.0 / 3600, 2)
    assert p["per_satellite"][7]["bytes"] == round(2 * (600 + 100) + 10 * 10)
