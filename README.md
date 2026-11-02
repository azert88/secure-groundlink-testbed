# Secure Ground-Link Testbed — TSYP14 Track 1 ("Ground Truth Under Attack")

Detection of **replay**, **sequence anomalies** and **narrowband interference** on real SatNOGS satellite recordings
(FUNCUBE-1 BPSK 1k2 and TECHSAT 1B FSK 9k6), with a per-frame HMAC envelope and Ed25519-signed model manifests.
Classical, explainable methods (frame hashes, counter-time consistency, natural-statistics tests, a gradient-boosted
window model, a narrowband line screen) fused into one verdict per recording: **accept / flag / quarantine**, every alert
with a plain-language reason and a calibrated confidence. CPU only.

Results: [`eval/results.md`](eval/results.md) (one table, every challenge metric, with confidence intervals).
Development log and every decision: [`docs/PROGRESS.md`](docs/PROGRESS.md). Dataset datasheet: [`data/DATA.md`](data/DATA.md).

## 1. Install

**Supported Python: 3.11, 3.12, 3.13 and 3.14** (64-bit, CPU only). All dependencies are pinned in `requirements.txt` and
install from binary wheels (no compiler, no system libraries: `soundfile` bundles libsndfile).
Tested end to end (demo + 151 unit tests) on Windows with Python 3.14 in a fresh environment built from `requirements.txt`,
and the demo on Kali Linux with Python 3.13 (see below); on Linux the pinned set resolves to binary wheels for Python 3.11–3.14
(checked with `pip download --platform manylinux…`).

### Ubuntu / Kali VM

```bash
sudo apt update && sudo apt install -y python3 python3-venv make git   # once, if missing
git clone <this repository> groundlink && cd groundlink
./setup.sh          # creates .venv, installs requirements.txt, checks the signed model lock
make demo           # offline demo, about 2-4 minutes
```

`setup.sh` picks the first of `python3.13`, `python3.12`, `python3.11`, `python3.14`, `python3` that is >= 3.11;
force one with `PYTHON=python3.12 ./setup.sh`. Conda alternative: `conda env create -f environment.yml`.

### Windows (PowerShell)

```powershell
py -3.12 -m venv .venv            # any of 3.11 - 3.14
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python scripts\demo.py
```

`git clone` keeps the signed files byte-exact on every OS (`.gitattributes` disables line-ending conversion for them),
so the lock check passes on Linux and Windows.

### Tested on Kali Linux

Fresh clone, then `bash setup.sh` and `make demo`, Python 3.13.12 (the `.venv` interpreter; `setup.sh` picks
python3.13 before the system default python3, 3.14.8). Demo done in 58 s: both clean recordings accepted,
both attacked recordings quarantined. Output (`make demo`):

```text
.venv/bin/python scripts/demo.py

=== observation 14932721 (FUNCUBE 1, NORAD 39444)
  clean recording: verdict ACCEPT (confidence attacked 0.00), 0 alert(s), 5 s
  attack applied (truth file, not read by the detector): replay of 11 frames (-158 s); sequence counter_jump; interference noise at 9159 Hz, snr_db +1.1
  attacked recording: verdict QUARANTINE (confidence attacked 1.00), 4 alert(s), 5 s
    - replay [quarantine, conf 0.99]: replay: same bytes as frame data_14932721_2026-09-04T06-02-41_g0 of this pass (-158 s; original = the copy on the counter-time line); 11 frame(s) shar
    - counter_jump [flag, conf 0.26]: counter_jump: counter 23153437 breaks the order of its neighbours (+50 s off the counter-time line) (p=0.005); +50 s off the counter-time line (counte
    - duplicate [quarantine, conf 0.95]: duplicate: identical bytes to frame data_14932721_2026-09-04T06-00-58_g0 (+158 s) (p=0.0017)
    - interference [quarantine, conf 0.94]: narrowband interference (line screen): line at 9094-9234 Hz (164 Hz wide at -3 dB), 20.5 dB above its local baseline on average and more than +10 dB i

=== observation 15080917 (TECHSAT 1B, NORAD 25397)
  clean recording: verdict ACCEPT (confidence attacked 0.00), 0 alert(s), 7 s
  attack applied (truth file, not read by the detector): replay of 7 frames (+24 s); sequence forged; interference tone at 2810 Hz, snr_db -2.6
  attacked recording: verdict QUARANTINE (confidence attacked 1.00), 2 alert(s), 8 s
    - replay [quarantine, conf 0.99]: replay: same bytes as frame data_15080917_2026-09-28T17-55-58_g0 of this pass (+24 s; first seen taken as original); 7 frame(s) share this shift; 1 by
    - interference [quarantine, conf 1.00]: narrowband interference: strongest line at 2812 Hz is 26.9 dB above the band median (clean passes: 4.8 dB), 23 Hz wide, same frequency for 742 previou

Demo done in 58 s. Outputs: out/demo/ (detector reports *.detect.md, spectrograms *.spectrogram.png, demo_summary.json)
```

