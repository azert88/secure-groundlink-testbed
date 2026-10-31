# Dataset provenance and datasheet

Datasheet written in Phase I (2026-10-31), following *Datasheets for Datasets* (Gebru et al.). The
build specification is `docs/DATASET_SPEC.md`; every decision quoted below as "decision N" is
recorded with its reason in `docs/PROGRESS.md`. Parameters live in `config.yaml` (seed 20261103).

## 1. Motivation

- **Purpose.** A testbed for detecting attacks on a satellite ground link from what a ground station
  records: replay of a valid 5–20-frame subsequence, one injected sequence anomaly, and narrowband
  interference added digitally at a hidden SNR. Benign conditions (gain change, noise rise, fading,
  weak signal at pass edges, naturally repeated beacons) are included so that false alarms can be
  measured, since the false-alarm rate is a tie-breaker.
- **Task.** Built for Track 1 ("Ground Truth Under Attack") of the IEEE TSYP14 challenge. The
  submission is anonymous: this datasheet names no person, team or institution.
- **Funding.** None declared.

## 2. Composition

- **Instances.** One instance = one *variant* of one SatNOGS observation: the clean recording, or a
  copy with a recipe applied (attack and/or benign condition). Each variant has a frame table
  (one row per decoded frame), a window table (one row per 1 s audio window, 0.5 s hop) and labels.
- **Sources.** 2 satellites, 77 observations in the tables (out of 96 downloaded):

  | satellite | NORAD | mode | observations | train / calib / test |
  |---|---|---|---|---|
  | TECHSAT 1B | 25397 | FSK 9k6 beacon (44-byte frames, uint32 seconds counter) | 42 | 26 / 8 / 8 |
  | FUNCUBE-1 | 39444 | BPSK 1k2 telemetry (256-byte frames, `seqnumber` counter) | 35 | 22 / 7 / 6 |

  Clean tables: 990 canonical frames, 71 114 windows. 74 observations have usable audio (audio QC),
  the others are frame-only.
- **Variants.** 1017 recipes (`data/recipes/<obs_id>/<variant_id>.json`), 1002 of them with audio:
  clean 77, benign 222, interference 267, SNR grid 140, replay 65, sequence 72, combo 147,
  evasion 27. Replay length L: 5–20 frames (median 11). Sequence subtypes: deletion, swap,
  duplicate, forged, timing, counter_jump (exactly one anomaly per variant). Interference: tone or
  20–200 Hz noise band, full pass or 5–60 s segment, snr_db ~ U[−10, +30] dB, plus a test-only grid
  −10…+35 dB. Evasion (test only): cross-pass replay, intermittent, near-threshold (+20…+30 dB),
  drifting and strongest interference. Benign: 1–2 of gain, noise rise, fading, edge attenuation.
  Some attacked variants also carry one benign condition underneath the attack (recipe probability
  0.3 where allowed; 14 % of attacked audio variants in the end).
- **Labels.** `data/labels/labels.csv` (clean tables) and `data/features/<obs_id>/labels.csv`
  (variants): per frame and per window `is_attack`, `attack_type`, `subtype`, `snr_db`, `inr_db`,
  `overlap_frac`. A window is attacked when the attack is active for ≥ 0.5 s of it. Label files are
  never read by `detect/` (checked in Phase H).
- **SNR definition.** snr_db = 10 log10(P_ref / P_interf) is a signal-to-interference ratio:
  P_ref = median in-band power of the clean observation over all its windows in [f_low, f_edge]
  (decision 56); low snr_db means strong interference (decision 62). `inr_db` is also given.
- **Split assignment.** By observation, never by frame or window (`data/splits.json`,
  SHA-256 `1b9ee1b8…19c2`). Test stations do not appear in train: TECHSAT 1B test stations 1664, 4073,
  4188, 5049; FUNCUBE-1 test stations 1763, 4806 (decisions 23, 30, 54).
- **Attacked share.** Attacked windows: 19–39 % of all windows, depending on satellite and split.
  Attacked frames: 0–24 %. Full tables are in `data/validation_report.md` section 5.
- **Personal information.** Stations are identified by their numeric SatNOGS station id only.
  Usernames and station coordinates from the API metadata are kept only in `data/raw/` (never
  distributed) and do not appear in any derived table or document (decision 29).

## 3. Collection process

- **APIs.** SatNOGS Network API (`https://network.satnogs.org/api`) for observations and files,
  SatNOGS DB API (`https://db.satnogs.org/api`) for satellites and transmitters. At most one request
  every 3 s, exponential backoff on 429/5xx, every response cached on disk (`data/cache/api/`).
- **Selection** (Phase A, decisions 1–22). Window 2026-08-30 to 2026-10-29 UTC (60 days, fixed).
  Filters: satellite status "in orbit"; observation status good with demodulated data; modes AFSK/BPSK
  1k2 or FSK/GMSK 9k6 supported by gr-satellites v5.9.0 (tarball SHA-256 pinned in `config.yaml`);
  at least 50 good observations with data and at least 10 stations (7 for the 1k2 class); a regular
  burst rhythm; a frame counter where possible.
- **Download** (Phase B). Pilot 2026-10-29, full run 2026-10-30. 96 observations: TECHSAT 1B 60
  (at most 6 per station), FUNCUBE-1 36 (at most 12 per station). Seeded per-station shuffle with
  round-robin (decision 25). Files: audio (`.ogg`), demodulated frames, waterfall, metadata. Each
  file's URL, UTC download time, size and SHA-256 are in `data/raw/manifest.csv`, summarised per run
  in Appendix A below. `.h5` artifacts were not fetched (they need a DB token).
