# Bench log (conducted hardware sessions)

One entry per session, written by the operator **before** the first transmission. No entry = no transmission.
Safety: no antenna; at least 30 dB of fixed attenuation in every TX -> RX path (Pluto RX abs. max +2.5 dBm, TX max about +7 dBm); unused ports terminated with 50 ohm.

## Template

```text
Session:        S-0xx
Date (UTC):     2026-11-__T__:__Z     Operator: ____
Purpose:        loop-back / mode A pass / mode B link / attack <kind> / fingerprint day N / RSSI-loss
Devices:        Pluto #1 (satellite) serial ____ fw ____ on RPi #1 (model ____, RAM ____)
                Pluto #2 (attacker)  serial ____ fw ____ on Kali VM
                Pluto #3 (station)   serial ____ fw ____ on RPi #2 (model ____, RAM ____)
Wiring:         Pluto#1 TX -[__ dB]-+                                    (attenuator values and models)
                Pluto#2 TX -[__ dB]-+-[combiner ____]-[__ dB]- Pluto#3 RX
                unused ports: 50 ohm loads on ____
Declared att.:  __ dB (bench/config.yaml safety.declared_attenuation_db)  -> worst case at RX: __ dBm
Frequency:      433.920 MHz, sample rate 528000 S/s
TX gains:       satellite __ dB, attacker __ dB      RX gain: __ dB
Measured:       pilot SNR __ dB, CFO __ Hz, IQ power __ dBFS (bench_features.csv)
Files:          out/bench/<...>  (IQ base names, obs ids 9xxxxxxxx)
Room temp.:     __ C, warm-up time __ min (fingerprint sessions)
Notes / incidents:
```

## Sessions

(none yet)
