# Dataset specification — TSYP14 Track 1 (SatNOGS ground-link attacks)

Read CLAUDE.md and docs/PROJECT_CONTEXT.md first. This file is the contract for building
the dataset. Follow it phase by phase. At every STOP, print a short report and wait for approval.

Target: dataset ready by 2026-10-29 so that detectors can be built before the 2026-11-03 deadline.

---------------------------------------------------------------------------------------------

## 0. Principles (non-negotiable)

1. Real data first. Only authentic SatNOGS Network observations are used as source material.
   Every attack is a deterministic, documented transformation of a real observation.
2. No leakage. Splits are by observation, and the test split is station-disjoint from train.
   Detector code never reads labels. (Arp et al., USENIX Security 2022)
3. No shortcut cues. Clean and attacked variants must go through the SAME processing chain
   (decode, headroom gain, re-encode, decode), so a model cannot separate them by codec traces,
   file length, amplitude range or metadata. (Arp et al. 2022: spurious correlations)
4. Non-trivial, realistic anomalies. Random positions, realistic density, wide SNR range,
   benign confounders included. No anomaly always at the end of a file. (Wu & Keogh, TKDE)
5. Recipes, not copies. Attacked audio is NOT stored by default: each variant is a JSON recipe
   (source obs, attack parameters, seed) applied in memory. Audio is materialized only for the
   demo sample and the standard-test set. (A 444 s float32 WAV at 48 kHz is ~85 MB.)
6. Reproducible. Rebuilding from the same raw files and config must give byte-identical tables.
7. Documented. data/DATA.md is a datasheet (Gebru et al., Datasheets for Datasets).
8. Anonymous. No names, usernames, emails, team or university names, absolute paths.

---------------------------------------------------------------------------------------------

## 1. Phase A — Satellite selection   (STOP after)

Query the SatNOGS Network and DB APIs (never guess). Rank candidates by:
- catalogued NORAD id (not a temporary ~98xxx id)
- >= 50 observations with vetted_status=good AND demoddata in the last 60 days
- >= 10 distinct ground stations
- packet mode: AFSK1k2, BPSK1k2, or FSK/GMSK 9k6
- regular beacon rhythm (measure burst period on 3 observations)
- optional: telemetry decoder exposing a frame counter or uptime field (satnogs-decoders);
  gr-satellites support is OPTIONAL (not required for Phase 1)

Output: table of top 5 (name, NORAD, mode, n_good_obs, n_stations, burst period,
counter field or none). Recommend 2 satellites: one narrowband (1k2) and one 9k6.

## 2. Phase B — Download   (STOP after the 5-observation pilot, then run the full download)

Script: data/download_satnogs.py
- Per satellite: target 60 observations (config), vetted_status=good, with demoddata,
  spread over as many stations as possible (cap 6 observations per station).
- Files per observation into data/raw/<obs_id>/: metadata.json, audio.ogg,
  demoddata/<original filenames>, waterfall.png.
- Rate limiting: at most 1 request every 3 s, back off exponentially on HTTP 429/5xx,
  cache API responses, resumable (skip files whose SHA-256 already matches).
- Never modify data/raw/ after download.
- Append provenance to data/DATA.md and data/raw/manifest.csv:
  obs_id, file, url, download_utc, size_bytes, sha256.

## 3. Phase C — Quality control and curation   (STOP after)

Script: data/qc.py -> data/qc_report.csv + data/qc_report.md
Reject an observation if:
- audio missing/unreadable, or |audio duration - (end - start)| > 10 s
- fewer than 10 demoddata frames
- sample rate not 48000 or not mono (record and skip)
Flag (do not auto-reject) for manual waterfall review if:
- a persistent narrowband line already exists: any frequency bin whose median-normalized PSD
  exceeds +10 dB in more than 30% of 1 s windows (pre-existing real RFI)
Write the list of flagged observations with their waterfall paths so a human can review.
Final status column: ok / rejected / flagged_reviewed_ok / flagged_reviewed_rejected.

