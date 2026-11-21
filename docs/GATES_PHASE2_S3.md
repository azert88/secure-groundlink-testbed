# Phase 2 gate addendum: S3 interference v3

Addendum to `docs/GATES_PHASE2.md`, which is signed (`models/gates_p2/lock.json`) and is not edited. Its S3 row stands
as signed: (a) worst-station window FA <= 2 %; (b) full-pass interference detection >= 0.55; (c) detection at SIR
20-30 dB > 0.04. This addendum fixes how those three numbers are computed, written and signed (with `docs/S3_PLAN.md`)
BEFORE any v3 code is run on dev data. Common rules of `docs/GATES_PHASE2.md` apply. Decision P2-17.

## Enrollment protocol
1. A scored pass p of station s, satellite n is enrolled from the **up to 3 most recent passes of (s, n) with start
   strictly before p's start** that pass Phase 1 audio QC (`data/qc.py`: readable, 48 kHz, mono,
   |audio duration - schedule| <= 10 s). The set is chronological, independent of file location: local data
   (`data/raw/`) first, else fetched. An earlier dev pass enters with its clean variant only.
2. Fetched passes: public SatNOGS Network API, `data/satnogs_api.py` client (1 request / 3 s), status `good`, same
   transmitters as Phase 1, start < 2026-11-01T00:00:00Z (never in the Phase 2 held-out window), metadata + audio
   only, stored in `data/raw_enroll/` with a SHA-256 manifest, never modified, **never scored**.
3. k = number of enrollment passes found: target 3, minimum 1. k = 0: interference v2 only (`fallback`).
4. Held-out (2026-12-04): same rule; candidates are earlier QC-passing passes of (s, n) from dev, from fetched
   passes before 2026-11, or earlier passes of the held-out set itself.

## Gate computation (LOSO on train + calib; v3 = v2 alert OR template alert)
- Population: every recording of a dev pass with k >= 1 (pooled over k). Windows with 0 < overlap_frac < 0.5
  excluded. tau_T for held-out station s is set on the other stations only.
- **(a)** per station: alerted / scored windows (after 3 of 5) on clean + benign recordings; max over stations
  with >= 1 scored pass **<= 2 %**.
- **(b)** **v3 alone** (no line screen): share of whole-pass random-SIR interference variants (`interference_*`,
  segment = whole recording) with >= 1 alert event **>= 0.55**. The line screen alone is reported on the same
  recordings, not gated.
- **(c)** dev SIR grid, seed tag `s3-dev`: per train + calib clean recording, one narrowband interferer at SIR
  0, 10, 20, 25, 30 dB (Phase 1 `plan_interference` with fixed snr_db, seed `recipe_seed(obs_id,
  "s3-dev/snrgrid_<+dd>")`, same chain; recipes in `data/recipes_s3dev/`). Share of the 20, 25 and 30 dB
  recordings with >= 1 alert event **> 0.04**. 0 and 10 dB give the curve, not gated.
- Reported next to the gate, not gated: results by k (1, 2, 3), by satellite, worst station, Clopper-Pearson 95 %;
  interference v2 (station-out-of-fold scores, decision 92) and v2 + line screen on exactly the same recordings;
  fallback (k = 0) recordings separately; poisoning stress test (interference variant enrolled instead of clean).
- A failed gate is reported next to v1 / v2 and v3 is not used in the fused verdict.