Same verdicts, alerts and attacks as on Windows (same seeds); only the timings differ.

## 2. Demo (offline, a few minutes)

`make demo` = `python scripts/demo.py`. For each recording in `data/sample/` (one FUNCUBE-1 and one TECHSAT 1B pass,
sanitized metadata) it

1. runs the detectors on the **clean** recording (expected: `accept`, no false alarm);
2. applies the **standard attack** (replay of 5-20 frames + one sequence anomaly + narrowband interference at a random
   snr_db), with `attacks/run_attack.py standard`, writing the truth to a separate file the detector never reads;
3. runs `detect/run.py` on the **attacked** recording and prints the verdict, the alerts and their reasons.

Real output (fresh environment from `requirements.txt`, Python 3.14, Windows; reasons cut at 150 characters by the demo):

```text
=== observation 14932721 (FUNCUBE 1, NORAD 39444)
  clean recording: verdict ACCEPT (confidence attacked 0.00), 0 alert(s), 10 s
  attack applied (truth file, not read by the detector): replay of 11 frames (-158 s); sequence counter_jump; interference noise at 9159 Hz, snr_db +1.1
  attacked recording: verdict QUARANTINE (confidence attacked 1.00), 4 alert(s), 10 s
    - replay [quarantine, conf 0.99]: replay: same bytes as frame data_14932721_2026-09-04T06-02-41_g0 of this pass (-158 s; original = the copy on the counter-time line); 11 frame(s) shar
    - counter_jump [flag, conf 0.26]: counter_jump: counter 23153437 breaks the order of its neighbours (+50 s off the counter-time line) (p=0.005); +50 s off the counter-time line (counte
    - duplicate [quarantine, conf 0.95]: duplicate: identical bytes to frame data_14932721_2026-09-04T06-00-58_g0 (+158 s) (p=0.0017)
    - interference [quarantine, conf 0.94]: narrowband interference (line screen): line at 9094-9234 Hz (164 Hz wide at -3 dB), 20.5 dB above its local baseline on average and more than +10 dB i

=== observation 15080917 (TECHSAT 1B, NORAD 25397)
  clean recording: verdict ACCEPT (confidence attacked 0.00), 0 alert(s), 13 s
  attack applied (truth file, not read by the detector): replay of 7 frames (+24 s); sequence forged; interference tone at 2810 Hz, snr_db -2.6
  attacked recording: verdict QUARANTINE (confidence attacked 1.00), 2 alert(s), 15 s
    - replay [quarantine, conf 0.99]: replay: same bytes as frame data_15080917_2026-09-28T17-55-58_g0 of this pass (+24 s; first seen taken as original); 7 frame(s) share this shift; 1 by
    - interference [quarantine, conf 1.00]: narrowband interference: strongest line at 2812 Hz is 26.9 dB above the band median (clean passes: 4.8 dB), 23 Hz wide, same frequency for 626 previou

Demo done in 111 s. Outputs: out/demo/ (detector reports *.detect.md, spectrograms *.spectrogram.png, demo_summary.json)
```

What it shows: no false alarm on either clean pass; replay and interference found on both attacked passes. The sequence
anomaly is found on FUNCUBE-1 (`counter_jump`; the `duplicate` alert is a replayed frame whose block was split by the
anomaly) but **not on TECHSAT 1B** (the forged frame is missed): the sequence detector cannot alert on TECHSAT 1B
(known limitation, decision 81 in `docs/PROGRESS.md`). Outputs land in `out/demo/`: `*.detect.md` / `*.detect.json`
(full detector reports), `*.truth.json`, `*.spectrogram.png` (before / after).