## 4. Phase D — Clean tables

Script: data/build_tables.py  (deterministic, idempotent, keyed by (obs_id, variant_id))

### observations.csv (one row per observation)
obs_id, norad, satellite, mode, station_id, start_utc, end_utc, duration_s, sample_rate,
n_frames, audio_sha256, f_edge_hz, qc_status, split

### frames.csv (one row per frame, per variant)
obs_id, variant_id, idx, filename, timestamp_utc, t_rel_s, burst_id, dt_prev_s, dt_burst_s,
length, sha256, hex, counter (empty if no decoder)
Rules: sort by timestamp then original filename; new burst when gap > 2 s.
NOTE: SatNOGS demoddata timestamps have 1 s resolution and several frames share a second.
Frame order within a second is the filename order. Do not invent sub-second times.

### windows.csv (one row per audio window, per variant)
obs_id, variant_id, win_idx, t_start_s, burst_active, power_db, noise_floor_db,
peak_to_median_db, peak_freq_hz, flatness, spectral_entropy, spectral_kurtosis,
occupied_bw_hz, peak_persistence, max_dev_from_obs_median_db
Processing:
- 1 s windows, 0.5 s hop, Welch PSD (Hann, nperseg=2048, 50% overlap) -> 23.4 Hz bins
- restrict to passband [100 Hz, f_edge - 100 Hz]; f_edge estimated per observation as the
  highest frequency where the median PSD is within 20 dB of its in-band median
- features as dB RATIOS where possible (gain-invariant)
- peak_persistence = number of consecutive previous windows whose peak bin is within +-2 bins
- burst_active = window overlaps [t_rel of first frame of a burst - 1 s, last frame + 1 s]
- spectral_kurtosis per Antoni (2006): kurtosis across time of STFT magnitude per bin,
  summarized as max over in-band bins

### labels.csv (separate, never read by detect/)
obs_id, variant_id, unit (frame|window), index, is_attack, attack_type, subtype,
snr_db, inr_db, overlap_frac, evasion (0/1)
Window label = 1 if attack active for >= 0.5 s of the window; always store overlap_frac.

## 5. Phase E — Splits   (STOP after)

Script: data/make_splits.py -> data/splits.json (+ its SHA-256 in DATA.md)
- Only qc_status ok / flagged_reviewed_ok observations.
- Per satellite: ~60% train, ~20% calib, ~20% test, BY OBSERVATION.
- Test stations must not appear in train (station-disjoint). Calib may share stations with train.
- Never re-split after features are computed. Print counts per split, satellite, station.

## 6. Phase F — Attack and benign recipes   (STOP after unit tests pass)

Directory: attacks/ (pure functions: clean data + recipe -> attacked data + labels)
Recipe JSON (data/recipes/<obs_id>/<variant_id>.json):
{ "obs_id", "variant_id", "split", "seed", "operations": [ {type, params...}, ... ] }
Seed = int from SHA-256 of f"{obs_id}:{variant_id}" (deterministic).

### 6.1 Common processing chain (applied to EVERY variant, including clean)
decode ogg -> float32 -> apply operations -> multiply by fixed headroom 0.5 ->
re-encode Ogg Vorbis at a fixed quality -> decode -> features.
This removes codec/amplitude shortcuts between clean and attacked variants.

### 6.2 Replay (standard test: valid 5–20-frame subsequence)
Frame level:
- L ~ UniformInt[5, 20]; choose consecutive frames [i, i+L) made of whole bursts when possible
- choose a later inter-burst gap long enough to hold them (fallback: append after last burst)
- insert copies there; rewrite timestamps to the gap times, preserving the original
  intra-burst spacing; recompute t_rel_s, burst_id, dt_*; mark copies is_attack=1
Audio level (paired, same variant):
- source segment = audio covering the copied bursts (+-0.5 s margin)
- OVERWRITE an inter-burst gap of the same length at the insertion time (file length unchanged)
- 10 ms raised-cosine crossfade at both edges
- windows overlapping the pasted segment get labels with overlap_frac
Variants: subtype intra_pass (default), cross_pass (source frames/audio from another
observation of the same satellite in the SAME split) — cross_pass is evasion=1.

