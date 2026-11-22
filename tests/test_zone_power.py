import numpy as np

from bench import dsp, iqfile, zone_power as Z

FS = 528000


def tone(f, a, n=FS // 2):
    return a * np.exp(2j * np.pi * f * np.arange(n) / FS)


def test_tone_lands_in_its_zone():
    r = Z.block_power(tone(130000, 0.1) + tone(60000, 0.01), FS)
    assert len(r["t0"]) == 10 and np.allclose(r["t1"] - r["t0"], 0.05)
    assert np.allclose(r["inband"], 0.01, rtol=1e-3)
    assert np.allclose(r["pilot"], 1e-4, rtol=1e-3)
    assert np.all(r["floor"] < 1e-12)


def test_partial_block_and_parseval():
    rng = np.random.default_rng(0)
    x = (rng.standard_normal(30000) + 1j * rng.standard_normal(30000)) / np.sqrt(2)
    r = Z.block_power(x, FS, {"all": (-FS, FS)}, t0=2.0)
    assert len(r["t0"]) == 2 and r["t0"][0] == 2.0 and np.isclose(r["t1"][-1], 2.0 + 30000 / FS)
    assert np.isclose(r["all"][0], np.mean(np.abs(x[:26400]) ** 2))
    assert np.isclose(r["all"][1], np.mean(np.abs(x[26400:]) ** 2))


def test_capture_power_from_sigmf(tmp_path):
    base = tmp_path / "cap"
    x = tone(140000, 0.25, FS)
    data, _ = iqfile.paths(base)
    dsp.to_cs16(x, 2048).tofile(data)
    iqfile.write_meta(base, FS, 433.92e6, extra={"full_scale": 2048, "rx_gain_db": 30})
    r = Z.capture_power(base, chunk_blocks=7)
    assert len(r["t0"]) == 20 and np.all(r["rx_gain_db"] == 30)
    assert np.allclose(Z.db(r["inband"]), 20 * np.log10(0.25), atol=0.05)
    Z.write_csv(tmp_path / "z.csv", r)
    assert (tmp_path / "z.csv").read_text().splitlines()[0].startswith("t0_s,t1_s,inband_dbfs")
