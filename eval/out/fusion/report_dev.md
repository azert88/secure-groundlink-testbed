# Phase N fusion: development evaluation (train + calib)

Line-screen frequency cap chosen: **f_edge**

| cap | false-line rate (no interference) | worst station | line recall | either recall | line recall full pass |
|---|---|---|---|---|---|
| none | 0.118 | 1.000 | 0.317 | 0.576 | 0.701 |
| f_edge | 0.000 | 0.000 | 0.267 | 0.569 | 0.590 |
| band_guard | 0.118 | 1.000 | 0.305 | 0.573 | 0.675 |

Recording-level quarantine points (<= 1 % of dev recordings with a false alert): {'sequence': 2.489301, 'interference_model': 0.990601}

Recordings 598, not attacked 243: not accepted (flag or quarantine) 0.136 (clean 0.159, benign 0.128); **quarantined 0.012** (stations: {'4272': 0.0625}); recording ECE 0.170

| station | not-attacked recordings | false-alarm rate |
|---|---|---|
| 6 | 8 | 0.000 |
| 431 | 8 | 0.000 |
| 754 | 8 | 0.000 |
| 1466 | 8 | 0.000 |
| 1696 | 8 | 0.000 |
| 2107 | 48 | 0.000 |
| 2173 | 8 | 0.000 |
| 2968 | 4 | 0.000 |
| 2987 | 8 | 0.000 |
| 3855 | 8 | 0.000 |
| 4011 | 4 | 0.000 |
| 4151 | 8 | 0.000 |
| 4171 | 4 | 0.000 |
| 4272 | 48 | 0.562 |
| 4394 | 8 | 0.000 |
| 4434 | 2 | 0.500 |
| 4451 | 8 | 0.000 |
| 4460 | 8 | 0.000 |
| 4738 | 8 | 0.000 |
| 4755 | 8 | 0.000 |
| 4791 | 8 | 0.000 |
| 4803 | 8 | 0.000 |
| 5155 | 5 | 1.000 |

| interference duration | n | either (any tier) | either, quarantine tier | window model | line screen |
|---|---|---|---|---|---|
| all | 262 | 0.569 | 0.443 | 0.531 | 0.267 |
| segment | 145 | 0.393 | 0.214 | 0.393 | 0.007 |
| full_pass | 117 | 0.786 | 0.726 | 0.701 | 0.590 |

Replay recall (recording) 1.000 (quarantine tier 1.000), sequence recall 0.851 (quarantine tier 0.690); false interference alerts on recordings without interference 0.159.