### 6.3 Sequence anomaly (standard test: exactly ONE per variant)
Draw one subtype:
- deletion: remove k ~ UniformInt[1, 3] consecutive frames (label the frame after the gap)
- swap: exchange two adjacent frames (both labelled)
- duplicate: repeat one frame immediately (copy labelled)
- forged: insert a frame built from a random existing frame with 1–4 random bytes changed
  outside the first 16 bytes (label it); its timestamp fits the local rhythm
- timing: shift one frame's timestamp by +-U[3, 8] s inside the pass (label it)
- counter_jump: only if a counter field exists; add U[2, 10] to one frame's counter
Sequence anomalies are frame-level only (audio untouched), unless subtype is forged, where
the audio is left untouched too (documented limitation).

### 6.4 Narrowband interference (standard test: hidden SNR)
Definitions (write them in the report):
- in-band = [f_low, f_edge] of the observation (f_edge = signal-band limit, decision 48)
- P_ref    = MEDIAN in-band power over ALL windows of the clean observation (both satellites)
- P_noise  = mean in-band power over non-burst windows (all windows when burst_active is unknown,
  i.e. timing-unreliable observations)
- snr_db = 10*log10(P_ref / P_interf);  inr_db = 10*log10(P_interf / P_noise)
- snr_db is a signal-to-interference ratio (SIR): LOW snr_db = STRONG interference. The column keeps
  its name; inr_db (interference-to-noise ratio) is kept alongside it (decision 62)
- Reason (decision 56, replaces "P_signal = mean in-band power over burst_active windows"): only 2 %
  of TECHSAT 1B windows are burst_active, and FM-demodulated audio power drops during bursts
  (FM quieting), so a burst-window mean is unstable and biased
- f0 is drawn over the full non-silent band [200 Hz, f_drop - 200 Hz] (decision 48), so no audible
  part of the band is blind; SNR stays referenced to [f_low, f_edge]
Parameters:
- shape: tone (p=0.5) or band-limited Gaussian noise (4th-order Butterworth band-pass,
  bandwidth B ~ Uniform[20, 200] Hz) (p=0.5)
- f0 ~ Uniform[200 Hz + B/2, f_drop - 200 Hz - B/2]  (full non-silent band, decision 48)
- snr_db ~ Uniform[-10, +30] for random variants;
  fixed grid {-10, -5, 0, +5, ..., +35} (10 values) for the Pd-vs-SNR curve variants (decision 62;
  the earlier ranges [-25, +10] and [-35, -25] were inverted for an SIR)
- duration: full pass (p=0.4) or a segment of U[5, 60] s at a random onset (p=0.6)
- tone amplitude A = sqrt(2 * P_interf); random phase; scale noise to P_interf exactly
Evasion subtypes (evasion=1, test split only): intermittent (on/off 1–5 s),
near-threshold SNR in [+20, +30] (weak interferer, decision 62), slow frequency drift (<= 5 Hz/s), placed at the
frequency of strongest signal energy.

### 6.5 Benign conditions (is_attack = 0, attack_type = benign)
- gain: multiply whole file or a U[10, 120] s segment by U[0.3, 3.0]
- noise_rise: add white noise raising the in-band floor by U[1, 6] dB on a segment
- fading: slow amplitude modulation, rate U[0.05, 0.5] Hz, depth up to 6 dB
- edge_attenuation: linear ramp down to -10 dB over the first and last U[20, 60] s
Combine 1–2 benign operations per benign variant. Benign operations may also be added
underneath attacks (p=0.3) so detectors cannot use "any change = attack".