- **Quality control** (Phase C). Audio QC: readable, 48 kHz mono, duration within 10 s of the
  scheduled pass. Frame QC: ≥ 10 canonical frames (byte-identical rewrites merged, decision 45/50).
  Narrowband-line screen with a local baseline and a TRAIN-only downlink band (decisions 43, 55),
  checked with a positive control on real audio (decision 52). Timing reliability flag per
  observation (decision 34).

## 4. Preprocessing

- **Decoding.** SatNOGS demodulated frames; FUNCUBE-1 counter = `seqnumber` × 24 + frame type
  (decision 31); TECHSAT 1B beacon check by length and the `TechSat` marker, counter = uint32 seconds
  counter (decision 39).
- **Common processing chain** (spec 6.1, decision 53), applied to EVERY variant including clean:
  multiply by 0.5 (headroom), encode to Ogg Vorbis at compression level 0.4, decode. Processing is
  in float; nothing is clipped. Decoded SatNOGS audio is not bounded to ±1: post-chain peak |x| is up
  to 2.09 on clean variants and 18.3 on one benign variant (decision 67). This is a property of the
  source audio, not a defect; detection features are mostly scale-invariant dB ratios.
- **Windows.** 1 s windows, 0.5 s hop, Welch PSD (nperseg 2048, overlap 1024) over the feature band
  [f_low = 100 Hz, f_drop − 200 Hz] (decision 48). Per-window features (`detect/features.py`):
  power_db, noise_floor_db, peak_to_median_db, peak_freq_hz, peak_width_hz, flatness, spectral
  entropy and kurtosis, occupied bandwidth, peak persistence, deviation from the observation median
  and from a TRAIN-only clean template (decision 41).
- **Raw data.** `data/raw/` is kept exactly as downloaded and is never modified.

## 5. Validation (Phase H, `data/validation_report.md`)

All 7 checks pass: leakage, label consistency (1017 variants, 0 problems), shortcut test,
single-feature baseline per SNR bin (reported), distribution (reported), reproducibility (2
observations rebuilt twice, byte-identical), recipe round-trip. The shortcut test is within
observation and matched on benign conditions (decisions 63–65). Structure cues equal the variant's
own clean in all 1002 audio variants, and no processing-chain trace was found. It passes with 5
documented limitations on TECHSAT 1B (section 6).

## 6. Uses and limitations

- **Intended use.** Training, calibration and testing of replay, sequence and interference
  detectors, and measuring false alarms under benign conditions. Train on `train`, calibrate
  thresholds on `calib`, report on `test`.
- **Binding rules for detectors** (decision 66): per-observation sample weights that balance attacked
  and not-attacked samples inside each observation; metrics per observation, then averaged; no
  duration, number of windows or observation-level metadata (station, split, observation id) as
  features; after training, feature importances must be reported, and absolute-level features
  (noise_floor_db, power_db) must not dominate.
- **Limitations.**
  - Timestamps have 1 s resolution (SatNOGS file names). Some FUNCUBE-1 passes have batched writes
    (`timing_reliable = False`): their timing features are NaN and frames are ordered by counter.
  - Audio only: no IQ recordings. Interference is added to demodulated audio, not at RF.
  - Sequence anomalies are frame-level only. Forged frames are not re-modulated: the audio is left
    untouched for every sequence subtype.
  - Replay audio is pasted around each copied frame (airtime FUNCUBE-1 4.33 s, TECHSAT 1B 0.1 s,
    margin 0.5 s, decision 59). Frame copies within the rewrite-merge window (FUNCUBE-1 0 s,
    TECHSAT 1B 5 s) would be merged as rewrites; generated copies are always ≥ 10 s away.
  - Small test set: TECHSAT 1B 8 observations, FUNCUBE-1 6 observations from 2 stations. Confidence
    intervals must be bootstrapped at observation level (decision 58).
  - Uneven composition (report it, do not learn it). Test observations carry 25–27 attacked
    variants against 3–9 elsewhere. TECHSAT 1B test passes are longer (median 870 s against
    518–533 s). SNR grid, evasion and TECHSAT 1B replay, sequence and combo variants exist only in test.
    75 % of not-attacked variants carry a benign condition, against 14 % of attacked ones.
  - Most TECHSAT 1B passes decode only frame fragments (fewer than 10 frames). Its replay and sequence
    variants come from the few frame-QC passes, all in test.
  - Shortcut-test limitations (TECHSAT 1B, 12 variants on 6 observations):
    - replay rms_db AUC 0.357 (0.486 without benign conditions);
    - sequence peak, RMS and mean power AUC 0.335, 0.325 and 0.370 (0.500 without benign conditions);
    - cause: the few attacked variants with a benign condition carry exactly one, while benign
      variants carry 1.48 on average.
  - Two satellites and 60 days of data. Results may not transfer to other modulations, stations or
    seasons.
- **Not intended for.** Judging SatNOGS stations or operators, or any use that identifies people.

## 7. Distribution

- **What is shared.** Code, `config.yaml`, recipes, splits, the per-file manifest with SHA-256, and
  derived tables. Raw SatNOGS files are not redistributed: they are re-downloaded from the manifest
  URLs, checked against the SHA-256, and the dataset is rebuilt deterministically from recipes
  (Phase H reproducibility check).