## 3. Run on a new SatNOGS recording

The detectors take one **raw observation folder**, laid out as SatNOGS serves it:

```text
my_obs/
  audio.ogg          the observation audio (SatNOGS "Audio" download, 48 kHz Ogg Vorbis)
  demoddata/         the demodulated frame files (SatNOGS "Data" tab), names data_<id>_<YYYY-MM-DDTHH-MM-SS>[...]
  metadata.json      at least {"id": <observation id>, "norad_cat_id": <NORAD>, "start": "YYYY-MM-DDTHH:MM:SSZ"}
```

`metadata.json` can be the record of `https://network.satnogs.org/api/observations/?id=<id>` (first element of the list);
only `id`, `norad_cat_id` and `start` are read. Missing `demoddata/` -> audio detectors only; missing `audio.ogg` ->
frame detectors only (the report says which detectors were unavailable). Then:

```bash
make detect OBS=path/to/my_obs
# or
.venv/bin/python detect/run.py --obs-dir path/to/my_obs --out out/my_obs.json --md out/my_obs.md
```

Output: verdict (`accept` / `flag` / `quarantine`), alerts with names (`replay`, `replay_cross_pass`, `duplicate`,
`forged`, `swap`, `deletion`, `timing`, `counter_jump`, `interference`), reasons, calibrated confidences, per-frame and
per-window decisions, runtime and peak memory. `detect/run.py` refuses to run if the signed lock of the models,
thresholds and detector code (`models/fusion/lock.json`, pinned public key `keys/public/ed25519_public.pem`) does not
verify (`make verify`).
Scope: models, thresholds and natural statistics were learned for **FUNCUBE-1 (39444) and TECHSAT 1B (25397)**; other
satellites are not supported. The replay archive holds the frames of the dataset passes, so a cross-pass replay is found
against those passes.

## 4. Attack scripts

`attacks/run_attack.py` applies one attack to a raw folder and writes an attacked folder, a separate truth file
(`<out>.truth.json`) and a before / after spectrogram (`<out>.spectrogram.png`). The input folder is never modified.

```bash
P=.venv/bin/python; OBS=data/sample/14932721
$P attacks/run_attack.py replay       --obs-dir $OBS --out out/rep  [--length 8] [--seed 1]
$P attacks/run_attack.py sequence     --obs-dir $OBS --out out/seq  --subtype swap   # deletion|swap|duplicate|forged|timing|counter_jump
$P attacks/run_attack.py interference --obs-dir $OBS --out out/int  --shape tone --f0 3000 --snr-db 5 [--bw 100] [--t0 10 --t1 60]
$P attacks/run_attack.py standard     --obs-dir $OBS --out out/std  [--seed 7]
$P detect/run.py --obs-dir out/std --md out/std.detect.md
```

`snr_db = 10 log10(P_signal / P_interference)`, P_signal = in-band power of the pass (low snr_db = strong interferer).
The same generators (`attacks/{replay,sequence,interference,benign}.py`, planned by `data/make_recipes.py`) built the dataset.

## 5. Reproduce the dataset and the results

Paths are relative to the repository root; every script has `--help`. Windows: replace `.venv/bin/python` by `.venv\Scripts\python`.

### 5.1 Check the reported results from the frozen models (no download)

Frozen models, thresholds, the fusion config and the signed locks are in `models/`; all reports are in `eval/out/`.

```bash
make verify                                  # signed lock of models + code
.venv/bin/python eval/lock.py check --name fusion
.venv/bin/python eval/results.py             # rebuilds eval/results.{md,json} from eval/out/*
.venv/bin/python trust/envelope.py bench     # security overhead of the per-frame envelope
.venv/bin/python -m pytest -q                # 151 unit tests
```

### 5.2 Rebuild everything from SatNOGS (hours; downloads about 0.7 GB)

The raw recordings are not redistributed (licence and anonymity: `data/raw/` is git-ignored, only its manifest with URLs
and SHA-256 is kept). The SatNOGS client caches every response and waits 3 s between requests.

