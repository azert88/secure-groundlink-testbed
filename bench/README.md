# Conducted hardware bench (Phase 2)

Three ADALM-Pluto, two Raspberry Pi, one Kali VM. **Conducted links only, no antenna.** Every session is logged in `docs/BENCH_LOG.md`.

```
 RPi #1 + Pluto #1 "satellite" TX --[30 dB]--+
                                             +--[2-way combiner]--[10 dB]-- RX Pluto #3 + RPi #2 "station"
 Kali VM + Pluto #2 "attacker" TX --[30 dB]--+
 (record-and-replay: for the recording session only, Pluto #1 TX --[40 dB]-- Pluto #2 RX)
```

## Safety (read before wiring)
- Pluto RX absolute maximum input: **+2.5 dBm**. Pluto TX maximum: about **+7 dBm**. With at least 30 dB of fixed attenuation in every TX → RX path, even a TX gain set to maximum by mistake gives about −23 dBm at the RX input.
- `bench/config.yaml` ships with `safety.declared_attenuation_db: 0`. Every TX script refuses to run until you wire the attenuators and declare their total. Default TX gain is −40 dB; above −10 dB a script needs `--i-have-30db-attenuation`.
- Terminate unused SMA ports with 50 Ω. Never connect an antenna. Never leave a TX running unattended.
- Transmissions are started by a person, never by an assistant or a scheduled job.

## Frequency plan (baseband, at 528 kS/s = 11 × 48 kHz, centre 433.92 MHz)
| Zone | Frequencies | Why |
| --- | --- | --- |
| LO leakage, DC | within ±22 kHz of 0 | two Plutos at ±25 ppm each |
| CW pilot | +60 kHz, searched ±23 kHz | carrier offset (CFO) per second: corrects the station and is radio evidence |
| Audio (USB) | +120 to +144 kHz | the 48 kHz SatNOGS audio band, 0–24 kHz |

## Install (Raspberry Pi OS 64-bit and Kali) [steps UNVERIFIED until the first install; record the exact versions]
```bash
sudo apt update && sudo apt install -y python3-venv libiio-utils gnuradio gr-satellites
./setup.sh                                   # Phase 1 environment (.venv)
.venv/bin/pip install -r requirements-bench.txt
.venv/bin/python -m bench.probe             # lists the Plutos; copy URIs into bench/config.yaml
gr_satellites --version                      # Phase 1 pin is v5.9.0; record any difference
```

## Mode A: a real SatNOGS pass through the bench
| Step | Where | Command |
| --- | --- | --- |
| 0. Offline check, no hardware | PC | `python -m bench.sim_loopback --obs-dir data/sample/14932721 --out out/bench_sim/fc` |
| 1. IQ file for the satellite | PC or RPi #1 | `python -m bench.prepare_tx --obs-dir <bench-pool obs> --out out/bench/tx_<id>` |
| 2. Start the station capture | RPi #2 | `python -m bench.rx_capture --device station --seconds 320 --out out/bench/rx_<n>` |
| 3. Transmit (operator) | RPi #1 | `python -m bench.tx_play --device satellite --iq out/bench/tx_<id>` |
| 4. Build the SatNOGS folder | RPi #2 or PC | `python -m bench.iq_to_satnogs --iq out/bench/rx_<n> --norad 39444 --obs-id 9000000<n> --out out/bench/obs_<n>` |
| 5. Detect | RPi #2 or PC | `python detect/run.py --obs-dir out/bench/obs_<n> --out out/bench/obs_<n>.json --md out/bench/obs_<n>.md` |

Source passes for mode A come from the **bench pool** of the Phase 2 held-out download (`docs/HOLDOUT_P2_SPEC.md` section 2). A pass from the Phase 1 dataset is in the replay archive, so retransmitting it is correctly reported as `replay_cross_pass` (checked in simulation).

## Attacks (Kali + Pluto #2, operator only)
```bash
python -m bench.make_attack_iq --kind tone --audio-hz 4000 --out out/bench/atk_tone        # no hardware
python -m bench.tx_play --device attacker --iq out/bench/atk_tone --cyclic --seconds 60     # transmits
python -m bench.make_attack_iq --kind narrow --audio-hz 4000 --width-hz 100 --out out/bench/atk_nb
python -m bench.make_attack_iq --kind wideband --out out/bench/atk_wb
# record-and-replay: capture the legitimate pass with the attacker's RX, then retransmit it later
python -m bench.rx_capture --device attacker --seconds 320 --out out/bench/atk_rec_<n>
python -m bench.tx_play --device attacker --iq out/bench/atk_rec_<n>
```
The received interference level (SIR) is set by the TX gain and the attenuators. It is **measured** from the station capture, never assumed.

## Outputs used as radio evidence
`bench_features.csv`, one row per second: `cfo_hz` (offset between the transmitter and the station), `pilot_snr_db`, `power_dbfs`. `bench_frames.csv` holds the frame times in milliseconds. These feed the fingerprint study (CFO, noise floor) and the power / loss consistency check (S4).

## Known limits
- The frame decoder in the loop is gr-satellites on the station's audio. `--frames-from` (simulation) copies the source frames instead, and is labelled as such.
- gr-satellites needs `--throttle` (real time) to timestamp the frames of an audio file. On the live bench, the frames get real reception times.
- Mode A reuses the source pass's audio, which already contains that station's noise. The bench adds its own noise on top.
