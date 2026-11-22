# Phase 2 gate addendum: transmitter fingerprint study (exploratory)

Addendum to `docs/GATES_PHASE2.md` (signed, `models/gates_p2/lock.json`, not edited). Plan: `docs/FINGERPRINT_PLAN.md`.
Signed BEFORE any fingerprint capture exists. Nothing below may be changed after signing; a change = a new version.

## A. Status: exploratory, no pass threshold
- There is no PASS / FAIL. The study reports measured separability, whatever it is. A negative result is reported.
- Output is informational only: never an input of fusion, of any verdict, or of any locked detector.

## B. Fixed before data
1. **Features** F1–F6 exactly as defined in `docs/FINGERPRINT_PLAN.md` section 2 (per 1 s window; capture value = median). No feature is added, dropped or redefined after day 1 data is seen; any extra feature is reported as post hoc, separately.
2. **Hypotheses** H1–H3 (plan section 3), stated before data.
3. **Classes and labels**: L, R1, R2 from `docs/BENCH_LOG.md` capture IDs only. Captures with a logged fault (overflow, wiring change, matched power outside ±1 dB) are excluded by that log entry and counted in the report.
4. **Split**: train = all captures of day 1; test = all captures of day 2 (first two logged fingerprint days). No random frame, window or capture split. Further days, if any, are reported as extra test days with the day 1 model.
5. **Tasks**: L vs R1 (primary), L vs R2, L vs (R1 ∪ R2); per mode (A, B) and pooled.
6. **Baselines first, in order**: B0 CFO threshold; B1 single-feature thresholds (F2–F6); B2 logistic regression (scikit-learn 1.9.1, default L2, C = 1.0, features standardised on day 1). Optional B3 random forest (500 trees, seed 0, other parameters default) only after B0–B2 are written, and always next to them. No hyper-parameter search.
7. **Metric**: capture-level AUC on day 2, 95 % percentile bootstrap interval, 2000 resamples of captures stratified by class, seed 0. Window-level AUC = secondary. Each AUC is reported with n per class.
8. **Lock**: models and day 1 statistics locked with `eval/lock.py freeze --name fp_v1` before day 2 features are computed; day 2 is processed once.
9. **Covariates reported** (not used to select data): warm-up state, temperature, R1 delay (short / long), TX gain, received power difference.

## C. Reporting
Results go in `docs/PROGRESS.md` next to the S1 replay results, with the limits of plan section 6. No claim of device identification beyond this bench.
