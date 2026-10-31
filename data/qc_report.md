# Phase C — QC report

| NORAD | obs | audio QC pass | frame QC pass | both | fragments | flagged | selected (audio) | timing reliable / unreliable (frame QC) |
|---|---|---|---|---|---|---|---|---|
| 25397 | 60 | 55 | 6 | 6 | 54 | 0 | 40 | 6 / 0 |
| 39444 | 36 | 32 | 30 | 27 | 0 | 0 | 32 | 22 / 8 |

## f_edge per satellite (Hz; f_drop = steepest drop of the median PSD)

| NORAD | f_edge min | median | max | f_drop median |
|---|---|---|---|---|
| 25397 | 10102 | 10148 | 10336 | 11074 |
| 39444 | 19523 | 19523 | 22805 | 19535 |

## Rejection reasons (counts)

- 25397 audio: audio duration differs from schedule: 5
- 25397 frame: canonical frames: 54
- 39444 audio: audio duration differs from schedule: 4
- 39444 frame: canonical frames: 6

## Failed observations

| obs | NORAD | station | audio reasons | frame reasons |
|---|---|---|---|---|
| 15004025 | 25397 | 4451 |  | 1 canonical frames < 10 |
| 15287655 | 25397 | 5049 | audio duration differs from schedule by -33.8 s | 2 canonical frames < 10 |
| 15179622 | 25397 | 5065 | audio duration differs from schedule by -164.2 s | 1 canonical frames < 10 |
| 15312338 | 25397 | 3855 |  | 1 canonical frames < 10 |
| 14942057 | 25397 | 4171 |  | 2 canonical frames < 10 |
| 15305494 | 25397 | 2968 |  | 8 canonical frames < 10 |
| 15066716 | 25397 | 4394 |  | 3 canonical frames < 10 |
| 15087940 | 25397 | 4791 |  | 2 canonical frames < 10 |
| 15087899 | 25397 | 4460 |  | 1 canonical frames < 10 |
| 15287498 | 25397 | 1466 |  | 3 canonical frames < 10 |
| 15087941 | 25397 | 4151 |  | 1 canonical frames < 10 |
| 14944671 | 25397 | 4755 |  | 3 canonical frames < 10 |
| 15236772 | 25397 | 2987 |  | 1 canonical frames < 10 |
| 15237441 | 25397 | 754 |  | 3 canonical frames < 10 |
| 15069330 | 25397 | 4073 |  | 1 canonical frames < 10 |
| 15173022 | 25397 | 4738 |  | 3 canonical frames < 10 |
| 14911777 | 25397 | 1696 |  | 1 canonical frames < 10 |
| 15140303 | 25397 | 1664 |  | 7 canonical frames < 10 |
| 14966459 | 25397 | 6 |  | 1 canonical frames < 10 |
| 14947840 | 25397 | 3762 | audio duration differs from schedule by +28.5 s | 2 canonical frames < 10 |
| 14956312 | 25397 | 4803 |  | 1 canonical frames < 10 |
| 15202727 | 25397 | 2173 |  | 1 canonical frames < 10 |
| 14915678 | 25397 | 431 |  | 4 canonical frames < 10 |
| 15063067 | 25397 | 4451 |  | 1 canonical frames < 10 |
| 15080453 | 25397 | 3855 |  | 2 canonical frames < 10 |
| 15091907 | 25397 | 4394 |  | 1 canonical frames < 10 |
| 15021469 | 25397 | 4791 |  | 2 canonical frames < 10 |
| 15252702 | 25397 | 4460 |  | 2 canonical frames < 10 |
| 15001383 | 25397 | 1466 |  | 1 canonical frames < 10 |
| 15033837 | 25397 | 4151 |  | 1 canonical frames < 10 |
| 14926205 | 25397 | 4755 |  | 1 canonical frames < 10 |
| 15230631 | 25397 | 2987 |  | 3 canonical frames < 10 |
| 15244766 | 25397 | 754 |  | 1 canonical frames < 10 |
| 15150189 | 25397 | 4738 |  | 2 canonical frames < 10 |
| 15128862 | 25397 | 1696 |  | 1 canonical frames < 10 |
| 14976061 | 25397 | 6 |  | 1 canonical frames < 10 |
| 14974899 | 25397 | 4803 |  | 1 canonical frames < 10 |
| 15252654 | 25397 | 2173 |  | 5 canonical frames < 10 |
| 14911770 | 25397 | 431 |  | 1 canonical frames < 10 |
| 14929885 | 25397 | 4451 |  | 1 canonical frames < 10 |
| 15298904 | 25397 | 5049 | audio duration differs from schedule by -34.3 s | 2 canonical frames < 10 |
| 15175144 | 25397 | 3855 |  | 2 canonical frames < 10 |
| 15093731 | 25397 | 4394 | audio duration differs from schedule by -16.2 s | 1 canonical frames < 10 |
| 15019641 | 25397 | 4791 |  | 1 canonical frames < 10 |
| 15039870 | 25397 | 4460 |  | 1 canonical frames < 10 |
| 15066393 | 25397 | 1466 |  | 3 canonical frames < 10 |
| 15029025 | 25397 | 4755 |  | 2 canonical frames < 10 |
| 15094462 | 25397 | 2987 |  | 1 canonical frames < 10 |
| 15269764 | 25397 | 754 |  | 3 canonical frames < 10 |
| 15243923 | 25397 | 4738 |  | 2 canonical frames < 10 |
| 14909273 | 25397 | 1696 |  | 1 canonical frames < 10 |
| 15131577 | 25397 | 4803 |  | 1 canonical frames < 10 |
| 15259181 | 25397 | 2173 |  | 1 canonical frames < 10 |
| 14911525 | 25397 | 431 |  | 1 canonical frames < 10 |
| 14998723 | 39444 | 4806 |  | 1 canonical frames < 10 |
| 15081287 | 39444 | 4434 | audio duration differs from schedule by -17.8 s |  |
| 14971294 | 39444 | 5155 | audio duration differs from schedule by -30.5 s |  |
| 15299816 | 39444 | 4434 | audio duration differs from schedule by -19.6 s |  |
| 15023587 | 39444 | 2107 |  | 3 canonical frames < 10 |
| 14980866 | 39444 | 5155 | audio duration differs from schedule by -29.9 s | 3 canonical frames < 10 |
| 15012409 | 39444 | 4272 |  | 5 canonical frames < 10 |
| 14987124 | 39444 | 4272 |  | 1 canonical frames < 10 |
| 14944444 | 39444 | 2107 |  | 8 canonical frames < 10 |