- **Licence of the source data** (checked 2026-10-31):
  - SatNOGS Network (https://network.satnogs.org/): "All observations results are public and all data
    are distributed freely under the Creative Commons Atribution-Share Alike license", linked to
    https://creativecommons.org/licenses/by-sa/4.0/ (CC BY-SA 4.0).
  - SatNOGS DB (https://db.satnogs.org/about/): "All data are public and freely distributed under the
    Creative Commons Atribution-Share Alike v4.0 license" (CC BY-SA 4.0).
  - Derived data (tables, attacked variants, labels) are therefore shared under CC BY-SA 4.0, with
    attribution to the SatNOGS project (Libre Space Foundation) and the observation ids listed in
    Appendix A.
- **Build scripts** (phase order A–H, all with `--config config.yaml`): `data/select_satellites.py`,
  `data/download_satnogs.py`, `data/qc.py` (+ `data/qc_positive_control.py`), `data/build_tables.py`,
  `data/make_splits.py`, `data/downlink_bands.py` (TRAIN only, so after the splits; the QC line
  screen then uses its output, decision 55), `data/make_recipes.py`, `data/build_features.py`,
  `data/validate.py`. `setup.sh` and the Makefile (`make dataset`, `make validate`) come later (project
  step 8).

## 8. Maintenance

- **Version.** Frozen on 2026-10-31 for detector training. Fingerprints (SHA-256):

  | item | SHA-256 |
  |---|---|
  | `data/splits.json` | `1b9ee1b873f6df5a4e3d2bb86624425db89df94a56cb884d43cb0fe0578719c2` |
  | `data/raw/manifest.csv` | `4a9f01b43c3f63addf86857979b33a0e8eaa1664e0007d0ae3c15f36afad0981` |
  | `data/tables/observations.csv` | `fbeab4714e99c070964ab58cde4c19fce249a7ec256a08ced79413acd8eff6b2` |
  | `data/labels/labels.csv` | `ac5b104551df538095c15adc043cd2244362a27659917c34873a6533b28b54c3` |
  | recipes (1017 JSON files, digest of sorted path + file SHA-256) | `b53020362a6e1b6f4758e86513aac2c2388086ba949a2ae48fadf6bdba006f39` |
  | Phase G features (231 CSV files, same digest) | `93f2eea6f5e6c52435f9ec597041b6ba5564754809bc6d59b976e48ea0bd2bbd` |

- **Changes.** The splits are never redrawn once features exist. Any change to recipes or the chain
  means rerunning Phases G and H and recording a new decision in `docs/PROGRESS.md`. Per the Phase H
  stop rule (decision 65), only a processing-chain trace justifies changing the dataset.
- **Contact.** Through the challenge's anonymous submission channel.

## Appendix A — Provenance per download run

Source: SatNOGS Network observations (https://network.satnogs.org). Full per-file provenance (URL, UTC download time, size, SHA-256): `data/raw/manifest.csv`. The sections below are appended by `data/download_satnogs.py`; the split-file hash by `data/make_splits.py`.

## Download run: pilot (2026-10-29T23:51:21Z)

| obs_id | NORAD | station | start (UTC) | files | audio SHA-256 | audio URL |
|---|---|---|---|---|---|---|
| 15004025 | 25397 | 4451 | 2026-09-16T09:03:25Z | 5 | acf776f8c7bc8a2ad63f4d764f29b228d59d0ceae003060da6d964775ca15b88 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/16/9/15004025/satnogs_15004025_2026-09-16T09-03-25.ogg |
| 15287655 | 25397 | 5049 | 2026-10-25T21:05:33Z | 7 | 0d857525145f5e69ede6e9169214e1cd0d75d2c3dc35517cefa364ab0d769abc | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/25/21/15287655/satnogs_15287655_2026-10-25T21-05-33.ogg |
| 15179622 | 25397 | 5065 | 2026-10-13T05:36:18Z | 5 | 04906af043a8b94a51fc9c1654be70d23d7fa160339b8b43254d21c7bc66fcb9 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/13/5/15179622/satnogs_15179622_2026-10-13T05-36-18.ogg |
| 15123656 | 39444 | 4011 | 2026-10-05T03:15:05Z | 85 | d1aa0f57f0998b40cb0dbbe3aca0cb54f3209d58edfff1d7ece14ce6c4d31e6a | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/5/3/15123656/satnogs_15123656_2026-10-05T03-15-05.ogg |
| 15242426 | 39444 | 4272 | 2026-10-19T13:22:13Z | 33 | 89e35eb0628dedaf0d18c91c4128b668be2b179a2bef636b4598a5c16fefd81e | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/19/13/15242426/satnogs_15242426_2026-10-19T13-22-13.ogg |

## Download run: full (2026-10-30T08:24:36Z)

| obs_id | NORAD | station | start (UTC) | files | audio SHA-256 | audio URL |
|---|---|---|---|---|---|---|
| 15004025 | 25397 | 4451 | 2026-09-16T09:03:25Z | 5 | acf776f8c7bc8a2ad63f4d764f29b228d59d0ceae003060da6d964775ca15b88 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/16/9/15004025/satnogs_15004025_2026-09-16T09-03-25.ogg |
| 15287655 | 25397 | 5049 | 2026-10-25T21:05:33Z | 7 | 0d857525145f5e69ede6e9169214e1cd0d75d2c3dc35517cefa364ab0d769abc | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/25/21/15287655/satnogs_15287655_2026-10-25T21-05-33.ogg |
| 15179622 | 25397 | 5065 | 2026-10-13T05:36:18Z | 5 | 04906af043a8b94a51fc9c1654be70d23d7fa160339b8b43254d21c7bc66fcb9 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/13/5/15179622/satnogs_15179622_2026-10-13T05-36-18.ogg |
| 15312338 | 25397 | 3855 | 2026-10-28T16:07:43Z | 4 | 4daee226646ebfdb4146eb25bd3ff8f94efe63baf55b812f17a817dcc1d6176f | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/28/16/15312338/satnogs_15312338_2026-10-28T16-07-43.ogg |
| 14942057 | 25397 | 4171 | 2026-09-05T22:01:33Z | 5 | f1e246eefe7d89b257fb993b88131548a88ebd2f9a302d08e89ee5a7cd61b786 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/5/22/14942057/satnogs_14942057_2026-09-05T22-01-33.ogg |
| 15305494 | 25397 | 2968 | 2026-10-28T02:59:56Z | 24 | c90be87f08e62b3125196bb8c6c7b47840df871cf7653d7d7cc933f1930b9a5c | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/28/2/15305494/satnogs_15305494_2026-10-28T02-59-56.ogg |
| 15066716 | 25397 | 4394 | 2026-09-26T20:58:58Z | 6 | 521842fd15d3625034653eb3a988d02093585a14ebbc293b28086dfd1c51ee24 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/26/20/15066716/satnogs_15066716_2026-09-26T20-58-58.ogg |
| 15087940 | 25397 | 4791 | 2026-09-29T15:52:20Z | 6 | ede871569a91231c8d5155afa297745777d86804b8a92682496603a7c1f2e135 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/29/15/15087940/satnogs_15087940_2026-09-29T15-52-20.ogg |
| 15087899 | 25397 | 4460 | 2026-09-29T15:51:00Z | 5 | cff53c143c5e6b2c3c0e8a3e1c87980ceb462b4aa54076cff4589971777ab1c4 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/29/15/15087899/satnogs_15087899_2026-09-29T15-51-00.ogg |
| 15287498 | 25397 | 1466 | 2026-10-25T05:57:40Z | 9 | 65a0c56cf5489ca7202d53e9d6f5a922c620a0c1d4e900fae58d7e2074c284da | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/25/5/15287498/satnogs_15287498_2026-10-25T05-57-40.ogg |
| 15087941 | 25397 | 4151 | 2026-09-30T05:44:44Z | 4 | 5ce8f37f082ae56deb2658ac2ccca8429d29c40c304d582d78ec64cb68dfafcb | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/30/5/15087941/satnogs_15087941_2026-09-30T05-44-44.ogg |
| 14944671 | 25397 | 4755 | 2026-09-06T03:37:07Z | 8 | 51e0fa2ce6654bfebb8c80655e964d3fdbf207750bfb61cf6557f542fe29fdf5 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/6/3/14944671/satnogs_14944671_2026-09-06T03-37-07.ogg |
| 15236772 | 25397 | 2987 | 2026-10-18T11:28:35Z | 4 | 128107a249f95030dd3d76c7c33aab4b02509d618d2bf1545ee91bb4cef8f171 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/18/11/15236772/satnogs_15236772_2026-10-18T11-28-35.ogg |
| 15237441 | 25397 | 754 | 2026-10-18T13:45:04Z | 6 | 2b72de941ca32d279924cd7f79e2e5e05e51191d7e3d85cc9f5beb83b523040c | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/18/13/15237441/satnogs_15237441_2026-10-18T13-45-04.ogg |
| 15069330 | 25397 | 4073 | 2026-09-27T05:14:55Z | 4 | 592731b0d91250aec6dbcc3bd93f028b131d85549f49ef81ce389ab714a7a064 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/27/5/15069330/satnogs_15069330_2026-09-27T05-14-55.ogg |
| 15173022 | 25397 | 4738 | 2026-10-10T08:01:18Z | 6 | c4bf0bb5441b59982aaaef5b67590749d54cb2285cd903773a5f62fcf44cc8ed | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/10/8/15173022/satnogs_15173022_2026-10-10T08-01-18.ogg |
| 14911777 | 25397 | 1696 | 2026-08-30T16:03:42Z | 5 | 7c49b0fdaf116651fd1fefe43a3a83743c4802251b2e51dbf30e17b49acd1e9a | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/8/30/16/14911777/satnogs_14911777_2026-08-30T16-03-42.ogg |
| 15140303 | 25397 | 1664 | 2026-10-06T16:25:38Z | 10 | 4912f4ee7ed6b2324d8ff56eb63d54adf23fe260cc9bc0f1966a510b5e7725f9 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/6/16/15140303/satnogs_15140303_2026-10-06T16-25-38.ogg |
| 14966459 | 25397 | 6 | 2026-09-10T15:01:39Z | 4 | 328c21fe9a372528c014cbf595069355e867b9f103f08d34337fb172db6b1d61 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/10/15/14966459/satnogs_14966459_2026-09-10T15-01-39.ogg |
| 14947840 | 25397 | 3762 | 2026-09-06T16:32:59Z | 5 | 8c149cc18819d183d87472c9dd667063997b8b13ac995c25bcc7634896af5ef0 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/6/16/14947840/satnogs_14947840_2026-09-06T16-32-30_48k.ogg |
| 14956312 | 25397 | 4803 | 2026-09-08T09:30:10Z | 5 | 00312c78337552259c65d8801fd2db9bc5964d9ac51e855547881e9e4e75f275 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/8/9/14956312/satnogs_14956312_2026-09-08T09-30-10.ogg |
| 15202727 | 25397 | 2173 | 2026-10-14T05:13:31Z | 4 | 047b648b154f7c624ae17d7da1819f0ddf7f82e94a898929c4e278cf6321e94b | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/14/5/15202727/satnogs_15202727_2026-10-14T05-13-31.ogg |
| 14915678 | 25397 | 431 | 2026-08-31T15:40:14Z | 11 | eeea73dd0c394e105eb583e81e069912ea773236b7f8da82469b5919122126be | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/8/31/15/14915678/satnogs_14915678_2026-08-31T15-40-14.ogg |
| 15080917 | 25397 | 4188 | 2026-09-28T17:54:17Z | 31 | fce295e7088bf6c137dc8c5954fb3a1de46aed12da4350cf7a1cc121b62f11a5 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/28/17/15080917/satnogs_15080917_2026-09-28T17-54-17.ogg |
| 15063067 | 25397 | 4451 | 2026-09-25T21:33:26Z | 5 | 620001c4e3532cefa53878b8568838ebed354aaec3340eb679e7b63b2d7698fd | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/25/21/15063067/satnogs_15063067_2026-09-25T21-33-26.ogg |
| 15091860 | 25397 | 5049 | 2026-09-30T20:56:42Z | 26 | 0526a76ada8ab79d6e52244312211f7d2b32e2151258240bf6530dabbdb9f489 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/30/20/15091860/satnogs_15091860_2026-09-30T20-56-42.ogg |
| 15080453 | 25397 | 3855 | 2026-09-28T16:17:02Z | 5 | 4909f881515ef640dd8201cc0374c9eb12d007e06560619dd4a96042e6ae771b | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/28/16/15080453/satnogs_15080453_2026-09-28T16-17-02.ogg |
| 15091907 | 25397 | 4394 | 2026-09-30T21:04:20Z | 4 | ed25f00dd5bdef45e10bb42425696069aa28c1afd296e09e8647219fb089a10a | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/30/21/15091907/satnogs_15091907_2026-09-30T21-04-20.ogg |
| 15021469 | 25397 | 4791 | 2026-09-18T15:12:05Z | 6 | bafabc187ef427c94dc6e6615c99e50c560c0b14d623c42c30728b9241c02154 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/18/15/15021469/satnogs_15021469_2026-09-18T15-12-05.ogg |
| 15252702 | 25397 | 4460 | 2026-10-20T15:54:26Z | 9 | 731baf67f0b7626f9b8e2344e8827ec4677c2bf5be564fbd2d58f577d1ea452a | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/20/15/15252702/satnogs_15252702_2026-10-20T15-54-26.ogg |
| 15001383 | 25397 | 1466 | 2026-09-15T16:27:21Z | 4 | 09202e20477bf78fc83f8caa932ba434b1ba6b8b72477134684f2cee37228c61 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/15/16/15001383/satnogs_15001383_2026-09-15T16-27-21.ogg |
| 15033837 | 25397 | 4151 | 2026-09-21T05:57:35Z | 4 | 4cdcb97a25491e36a8517e67661f8ccb0bb604b8b4e28c17bfcf7e60e1855c8a | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/21/5/15033837/satnogs_15033837_2026-09-21T05-57-35.ogg |
| 14926205 | 25397 | 4755 | 2026-09-02T14:46:38Z | 5 | 6e2bc29a8940ae4fd39c3440b901883de14b89870e4031a2d6cf07ea487b285c | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/2/14/14926205/satnogs_14926205_2026-09-02T14-46-38.ogg |
| 15230631 | 25397 | 2987 | 2026-10-18T00:22:50Z | 6 | 68f677d1204441d8f7fe0698d4948e485d215d564a46ed6010b09ceb93670fb5 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/18/0/15230631/satnogs_15230631_2026-10-18T00-22-50.ogg |
| 15244766 | 25397 | 754 | 2026-10-20T00:40:28Z | 4 | 51ecd51154b9aa9d533b41115675211410a067fb49befb04967a8ebb1879276c | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/20/0/15244766/satnogs_15244766_2026-10-20T00-40-28.ogg |
| 15150189 | 25397 | 4738 | 2026-10-07T21:31:03Z | 5 | 170d5081ea6320e1d22c45d42bed4ff9130578c3a9e7623b855b88da38956a3a | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/7/21/15150189/satnogs_15150189_2026-10-07T21-31-03.ogg |
| 15128862 | 25397 | 1696 | 2026-10-05T16:53:00Z | 5 | 174d7a1a64039e2c36286bc6f01997e0cded77a6220d99f59df7b339733faafa | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/5/16/15128862/satnogs_15128862_2026-10-05T16-53-00.ogg |
| 15033863 | 25397 | 1664 | 2026-09-20T17:46:01Z | 18 | 2a60a34b6e2dccc2f0da52da9238eda672177f5574d2bc9c95d0e325e9e8c12a | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/20/17/15033863/satnogs_15033863_2026-09-20T17-46-01.ogg |
| 14976061 | 25397 | 6 | 2026-09-11T14:37:58Z | 4 | d10caeff1948a8995af2f10ae6e60a8e331d3643e7ff32949ddbaa33995bfe05 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/11/14/14976061/satnogs_14976061_2026-09-11T14-37-58.ogg |
| 14974899 | 25397 | 4803 | 2026-09-11T19:43:46Z | 5 | ffdc01ed1c628bf1d12ea2954794cb259cddb107946a70778fb5a703f5ac7cf5 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/11/19/14974899/satnogs_14974899_2026-09-11T19-43-46.ogg |
| 15252654 | 25397 | 2173 | 2026-10-20T15:57:56Z | 15 | 32339263cb0b6aadff43781d540eb8c7ff854fdc73c4c0d2e397e8521cc5be64 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/20/15/15252654/satnogs_15252654_2026-10-20T15-57-56.ogg |
| 14911770 | 25397 | 431 | 2026-08-30T16:03:42Z | 5 | 630c162865e6d0ab0fb48eec8e3be0a3ae783bff89bcde84598cdca73496456e | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/8/30/16/14911770/satnogs_14911770_2026-08-30T16-03-42.ogg |
| 15080837 | 25397 | 4188 | 2026-09-28T16:12:59Z | 38 | c9d7336b0562200fb09f09e14879ea77c47357b15ba6795274cc9228cd3433c1 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/28/16/15080837/satnogs_15080837_2026-09-28T16-12-59.ogg |
| 14929885 | 25397 | 4451 | 2026-09-03T21:53:29Z | 5 | dd5bf1e1b57e0cbd3cb93437c899a90557b78b99e683c14e29844891fb45157c | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/3/21/14929885/satnogs_14929885_2026-09-03T21-53-29.ogg |
| 15298904 | 25397 | 5049 | 2026-10-27T07:58:41Z | 7 | cdad1eb1c9fbf73ca246ee552d36e77fa0424911c746b0fae22d090424028576 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/27/7/15298904/satnogs_15298904_2026-10-27T07-58-41.ogg |
| 15175144 | 25397 | 3855 | 2026-10-11T16:09:41Z | 5 | c569c1f6fabf7db703ec43483fa3e5ca49d2b8e0854ddc5efd3c3a8ef9df7e06 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/11/16/15175144/satnogs_15175144_2026-10-11T16-09-41.ogg |
| 15093731 | 25397 | 4394 | 2026-10-01T08:13:27Z | 4 | 79c74811c5c0bf86036ac0fd271af772fb253e51633ffd8da121b37ec3dd60d3 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/1/8/15093731/satnogs_15093731_2026-10-01T08-13-27.ogg |
| 15019641 | 25397 | 4791 | 2026-09-18T03:53:09Z | 4 | 4207af982c9c6bb1ced1f0e4c67ff66f2b9202cb562cf85bb32bfb92d097486b | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/18/3/15019641/satnogs_15019641_2026-09-18T03-53-09.ogg |
| 15039870 | 25397 | 4460 | 2026-09-21T15:39:54Z | 5 | ec59e0e3aab0af9240ab4a53992786360dfda6ab3ce454def13f2cb5a3e7f725 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/21/15/15039870/satnogs_15039870_2026-09-21T15-39-54.ogg |
| 15066393 | 25397 | 1466 | 2026-09-26T05:43:26Z | 9 | 8fa85d99238235ea55acbf08d246a21ea85de210df802532711596967f3fbf08 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/26/5/15066393/satnogs_15066393_2026-09-26T05-43-26.ogg |
| 15029025 | 25397 | 4755 | 2026-09-20T03:02:26Z | 6 | 318fcff354cc0b95888263cf4111e5f307f7e804e7a81069718eba86c779da17 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/20/3/15029025/satnogs_15029025_2026-09-20T03-02-26.ogg |
| 15094462 | 25397 | 2987 | 2026-10-01T00:24:50Z | 4 | c606a3236f7c01e42cdbceb874c25a2cbff49960c4cd6e4c6fa347e1d0d06efa | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/1/0/15094462/satnogs_15094462_2026-10-01T00-24-50.ogg |
| 15269764 | 25397 | 754 | 2026-10-22T13:50:28Z | 6 | 6b7d940355b4f61900dcbc34a594ac90c32caf19487d1553adaf57ec3a5d8626 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/22/13/15269764/satnogs_15269764_2026-10-22T13-50-28.ogg |
| 15243923 | 25397 | 4738 | 2026-10-19T21:47:23Z | 5 | ec4e9e8588939129d48f2b2f4fef6df076a73514ae534160abd4cb342d53923d | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/19/21/15243923/satnogs_15243923_2026-10-19T21-47-23.ogg |
| 14909273 | 25397 | 1696 | 2026-08-30T04:37:52Z | 4 | 53f48eaaf1fda865ee2547c3d96258cada2f04f42993f01c619dddb309e2ca5a | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/8/30/4/14909273/satnogs_14909273_2026-08-30T04-37-52.ogg |
| 15034285 | 25397 | 1664 | 2026-09-21T05:54:01Z | 26 | 352afb7112190b93a29b87d1bbb441ee6a2d4972335188e68b3245594f9609c4 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/21/5/15034285/satnogs_15034285_2026-09-21T05-54-01.ogg |
| 15131577 | 25397 | 4803 | 2026-10-06T10:07:35Z | 5 | 3220290bf72d9069ddb7c94651623201f7efc73bb663817b8a28c559d6ff37e3 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/6/10/15131577/satnogs_15131577_2026-10-06T10-07-35.ogg |
| 15259181 | 25397 | 2173 | 2026-10-21T17:15:15Z | 6 | cbae97c9838e5f3d4c037e34c50f6735f9b5614bbe35c15d2148cd795e0c93a0 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/21/17/15259181/satnogs_15259181_2026-10-21T17-15-15.ogg |
| 14911525 | 25397 | 431 | 2026-08-30T14:26:40Z | 4 | 0cccf7a970eac2394a4972630abfd135481f21f5549a405c04f1d7981f5480e2 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/8/30/14/14911525/satnogs_14911525_2026-08-30T14-26-40.ogg |
| 15042383 | 25397 | 4188 | 2026-09-22T05:32:45Z | 46 | e5833c34d20e74d935a778af694cc321973ad485734873544412552dad9af0cb | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/22/5/15042383/satnogs_15042383_2026-09-22T05-32-45.ogg |
| 15123656 | 39444 | 4011 | 2026-10-05T03:15:05Z | 85 | d1aa0f57f0998b40cb0dbbe3aca0cb54f3209d58edfff1d7ece14ce6c4d31e6a | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/5/3/15123656/satnogs_15123656_2026-10-05T03-15-05.ogg |
| 15242426 | 39444 | 4272 | 2026-10-19T13:22:13Z | 33 | 89e35eb0628dedaf0d18c91c4128b668be2b179a2bef636b4598a5c16fefd81e | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/19/13/15242426/satnogs_15242426_2026-10-19T13-22-13.ogg |
| 14995840 | 39444 | 1763 | 2026-09-14T14:05:49Z | 19 | d4991cdfef7e89b5a58a1187ed3e87086ebb84c754a3d06333f7d167ee529354 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/14/14/14995840/satnogs_14995840_2026-09-14T14-05-49.ogg |
| 14998723 | 39444 | 4806 | 2026-09-15T20:15:14Z | 4 | 7634e41b15f01c38715931728708b2d19b66f13b6c92f997e5c726f947ba70de | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/15/20/14998723/satnogs_14998723_2026-09-15T20-15-14.ogg |
| 14966979 | 39444 | 5155 | 2026-09-10T03:55:22Z | 16 | f8f79fc449c02cdbc2a7de662ecc87e22a4347bdcb14549b6d2109740143f5bf | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/10/3/14966979/satnogs_14966979_2026-09-10T03-55-22.ogg |
| 15081287 | 39444 | 4434 | 2026-09-29T06:53:46Z | 19 | 0450c6ff75798bd99759ff737ee9640cce3e632c1c323b5af5d19c87e9dfdbc2 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/29/6/15081287/satnogs_15081287_2026-09-29T06-53-46.ogg |
| 15192857 | 39444 | 2107 | 2026-10-13T06:19:27Z | 52 | 1271c92a747ff6556950ddcf6e523e7dc41a7410889f4d2f7a64cb6d9b0c9765 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/13/6/15192857/satnogs_15192857_2026-10-13T06-19-27.ogg |
| 15230978 | 39444 | 4272 | 2026-10-18T02:49:09Z | 33 | 238b3dde1cb0ee4256d13ccad24bc0025cff0003d3ec2235ba3f5a970012cd21 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/18/2/15230978/satnogs_15230978_2026-10-18T02-49-09.ogg |
| 14995843 | 39444 | 1763 | 2026-09-16T03:01:20Z | 24 | 89dcff7981292b400a53e4b43dee29c70b311252431da6c88e9f2e4e87f452bb | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/16/3/14995843/satnogs_14995843_2026-09-16T03-01-20.ogg |
| 15229495 | 39444 | 4806 | 2026-10-18T09:10:13Z | 21 | c6fbb1fec0fcbacf0d51daf09cf7a24059040523216a467c404c70ef87065fd1 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/18/9/15229495/satnogs_15229495_2026-10-18T09-10-13.ogg |
| 14971294 | 39444 | 5155 | 2026-09-10T14:44:50Z | 15 | d5270d89ac77679657a916778d7b3dd2ae495da55b30a09bf8c122f45202578a | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/10/14/14971294/satnogs_14971294_2026-09-10T14-44-50.ogg |
| 15299816 | 39444 | 4434 | 2026-10-28T07:04:56Z | 33 | 2a8888b5970b7b0b7e46ed793bd6662009beb01561db63ef35e78c546f708511 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/28/7/15299816/satnogs_15299816_2026-10-28T07-04-56.ogg |
| 15023587 | 39444 | 2107 | 2026-09-19T06:53:27Z | 6 | fa03781f4aac53791cad9867430224fb45245b256db1f404ce38643f4a7142bf | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/19/6/15023587/satnogs_15023587_2026-09-19T06-53-27.ogg |
| 15237468 | 39444 | 4272 | 2026-10-18T13:31:32Z | 33 | b6b8f286801e64a961ced8ebf8e80683afe67c84417a0b99c2e6223453e12ed5 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/18/13/15237468/satnogs_15237468_2026-10-18T13-31-32.ogg |
| 14995842 | 39444 | 1763 | 2026-09-15T13:56:40Z | 18 | d28f1d535f24bb9c83878f0c448f179b0e1819003a48d3e5ce52500a5f859f74 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/15/13/14995842/satnogs_14995842_2026-09-15T13-56-40.ogg |
| 15244977 | 39444 | 4806 | 2026-10-20T08:51:22Z | 20 | 156218f6398548e592ed3976de0c5d9edf1aadd4a4679e63ef316a2d83436ef9 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/20/8/15244977/satnogs_15244977_2026-10-20T08-51-22.ogg |
| 14980866 | 39444 | 5155 | 2026-09-12T03:37:06Z | 6 | d3f894169528e25c9c9fc1489039aa065a9216c9a56e2ab6f2bc9aaae48d1c63 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/12/3/14980866/satnogs_14980866_2026-09-12T03-37-06.ogg |
| 15275276 | 39444 | 2107 | 2026-10-23T19:34:15Z | 22 | 674cdbef39cd84e17dc6a0c2180ec4fe9ec11f8aa97798293c83acaa3c63f19d | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/23/19/15275276/satnogs_15275276_2026-10-23T19-34-15.ogg |
| 15202641 | 39444 | 4272 | 2026-10-14T01:53:02Z | 33 | 9902904f5515f027ea11e95134593cf558999947ab17a636782952fa51a96e1e | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/14/1/15202641/satnogs_15202641_2026-10-14T01-53-02.ogg |
| 15305451 | 39444 | 2107 | 2026-10-28T07:07:27Z | 48 | ba623d8241966be881bab767ed3844b032dec2e1c2d4d3db82142381511c1ea2 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/28/7/15305451/satnogs_15305451_2026-10-28T07-07-27.ogg |
| 15012409 | 39444 | 4272 | 2026-09-17T02:53:16Z | 8 | a4bf7bfdb72c3c6049924e43fac0907cb7007604e64da39584bb2bb5ac4246cf | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/17/2/15012409/satnogs_15012409_2026-09-17T02-53-16.ogg |
| 14934136 | 39444 | 2107 | 2026-09-04T19:15:27Z | 18 | 62e530a1b7ce056cac011986b4b1b29973b226f48b4c572d0696f74acb0346c1 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/4/19/14934136/satnogs_14934136_2026-09-04T19-15-27.ogg |
| 15301862 | 39444 | 4272 | 2026-10-27T13:40:25Z | 42 | acf285663f7538c68574ae4d244a3905f0d966555316ab046d5a07405facb3d1 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/27/13/15301862/satnogs_15301862_2026-10-27T13-40-25.ogg |
| 14957048 | 39444 | 2107 | 2026-09-08T06:59:25Z | 18 | eea913533146050d8bfdafbaeb8dc9615891b9b024583063876208ab30deeabe | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/8/6/14957048/satnogs_14957048_2026-09-08T06-59-25.ogg |
| 14987124 | 39444 | 4272 | 2026-09-13T03:30:19Z | 4 | 599a526d46be8673f7280ab39456291efa5202bc7e48cc92b7901ef212920210 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/13/3/14987124/satnogs_14987124_2026-09-13T03-30-19.ogg |
| 15285174 | 39444 | 2107 | 2026-10-24T19:24:56Z | 17 | 3204cd0bdcea65411f6a6abf8e1ebe3025135f8ab885291f88f761b4858d2ada | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/24/19/15285174/satnogs_15285174_2026-10-24T19-24-56.ogg |
| 15228452 | 39444 | 4272 | 2026-10-17T13:40:54Z | 33 | 963e993c1c427765806a5db94553394f6609272b65be1a232065def321eb94b6 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/17/13/15228452/satnogs_15228452_2026-10-17T13-40-54.ogg |
| 15178847 | 39444 | 2107 | 2026-10-11T19:52:49Z | 20 | 24ed97b3de31b231559c12417e735961fe4d3ce3eb16df083ccbcbf36f39b995 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/11/19/15178847/satnogs_15178847_2026-10-11T19-52-49.ogg |
| 15297741 | 39444 | 4272 | 2026-10-26T13:49:56Z | 33 | 26eff29885f47e74f3d52c79668e7fb2e07592f5a8ab0a954920567ca0782aa8 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/26/13/15297741/satnogs_15297741_2026-10-26T13-49-56.ogg |
| 14944444 | 39444 | 2107 | 2026-09-06T05:42:01Z | 11 | fee3b3f704d743fe5e23d6636c4145962c11b6c809b9b695ad75c512d55819bb | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/6/5/14944444/satnogs_14944444_2026-09-06T05-42-01.ogg |
| 15211184 | 39444 | 4272 | 2026-10-15T13:59:47Z | 33 | 982b3b944e74c8be23dde1b9760e57150ddc4fcb861c1f3f09cdd5d384711ce5 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/15/13/15211184/satnogs_15211184_2026-10-15T13-59-47.ogg |
| 15090581 | 39444 | 2107 | 2026-09-30T06:46:25Z | 15 | fa20a095776db74a1ac5da4ff2f8d54919b539331032ccfb3d35137b98f85d81 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/30/6/15090581/satnogs_15090581_2026-09-30T06-46-25.ogg |
| 15222456 | 39444 | 4272 | 2026-10-16T13:50:19Z | 33 | 82bc1114c310985514ccc97470775cca35965e638c92950ef4cf70d6b63d175a | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/16/13/15222456/satnogs_15222456_2026-10-16T13-50-19.ogg |
| 14932721 | 39444 | 2107 | 2026-09-04T05:59:54Z | 19 | b0199edc1d6e278e737fefff30927817ca3f2aa43c1af394b0d90fd08af7e78f | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/4/5/14932721/satnogs_14932721_2026-09-04T05-59-54.ogg |
| 15192903 | 39444 | 4272 | 2026-10-13T02:02:10Z | 33 | 84b9c7c8d929b2341e3e1af930bc8c34eab76b15f34ccd591bb87005522f2d93 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/13/2/15192903/satnogs_15192903_2026-10-13T02-02-10.ogg |
| 15202507 | 39444 | 2107 | 2026-10-14T06:10:02Z | 62 | 9b1a4a6583e4d7f1a7a9fc0fab3bde8c036e9017170de8f82a0c3ed03b91fe33 | https://network-satnogs.freetls.fastly.net/media/data_obs/2026/10/14/6/15202507/satnogs_15202507_2026-10-14T06-10-02.ogg |

## Splits

`data/splits.json` SHA-256: `1b9ee1b873f6df5a4e3d2bb86624425db89df94a56cb884d43cb0fe0578719c2` (data/make_splits.py, Phase E).
