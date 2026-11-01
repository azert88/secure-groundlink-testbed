# Sequence detector - test

14 observations, 7379 frames (162 sequence-attacked, 5793 not attacked). tau = 2.30535 (score = -log10 p >= tau).

| metric (per observation, 95% CI) | value |
|---|---|
| precision | 0.637 [0.328, 0.937] |
| recall | 0.291 [0.102, 0.499] |
| f1 | 0.267 [0.076, 0.473] |
| fpr | 0.009 [0.000, 0.022] |
| ROC AUC (pooled) | 0.782 |

Pooled: tp 48, fp 73, fn 114, tn 5720, precision 0.397, recall 0.296, F1 0.339, FPR 0.0126. FPR by category: benign 0.0109 (n=552), clean 0.0109 (n=184), other 0.0129 (n=5057); online FPR 0.0117.

## Recall per subtype (timing: timing-reliable observations only)

| subtype | all | FUNCUBE-1 (39444) | TECHSAT 1B (25397) |
|---|---|---|---|
| deletion | 0.000 (n=21) | 0.000 (n=6) | 0.000 (n=15) |
| swap | 0.233 (n=60) | 0.438 (n=32) | 0.000 (n=28) |
| duplicate | 0.455 (n=22) | 1.000 (n=10) | 0.000 (n=12) |
| forged | 0.483 (n=29) | 0.824 (n=17) | 0.000 (n=12) |
| timing | 0.167 (n=18) | 0.750 (n=4) | 0.000 (n=14) |
| counter_jump | 0.583 (n=12) | 1.000 (n=7) | 0.000 (n=5) |

Events: 41 / 132 detected, per-obs rate 0.311 [0.114, 0.523], median delay 0 s (max 45 s); by subtype counter_jump 7/12, deletion 0/21, duplicate 10/22, forged 14/29, swap 7/30, timing 3/18.
Fires on other attacks: replay/cross_pass 0.06 (n=16), replay/intra_pass 0.56 (n=1408)

## Baselines and ablations (thresholds by the same rule)

| detector | tau | recall | FPR | F1 | per-obs F1 | AUC |
|---|---|---|---|---|---|---|
| baseline energy | 9.74105 | 0.000 | 0.0007 | 0.000 | 0.000 [0.000, 0.000] | 0.461 |
| baseline counter_step | 14 | 0.037 | 0.0003 | 0.071 | 0.066 [0.011, 0.140] | 0.798 |
| baseline abs_time_dev | 6 | 0.235 | 0.0000 | 0.380 | 0.344 [0.182, 0.496] | 0.529 |
| ablation no_dup_payload | 2.30535 | 0.247 | 0.0193 | 0.255 | 0.205 [0.042, 0.389] | 0.747 |
| ablation no_same_counter | 2.30535 | 0.296 | 0.0140 | 0.330 | 0.256 [0.069, 0.459] | 0.781 |
| ablation no_inversion | 2.28929 | 0.204 | 0.0117 | 0.251 | 0.210 [0.061, 0.379] | 0.709 |
| ablation no_time_dev | 2.30535 | 0.290 | 0.0126 | 0.333 | 0.262 [0.075, 0.463] | 0.736 |
| ablation no_gap | 2.30535 | 0.296 | 0.0124 | 0.340 | 0.267 [0.076, 0.473] | 0.768 |
| ablation no_content | 2.30535 | 0.296 | 0.0016 | 0.438 | 0.328 [0.115, 0.556] | 0.853 |

Strict point (alternative only, target 0.001): tau 2.78247, recall 0.253, FPR 0.0116.

## Naming (detect/naming.py; replay tau 0.35)

Name accuracy on alerted attacked frames: 0.994. Frames flagged by both detectors: 803, named {'replay': 790, 'duplicate': 10, 'forged': 2, 'replay_cross_pass': 1}, truth {'replay': 790, 'duplicate': 10, 'forged': 2, 'replay_cross_pass': 1}.

| truth | names given |
|---|---|
| counter_jump | counter_jump 4, swap 3 |
| duplicate | duplicate 21, replay 1 |
| forged | counter_anomaly 4, forged 14 |
| not_attacked | counter_jump 3, deletion 1, forged 64, swap 5 |
| replay | replay 1408 |
| replay_cross_pass | replay_cross_pass 16 |
| swap | swap 14 |
| timing | swap 1, timing 2 |
