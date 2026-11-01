# Detector specification — TSYP14 Track 1

Read CLAUDE.md, docs/PROJECT_CONTEXT.md, docs/PROGRESS.md and data/DATA.md first. The dataset is
FROZEN (DATA.md section 8): never modify data/raw, splits, recipes or features. Follow this file
phase by phase; at every STOP print a short report and wait for approval.

Deadline: submission 2026-11-03 before 18:00 (official 23:59 GMT+1).
Target: detectors + calibration on 2026-11-01; fusion, standard test, evasion and metrics on
2026-11-02 morning; packaging and report afterwards.

---------------------------------------------------------------------------------------------

## 0. Binding rules

1. Decision 66 (DATASET_SPEC section 8): per-observation sample weights that balance attacked and
   not-attacked samples inside each observation; metrics per observation, then averaged (pooled
   values also reported); never use duration, number of windows, station, split, observation id
   or any observation-level metadata as a feature; report feature importances and check that
   absolute-level features (power_db, noise_floor_db) do not dominate.
2. Train on `train`, calibrate thresholds and probabilities on `calib`, report on `test` only.
   Never tune anything on test.
3. False alarms first: every threshold is set for a target false-alarm rate on not-attacked
   calib samples (clean + benign). Default target: 1% per window / per frame. Report FPR on clean
   and on benign separately.
4. Detectors work on ONE recording at a time, from raw inputs (audio.ogg and/or demoddata/), with
   no labels. The same code computes features for evaluation and for the jury's recording
   (reuse detect/canonical.py, detect/features.py, detect/counters.py).
5. Explainability: every alert carries a reason in plain words (which rule or feature, values).
6. CPU only, 1 worker, peak memory well under 2 GB (machine has 7.7 GB). Long runs are launched
   by the user in PowerShell with a resumable command.
7. Every module: argparse CLI where relevant, JSON outputs, pytest tests, relative paths only,
   no personal names (anonymous submission).
8. Baselines are mandatory: every learned detector is compared, on the same splits and at the same
   false-alarm rate, with (a) an energy detector and (b) a threshold on a single feature. Report
   ablations (without absolute-level features; without each feature group).
9. Metrics: per observation with bootstrap confidence intervals at observation level (pooled
   values for reference); event-level detection and detection delay as well as sample-level;
   EER for replay; false-alarm rate on clean and on benign separately.
10. Test lock: before ANY evaluation on test, freeze models, thresholds and calibration files,
   sign them with the trust layer (Ed25519) and record their SHA-256 in docs/PROGRESS.md. Test
   results are produced only after that, by eval scripts, and nothing is retuned afterwards.
   Until then, develop and tune on train (grouped cross-validation by observation) and calib only.

## 1. Phase J — Replay detector (frames)   (STOP after)

Module: detect/replay_det.py. Input: canonical frame stream of one recording + a hash history of
EARLIER passes of the same satellite. The history holds the clean frames of all passes that start
before the recording under test (chronological order, any split), as a real ground station would
keep; it never holds labels or frames of the recording under test. Also report the EER.
Evidence, each with a score and a reason:
- run of frames whose sha256 was already seen earlier in the same pass (run length >= 2), or a
  single repeated frame far from its original (> rewrite window);
- frames whose sha256 is in the cross-pass hash store (cross-pass replay);
- counter regression: counter <= max counter already seen (FUNCUBE-1 per-frame counter,
  TECHSAT 1B seconds counter), when the counter is decodable;
- "probable rewrite" events (n_copies > 0 from the canonical stream): low but non-zero score.
Calibrate the score threshold on calib (target FPR 1% on not-attacked frames).
Report on test: per-frame precision/recall/F1, event-level detection (a replay event is detected
if any of its copies is flagged), FPR on clean and benign, per satellite, per subtype
(intra_pass, cross_pass).

## 2. Phase K — Sequence detector (frames)   (STOP after)

Module: detect/sequence_det.py. Learn on TRAIN clean frames, per satellite:
- natural counter-gap distribution (missed frames) and time-per-count (FUNCUBE-1 ~5 s per step,
  TECHSAT 1B seconds counter);
- natural duplicate / rewrite rate;
- per-byte-position profile of frame contents (bytes that are constant or slowly varying).
Evidence per frame:
- counter goes backwards then forwards (swap), equal counters or identical payloads (duplicate);
- time-vs-counter inconsistency |dt - s_per_count * dcounter| > tolerance (timing, counter_jump),
  only when timing_reliable;
- counter jump whose probability under the learned gap distribution is very low (deletion,
  counter_jump);
