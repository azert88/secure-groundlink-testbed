# Phase 2 gate addendum: S1 replay v2.1

Addendum to `docs/GATES_PHASE2.md`, which is signed (`models/gates_p2/lock.json`) and is not edited. Its S1 v2 row and
v2's failure (decision P2-12) stand. Common rules of `docs/GATES_PHASE2.md` apply. Written after the S1 dev diagnosis
(decision P2-13): the dev data have been seen, so the v2.1 dev numbers are not independent; the independent test is the
Phase 2 held-out set (`docs/HOLDOUT_P2_SPEC.md`).

| Version | Fixes | Gate (all must hold, LOSO on train + calib) | Reference |
| --- | --- | --- | --- |
| **S1 replay v2.1** (replay v2 with E2 restricted: masked Hamming d **<= 5** bytes, half the nearest natural pair 11; E2 counts only when **>= 2** near-duplicate frames share one time shift; tau unchanged, v1 value 0.35) | W1 | (a) near-copy replay (1 byte changed per copy) named `replay` in **>= 0.80** of attacked dev recordings, pooled over LOSO folds, worst station reported; (b) replay frame false alarms on clean + benign dev **<= v1 on the same frames** (pooled), worst station reported; (c) dev recordings not accepted (clean + benign) **<= 13.6 %** (v1). Also: reported on the held-out set on 2026-12-04 next to v1 and v2 | v1: near-copy named 0.10 on dev, frame FA 0.26 % on the same dev frames (8 / 3050), not accepted 13.6 %; v2: 1.00 / 0.39 % / 15.2 % (P2-12, FAILED) |
