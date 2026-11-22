# Phase 2 gate addendum: S4 power / frame-loss consistency (`jam_v1`)

Addendum to `docs/GATES_PHASE2.md` (signed, `models/gates_p2/lock.json`; S4 row: FA <= 1 % on benign dev conditions,
bench FA <= 1 % on clean, low-SNR and attenuated sessions, wideband jamming detection reported with an interval and
without a pass threshold). That row is not edited. Plan and decisions: `docs/S4_PLAN.md` (P2-31). This addendum is
signed BEFORE any S4 code runs on dev data. Nothing below may be changed after signing; a change = a new version.

## A. Definition of jam_v1 (no pilot, no CFO, no fingerprint input)
- **Expected frames.** Mode B: envelope counters (first to last received counter of the pass, plus the ones the
  receiver reports missing). Simulation: the transmitted frames. SatNOGS: FUNCUBE-1 counters from the first to the
  last decoded counter of the pass. Each expected frame has a time span (simulation: its slot; otherwise linear
  interpolation of the counter-time line of the decoded frames).
- **Windows.** 10 consecutive expected frames, non-overlapping, from the first expected frame; a last window with
  < 5 expected frames is dropped.
- **L_w** = lost / expected frames of the window (lost = not received with a valid CRC / not decoded).
- **P_w (primary statistic)** = 10 log10 of the mean linear in-band power over the window span.
  Bench / simulation: in-band zone = [98, 166] kHz of the 528 kS/s baseband (link carrier +132 kHz ± 34 kHz, i.e. the
  worst carrier offset), power per 50 ms block by FFT (`bench/zone_power.py`), each block referred to the RX input by
  subtracting its recorded RX gain (dB) before averaging. SatNOGS: in-band audio power on [f_low, f_edge] (Phase 1
  band) over the window span, in dB relative to the median in-band power of 10 s blocks of the same audio file.
- **Rule.** Candidate window: L_w >= L0 AND P_w >= P0. `jamming_suspected` = every window that belongs to a run of
  >= 2 consecutive candidate windows. Per pass: jamming_suspected if >= 1 alert window.
- **L0 = 0.3 (fixed).** **P0** = 99th percentile (numpy, linear interpolation) of P_w over the benign windows with
  L_w >= L0 of the threshold set; if that set has < 20 windows, P0 = its maximum; if it is empty, P0 = +inf (no alert,
  reported). Threshold set: simulation = calib runs (B.3); SatNOGS = the other stations (LOSO, C); bench = benign
  bench calib sessions (D). No jamming data sets any threshold.
- Output: separate field `jamming_suspected` (+ L_w, P_w per window). Fusion and every locked file untouched.
- Pilot SNR (`bench/dsp.pilot_estimate`) is a diagnostic reported per condition, never an input of jam_v1.

## B. Development data: mode B simulation (seed tag `s4-dev`)
1. Channel. Real PHY (`bench/link_phy.py` modulate / demodulate). Payloads: FUNCUBE-1 frames (`data/tables/frames.csv`,
   clean variant) of one source observation sealed by `bench/link_signed.Sender`, cycled to 60 frames per pass
   (6 windows). Each slot = burst + 0.2 s idle. Satellite carrier offset U[-15, 15] kHz per pass; pilot tone at
   +60 kHz + offset, amplitude 0.2 x burst amplitude (diagnostic only). Receiver noise white over 528 kHz, -45 dBFS
   in 24 kHz; SNR = burst power / noise power in 24 kHz (`link_phy` convention). RNG: numpy
   `default_rng([crc32(b"s4-dev"), split, condition, run])`.
2. Conditions. Benign (gated):
   nominal (SNR 15 dB) | low_snr (SNR U[2, 8] dB per pass) | attenuation_step (15 dB, then 3 dB from frame U{20..40})
   | fading (Rayleigh per frame, AR(1) complex gain rho = 0.8, mean SNR 14 dB) | pass_ramp (SNR_k = 16 sin(pi (k + 0.5) / 60) dB)
   | noise_rise (SNR 15 dB; noise raised by U[1, 6] dB on frames 20-39, Phase 1 range) | gain_step (pass_ramp; RX gain
   +10 dB from frame 30, recorded and removed).
   Jamming (link SNR 15 dB, whole pass), SIR = burst power / jammer power in 24 kHz around the carrier,
   SIR in {-10, -5, 0, 5, 10, 20} dB x kind in {continuous: Gaussian noise band-limited to 30 kHz, centred on the
   carrier + U[-3, 3] kHz | pulsed: the same, on/off 50 % duty, period 0.25 s, random phase | fullband: white over
   the whole 528 kHz band, covering the pilot zone}.
3. Runs. Calib (threshold set): 20 runs per benign condition, sources = FUNCUBE-1 calib observations.
   Evaluation: 50 runs per benign condition (300 windows), 20 runs per jamming condition (120 windows), sources =
   FUNCUBE-1 train observations. Test observations are never used.

## C. SatNOGS false alarms (FUNCUBE-1, train + calib only; `eval/s4_satnogs_fa.py`, written later, before the S4 verdict)
Observations with frames and audio; conditions: clean, and benign = the Phase 1 benign variants (`data/recipes/`,
group benign; each combines operations among gain, noise_rise, fading, edge_attenuation) of those observations. P0 by leave-one-station-out (threshold set = lossy benign windows of all other
stations). Passes with < 2 decoded counters or non-increasing counters are excluded and counted. The noise_rise FA is
**optimistic** (frames are not re-decoded, so the noise never adds loss). TECHSAT 1B not used (too few frames).

## D. Bench (later, hardware sessions run by the user)
P0 refit on benign bench calib sessions (>= 10 passes) by rule A, then `jam_v1` bench parameters locked
(`eval/lock.py`, name `jam_v1_bench`) before any bench test session. Test conditions: clean, low-SNR, attenuated
(>= 300 windows each) and jamming at measured SIR.

## E. Gate
- **(a) Simulation FA:** for each benign condition, FA = alert windows / windows of the evaluation runs.
  **max over conditions <= 1 %.**
- **(b) SatNOGS FA:** FA per condition (clean, benign), pooled over stations under LOSO. **max over conditions
  <= 1 %.** Reported, not gated: worst station; benign variants broken down by operation type they contain.
- **(c) Bench FA (D):** **<= 1 %** on each of clean, low-SNR and attenuated sessions, each with >= 300 windows.
- **(d) Detection, no threshold:** per kind x SIR, share of jammed windows alerted and share of passes with >= 1
  alert window, Wilson 95 % interval.
- Every FA is reported with its one-sided 95 % Clopper-Pearson upper bound and the per-pass FA (share of benign passes
  with >= 1 alert window), because windows inside a pass are correlated.
- Reported, not gated: P0 values; pilot SNR median per condition; ablations (gain not removed in gain_step;
  per-frame variant P = power over each lost frame's own burst span; L0 = 0.2 and 0.5).
- Verdict: dev passes if (a) and (b) pass; S4 passes if (a), (b) and (c) pass. Any failure is reported as is,
  next to the existing detectors; no further dev iteration without a new signed addendum.

## F. Independence
The simulated conditions and levels are chosen by the author and the simulator is not hardware (W13). The evaluation
runs use other seeds and other source observations than the threshold runs, but the same simulator. The simulation
is necessary, not sufficient; the independent evidence is the bench (D).