- content anomaly: changes in byte positions that are constant in clean data (forged).
Be honest about limits: a deletion that looks like a natural loss may be undetectable; report
per-subtype recall rather than hiding it. Calibrate on calib, report on test as in Phase J.

## 3. Phase L — Interference detector (windows)   (STOP after)

Module: detect/interference_det.py.
- Features: relative / scale-invariant first (peak_to_median_db, peak_width_hz, peak_persistence,
  max_dev_from_obs_median_db, deviation from the TRAIN-only clean template, flatness, spectral
  entropy, spectral kurtosis, occupied bandwidth). Absolute-level features only if the ablation
  shows they help without dominating.
- Labels for training: positive = attack_type interference and is_attack 1; negative = clean,
  benign and replay-audio windows; exclude windows with 0 < overlap_frac < 0.5 (ambiguous).
- Baselines first: energy detector (in-band power vs the observation median) and a threshold on
  peak_to_median_db alone, both thresholded for 1% FPR on calib.
- Model A (main): HistGradientBoostingClassifier trained on TRAIN windows with decision-66 weights.
  Small hyperparameter grid (learning rate, max leaf nodes, min samples per leaf, l2), chosen by
  5-fold GroupKFold by observation on TRAIN only, with early stopping.
  Calibration on CALIB: isotonic and Platt, keep the one with the lower Brier score.
  Threshold for 1% window FPR on calib not-attacked windows.
- Model B (backup for unseen interference): IsolationForest trained on TRAIN not-attacked windows
  only; threshold for 1% FPR on calib.
- Temporal smoothing: k-of-n rule over consecutive windows (default 2 of 3), tuned on calib, to cut
  isolated false alarms; report results with and without it.
- Held-out attack simulation: retrain once without the noise-band shape and test on noise bands,
  and once without tones and test on tones (leave-one-shape-out).
Report on test: window-level ROC AUC, TPR at 1% and 0.1% FPR, FPR on clean and on benign,
Pd vs snr_db curve over the SNR grid (and vs inr_db), minimum detectable snr_db (Pd >= 0.9 at the
calibrated threshold), ECE + reliability diagram, feature importances, ablation without
absolute-level features, results per satellite and per shape.

## 4. Phase M — Trust layer (prevention)   (STOP after)

Module: trust/. Emulated ground-station trust anchor:
- per-frame envelope: session id, monotonic counter, timestamp, sha256 of previous envelope,
  HMAC-SHA256 with a session key;
- Ed25519-signed manifest of the dataset fingerprints and of the trained model files; pinned public
  key; the detector refuses to run with a model whose signature does not verify;
- private key generated locally by setup, never committed (keys/private/ is gitignored).
Tests: replay, reorder, deletion, modification and forged frames on a signed stream are all
rejected deterministically. Measure overhead: microseconds per sign / verify, bytes per frame.

## 5. Phase N — Fusion, response and standard test   (STOP after)

- detect/fusion.py: per frame and per window: accept / flag / quarantine, with calibrated
  confidence and reason text; per recording: a verdict and the list of alert events.
- detect/run.py --obs-dir <folder>: runs on a raw recording folder (audio.ogg, demoddata/,
  metadata.json optional). Missing demoddata -> audio detectors only, and the report says frame
  detectors were unavailable (gr-satellites decoding is a later option). Outputs JSON + a short
  Markdown report.
- eval/run_standard_test.py: takes a TEST observation, applies the official scenario (replay of
  5-20 frames + one sequence anomaly + narrowband interference at a random hidden snr_db), runs
  detect/run.py on it, scores against the labels. Runs over all test observations with several
  seeds; bootstrap confidence intervals at observation level.

## 6. Phase O — Evasion and efficiency   (STOP after)

- Evaluate the evasion variants of the frozen dataset (cross-pass replay, intermittent,
  near-threshold +20..+30 dB, drifting, strongest-frequency placement) and report honestly what
  passes undetected.
- Efficiency: latency in ms per frame and per window, end-to-end seconds per recording, peak memory,
  model sizes, trust-layer overhead.
- One results file: eval/results.md + results.json with every metric required by the challenge
  (detection quality, false alarms, latency, memory, security overhead).

## 7. Phase P — Packaging   (STOP after)

- setup.sh (Ubuntu / Kali) and a Windows equivalent note, requirements.txt with pinned versions,
  Makefile: `make dataset`, `make validate`, `make train`, `make evaluate`, `make demo`.
- `make demo` works offline on data/sample/ (2 observations, sanitized metadata) in a few minutes.
- README: install, one-command demo, how to run on a new SatNOGS recording, where results are.
- Test on a fresh environment. Anonymity scan of every tracked file and of git metadata.