### 6.6 Variant mix per observation
| variant group | train | calib | test |
| clean | 1 | 1 | 1 |
| benign | 3 | 3 | 3 |
| interference (random SNR) | 4 | 3 | 3 |
| interference SNR grid (10 values) | 0 | 0 | 10 |
| replay | 2 | 2 | 2 |
| sequence (one anomaly) | 2 | 2 | 2 |
| standard combo (replay + 1 sequence anomaly + interference) | 2 | 2 | 10 (decision 58) |
| evasion | 0 | 0 | 2 |
Materialize audio (ogg) only for: data/sample/ (2 observations, all groups) and
data/standard_test/ (10 test combo variants with a separate hidden labels file).

## 7. Phase G — Features for all variants

Script: data/build_features.py
- For every recipe: load clean raw data, apply recipe in memory, run the common chain,
  compute frames.csv and windows.csv rows, append labels.csv rows.
- Parallelize per observation (multiprocessing), show progress, log runtime per observation.
- Output Parquet if available, else CSV.

## 8. Phase H — Dataset validation   (STOP after; write data/validation_report.md)

Checks (fail loudly):
1. Leakage: no obs_id or test station shared across forbidden splits.
2. Label consistency: replay variants have exactly L labelled copies; sequence variants
   exactly one anomaly event; interference windows match recipe times.
3. Shortcut test (revised 2026-10-31, decisions 63–67; code `data/diag_shortcut.py`, rerun from the
   cue cache with `python data/validate.py --only shortcut`). Goal: show that the processing chain
   leaves no trace that separates attacked from not-attacked variants.
   - Cues. Structure cues: file duration, number of windows. Level cues: post-chain peak |x|,
     RMS of the whole file, mean window power_db. Level cues are NOT non-physical: strong
     interference raises the file level, and benign conditions (gain, noise rise, fading, edge
     attenuation) change it by design.
   - Within observation, benign-matched. Attacks are not spread evenly (test observations carry
     many more attacked variants and some are longer; 75 % of not-attacked variants carry a benign
     condition against 14 % of attacked ones), so a pooled test learns these confounds. Weights are
     balanced inside each stratum (observation × has-a-benign-condition): every stratum with both
     classes gets total weight 1, half attacked, half not attacked. Weighted fit, weighted ROC AUC.
     Every variant is also compared with its own clean variant (paired deltas, reported).
   - Rule: |AUC − 0.5| < 0.1 (`validation.shortcut_auc_dev_max`), per satellite and pooled, for
     (a) structure cues: every comparison and every SNR bin;
     (b) level cues: replay, sequence, power-neutral attacked vs not attacked, and interference with
         snr_db ≥ +15 dB (`validation.shortcut_level_min_snr_db`);
     classifiers (logistic regression, random forest; train+calib → test and 5-fold by observation)
     on structure cues (all variants) and on all cues (power-neutral variants).
   - Reported, not ruled (expected physical effects): level cues of interference below +15 dB, as a
     per-snr_db-bin table, and level cues of benign vs clean. Also reported: the composition table
     (attack density, duration, benign-condition share per split).
   - Pass = no processing-chain trace: structure cues equal to the own clean in every variant, and
     every rule failure disappears on the stratum without benign conditions. A failure that
     disappears there (composition, small samples) is listed with its numbers as a documented
     limitation and is NOT iterated on. Only a real chain trace justifies changing the dataset.
   - Amplitude: processing is in float, nothing is clipped; decoded SatNOGS audio is not bounded to
     ±1 (post-chain clean peak up to 2.09, one benign variant 18.3). Known property, not a defect.

   Consequences for the detector phase (binding):
   - training uses per-observation sample weights that balance attacked and not-attacked samples
     inside each observation;
   - metrics are computed per observation and then averaged (plus pooled values for reference);
   - detectors never use duration, number of windows or observation-level metadata (station,
     split, satellite-level counts, observation id) as features;
   - after training, report feature importances and check that absolute-level features
     (noise_floor_db, power_db) do not dominate; prefer scale-invariant dB ratios.
4. Trivial-baseline test: report AUC of single features (e.g. peak_to_median_db alone) per
   SNR bin, to show the task is not trivial at low SNR. (Wu & Keogh)
