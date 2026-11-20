# Results summary (Phase 1)

Assembled by `eval/results.py` from the reports in `eval/out/` (TEST split unless stated); full values in `eval/results.json`. CI: per-observation bootstrap 95 % `mean [lo, hi]`, or Clopper-Pearson 95 % for counts.

| metric | value |
|---|---|
| **Standard test** (TEST, 110 attacked recordings) — replay detected | 1.000 [1.000, 1.000] |
| sequence anomaly detected / named correctly | 0.309 [0.182, 0.446] / 0.273 [0.145, 0.400] |
| interference detected | 0.582 [0.464, 0.709] (segment 0.515, full pass 0.690) |
| all three detected | 0.136 [0.064, 0.209] |
| attacked recording not accepted | 1.000 [1.000, 1.000] |
| replay frames P / R / F1 | 0.999 / 1.000 / 1.000 |
| **False alarms, recording** (TEST, 44 clean + benign) | 0.000 [0.000, 0.080]; clean 0.000 [0.000, 0.285], benign 0.000 [0.000, 0.106]; every station 0% |
| upper bound for 0 / 44 (95 %) | rule of three 6.8%, exact one-sided 6.6% |
| false alarms, recording, development (train + calib, 243) | not accepted 13.6%, quarantined 1.2%; stations {'4272': 0.5625, '4434': 0.5, '5155': 1.0} |
| false alarms, frame (TEST) | replay 0.000 [0.000, 0.001]; sequence as fused (content off) 0.002 [0.001, 0.003]; locked v1 with content 1.26% |
| false alarms, window (interference v2, TEST) | clean 0.14%, benign 0.16%; worst station 4806 0.66% |
| Pd vs SIR, window level (snr_db: Pd) | -10: 0.77, -5: 0.73, 0: 0.90, 5: 0.66, 10: 0.84, 15: 0.62, 20: 0.25, 25: 0.01, 30: 0.00, 35: 0.00 |
| Pd vs SIR, recording level (standard test, 10 dB bins) | [-10, 0): 0.89 (n=27), [0, 10): 0.69 (n=29), [10, 20): 0.66 (n=29), [20, 30): 0.04 (n=25) |
| Evasion J: cross-pass replay (frames) | recall 1.00 (n=16, 1 event) |
| Evasion K: sequence | no dedicated evasion variant; blind spots: deletion 0/21, TECHSAT 1B cannot alert (decision 81) |
| Evasion L: interference (intermittent, drift, strongest bin, near-threshold), window TPR | v1 0.84, v2 (used) 0.44 |
| Evasion N: weak interference, snr_db 20–30 dB | 0.04 (n=25) |
| **Evasion O: near-copy replay** (1 byte changed per copy, 133 recordings) | named replay 0.000 [0.000, 0.000] vs exact copies 1.000 [1.000, 1.000]; still not accepted 0.523 [0.265, 0.780] (sequence alerts; FUNCUBE-1 1.00, TECHSAT 1B 0.12) |
| Latency | replay 0 s (first copy); interference model median 9 s (max 226 s); line screen offline: decides at the end of the pass |
| Runtime and memory per recording | median 28.9 s (max 85 s, 4 parallel workers); peak traced memory 563 MB |
| Security: per-frame HMAC envelope | 102 B per frame (40 % of 256 B, 232 % of 44 B); seal 4.2 µs, verify 4.7 µs |
| Security: Ed25519 signed manifests / model locks | sign 50 µs, verify 99 µs, 64 B signature |

Near-copy diagnosis (Phase O, decision 102, nothing retuned): without hash matches the copies enter the counter-time line fit; when they are a large share of the pass the fit fails, which disables the counter-regression evidence. Planned fix: a robust line (dominant line + lower-counter off-line cluster = copies), as a new locked version.
