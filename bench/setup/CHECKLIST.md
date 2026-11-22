# Bench setup checklist (day 1: install + receive-only test)

Print this page. Tick each box. **Today nothing transmits.** No antenna, ever.

## A. Before power
- [ ] No antenna anywhere on the bench (put the supplied Pluto antennas in a drawer).
- [ ] Nothing connected to any Pluto **TX** or **RX** SMA port, except 50 ohm terminators.
- [ ] 50 ohm terminator on the **TX** port of each Pluto (an idle Pluto may leak its LO on TX [UNVERIFIED]).
- [ ] RX port: 50 ohm terminator (noise-only capture) or left open; never a cable to another Pluto's TX today.
- [ ] `bench/config.yaml` still has `safety.declared_attenuation_db: 0` (TX scripts refuse to run).
- [ ] Label the Plutos #1 satellite / #2 attacker / #3 station, and the two RPi #1 / #2.
- [ ] Each RPi on its official power supply (a Pluto draws USB power from the Pi).

## B. Install (one command per machine, needs network + sudo)
Branch `phase2` must be pushed to GitHub first (the scripts clone it). Private repo: run `gh auth login` or set a token first.
```bash
wget -O setup_rpi.sh  https://raw.githubusercontent.com/azert88/secure-groundlink-testbed/phase2/bench/setup/setup_rpi.sh
bash setup_rpi.sh                    # RPi #2 station (decodes: needs gr-satellites)
NO_GNURADIO=1 bash setup_rpi.sh      # RPi #1 satellite (optional: skips GNU Radio)
wget -O setup_kali.sh https://raw.githubusercontent.com/azert88/secure-groundlink-testbed/phase2/bench/setup/setup_kali.sh
bash setup_kali.sh                   # Kali VM: set USB pass-through first (header of setup_kali.sh)
```
| Machine | Install OK | Tests (pass / total) | Warnings | Log file |
| --- | --- | --- | --- | --- |
| RPi #1 | [ ] | ___ / ___ | | |
| RPi #2 | [ ] | ___ / ___ | | |
| Kali VM | [ ] | ___ / ___ | | |

- [ ] If `data/tables/frames.csv` is missing (git-ignored), copy it from the PC with `scp`, rerun the tests.
- [ ] Mode B later: copy `keys/private/master.key` to RPi #1 and RPi #2 by `scp` only (never git, never USB stick left behind).
- [ ] Paste each "paste into docs/BENCH_LOG.md" block into the log.

## C. Probe results to note (`python -m bench.probe`, `iio_info -s`, `iio_attr -u <uri> -C`)
| Pluto | Host | URI (`usb:x.y.z`) | Serial (`hw_serial`) | Firmware (`fw_version`) | Model / revision |
| --- | --- | --- | --- | --- | --- |
| #1 satellite | RPi #1 | | | | |
| #2 attacker | Kali VM | | | | |
| #3 station | RPi #2 | | | | |
- [ ] URIs copied into `bench/config.yaml` `devices` on each machine (local edit, `bench/config.yaml` is not a locked file).
- [ ] `gr_satellites --version` on RPi #2 = ______ (Phase 1 pin v5.9.0; record any difference).

## D. Receive-only test (no transmission), on RPi #2 with Pluto #3
TX port terminated, RX port terminated. Only `rx_capture` runs; it never opens a TX buffer.
```bash
cd ~/secure-groundlink-testbed
.venv/bin/python -m bench.rx_capture --device station --seconds 10 --out out/bench/rx_test0
.venv/bin/python -c "import numpy as np; from bench import iqfile; x=iqfile.open_cs16('out/bench/rx_test0').astype(float); print('samples', x.size//2, 'rms dBFS', round(10*np.log10(2*np.mean(x**2)/2048**2), 1))"
```
- [ ] No "WARNING: wall time > expected" (else buffers dropped: note the Pi model and USB port).
- [ ] Samples = 5,280,000 (10 s x 528 kS/s) ± one buffer. Measured: ________
- [ ] Noise RMS (dBFS) with terminator: ______ at RX gain 30 dB (`bench/config.yaml rx.hardware_gain_db`).
- [ ] Same test on RPi #1 with `--device satellite` and in the Kali VM with `--device attacker` (checks pass-through throughput).
- [ ] Results written in `docs/BENCH_LOG.md` (session S-001, purpose "install + RX-only").

## E. Do NOT do before the attenuators are wired and declared
- Do not run `bench.tx_play`, `bench.link_signed` in TX mode, `bench.demo_link` on hardware, or any GNU Radio flowgraph with a TX sink.
- Do not connect a cable from any TX port to any RX port, even "just to check", without >= 30 dB fixed attenuation in the path.
- Do not change `safety.declared_attenuation_db` from 0 before the attenuators are physically in place and their values read off their labels.
- Do not use `--i-have-30db-attenuation`.
- Do not connect an antenna, and do not leave a Pluto powered with an open TX port unattended.
- Do not edit `config.yaml` (root, locked) — the bench reads `bench/config.yaml`.
