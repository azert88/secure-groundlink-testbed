# CLAUDE.md – Secure Ground-Link Testbed, Phase 2

Read at the start of every session. Keep replies short. The user writes in French; code, comments, docs and reports are in English.

## Project in one paragraph
TSYP14 Track 1. Phase 1 (done, locked): detectors for replay, sequence anomalies and narrowband interference on SatNOGS recordings (FUNCUBE-1 BPSK 1k2, TECHSAT 1B FSK 9k6), plus a per-frame HMAC-SHA256 envelope (`trust/envelope.py`) and Ed25519-signed locks (`eval/lock.py`). Phase 2 (deadline 2026-12-11): a conducted hardware bench (3 ADALM-Pluto, 2 Raspberry Pi, Kali VM) and new locked versions that fix measured weaknesses W1–W15 (see `docs/PROGRESS.md`). Phase 2 deliverables are NOT anonymous.

## Hard rules
1. **RF safety.** Never run, schedule or suggest running any command that transmits (any script under `bench/` that opens a TX, any `iio` / `pyadi-iio` / GNU Radio flowgraph with a TX sink). Write the code; the user runs it. Every TX path must go through `bench/safety.py` (default tx gain -40 dB, max -10 dB unless `--i-have-30db-attenuation`, min 30 dB fixed attenuation in config). No antenna, conducted only.
2. **Memory.** The PC has 7.7 GB RAM. One worker for heavy work. Never start long computations or downloads yourself: write the script, make it resumable, give the user the exact PowerShell command, STOP.
3. **Rigour.** Split by observation, test stations disjoint. Train on train, thresholds on calib, test once. Every new version: gate written in `docs/GATES_PHASE2.md` BEFORE evaluation, validated leave-one-station-out, then locked with `eval/lock.py` before any test read; UTC time in `docs/PROGRESS.md`. New versions are reported NEXT TO v1/v2, never replace them.
4. **Held-out set.** `data/holdout_p2/` is never read for diagnosis, plots or debugging. Only `eval/` scripts run under an active lock may open it, once. Do not commit its raw files (hashes only).
5. **Phase 1 artefacts are frozen.** Do not edit locked files (`models/*/lock.json` and the files they list, `data/splits.json`, `data/raw/manifest.csv`, `data/recipes/**`). New code goes in new modules or new versioned files.
6. **Honesty.** Never invent a number, reference, result or hardware capability. Mark uncertain items `[UNVERIFIED]`. Report failures and negative results.
7. **Dependencies.** `requirements.txt` is pinned and must stay installable on Python 3.11–3.14. Bench-only dependencies go in `requirements-bench.txt`.
8. **Secrets.** Never commit private keys, HMAC keys or `.env`. Only `keys/public/` is tracked.
9. **config.yaml is frozen.** `config.yaml` is in the `replay_v21` lock (and Phase 1 locks): never edit it. New versions read their parameters from new files under `models/<version>/` or a new `config_p2.yaml`. Select versions with command-line options (e.g. `--replay-version v2.1`).

## Way of working
- Plan first, then code, then run only the unit tests you touched (`pytest tests/<file> -q`).
- Each task ends with: files changed, test result, numbers (with source file), open questions, then **STOP**. Max 15 lines.
- Add a decision row to `docs/PROGRESS.md` for every non-trivial choice (number, decision, reason).
- Bench sessions are logged in `docs/BENCH_LOG.md` by the user; read it, do not invent entries.

## Layout (Phase 2 additions)
- `bench/` – hardware bench (see `bench/README.md`): `config.yaml` (frequency plan, safety), `safety.py` (TX guard), `pluto.py` (only module that opens a Pluto), `probe.py`, `prepare_tx.py` (SatNOGS audio -> IQ, mode A), `tx_play.py` (TX, operator only), `rx_capture.py` (RX -> SigMF), `iq_to_satnogs.py` (IQ -> SatNOGS folder + radio evidence), `make_attack_iq.py`, `sim_loopback.py` (offline channel simulation), `dsp.py`, `iqfile.py`, `kiss.py`. To do: `link_signed.py` (mode B).
- `trust/session_keys.py` – per-pass HKDF session keys + anti-rollback (S8).
- `detect/` – new versions as new files (e.g. `replay_det_v2.py`); `detect/run.py` selects versions from config.
- `docs/GATES_PHASE2.md`, `docs/HOLDOUT_P2_SPEC.md`, `docs/BENCH_LOG.md`; `data/holdout_p2/` (overlay + fetch script; raw files git-ignored).

## Commands
- Tests: `.venv\Scripts\python -m pytest -q` (Windows) / `.venv/bin/python -m pytest -q`
- Demo: `make demo` / `python scripts/demo.py`
- Lock check: `python eval/lock.py check --name fusion`
