# Replay detector — calib

15 observations, 1898 frames (228 replay, 1648 not attacked). Threshold tau = 0.35 (score >= tau).

## Per observation (mean, 95% bootstrap CI over observations)

| metric | value |
|---|---|
| precision | 1.000 [1.000, 1.000] |
| recall | 1.000 [1.000, 1.000] |
| f1 | 1.000 [1.000, 1.000] |
| fpr | 0.000 [0.000, 0.000] |
| EER (pooled, CI) | 0.000 [0.000, 0.000] |
| ROC AUC (pooled) | 1.000 |

## Pooled

| tp | fp | fn | tn | precision | recall | F1 | FPR |
|---|---|---|---|---|---|---|---|
| 228 | 0 | 0 | 1648 | 1.000 | 1.000 | 1.000 | 0.0000 |

FPR by category: benign 0.0000 (n=411), clean 0.0000 (n=137), other 0.0000 (n=1100); online (ever alerted) FPR 0.0000

## Events

20 / 20 replay events detected; per-observation detection rate 1.000 [1.000, 1.000]; median delay 0.0 s (max 0.0 s), median copies until alert 1.0.

| subtype | events | detected |
|---|---|---|
| intra_pass | 20 | 20 |

## Per satellite

| NORAD | tp | fp | fn | tn | recall | FPR | EER |
|---|---|---|---|---|---|---|---|
| 25397 | 0 | 0 | 0 | 84 | nan | 0.0000 | nan |
| 39444 | 228 | 0 | 0 | 1564 | 1.000 | 0.0000 | 0.000 |

Fires on other attacks: sequence/counter_jump 0.00 (n=5), sequence/deletion 0.00 (n=4), sequence/duplicate 1.00 (n=5), sequence/swap 0.00 (n=4), sequence/timing 0.25 (n=4)

## Baselines and ablations (same splits, thresholds for the same target FPR on calib)

| detector | tau | recall | FPR | F1 | per-obs F1 | EER |
|---|---|---|---|---|---|---|
| baseline energy | 9.74105 | 0.000 | 0.0012 | 0.000 | 0.000 [0.000, 0.000] | 0.544 [0.469, 0.623] |
| baseline n_earlier_same_bytes | > 1 | 0.000 | 0.0000 | 0.000 | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| baseline counter_le_max | > 1 | 0.000 | 0.0000 | 0.000 | 0.000 [0.000, 0.000] | 0.011 [0.002, 0.017] |
| baseline (natural tau=1, FPR not controlled) n_earlier_same_bytes | 1 | 1.000 | 0.0000 | 1.000 | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] |
| baseline (natural tau=1, FPR not controlled) counter_le_max | 1 | 0.996 | 0.0109 | 0.960 | 0.961 [0.937, 0.985] | 0.011 [0.002, 0.017] |
| ablation no_cross | 0.35 | 1.000 | 0.0000 | 1.000 | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] |
| ablation no_intra | 0.35 | 0.996 | 0.0000 | 0.998 | 0.999 [0.996, 1.000] | 0.004 [0.000, 0.011] |
| ablation no_regress | > 0.15 | 1.000 | 0.0000 | 1.000 | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] |
| ablation no_rewrite | 0.35 | 1.000 | 0.0000 | 1.000 | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] |
| ablation no_attribution | 0.85 | 1.000 | 0.0000 | 1.000 | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] |
| ablation no_runs | 0.35 | 1.000 | 0.0000 | 1.000 | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] |

Strict operating point (target 0.001 FPR): tau > 0.35, recall 1.000, FPR 0.0000, per-obs F1 1.000 [1.000, 1.000], events 20 / 20.
Energy baseline coverage (frames with audio windows): 1.00.