5. Distribution report: counts per split / satellite / station / attack type / subtype,
   SNR histogram, fraction of attacked windows (keep it realistic, report it).
6. Reproducibility: rebuild 2 observations twice; tables must be byte-identical.
7. Recipe round-trip: materialized sample audio re-processed gives the same features.

## 9. Phase I — Datasheet

data/DATA.md following Datasheets for Datasets: motivation, composition, collection process
(SatNOGS APIs, dates, filters), preprocessing (chain, parameters), uses and limits
(1 s timestamp resolution, audio-only, no IQ, forged frames not re-modulated), distribution
(script + manifest, data licence as published by SatNOGS — check and cite it), maintenance.

## 10. Commands and config

config.yaml holds every parameter above (counts, ranges, seeds, paths).
Makefile targets: `make dataset` (A–I non-interactive with existing choices),
`make sample` (sample only), `make validate`.
Tests: tests/test_attacks.py (each operation: label counts, SNR within 0.5 dB of target,
file length unchanged), tests/test_tables.py, tests/test_splits.py.

---------------------------------------------------------------------------------------------

## References (why each rule exists)

- D. Arp et al., "Dos and Don'ts of Machine Learning in Computer Security," USENIX Security 2022.
  -> grouped splits, shortcut test, common processing chain.
- R. Wu, E. Keogh, "Current Time Series Anomaly Detection Benchmarks are Flawed and are Creating
  the Illusion of Progress," IEEE TKDE (arXiv:2009.13807). -> non-trivial anomalies, random
  positions, realistic density, single-feature baselines.
- T. Gebru et al., "Datasheets for Datasets," Communications of the ACM, 2021 (arXiv:1803.09010).
  -> DATA.md structure.
- J. Smailes et al., "Sticky Fingers: Resilience of Satellite Fingerprinting against Jamming
  Attacks," NDSS SpaceSec 2024 (arXiv:2402.05042). -> precedent: tone and Gaussian jamming added
  at varying power to real satellite recordings.
- J. Smailes et al., "Watch This Space," ACM CCS 2023 (arXiv:2305.06947). -> real satellite
  capture dataset with rich metadata.
- T. J. O'Shea, T. Roy, T. C. Clancy, "Over-the-Air Deep Learning Based Radio Signal
  Classification," IEEE JSTSP 2018. -> SNR-swept labelled dataset, per-SNR reporting.
- S. Gecgel, G. Karabulut Kurt, "Intermittent Jamming against Telemetry and Telecommand of
  Satellite Systems...," ACM WiseML 2021 (arXiv:2107.06181). -> SNR/SJR grids, intermittent
  jamming as a harder variant.
- N. L. Raichur, F. Ott et al., "Evaluation of (Un-)Supervised ML Methods for GNSS Interference
  Classification with Real-World Data Discrepancies," arXiv:2503.23775 (preprint).
  -> station-disjoint test split.
- K. Kotowski et al., "European Space Agency Benchmark for Anomaly Detection in Satellite
  Telemetry," arXiv:2406.17826. -> anomalies as annotated events, event-wise evaluation.
- K. D. Wesson et al., "GNSS Signal Authentication via Power and Distortion Monitoring,"
  arXiv:1702.06554. -> benign confounders must be part of evaluation.
- K. Hundman et al., "Detecting Spacecraft Anomalies Using LSTMs and Nonparametric Dynamic
  Thresholding," KDD 2018. -> interval labels, false-positive pruning.
- P. D. Welch, IEEE Trans. Audio and Electroacoustics, 1967 (DOI 10.1109/TAU.1967.1161901).
  -> PSD estimation.
- J. Antoni, "The spectral kurtosis: a useful tool for characterising non-stationary signals,"
  Mechanical Systems and Signal Processing, 2006. -> spectral kurtosis feature.
- A. N. Angelopoulos, S. Bates, "A Gentle Introduction to Conformal Prediction and
  Distribution-Free Uncertainty Quantification," arXiv:2107.07511. -> why a separate,
  untouched calibration split is required for FPR-controlled thresholds.
