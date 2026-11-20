# Phase 2 pre-registered gates

Status: **DRAFT, to be agreed by the team, then signed** (`python eval/lock.py freeze --name gates_p2 --files docs/GATES_PHASE2.md`) **before** any Phase 2 version is evaluated.

Common rules:
- Validation = leave-one-station-out (LOSO) on train + calib. The held-out set (docs/HOLDOUT_P2_SPEC.md) is never used for validation.
- A version that fails its gate is still reported, next to v1 / v2, with its failure. It is not used in the fused verdict.
- Phase 1 reference numbers are taken from `eval/results.md` (commit 7b74bd5).

| Version | Fixes | Gate (all must hold, LOSO on train + calib) | Reference |
| --- | --- | --- | --- |
| **S1 replay v2** (robust counter-time line + low-Hamming-distance frame matching) | W1 | (a) near-copy replay (1 byte changed per copy) named `replay` in **≥ 0.80** of attacked dev recordings; (b) replay false alarms per frame **≤ 0.1 %** on clean + benign dev; (c) dev recordings not accepted (clean + benign) **≤ 13.6 %** (no increase over v1). All three pooled over LOSO folds (train + calib); worst station always reported next to it. Not gated, reported: stress curve of near-copies with 1, 2, 4, 8, 16 modified bytes; "same counter, different bytes" used as evidence only if its natural TRAIN rate is ~0 (`docs/S1_PLAN.md` §6) | v1: near-copy 0 / 133 (test); frame FA 0.000 |
| **S2 sequence v3** (designed LOSO; content evidence per station or removed) | W2, W3 | (a) worst-station frame FA **≤ 2.65 %**; (b) FUNCUBE-1 recall **≥ 0.765**; (c) TECHSAT 1B: max attainable score **> τ** (the detector can alert) | v1: worst station 2.65 %, recall 0.765, TECHSAT 1B capped at 1.57 < τ |
| **S3 interference v3** (per-station clean templates, archive of station spectra; fixed feature band per station) | W5, W6, W7, W10 | (a) worst-station window FA **≤ 2 %**; (b) full-pass interference detection **≥ 0.55** (line screen); (c) detection at SIR 20–30 dB **> 0.04** | v2: worst station 0.66 % (test), weak SIR 0.04 |
| **S4 power / loss consistency** | W8 | FA **≤ 1 %** on benign dev conditions (noise rise, fading, gain); bench: FA ≤ 1 % on clean, low-SNR and attenuated sessions. Wideband jamming detection is reported with an interval, **without** a pass threshold | — |
| **Bench station** (mode A, audio low-pass per satellite fixed from TRAIN median f_drop) | W13 | On ≥ 5 clean bench passes per satellite from the bench pool: interference alerts **≤** those of the same passes as downloaded (paired) | simulation: to be re-measured (decision P2-07) |
| **Fingerprint / radio evidence** (CFO, noise floor, pilot SNR) | W1 (hardware) | **No gate** (exploratory): AUC with interval, trained on day 1, tested on day 2. A negative result is reported | — |

Values marked **bold** are the proposals in `PLAN_PHASE2.md` section 5. Change them only before signing.
