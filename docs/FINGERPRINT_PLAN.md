# Fingerprint study plan – can the station tell Pluto #1 "satellite" from Pluto #2 "attacker" replaying it?

Status: plan only, exploratory, no measurement. Gate: `docs/GATES_PHASE2_FP.md` (no pass threshold). Informational: never an input of fusion or any locked verdict.

## 1. Why
Content evidence is weak where replay matters most: TECHSAT 1B near-dup (Hamming) evidence is off (d = 0, 36 / 54 TRAIN beacons have a natural neighbour at 1 byte, P2-10), its natural counter-rewrite rate is 16.7 % (decision 72), and no TECHSAT 1B replay was measured on dev (P2-15). A physical-layer cue would be independent of frame bytes.

## 2. Features (from the station IQ, Pluto #3; per 1 s window, then per capture = median over windows)
- F1 `cfo_hz`: pilot offset (`bench/dsp.pilot_estimate`, `bench_features.csv`); F1b: offset of the signal carrier (mode B, from the demodulator) as a cross-check.
- F2 IQ imbalance: image rejection ratio = power at the mirror of the pilot (-60 kHz - CFO) minus pilot power, dB. The station's own imbalance is common to both classes; measurability [UNVERIFIED].
- F3 noise floor: power in the floor zone [-250, -180] kHz minus RX gain (as `bench/zone_power.py`).
- F4 SNR: `pilot_snr_db`, and in-band SNR (audio zone vs floor zone).
- F5 phase noise: integrated power of the pilot skirt at 100 Hz–5 kHz from the peak, relative to the peak (dBc).
- F6 CFO stability: std of F1 over 1 s windows within the capture (drift).
Implemented later in a new `bench/fp_features.py` (tests on `bench/fake_pluto.py` data); no locked file is touched.

## 3. Record-and-replay hypothesis (H1–H3 written before data)
- H1: a Pluto's RX and TX LOs share one reference oscillator. Recording with Pluto #2 RX shifts the signal by -e2, retransmitting with Pluto #2 TX shifts it by +e2: a same-device relay cancels its own CFO, so F1 of a replay ≈ F1 of the legit link (residual = drift of e2 between recording and replay).
- H2: what does not cancel: the attacker adds its RX noise + 12-bit quantisation and its TX noise (F3 up / F4 down at equal received power), a second IQ imbalance (F2) and a second phase-noise contribution (F5).
- H3: a non-relay attacker (Pluto #2 plays the prepared IQ file itself, R2) keeps its own CFO, so F1 alone should separate it.

## 4. Capture protocol (operator only, `docs/BENCH_LOG.md` purpose "fingerprint day N"; nothing run by the assistant)
- >= 2 days (not consecutive allowed), 2 sessions per day (morning, afternoon). Each session: log room temperature (thermometer) and, if exposed, the AD936x temperature [UNVERIFIED for Pluto firmware]; log power-on time.
- Warm-up: one cold block (capture within 2 min of power-on) and the rest after >= 20 min warm-up; warm-up state is a column, not a filter.
- Classes per session and per mode (A: SatNOGS pass via `prepare_tx`; B: `link_signed`): L = Pluto #1 legit; R1 = Pluto #2 records Pluto #1 (40 dB path) and retransmits; R2 = Pluto #2 plays the same IQ file. 3 captures per class, mode A 320 s, mode B 60 s; order interleaved (L R1 R2 R2 R1 L ...) so time / temperature drift is not confounded with class.
- R1 delay between recording and replay: short (<= 5 min) and long (>= 60 min), half each.
- TX gains: two satellite gains (-30, -20 dB); attacker gain set so the received pilot power matches L within ±1 dB (measured, logged), so received power cannot be the trivial cue. Default safety limits of `bench/safety.py` apply; no antenna.
- Labels come only from BENCH_LOG session / capture IDs. Expected: 2 days x 2 sessions x 2 modes x 3 classes x 3 = 72 captures.

## 5. Evaluation (fixed now; details in the gate file)
- Strictly train on day 1, test on day 2 (Arp et al., "Dos and Don'ts of Machine Learning in Computer Security", USENIX Security 2022: no random frame / window split; windows of one capture are never on both sides).
- Tasks: L vs R1 (main), L vs R2, L vs (R1 ∪ R2); per mode and pooled.
- Order: B0 CFO threshold (|F1 - median L day 1|), B1 single-feature thresholds F2–F6, B2 logistic regression (F1–F6, standardised on day 1). A learned model (scikit-learn random forest, pinned 1.9.1) only after B0–B2 are reported, and reported next to them.
- Metric: capture-level AUC on day 2 with a 95 % bootstrap interval (2000 resamples of captures, stratified by class); window-level AUC reported only as secondary.
- Model and day 1 thresholds locked (`eval/lock.py`, name `fp_v1`) before day 2 features are computed.
- A negative result (e.g. AUC interval containing 0.5 for L vs R1) is acceptable and reported as is.

## 6. Limits
Conducted, one cable path, 3 devices of one model: a result does not generalise to other radios or to over-the-air links. 2 days is a minimum, not a stability study.