## Frame QC passes: timing

| obs | NORAD | station | frames | valid | max/s | overfull s | counter-time consistent | timing_reliable |
|---|---|---|---|---|---|---|---|---|
| 15080917 | 25397 | 4188 | 10 | 10 | 1 | 0 | 1.0 | True |
| 15091860 | 25397 | 5049 | 10 | 8 | 1 | 0 | 1.0 | True |
| 15033863 | 25397 | 1664 | 15 | 15 | 1 | 0 | 1.0 | True |
| 15080837 | 25397 | 4188 | 13 | 13 | 1 | 0 | 1.0 | True |
| 15034285 | 25397 | 1664 | 23 | 23 | 1 | 0 | 1.0 | True |
| 15042383 | 25397 | 4188 | 17 | 14 | 1 | 0 | 1.0 | True |
| 15123656 | 39444 | 4011 | 82 | 82 | 2 | 1 | 0.975 | True |
| 15242426 | 39444 | 4272 | 30 | 29 | 1 | 0 | 1.0 | True |
| 14995840 | 39444 | 1763 | 16 | 10 | 16 | 1 | 0.0 | False |
| 14966979 | 39444 | 5155 | 13 | 13 | 2 | 1 | 0.75 | False |
| 15081287 | 39444 | 4434 | 16 | 15 | 2 | 1 | 0.857 | False |
| 15192857 | 39444 | 2107 | 49 | 49 | 1 | 0 | 1.0 | True |
| 15230978 | 39444 | 4272 | 30 | 29 | 1 | 0 | 1.0 | True |
| 14995843 | 39444 | 1763 | 21 | 16 | 21 | 1 | 0.0 | False |
| 15229495 | 39444 | 4806 | 18 | 18 | 1 | 0 | 1.0 | True |
| 14971294 | 39444 | 5155 | 12 | 12 | 1 | 0 | 1.0 | True |
| 15299816 | 39444 | 4434 | 30 | 30 | 2 | 1 | 0.897 | False |
| 15237468 | 39444 | 4272 | 30 | 29 | 2 | 2 | 0.857 | False |
| 14995842 | 39444 | 1763 | 15 | 12 | 15 | 1 | 0.0 | False |
| 15244977 | 39444 | 4806 | 17 | 17 | 1 | 0 | 1.0 | True |
| 15275276 | 39444 | 2107 | 19 | 19 | 1 | 0 | 1.0 | True |
| 15202641 | 39444 | 4272 | 30 | 29 | 1 | 0 | 1.0 | True |
| 15305451 | 39444 | 2107 | 45 | 45 | 2 | 1 | 0.955 | True |
| 14934136 | 39444 | 2107 | 15 | 14 | 2 | 2 | 0.846 | False |
| 15301862 | 39444 | 4272 | 39 | 39 | 1 | 0 | 1.0 | True |
| 14957048 | 39444 | 2107 | 15 | 14 | 1 | 0 | 1.0 | True |
| 15285174 | 39444 | 2107 | 14 | 14 | 1 | 0 | 1.0 | True |
| 15228452 | 39444 | 4272 | 30 | 29 | 2 | 1 | 0.929 | True |
| 15178847 | 39444 | 2107 | 17 | 17 | 1 | 0 | 1.0 | True |
| 15297741 | 39444 | 4272 | 30 | 29 | 1 | 0 | 0.964 | True |
| 15211184 | 39444 | 4272 | 30 | 29 | 2 | 1 | 0.929 | True |
| 15090581 | 39444 | 2107 | 12 | 12 | 1 | 0 | 1.0 | True |
| 15222456 | 39444 | 4272 | 30 | 29 | 2 | 1 | 0.929 | True |
| 14932721 | 39444 | 2107 | 16 | 15 | 1 | 0 | 1.0 | True |
| 15192903 | 39444 | 4272 | 30 | 29 | 1 | 0 | 1.0 | True |
| 15202507 | 39444 | 2107 | 59 | 59 | 1 | 0 | 1.0 | True |

