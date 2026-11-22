# Conducted bench: parts list (buy only what is missing)

All SMA, 50 ohm. Wiring: see `bench/README.md` (2 TX paths of 30 dB -> combiner -> 10 dB -> station RX).

| Qty | Item | Specification | Why |
| --- | --- | --- | --- |
| 2 | SMA fixed attenuator, 30 dB | male-female, DC-3 GHz, >= 1 W | One per TX (satellite, attacker): Pluto TX max about +7 dBm -> about -23 dBm, below the RX absolute maximum (+2.5 dBm) even at full TX gain. |
| 1 | SMA fixed attenuator, 10 dB | male-female, DC-3 GHz, >= 1 W | After the combiner before the station RX: extra margin and a better 50 ohm match at the RX input; also the 30 + 10 = 40 dB path of the record-and-replay session. |
| 1 | SMA 2-way power splitter / combiner | 50 ohm, band covering 433 MHz (e.g. 5-1000 MHz), resistive or Wilkinson | Sums the satellite and attacker signals into one station RX (interference and replay attacks at a known level). Adds about 3.5-6 dB loss depending on type: measure it, do not assume. |
| 4 | SMA male-male coax cable, 30-50 cm | RG316 or similar, 50 ohm | TX -> attenuator -> combiner -> attenuator -> RX; short cables keep loss and pick-up low. |
| 2 | SMA 50 ohm terminator | male, >= 1 W (DC-3 GHz) | Terminate unused ports (idle TX, unused combiner port) so nothing radiates and the RX sees a defined load. |

Notes
- Attenuators rated >= 1 W are far above the Pluto's output (+7 dBm = 5 mW); the margin covers wiring mistakes.
- Check gender: Pluto ports are SMA female; a male-female attenuator screws directly on a port.
- Record maker, model and measured values of each part in `docs/BENCH_LOG.md` (wiring line).
- A 2-way combiner has 3 ports; with only one TX connected, terminate the free input port with 50 ohm.
