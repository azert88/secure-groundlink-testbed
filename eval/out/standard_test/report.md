# Standard test (Track 1 scenario), end to end on raw folders, TEST split

11 test observations x 10 seeds = 110 attacked recordings; 44 clean / benign recordings as negatives. Values: per-observation mean [95 % bootstrap CI over observations].

| metric | value |
|---|---|
| replay detected | 1.000 [1.000, 1.000] |
| sequence anomaly detected | 0.309 [0.182, 0.446] |
| sequence anomaly named correctly | 0.273 [0.145, 0.400] |
| interference detected | 0.582 [0.464, 0.709] |
| all three detected | 0.136 [0.064, 0.209] |
| attacked recording not accepted | 1.000 [1.000, 1.000] |
| **false alarm (clean / benign recording not accepted)** | 0.000 [0.000, 0.000] |
| replay frames P / R / F1 (pooled) | 0.999 / 1.000 / 1.000 |
| recording ECE | 0.001 |

False alarms: clean 0.000, benign 0.000; by station {'1664': 0.0, '1763': 0.0, '4188': 0.0, '4806': 0.0, '5049': 0.0}; alert names on negatives {}.
Sequence by subtype: {'counter_jump': {'n': 9, 'detected': 0.444, 'named': 0.333}, 'deletion': {'n': 17, 'detected': 0.0, 'named': 0.0}, 'duplicate': {'n': 19, 'detected': 0.579, 'named': 0.526}, 'forged': {'n': 24, 'detected': 0.542, 'named': 0.5}, 'swap': {'n': 25, 'detected': 0.16, 'named': 0.16}, 'timing': {'n': 16, 'detected': 0.125, 'named': 0.062}}.
Interference by duration: {'segment': {'n': 68, 'detected': 0.515, 'by_window_model': 0.515, 'by_line_screen': 0.0}, 'full_pass': {'n': 42, 'detected': 0.69, 'by_window_model': 0.571, 'by_line_screen': 0.548}}; by snr_db (10 dB bins): {'-10': {'n': 27, 'detected': 0.889}, '0': {'n': 29, 'detected': 0.69}, '10': {'n': 29, 'detected': 0.655}, '20': {'n': 25, 'detected': 0.04}}.
Latency: {'replay_delay_s_median': 0.0, 'replay_delay_s_max': 0.0, 'interference_delay_s_median': 9.0, 'interference_delay_s_max': 226.5, 'line_screen': 'offline: decides at the end of the pass'}. Efficiency: {'runtime_s_median': 28.91, 'runtime_s_max': 85.21, 'peak_traced_mb_max': 563.3, 'stage_s_median': {'replay': 0.13, 'sequence': 0.049, 'audio_front': 20.547, 'interference': 0.198, 'line_screen': 7.706, 'fusion': 0.003}}.
