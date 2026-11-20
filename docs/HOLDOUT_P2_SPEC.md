# Phase 2 held-out set (S5) – pre-registered selection rule

Status: **DRAFT until signed** (`python eval/lock.py freeze --name holdout_p2_spec --files docs/HOLDOUT_P2_SPEC.md data/holdout_p2/config_overlay.yaml`).
Purpose: independent numbers for every Phase 2 version and for interference v2, which was built after reading the Phase 1 test (weaknesses W9, W15).

## 1. Source and window
- SatNOGS Network observations of **FUNCUBE-1 (NORAD 39444)** and **TECHSAT 1B (NORAD 25397)**, same transmitter classes as Phase 1 (`config.yaml` `selection.mode_classes`).
- Observation start in **[2026-11-01T00:00:00Z, 2026-12-01T00:00:00Z)**. This is strictly after the Phase 1 window (2026-08-30 to 2026-10-29), so none of these files existed when Phase 1 was designed.
- Eligibility, decoder pin (gr-satellites v5.9.0), QC and canonical frames: **identical to Phase 1** (`docs/DATASET_SPEC.md`, `data/qc.py`). No parameter is changed for this set.
- All eligible observations, with at most 12 per station for FUNCUBE-1 and at most 6 per station for TECHSAT 1B (the Phase 1 caps). Selection is the Phase 1 deterministic round-robin with the Phase 1 seed.

## 2. Split into held-out and bench source (fixed before download)
Each downloaded observation gets `u = int(SHA-256("tsyp14-p2-holdout/" + str(obs_id))[:8], 16) / 2^32`.
- `u < 0.75`: **held-out**. Opened once, by the locked Phase 2 evaluation.
- `u >= 0.75`: **bench source pool**. These audio files are what the Pluto "satellite" may retransmit in mode A. They are never in the replay archive, and never in the held-out.

The rule depends only on the observation id, so it cannot be tuned after looking at the data.

## 3. What may and may not be done
| Allowed | Not allowed |
| --- | --- |
| Download (resumable), SHA-256 manifest, QC pass/fail counts, number of observations and stations | Opening audio or frames of held-out observations for plots, debugging or threshold choice |
| One run of `eval/run_holdout_p2.py` under active locks, all versions side by side (v1, v2, Phase 2 versions) | A second run after any change (a re-run is reported as post hoc, next to the first) |
| Bench-pool observations: any use on the bench | Using bench-pool observations as an evaluation set for the SatNOGS detectors |

Station novelty: stations that appear in Phase 1 train / calib / test (`data/splits.json`) are reported separately from new stations. The headline false-alarm rate is computed on all held-out observations, with per-station values.

## 4. Attacks on the held-out
The same Phase 1 recipe generator (`data/make_recipes.py`), with a new seed **20261201**, standard scenario plus the Phase 1 evasion families plus near-copy replay (Phase O). Recipes are generated and hashed **before** the first detector run.

## 5. Timeline
- 2026-11-20: rule written; signed by the team (UTC time recorded in `docs/PROGRESS.md`).
- 2026-12-01: download (`python data/holdout_p2/fetch_holdout.py --full`), manifest frozen and its SHA-256 added to the lock.
- 2026-12-04: single locked evaluation.

## 6. Known limits
- November passes are fewer than the 60-day Phase 1 window. If a satellite has fewer than 3 eligible stations, this is reported and the satellite is evaluated on what exists. The rule is not relaxed.
- The raw files are not redistributed (licence CC BY-SA 4.0 attribution kept, as in Phase 1). The repository holds the manifest only.
