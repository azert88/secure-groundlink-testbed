# Phase 2 gate addendum: S3 interference v3.1

Addendum to `docs/GATES_PHASE2.md` (signed, `models/gates_p2/lock.json`) and `docs/GATES_PHASE2_S3.md` (signed,
`models/gates_p2_s3/lock.json`); neither is edited. Interference v3 failed gate (a) on dev (P2-22) and stays off.
This addendum defines v3.1 and its gate, written and signed BEFORE any v3.1 code is run on dev data. Decision P2-23.

## Definition of v3.1
v3.1 = v3 (`docs/S3_PLAN.md`, `detect/interference_v3.py`) with exactly two changes; everything else is identical
(enrollment protocol and k rule, Welch window PSD, band-median normalisation, sigma = max(1.4826 MAD, 1 dB), 3-bin
smoothing, 3 of 5 at tau_T, v3.1 alert = v2 alert OR template alert, k = 0 -> v2 only, tau_T procedure).
1. **Band edge on the scored (re-encoded) audio.** Per enrollment pass, f_drop is `detect/audio_front.steepest_drop`
   (Phase 1 `qc.psd` parameters) of the per-bin median over windows of the window PSD in dB of the common-chain
   (Ogg Vorbis re-encoded) audio, i.e. the same spectra the template is built from, not of the raw audio.
   Band B = [f_low, median f_drop of the enrollment passes - 200 Hz].
2. **Empty bins ignored.** A bin f of B whose template level m(f) (per-bin median of the band-median-normalised
   enrollment spectra) is below **-60 dB** is excluded from T_w; a 3-bin smoothing group counts only if none of its
   bins is excluded. If every bin is excluded, T_w = 0 (no template alert). **-60 dB is fixed here and not tuned.**

## Gate (same three criteria as the signed S3 gate, same population and computation)
Population: the dev recordings of `docs/GATES_PHASE2_S3.md` (train + calib, Phase 1 recipes + `data/recipes_s3dev/`
SIR grid), same chronological enrollment, LOSO tau_T (held-out station's tau_T set on the other stations' scored
not-attacked windows, <= 1 % pooled and per satellite). Interference v2 scores, labels and line-screen results are
those of the v3 run (`out/s3_dev/rec/`, station-out-of-fold v2, decision 92); only the template score is recomputed.
- **(a)** worst-station window FA (clean + benign, 3 of 5, stations with >= 1 scored pass) of v3.1 **<= 2 %**.
- **(b)** **v3.1 alone** on whole-pass random-SIR `interference_*` variants, share with >= 1 alert event
  **>= 0.55**; line screen alone and v2 reported next to it on the same recordings, not gated.
- **(c)** v3.1 on the dev SIR grid at 20, 25 and 30 dB, share with >= 1 alert event **> 0.04**; v2 next to it.
- Reported, not gated: by k (1, 2, 3) and pooled, by satellite, fallback k = 0 separately, detection-vs-SIR curve
  (0, 10, 20, 25, 30 dB), Clopper-Pearson 95 %, v3 (P2-22) next to v3.1.
- **Ablations, reported WITHOUT a gate:** "fix 1 only" (re-encoded band edge, no bin exclusion) and "fix 2 only"
  (raw-audio band edge, -60 dB exclusion), each with its own LOSO tau_T by the same procedure.

## Independence
The dev numbers are NOT independent: v3.1 was designed after reading the v3 dev result and diagnosis (P2-22, P2-23)
on the same dev recordings. A dev pass is necessary, not sufficient. The independent test is the Phase 2 held-out
set (`docs/HOLDOUT_P2_SPEC.md`) on 2026-12-04, read once under an active v3.1 lock. A failed dev gate is reported next
to v2 / v3 and v3.1 is not used in the fused verdict.
