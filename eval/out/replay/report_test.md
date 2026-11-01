# Replay detector — test

14 observations, 7379 frames (1424 replay, 5793 not attacked). Threshold tau = 0.35 (score >= tau).

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
| 1424 | 0 | 0 | 5793 | 1.000 | 1.000 | 1.000 | 0.0000 |

FPR by category: benign 0.0000 (n=552), clean 0.0000 (n=184), other 0.0000 (n=5057); online (ever alerted) FPR 0.0000

## Events

133 / 133 replay events detected; per-observation detection rate 1.000 [1.000, 1.000]; median delay 0.0 s (max 0.0 s), median copies until alert 1.0.

| subtype | events | detected |
|---|---|---|
| cross_pass_evasion | 1 | 1 |
| intra_pass | 132 | 132 |

## Per satellite

| NORAD | tp | fp | fn | tn | recall | FPR | EER |
|---|---|---|---|---|---|---|---|
| 25397 | 618 | 0 | 0 | 2963 | 1.000 | 0.0000 | 0.000 |
| 39444 | 806 | 0 | 0 | 2830 | 1.000 | 0.0000 | 0.000 |

Fires on other attacks: sequence/counter_jump 0.00 (n=12), sequence/deletion 0.00 (n=21), sequence/duplicate 1.00 (n=22), sequence/forged 0.21 (n=29), sequence/swap 0.00 (n=60), sequence/timing 0.00 (n=18)

## Baselines and ablations (same splits, thresholds for the same target FPR on calib)

| detector | tau | recall | FPR | F1 | per-obs F1 | EER |
|---|---|---|---|---|---|---|
| baseline energy | 9.74105 | 0.005 | 0.0007 | 0.010 | 0.009 [0.000, 0.025] | 0.695 [0.516, 0.801] |
| baseline n_earlier_same_bytes | > 1 | 0.000 | 0.0000 | 0.000 | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| baseline counter_le_max | > 1 | 0.000 | 0.0000 | 0.000 | 0.000 [0.000, 0.000] | 0.067 [0.023, 0.116] |
| baseline (natural tau=1, FPR not controlled) n_earlier_same_bytes | 1 | 1.000 | 0.0000 | 1.000 | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] |
| baseline (natural tau=1, FPR not controlled) counter_le_max | 1 | 0.929 | 0.0147 | 0.934 | 0.937 [0.915, 0.959] | 0.067 [0.023, 0.116] |
| ablation no_cross | 0.35 | 0.989 | 0.0000 | 0.994 | 0.995 [0.986, 1.000] | 0.011 [0.000, 0.033] |
| ablation no_intra | 0.35 | 0.472 | 0.0000 | 0.641 | 0.624 [0.395, 0.814] | 0.365 [0.232, 0.462] |
| ablation no_regress | > 0.15 | 1.000 | 0.0000 | 1.000 | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] |
| ablation no_rewrite | 0.35 | 1.000 | 0.0000 | 1.000 | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] |
| ablation no_attribution | 0.85 | 1.000 | 0.0000 | 1.000 | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] |
| ablation no_runs | 0.35 | 1.000 | 0.0000 | 1.000 | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] |

Strict operating point (target 0.001 FPR): tau > 0.35, recall 1.000, FPR 0.0000, per-obs F1 1.000 [1.000, 1.000], events 133 / 133.
Energy baseline coverage (frames with audio windows): 1.00.
