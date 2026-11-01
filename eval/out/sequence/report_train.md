# Sequence detector - train

48 observations, 8347 frames (80 sequence-attacked, 7586 not attacked). tau = 2.30535 (score = -log10 p >= tau).

| metric (per observation, 95% CI) | value |
|---|---|
| precision | 0.775 [0.623, 0.905] |
| recall | 0.862 [0.772, 0.938] |
| f1 | 0.770 [0.645, 0.885] |
| fpr | 0.007 [0.001, 0.016] |
| ROC AUC (pooled) | 0.973 |

Pooled: tp 70, fp 59, fn 10, tn 7527, precision 0.543, recall 0.875, F1 0.670, FPR 0.0078. FPR by category: benign 0.0049 (n=1833), clean 0.0075 (n=669), other 0.0089 (n=5084); online FPR 0.0132.

## Recall per subtype (timing: timing-reliable observations only)

| subtype | all | FUNCUBE-1 (39444) | TECHSAT 1B (25397) |
|---|---|---|---|
| deletion | 0.000 (n=8) | 0.000 (n=8) | - |
| swap | 1.000 (n=26) | 1.000 (n=26) | - |
| duplicate | 0.882 (n=17) | 0.882 (n=17) | - |
| forged | 1.000 (n=12) | 1.000 (n=12) | - |
| timing | 1.000 (n=8) | 1.000 (n=8) | - |
| counter_jump | 1.000 (n=9) | 1.000 (n=9) | - |

Events: 57 / 67 detected, per-obs rate 0.850 [0.762, 0.925], median delay 0 s (max 80 s); by subtype counter_jump 9/9, deletion 0/8, duplicate 15/17, forged 12/12, swap 13/13, timing 8/8.
Fires on other attacks: replay/intra_pass 1.00 (n=681)

## Baselines and ablations (thresholds by the same rule)

| detector | tau | recall | FPR | F1 | per-obs F1 | AUC |
|---|---|---|---|---|---|---|
| baseline energy | 9.74105 | 0.000 | 0.0020 | 0.000 | 0.000 [0.000, 0.000] | 0.419 |
| baseline counter_step | 14 | 0.087 | 0.0054 | 0.109 | 0.118 [0.046, 0.193] | 0.939 |
| baseline abs_time_dev | 6 | 0.350 | 0.0034 | 0.418 | 0.517 [0.396, 0.640] | 0.893 |
| ablation no_dup_payload | 2.30535 | 0.787 | 0.0087 | 0.603 | 0.705 [0.570, 0.833] | 0.933 |
| ablation no_same_counter | 2.30535 | 0.863 | 0.0078 | 0.663 | 0.765 [0.644, 0.874] | 0.973 |
| ablation no_inversion | 2.28929 | 0.575 | 0.0037 | 0.597 | 0.663 [0.529, 0.795] | 0.954 |
| ablation no_time_dev | 2.30535 | 0.838 | 0.0071 | 0.667 | 0.761 [0.654, 0.859] | 0.962 |
| ablation no_gap | 2.30535 | 0.875 | 0.0071 | 0.686 | 0.798 [0.682, 0.908] | 0.928 |
| ablation no_content | 2.30535 | 0.863 | 0.0078 | 0.663 | 0.765 [0.640, 0.874] | 0.973 |

Strict point (alternative only, target 0.001): tau 2.78247, recall 0.750, FPR 0.0005.

## Naming (detect/naming.py; replay tau 0.35)

Name accuracy on alerted attacked frames: 0.992. Frames flagged by both detectors: 731, named {'replay': 680, 'duplicate': 15, 'counter_jump': 14, 'swap': 13, 'forged': 6, 'timing': 3}, truth {'replay': 680, 'not_attacked': 26, 'duplicate': 15, 'forged': 6, 'timing': 3, 'swap': 1}.

| truth | names given |
|---|---|
| counter_jump | counter_jump 6, deletion 1, swap 2 |
| duplicate | duplicate 17 |
| forged | forged 11, swap 1 |
| not_attacked | counter_jump 14, duplicate 3, forged 1, swap 31, timing 10 |
| replay | replay 681 |
| swap | counter_jump 2, swap 24 |
| timing | timing 8 |
