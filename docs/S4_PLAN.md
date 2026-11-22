# S4 plan – power / frame-loss consistency against wideband jamming (W8)

Status: PLAN with decisions (P2-31), signed together with `docs/GATES_PHASE2_S4.md` (`models/gates_p2_s4/lock.json`).
Idea (Xu et al., MobiHoc 2005): high received power in the signal band + high frame loss = jamming; low power + loss = poor propagation. Nothing evaluated yet.

## Decisions (user, 2026-11-22)
- **Q1 yes.** Detection is evaluated on the mode B simulation (dev) and on the bench (later) only. SatNOGS is used for false alarms only, and that FA is **optimistic for `noise_rise`** (the attack chain does not re-decode, so a noise rise never adds loss there).
- **Q2 no** re-decoding of SatNOGS audio before 2026-12-11.
- **Q3:** `jamming_suspected` is a separate output field; fusion and every locked file are untouched.
- **Realism:** the primary statistic uses **no pilot** (a real satellite has none, a real jammer is co-channel). Pilot SNR is a bench-only diagnostic, never read by `jam_v1`. A full-band jammer (covers the pilot zone too) is simulated to show the statistic does not depend on the pilot.

## 1. Evidence in the two worlds
**(a) SatNOGS.** No RSSI, no IQ: 48 kHz audio + decoded frames. Checked in the code: `attacks/apply.py` changes only the audio for `interference`, `noise_rise`, `gain`, `fading`, `edge_attenuation`; only `replay` / `sequence` change the frames. A jammed recording keeps all its frames: **detection cannot be measured on SatNOGS data.** Usable for FA: natural loss from the FUNCUBE-1 counter (+1 per 5 s; e.g. obs 14932721 counters 23153410 -> 23153419 at t 77 -> 122 s, 8 frames missing) and in-band audio power on [f_low, f_edge], relative to the pass (the audio level is not calibrated). TECHSAT 1B: too few frames (54 TRAIN frames on 26 obs, P2-10); not used.
**(b) Bench / mode B simulation.** Loss: expected = envelope counters (`link_signed`, P2-28), decoded = CRC-valid bursts. Power: in-band zone power per block from the IQ (`bench/zone_power.py`, new), referred to the RX input (dBFS − recorded manual RX gain). `bench_features.csv` (pilot SNR, whole-band power, CFO) is kept as a diagnostic.

## 2. Detector `jam_v1` (definition fixed in the gate)
Windows of 10 consecutive expected frames. Per window: loss L_w = lost / expected; in-band power P_w = mean in-band power over the window span (dB). Candidate when L_w ≥ L0 and P_w ≥ P0; `jamming_suspected` on every window of a run of ≥ 2 consecutive candidates. L0 = 0.3 (fixed); P0 = 99th percentile of P_w over the lossy (L_w ≥ L0) benign windows of the calib runs. No jamming data sets any threshold.
Benign cases it must not flag: low SNR / attenuation and low elevation (loss at low power), fading, gain change (recorded gain removed), noise rise (raises power, little loss at the Phase 1 levels, U[1, 6] dB).
Known limit: a benign noise rise strong enough to cause loss on a strong link is physically a jammer: power / loss cannot separate them. Per-window averaging can mix a deep fade with strong frames; a per-frame variant (power over each lost frame's own burst) is reported as an ablation, not gated.

## 3. Dev data (seed tag `s4-dev`; frame sources: FUNCUBE-1 train obs for evaluation runs, calib obs for threshold runs)
Mode B simulation through the real PHY (`bench/link_phy.py` demodulates; frames are lost when the jammer wins), 60-frame passes (6 windows), satellite carrier offset U[−15, 15] kHz, pilot at +60 kHz (diagnostic only).
- Benign: nominal, low_snr, attenuation_step, fading, pass_ramp, noise_rise, gain_step (definitions in the gate).
- Jamming (link SNR 15 dB, whole pass): SIR −10, −5, 0, 5, 10, 20 dB × {continuous co-channel, 50 % duty co-channel, full-band incl. pilot zone}.
Realistic as an RF channel model, not as hardware (W13): no ADC clipping, no AGC, no multipath, no Pluto spurs.

## 4. Gate: see `docs/GATES_PHASE2_S4.md` (signed).

## 5. Files, runtime, memory
New only: `bench/zone_power.py`, `bench/link_jam_sim.py`, `detect/jam_v1.py`, `models/jam_v1/params.json`, `eval/s4_dev_eval.py` (simulation), later `eval/s4_satnogs_fa.py` and `eval/s4_bench_eval.py`, tests `tests/test_zone_power.py`, `tests/test_jam_v1.py`, `tests/test_link_jam_sim.py`. Untouched: `config.yaml`, `bench/config.yaml`, all locked files, fusion.
Simulation: one worker, one window (~5.5 s of IQ, ~3 M samples) in memory at a time, < 1 GB; resumable (one JSON per run). Runtime measured on a smoke run before the full run.

## 6. Risks
Absolute power needs a calibrated receive chain: P0 from the simulation does not transfer to the bench (refit on benign bench calib sessions, gate section D) nor to SatNOGS (relative power, LOSO). Hardware FA needs ≥ 300 windows per condition (≥ 50 passes of 60 frames). Mode A decoding may not use the pinned gr-satellites (Kali 5.8.0 vs pin 5.9.0; recorded). Simulated and real Pluto jammers differ and are measured, not assumed.
