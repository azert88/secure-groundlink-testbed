# Measured weaknesses W1-W15 (Phase 1) and their Phase 2 fix

Numbers from `eval/results.md` and `docs/PROGRESS.md`. Gates: `docs/GATES_PHASE2.md` and its addenda.

| # | Weakness | Phase 2 fix |
| --- | --- | --- |
| W1 | Near-copy replay (named 0 / 133) | S1 (done, replay v2.1) |
| W2 | Sequence cannot alert on TECHSAT 1B (score capped 1.57 < tau) | S2 |
| W3 | Sequence content evidence (70 / 73 test FA, one station) | S2 |
| W4 | Deletions / swaps / time shifts (0 / 0.16 / 0.13) | Bench ms timestamps |
| W5 | Weak interference (0.04 above +20 dB SIR) | S3 |
| W6 | Whole-pass interference (v2 blind, line screen ~0.55) | S3 |
| W7 | TECHSAT 1B band edge | S3 |
| W8 | Wideband jamming partly covered | S4 |
| W9 | Small test (11 obs, 5 stations; 6.6-24 % bound) | S5 |
| W10 | Per-station FA (13.6 % dev, station 4272) | S3 |
| W11 | Calibration ECE 0.168 | S6 |
| W12 | Adaptive adversary untested | S7 |
| W13 | Realism (digital interference on demodulated audio) | Bench |
| W14 | Trust layer key management, 232 % overhead on 44 B | S8 (done) |
| W15 | Interference v2 built after reading test | S5 |