## Flagged for waterfall review (narrowband line <= 200 Hz)

| obs | NORAD | station | status | narrow lines (Hz, width, max frac) | waterfall |
|---|---|---|---|---|---|

## Narrow lines inside the own signal band (in_signal_band, not flagged)

| obs | NORAD | station | downlink region, TRAIN (Hz) | lines (Hz, -3 dB width, max frac) |
|---|---|---|---|---|

## Natural rewrites per station (byte-identical copy within max_rewrite_delay_s {39444: 0.0, 25397: 5.0})

| NORAD | station | obs | canonical frames | rewritten | rate | delays (s) |
|---|---|---|---|---|---|---|
| 25397 | 4188 | 3 | 40 | 31 | 77.5% | 1 |
| 25397 | 2968 | 1 | 8 | 5 | 62.5% | 4 |
| 25397 | 5049 | 3 | 14 | 5 | 35.7% | 1 |
| 25397 | 2173 | 3 | 7 | 4 | 57.1% | 2 |
| 25397 | 4460 | 3 | 4 | 2 | 50.0% | 2, 3 |
| 25397 | 1466 | 3 | 7 | 1 | 14.3% | 2 |
| 25397 | 4755 | 3 | 6 | 1 | 16.7% | 2 |
| 25397 | 6 | 2 | 2 | 0 | 0.0% |  |
| 25397 | 431 | 3 | 6 | 0 | 0.0% |  |
| 25397 | 754 | 3 | 7 | 0 | 0.0% |  |
| 25397 | 1664 | 3 | 45 | 0 | 0.0% |  |
| 25397 | 1696 | 3 | 3 | 0 | 0.0% |  |
| 25397 | 2987 | 3 | 5 | 0 | 0.0% |  |
| 25397 | 3762 | 1 | 2 | 0 | 0.0% |  |
| 25397 | 3855 | 3 | 5 | 0 | 0.0% |  |
| 25397 | 4073 | 1 | 1 | 0 | 0.0% |  |
| 25397 | 4151 | 2 | 2 | 0 | 0.0% |  |
| 25397 | 4171 | 1 | 2 | 0 | 0.0% |  |
| 25397 | 4394 | 3 | 5 | 0 | 0.0% |  |
| 25397 | 4451 | 3 | 3 | 0 | 0.0% |  |
| 25397 | 4738 | 3 | 7 | 0 | 0.0% |  |
| 25397 | 4791 | 3 | 5 | 0 | 0.0% |  |
| 25397 | 4803 | 3 | 3 | 0 | 0.0% |  |
| 25397 | 5065 | 1 | 1 | 0 | 0.0% |  |
| 39444 | 1763 | 3 | 52 | 0 | 0.0% |  |
| 39444 | 2107 | 12 | 272 | 0 | 0.0% |  |
| 39444 | 4011 | 1 | 82 | 0 | 0.0% |  |
| 39444 | 4272 | 12 | 315 | 0 | 0.0% |  |
| 39444 | 4434 | 2 | 46 | 0 | 0.0% |  |
| 39444 | 4806 | 3 | 36 | 0 | 0.0% |  |
| 39444 | 5155 | 3 | 28 | 0 | 0.0% |  |

## Wide lines (reported, not flagged)

| obs | NORAD | lines (Hz, width) |
|---|---|---|

## Burst period remeasured (different stations)

- NORAD 25397: median period 30.0 s
  - obs 15034285 (station 1664): 23 frames (23 valid), 23 bursts, period 30.0 s, regular 1.0, counter step median 30.0 s
  - obs 15042383 (station 4188): 17 frames (14 valid), 17 bursts, period 30.0 s, regular 0.625, counter step median 30 s
  - obs 15091860 (station 5049): 10 frames (8 valid), 10 bursts, period 30.0 s, regular 0.7777777777777778, counter step median 30 s
