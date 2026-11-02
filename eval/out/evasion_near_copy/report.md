# Near-copy replay evasion (Phase O), locked fusion, TEST split

11 test observations, 133 recordings with a replay per mode (frames only). Values: per-observation mean [95 % bootstrap CI].

| mode | replay detected (named replay) | any alert on a replayed frame | recording not accepted | frame recall | frame precision | events |
|---|---|---|---|---|---|---|
| exact | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 | 0.999 | 133 / 133 |
| near_copy | 0.000 [0.000, 0.000] | 0.455 [0.182, 0.727] | 0.523 [0.265, 0.780] | 0.000 | 0.000 | 0 / 133 |

- exact: by satellite {'25397': 1.0, '39444': 1.0}; by timing reliable {'False': 1.0, 'True': 1.0}; by group {'combo': 1.0, 'evasion': 1.0, 'replay': 1.0}; evidence {'intra_run': 132, 'regress_run': 73, 'rewrite': 48, 'cross_run': 1}; verdicts {'quarantine': 133}.
- near_copy: by satellite {'25397': 0.0, '39444': 0.0}; by timing reliable {'False': 0.0, 'True': 0.0}; by group {'combo': 0.0, 'evasion': 0.0, 'replay': 0.0}; evidence {}; verdicts {'quarantine': 70, 'accept': 63}.