```bash
P=.venv/bin/python
# dataset (docs/DATASET_SPEC.md)
$P data/select_satellites.py --focus      # A  satellite selection (cached API pages)
$P data/download_satnogs.py --full        # B  download, resumable, verified against data/raw/manifest.csv
$P data/qc.py                             # C  quality control
$P data/build_tables.py                   # D  clean tables (frames, windows, labels)
$P data/make_splits.py                    # E  station-disjoint splits -> data/splits.json
$P data/downlink_bands.py                 #    per-satellite downlink band (TRAIN only)
$P data/make_recipes.py                   # F  attack / benign recipes (seeded) -> data/recipes/
$P data/build_features.py --workers 4 --resume   # G  apply recipes, features
$P data/validate.py                       # H  dataset validation
# detectors (docs/DETECTOR_SPEC.md); each phase is locked (eval/lock.py) before its single test run
$P eval/replay_eval.py fit; for s in train calib; do $P eval/replay_eval.py score --split $s; done
$P eval/replay_eval.py calibrate; $P eval/replay_eval.py score --split test; $P eval/replay_eval.py report --split test
$P eval/sequence_eval.py fit; for s in train calib; do $P eval/sequence_eval.py score --split $s; done
$P eval/sequence_eval.py calibrate; $P eval/sequence_eval.py score --split test; $P eval/sequence_eval.py report --split test
for s in train calib test; do $P eval/interference_eval.py cache --split $s; done
$P eval/interference_eval.py fit; $P eval/interference_eval.py report --split test
$P eval/interference_v2.py loso; $P eval/interference_v2.py report
for s in train calib; do $P eval/fusion_eval.py lines --split $s --workers 4; done; $P eval/fusion_eval.py dev
$P eval/run_standard_test.py run --workers 4; $P eval/run_standard_test.py report      # official standard scenario
$P eval/evasion_near_copy.py run --workers 4; $P eval/evasion_near_copy.py report       # near-copy replay evasion
$P trust/envelope.py bench --out eval/out/trust/overhead.json
$P eval/results.py
```

Rebuilt models get new hashes: the test stages then refuse to run until you freeze and sign new locks with your own key
(`eval/lock.py freeze`, see `trust/manifest.py`); the shipped locks only verify the shipped models.
Byte-identical rebuild of the dataset is checked by the SHA-256 fingerprints in `data/DATA.md` section 8.

## 6. Where things are

| path | content |
|---|---|
| `eval/results.md`, `eval/results.json` | the results table (detection per attack, false alarms, Pd vs SIR, evasion, latency, memory, security overhead) |
| `eval/out/standard_test/report.md` | official standard scenario on the test split (110 attacked + 44 clean / benign recordings) |
| `eval/out/{replay,sequence,interference,interference_v2,fusion,evasion_near_copy,trust}/` | per-detector reports, evasion, overhead |
| `docs/PROGRESS.md` | phase-by-phase log, all decisions and their reasons, known limitations |
| `docs/DATASET_SPEC.md`, `docs/DETECTOR_SPEC.md`, `data/DATA.md` | specifications, datasheet and provenance |
| `detect/` | detectors (`replay_det`, `sequence_det`, `interference_det`, `line_screen`), fusion, `run.py` (entry point) |
| `attacks/` | attack and benign generators, `run_attack.py` (CLI) |
| `trust/` | per-frame HMAC envelope (`envelope.py`), Ed25519 manifests and locks (`manifest.py`) |
| `models/` | frozen models, thresholds, fusion config, signed locks; `keys/public/` pinned verification key |
| `data/sample/` | 2 demo recordings (sanitized metadata: no names, no coordinates); `data/recipes/`, `data/splits.json` |
| `tests/` | pytest suite |

## 7. Known limitations (details in `docs/PROGRESS.md`)

- Small test set: 11 test observations from 5 stations; 0 / 44 recording false alarms bounds the rate at about 6.6 % (95 %).
- Sequence anomalies: 0.31 recall on the standard test; TECHSAT 1B cannot alert (decision 81); deletions look like natural losses.
- Interference: weak interferers (snr_db >= 20 dB) are missed; full-pass interference relies on the line screen.
- Replay with a modified byte (near-copy) is no longer named replay (0 / 133); half of those recordings are still
  stopped by sequence alerts (decision 102).
- Cross-station shift is the main difficulty; the fix planned for phase 2 is a per-station clean template (decision 93).
