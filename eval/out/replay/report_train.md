# Replay detector — train

48 observations, 8347 frames (681 replay, 7586 not attacked). Threshold tau = 0.35 (score >= tau).

## Per observation (mean, 95% bootstrap CI over observations)

| metric | value |
|---|---|
| precision | 0.969 [0.925, 1.000] |
| recall | 1.000 [1.000, 1.000] |
| f1 | 0.982 [0.957, 1.000] |
| fpr | 0.002 [0.000, 0.006] |
| EER (pooled, CI) | 0.000 [0.000, 0.000] |
| ROC AUC (pooled) | 1.000 |

## Pooled

| tp | fp | fn | tn | precision | recall | F1 | FPR |
|---|---|---|---|---|---|---|---|
| 681 | 26 | 0 | 7560 | 0.963 | 1.000 | 0.981 | 0.0034 |

FPR by category: benign 0.0033 (n=1833), clean 0.0030 (n=669), other 0.0035 (n=5084); online (ever alerted) FPR 0.0131

## Events

60 / 60 replay events detected; per-observation detection rate 1.000 [1.000, 1.000]; median delay 0.0 s (max 228.0 s), median copies until alert 1.0.

| subtype | events | detected |
|---|---|---|
| intra_pass | 60 | 60 |

## Per satellite

| NORAD | tp | fp | fn | tn | recall | FPR | EER |
|---|---|---|---|---|---|---|---|
| 25397 | 0 | 0 | 0 | 432 | nan | 0.0000 | nan |
| 39444 | 681 | 26 | 0 | 7128 | 1.000 | 0.0036 | 0.000 |

Fires on other attacks: sequence/counter_jump 0.00 (n=9), sequence/deletion 0.00 (n=8), sequence/duplicate 1.00 (n=17), sequence/forged 0.50 (n=12), sequence/swap 0.04 (n=26), sequence/timing 0.38 (n=8)

## Baselines and ablations (same splits, thresholds for the same target FPR on calib)

| detector | tau | recall | FPR | F1 | per-obs F1 | EER |
|---|---|---|---|---|---|---|
| baseline energy | 9.741 | 0.019 | 0.0020 | 0.037 | 0.028 [0.000, 0.067] | 0.579 [0.539, 0.620] |
| baseline n_earlier_same_bytes | > 1 | 0.066 | 0.0000 | 0.124 | 0.051 [0.000, 0.154] | 0.099 [0.022, 0.206] |
| baseline counter_le_max | > 1 | 0.000 | 0.0000 | 0.000 | 0.000 [0.000, 0.000] | 0.097 [0.013, 0.199] |
| baseline (natural tau=1, FPR not controlled) n_earlier_same_bytes | 1 | 0.897 | 0.0675 | 0.677 | 0.737 [0.548, 0.905] | 0.099 [0.022, 0.206] |
| baseline (natural tau=1, FPR not controlled) counter_le_max | 1 | 0.896 | 0.0276 | 0.813 | 0.685 [0.505, 0.855] | 0.097 [0.013, 0.199] |
| ablation no_cross | 0.35 | 1.000 | 0.0034 | 0.981 | 0.982 [0.957, 1.000] | 0.000 [0.000, 0.000] |
| ablation no_intra | 0.35 | 0.831 | 0.0034 | 0.889 | 0.796 [0.638, 0.929] | 0.146 [0.053, 0.248] |
| ablation no_regress | > 0.15 | 1.000 | 0.0000 | 1.000 | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] |
| ablation no_rewrite | 0.35 | 1.000 | 0.0034 | 0.981 | 0.982 [0.957, 1.000] | 0.000 [0.000, 0.000] |
| ablation no_attribution | 0.85 | 0.897 | 0.0092 | 0.897 | 0.870 [0.715, 1.000] | 0.095 [0.000, 0.197] |
| ablation no_runs | 0.35 | 1.000 | 0.0034 | 0.981 | 0.982 [0.957, 1.000] | 0.000 [0.000, 0.000] |

Strict operating point (target 0.001 FPR): tau > 0.35, recall 1.000, FPR 0.0000, per-obs F1 1.000 [1.000, 1.000], events 60 / 60.
Energy baseline coverage (frames with audio windows): 0.95.
