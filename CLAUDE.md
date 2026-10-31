# TSYP14 Track 1 — Secure Ground-Link Testbed

## Goal
IEEE TSYP14 challenge "Ground Truth Under Attack", Track 1.
Phase 1 deadline: 2026-11-03 23:59 GMT+1. Submission is ANONYMOUS.

Official standard test (on SatNOGS Network recordings):
1. Replay of a valid 5–20-frame subsequence
2. One injected sequence anomaly
3. Narrowband interference added digitally at a hidden SNR
The jury supplies a held-out recording.

We must report: detection quality, false alarms, latency, memory, security overhead.
False-alarm rate is an official tie-breaker: every design choice must keep false positives low.
Benign conditions (gain change, weak signal at pass edges, noise rise, fading,
naturally repeated beacons) must NOT raise alerts.

## Environment
- Ubuntu (WSL2) or any Ubuntu/Kali VM, Python 3.11+, CPU only.
- The jury must be able to run everything on a fresh VM with `./setup.sh` then `make demo`.
- Allowed dependencies: numpy, scipy, pandas, scikit-learn, cryptography, soundfile,
  matplotlib, pyyaml, requests, pytest. System tools: sox or ffmpeg, gr-satellites.
- No GPU, no deep-learning frameworks unless explicitly asked.

## Repo layout
```
data/raw/          original SatNOGS observations (never modified)
data/attacked/     attacked copies
data/labels/       ground truth (JSON), one file per attacked observation
data/splits.json   train / calib / test observation IDs
data/DATA.md       provenance: URL, observation ID, download date, SHA-256
attacks/           replay.py, sequence.py, interference.py, benign.py
trust/             per-frame envelope (counter, timestamp, prev-hash, HMAC), Ed25519 manifests
detect/            features.py, replay_det.py, sequence_det.py, interference_det.py, fusion.py
eval/              run_standard_test.py, metrics.py
report/            report + figures
tests/             pytest tests, one file per module
config.yaml        paths, seeds, parameters
```

## Hard rules
- Code in detect/ must NEVER read data/labels/. Labels are read only in eval/.
- Splits are by observation ID, never by frame or window. Store them in data/splits.json.
- Every random operation takes an explicit seed (default seed in config.yaml).
- Never modify data/raw/. Attacks write to data/attacked/ and data/labels/.
- Anonymity: no personal names, usernames, emails, team or university names,
  and no absolute paths (/home/..., C:\Users\...) in code, outputs, notebooks or docs.
- SatNOGS API: at most 1 request every 2 seconds, cache responses, log provenance in data/DATA.md.
- SNR definition for interference: SNR_dB = 10*log10(P_signal / P_interference),
  P_signal measured over the portion where the satellite signal is present.
- Every script: argparse CLI, JSON outputs, English comments, a pytest test.
- Keep code simple and readable; prefer classical, explainable methods.

## Workflow
- Work one step at a time. Before coding a step, propose a short plan and wait for approval.
- After each step, run the tests and show a short summary of results.
- Do not start the next step without being asked.

## Steps
1. Download SatNOGS observations (audio, demodulated frames, waterfall, metadata) + DATA.md
2. Prepare data: ogg→wav, decode with gr-satellites, extract frame timestamps and counters, make splits
3. Attack scripts: replay (5–20 frames), one sequence anomaly, narrowband interference, benign cases
4. Trust layer: signed per-frame envelope + signed manifests, with overhead measurement
5. Detectors: replay, sequence, interference (calibrated confidence, threshold for ~1% false alarms)
6. Standard scenario end to end: eval/run_standard_test.py
7. Evasion tests + metrics (F1, FPR, Pd vs SNR, ECE, latency, memory, security overhead)
8. setup.sh, Makefile (`make demo`), README install guide, report
