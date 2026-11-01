# Sequence detector - calib

15 observations, 1898 frames (22 sequence-attacked, 1648 not attacked). tau = 2.30535 (score = -log10 p >= tau).

| metric (per observation, 95% CI) | value |
|---|---|
| precision | 0.920 [0.840, 1.000] |
| recall | 0.820 [0.660, 0.960] |
| f1 | 0.844 [0.756, 0.889] |
| fpr | 0.001 [0.000, 0.002] |
| ROC AUC (pooled) | 0.986 |

Pooled: tp 18, fp 2, fn 4, tn 1646, precision 0.900, recall 0.818, F1 0.857, FPR 0.0012. FPR by category: benign 0.0000 (n=411), clean 0.0000 (n=137), other 0.0018 (n=1100); online FPR 0.0006.

## Recall per subtype (timing: timing-reliable observations only)

| subtype | all | FUNCUBE-1 (39444) | TECHSAT 1B (25397) |
|---|---|---|---|
| deletion | 0.000 (n=4) | 0.000 (n=4) | - |
| swap | 1.000 (n=4) | 1.000 (n=4) | - |
| duplicate | 1.000 (n=5) | 1.000 (n=5) | - |
| forged | - | - | - |
| timing | 1.000 (n=4) | 1.000 (n=4) | - |
| counter_jump | 1.000 (n=5) | 1.000 (n=5) | - |

Events: 16 / 20 detected, per-obs rate 0.800 [0.650, 0.950], median delay 0 s (max 10 s); by subtype counter_jump 5/5, deletion 0/4, duplicate 5/5, swap 2/2, timing 4/4.
Fires on other attacks: replay/intra_pass 1.00 (n=228)

## Baselines and ablations (thresholds by the same rule)

| detector | tau | recall | FPR | F1 | per-obs F1 | AUC |
|---|---|---|---|---|---|---|
| baseline energy | 9.74105 | 0.000 | 0.0012 | 0.000 | 0.000 [0.000, 0.000] | 0.526 |
| baseline counter_step | 14 | 0.000 | 0.0006 | 0.000 | 0.000 [0.000, 0.000] | 0.966 |
| baseline abs_time_dev | 6 | 0.318 | 0.0000 | 0.483 | 0.438 [0.200, 0.714] | 0.910 |
| ablation no_dup_payload | 2.30535 | 0.818 | 0.0036 | 0.783 | 0.780 [0.703, 0.857] | 0.985 |
| ablation no_same_counter | 2.30535 | 0.818 | 0.0012 | 0.857 | 0.844 [0.756, 0.889] | 0.986 |
| ablation no_inversion | 2.28929 | 0.545 | 0.0000 | 0.706 | 0.705 [0.590, 0.819] | 0.957 |
| ablation no_time_dev | 2.30535 | 0.818 | 0.0012 | 0.857 | 0.844 [0.756, 0.889] | 0.987 |
| ablation no_gap | 2.30535 | 0.818 | 0.0012 | 0.857 | 0.844 [0.756, 0.889] | 0.902 |
| ablation no_content | 2.30535 | 0.818 | 0.0012 | 0.857 | 0.844 [0.756, 0.889] | 0.993 |

Strict point (alternative only, target 0.001): tau 2.78247, recall 0.727, FPR 0.0000.

## Naming (detect/naming.py; replay tau 0.35)

Name accuracy on alerted attacked frames: 0.992. Frames flagged by both detectors: 234, named {'replay': 228, 'duplicate': 5, 'timing': 1}, truth {'replay': 228, 'duplicate': 5, 'timing': 1}.

| truth | names given |
|---|---|
| counter_jump | counter_jump 5 |
| duplicate | duplicate 5 |
| not_attacked | swap 2 |
| replay | replay 228 |
| swap | swap 4 |
| timing | swap 2, timing 2 |
