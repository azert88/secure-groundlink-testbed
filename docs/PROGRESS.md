# Progress log — dataset build (DATASET_SPEC.md)

Last update: 2026-11-02 (Phases O and P done, decisions 102–106).

## Status by phase

| Phase | Status |
|---|---|
| A — Satellite selection | **Approved** (2026-10-30): TECHSAT 1B (9k6) + FUNCUBE-1 (1k2) |
| B — Download | **Complete**: 96/96 observations (60 TECHSAT 1B, 36 FUNCUBE-1); the last 29 FUNCUBE-1 were downloaded by the user outside Claude Code |
| C — QC | **Approved** (2026-10-30) with decisions 48–52; rerun with per-satellite rewrite windows (same counts); positive control run |
| D — Tables | **Approved** (2026-10-30): `data/tables/{observations,frames,windows}.csv`, `data/labels/labels.csv`; 77 obs, 990 frames, 71 114 windows; rebuild byte-identical |
| E — Splits | **Approved** (2026-10-30), with decisions 55–58; audio flag of 2 frame-set observations updated (assignments unchanged) |
| F — Recipes | **Done, at STOP**: `attacks/`, `data/make_recipes.py`, 1017 recipes in `data/recipes/` (regenerated after decision 62); 75 tests pass. Phase G resumable (`--resume`); full run to be launched by the user |
| G — Features | **Done** (run by the user): 77/77 observations in `data/features/` (`runtime.json`, finished 2026-10-30 21:48 UTC) |
| H — Validation | **Done** (run by the user; shortcut check revised and rerun from the cache, 2026-10-31): all 7 checks pass, shortcut test with 5 documented limitations (see Phase H) |
| I — Datasheet | **Done** (2026-10-31): `data/DATA.md` sections 1–8 (Datasheets for Datasets) + provenance appendix; source licence checked (SatNOGS Network and DB: CC BY-SA 4.0). **Dataset frozen for detector training** (SHA-256 fingerprints in DATA.md section 8). 83 tests pass |
| J — Replay detector | **Approved** (2026-11-01): locked, test F1 1.000, FPR 0 |
| K — Sequence detector | **Version 1 is the reported result** (locked, test per-obs recall 0.29, FPR 1.26 %); version 2 (changes a, b of decision 84) is a planned fix, not built (decision 85) |
| L — Interference detector | **Done, at STOP** (2026-11-01): version 1 locked; test AUC 0.84 but FPR 34–36 % at the calibrated threshold (station shift, decision 89); `detect/run.py` prototype runs it on a raw folder; **v2 (post hoc, decision 92)** locked and tested once: FPR clean / benign 0.14 % / 0.16 %, TPR 0.48 |
| N — Fusion and standard test | **Done** (2026-11-02): line screen + fusion, dev on train + calib, sequence v2 gate failed (v1 kept, content off in fusion, decision 100), lock 08:07 UTC, standard test run once: 0 / 44 false alarms, replay 1.00, sequence 0.31, interference 0.58 |
| O — Near-copy evasion, results table | **Done** (2026-11-02): near-copy replay 0 / 133 named replay (exact 133 / 133), 52 % still not accepted via sequence alerts; `eval/results.{md,json}` |
| P — Packaging | **Done** (2026-11-02): `attacks/run_attack.py`, `scripts/demo.py` / `make demo`, `data/sample/` (2 obs), pinned `requirements.txt` (Python 3.11–3.14), `setup.sh`, `environment.yml`, `README.md`, anonymity scan; 151 tests pass |
| M — Trust layer | **Done, at STOP** (2026-11-02): per-frame HMAC envelope (`trust/envelope.py`), 9 deterministic rejection tests, 102 B and ~4.5 µs per frame |

## Phase A — what exists

| File | Purpose |
|---|---|
| `config.yaml` | All parameters (API politeness, selection window and thresholds, mode classes, gr-satellites pin, focus list) |
| `data/satnogs_api.py` | Shared polite SatNOGS client (cache, throttle, backoff, pagination, token from `.env`) |
| `data/select_satellites.py` | Phase A: offline pre-filter (`--prefilter-only`), full ranking (default), focused run (`--focus`) |
| `tests/test_select_satellites.py` | 14 offline tests (mode classes, filename timestamps, burst period, counter regex, client cache/retry/token, pre-filter, early stop, filter guard) |
| `data/selection/prefilter.json` | Output of the offline pre-filter (45 candidates kept) |
| `data/selection/focus.json`, `focus.md` | Output of the focused run (written when the run ends) |
| `.gitignore`, `.env.example` | `.env` (token), `.venv/` and `data/cache/` are never committed |

## Phase A — pipeline as run

1. SatNOGS DB: full satellite and transmitter lists (2 requests, cached).
2. Mode filter: catalogued NORAD (< 90000), satellite status `in orbit`, transmitter alive,
   mode AFSK 1200, BPSK 1200, FSK 9600 or GMSK 9600 → 178 (satellite, class) candidates.
3. Offline gr-satellites filter (no API calls): satellite present in gr-satellites AND a
   gr-satellites transmitter of compatible modulation at the same baud → **45 candidates
   (43 satellites, 53 transmitters)**: 15 narrow_1k2, 30 fsk_9k6.

   | class | mode OK | not in gr-satellites | in gr-satellites, other mode/baud | kept |
   |---|---|---|---|---|
   | narrow_1k2 | 60 | 40 | 5 | 15 |
   | fsk_9k6 | 118 | 86 | 2 | 30 |

4. Focused scan (current plan, replaces the full 45-candidate ranking):
   - 9k6: TECHSAT 1B (NORAD 25397), already fully scanned from cache:
     **290 good observations with demoddata, 24 stations, exact count** (passes 50 / 10).
   - 1k2: FUNCUBE-1 (NORAD 39444) + the 2 best other 1k2 candidates from a one-page probe.
   - 1k2 transmitters stop paging as soon as they reach 50 good observations with demoddata
     from 10 stations (counts are then lower bounds, flagged `stopped_early`).
   - For each: burst period from 3 observations, counter/uptime fields from the
     satnogs-decoders struct, gr-satellites name.

## Phase A — results (focused scan, all counts exact, window 2026-08-30 .. 2026-10-29)

| name | NORAD | class | gr-satellites name | good obs | with demod | stations (with demod) | burst period | regular | frames/burst | counter / uptime |
|---|---|---|---|---|---|---|---|---|---|---|
| TECHSAT 1B | 25397 | FSK 9k6 | GO-32 | 470 | 290 | 24 | 30 s | 93% | 3 | none (AX.25 only) |
| FUNCUBE-1 | 39444 | BPSK 1k2 | AO-73 | 378 | 69 | **7** | 5 s | 100% | 1 | `seqnumber` (24 bit, gr-satellites `funcube` parser) |
| CUAVA-2 | 60527 | BPSK 1k2 | CUAVA-2 | 97 | 57 | **6** | 31 s | 100% | 3 | none (AX.25 only) |
| Waratah Seed-1 | 60469 | BPSK 1k2 | WS-1 | 112 | 54 | **8** | 31 s | 75% | 3 | none (AX.25 only) |

- One-page probe of the other 1k2 candidates (good obs with demoddata on page 1 / stations): CUAVA-2 17/3,
  Waratah Seed-1 12/4, ITASAT 1 10/3, RANDEV 4/4, SATURN 3/2, Eaglet 1 2/2, others 0.
- The SatNOGS DB lists no satnogs-decoders struct for any of the 4 satellites; counter fields were
  checked in the gr-satellites telemetry parsers instead (offline, pinned v5.9.0 source).
- API cost of the focused run: 40 network requests; the rerun after the bug fix used 0 (cache only).

### Problems found
1. **No 1k2 candidate meets the 10-station threshold** (best: Waratah Seed-1 8, FUNCUBE-1 7).
   Passing it needs a decision (see open items).
2. Demoddata filename suffixes vary: none, `_1`, `_0015`, `_g0` / `_g1`. The first parser missed
   `_gN` and crashed the report; fixed and tested.
3. TECHSAT 1B files come in `_g0` / `_g1` pairs with the same timestamp (probably two decoder
   branches). They may be identical frames: natural duplicates the replay detector must
   learn. To check in Phase D by hash.
4. Some FUNCUBE-1 observations have many files with the same second and `_0000`..`_0015`
   suffixes (e.g. 16 files at one timestamp). Their timestamps are probably write times, not
   reception times, so the timing features are unreliable there. To check in Phase C.
5. The 3 TECHSAT 1B observations used for the burst period all come from one station (4188).

## Phase B — pilot results (2026-10-30)

Script `data/download_satnogs.py` (`--plan`, `--pilot`, `--full`); tests `tests/test_download_satnogs.py`.
Plan `data/download/plan.json`; report `data/download/pilot_report.json`; provenance in
`data/raw/manifest.csv` (135 file rows) and `data/DATA.md`.

| obs | satellite | station | scheduled s | audio s | demod files | requests | MB | time s |
|---|---|---|---|---|---|---|---|---|
| 15004025 | TECHSAT 1B | 4451 | 180 | 176.3 | 2 | 4 | 2.88 | 12 |
| 15287655 | TECHSAT 1B | 5049 | 913 | 879.2 | 4 | 6 | 7.77 | 19 |
| 15179622 | TECHSAT 1B | 5065 | 585 | 420.8 | 2 | 4 | 4.58 | 13 |
| 15123656 | FUNCUBE-1 | 4011 | 438 | 433.8 | 82 | 84 | 15.10 | 266 |
| 15242426 | FUNCUBE-1 | 4272 | 296 | 294.6 | 30 | 32 | 4.53 | 102 |

- 1 request per file, no retries; measured 3.18 s per request; all audio 48 kHz mono.
- Projection for the full download (76 observations): 1161 requests, about 1.0 h, about 0.55 GB
  (TECHSAT 1B: 6.5 requests and 5.1 MB per observation; FUNCUBE-1: 25.1 requests and 9.7 MB).
  Under the 4 h limit, so no cuts are needed.
- **Problem**: 2 of 3 TECHSAT 1B audio files are shorter than the scheduled observation
  (-164 s and -34 s). The Phase C rule (|audio - (end - start)| > 10 s → reject) would reject them.
  The audio length is not in the API record, so it is only known after download.

## TECHSAT 1B frame analysis (all 60 downloaded observations, 2026-10-30)

- 327 frame files → 239 canonical frames (`_g0` / `_g1` are byte-identical copies).
- 200 frames are 44-byte beacons containing ASCII `TechSat-V4.2`; bytes 6..9 are a
  little-endian uint32 seconds counter that rises by 30 between consecutive beacons
  (= the 30 s burst period). 39 frames are 1–40 bytes (fragments; content not identified).
- Frames are not AX.25 framed, so the AX.25 plausibility check marks all of them invalid;
  it must be replaced by a beacon check (length 44 + `TechSat` marker).
- Same beacon content can appear at different timestamps (natural repeats).
- **Only 7 of 60 observations have >= 10 canonical frames** (most passes decode 1–3 beacons).
  In the whole Phase A window only 20 TECHSAT 1B observations have >= 20 files (~10 canonical
  frames), from 4 stations (17 from one station). The fallback for short audio does not help:
  the frame count is the binding constraint.

## Phase C — results (2026-10-30)

Run: `.venv/Scripts/python data/qc.py` (exit 0, 28 tests pass). Outputs `data/qc_report.csv`, `.json`, `.md`.

| satellite | obs | audio QC pass | frame QC pass | both | fragments | flagged (review) | selected for audio |
|---|---|---|---|---|---|---|---|
| TECHSAT 1B | 60 | 55 | 7 | 7 | 53 | 0 | 40 (keep-first-40) |
| FUNCUBE-1 | 36 | 32 | 30 | 27 | 0 | 7 | 32 |

- Rejection reasons: TECHSAT 1B audio: duration mismatch 5 (−164.2, −34.3, −33.8, −16.2, +28.5 s);
  frame: < 10 canonical frames 53. FUNCUBE-1 audio: duration mismatch 4 (−17.8 to −30.5 s);
  frame: < 10 canonical frames 6. No sample-rate, channel or unreadable failures.
- timing_reliable (frame-QC passes): TECHSAT 1B 7/7 reliable; FUNCUBE-1 22 reliable, 8 unreliable
  (station 1763: 3 obs with all files in one second, counter-time consistency 0.0; stations 5155, 4434 ×2,
  4272, 2107: 2 frames in one second and consistency 0.75–0.90).
- TECHSAT 1B burst period remeasured on 3 stations (4188, 1664, 5049): 30 s on all three; the seconds
  counter steps by a median of 30 s too. Regularity 0.63 / 1.00 / 0.78 (missed beacons).
- 7 FUNCUBE-1 observations flagged for waterfall review: narrow lines 23–70 Hz wide, all at
  11.06–12.38 kHz, i.e. inside or at the edge of the satellite's own BPSK band. Some also have a wide line there.

### Problems found
6. **TECHSAT 1B narrowband screen is blind inside the data band**: f_edge comes out at about 24 kHz (Nyquist)
   on most observations, so the per-window median falls in the quiet band above 11 kHz and the whole
   117–11320 Hz FSK band counts as one wide "line" (35 obs). A real narrow RFI line inside it would merge
   into that region and not be flagged.
7. **FUNCUBE-1 flags are probably the satellite's own signal**: all 7 narrow lines lie at 11–12.4 kHz,
   where the downlink sits. The 200 Hz rule removes the main BPSK lobe but not narrow peaks inside it.
8. **TECHSAT 1B station 2968 (obs 15305494)** writes a byte-identical, suffix-free copy of each beacon
   4 s after the `_g0` file. The (timestamp, sha256) collapse does not merge them (different seconds), so the
   observation counts 13 canonical frames (about 8 distinct beacons) and its counter-time consistency is 0.18.
   It still passes frame QC by count. These are natural repeats the replay detector must not flag.

## Phase C — rerun with decisions 43–47 (2026-10-30)

| satellite | obs | audio QC pass | frame QC pass | both | fragments | flagged | selected for audio | timing reliable / unreliable (frame QC) |
|---|---|---|---|---|---|---|---|---|
| TECHSAT 1B | 60 | 55 | 6 | 6 | 54 | 0 | 40 | 6 / 0 |
| FUNCUBE-1 | 36 | 32 | 30 | 27 | 0 | 0 | 32 | 22 / 8 |

- Problems 6–8 are resolved by decisions 43–45.
- f_edge: TECHSAT 1B 10102–10336 Hz (median 10148), steepest drop median 11074 Hz;
  FUNCUBE-1 19523–22805 Hz (median 19523), steepest drop median 19535 Hz.
- Local-baseline line screen: no narrow line anywhere (0 flagged, 0 in_signal_band, 0 wide lines on 87
  audio-QC passes). The 7 old FUNCUBE-1 flags came from the global median; reviewed ok in `data/qc_review.yaml`.
  Signal band (decision 47) found on 23 of 32 FUNCUBE-1 audio passes, at about 10.6–12.6 kHz.
- Rewrites (decision 45): TECHSAT 1B only, 49 of 190 canonical frames (25.8%) had a byte-identical later copy,
  at 7 of 24 stations (4188 77.5%, 2968 62.5%, 2173 57.1%, 4460 50%, 5049 35.7%, 4755 16.7%, 1466 14.3%);
  delays 1–4 s. FUNCUBE-1: 0 rewrites on 831 frames.
- TECHSAT 1B frame set is now 6 observations from 3 stations (4188 ×3, 1664 ×2, 5049). 15305494 drops to 8
  frames (accepted, decision 45); 15080917 and 15091860 sit exactly at 10 frames.
- Burst period unchanged: 30 s on stations 1664, 4188, 5049.

## Phase C — positive control (decision 52, 2026-10-30)

| satellite / obs | interferer | −10 dB | 0 dB | +10 dB | +20 dB | +30 dB |
|---|---|---|---|---|---|---|
| TECHSAT 1B 15034285 | tone 4 kHz (inside FSK band) | **flag** | flag | flag | – | – |
| TECHSAT 1B 15034285 | noise 7 kHz, 150 Hz | **missed** (run 281 Hz wide, taken as a signal band) | missed | – | – | – |
| FUNCUBE-1 15123656 | tone 6 kHz | **flag** | flag | flag | flag | – |
| FUNCUBE-1 15123656 | noise 3 kHz, 150 Hz | **missed** (run 352 Hz wide, taken as a signal band) | missed | missed | – | – |
| FUNCUBE-1 15123656 | tone 11.65 kHz (inside downlink) | in_signal_band | in_signal_band | in_signal_band | – | – |

"–" = no line found. Tones ≤ 200 Hz are flagged down to +10 dB (TECHSAT 1B) and +20 dB (FUNCUBE-1).
**Problem 9**: a strong 150 Hz noise band is missed. Its run above +10 dB is wider than 200 Hz (filter
skirts), so the width rule calls it wide, and the signal-band detector then accepts it as a downlink.
The clean QC result cannot change with a fix (no hot run of any width on clean data), but the screen
has a blind spot. Needs a decision.

## Phase D — results (2026-10-30)

- `data/build_tables.py` (shared code: `detect/canonical.py`, `detect/features.py`). 77 observations
  (TECHSAT 1B 42, FUNCUBE-1 35), 990 frames (162 / 828), 71 114 windows (42 / 32 observations with audio).
- Two builds of 3 observations give byte-identical tables (decoded Vorbis is bit-identical).
- Decision 46 checked: 8 timing-unreliable observations have NaN dt_prev_s, dt_burst_s and burst_active;
  FUNCUBE-1 frames ordered by counter. 47 TECHSAT 1B frames carry rewrite copies (n_copies > 0).
- burst_active share of windows: FUNCUBE-1 16 %, TECHSAT 1B 2 % (1–3 beacons per pass). P_signal of
  TECHSAT 1B in Phase F rests on few windows.
- Incident: one large Ogg Vorbis write crashed the interpreter (Windows stack overflow, exit 0xC00000FD);
  fixed by writing in 4096-sample blocks.

## Phase E — results (2026-10-30)

| satellite | train | calib | test | test stations | frame obs in test |
|---|---|---|---|---|---|
| TECHSAT 1B | 26 | 8 | 8 | 1664, 4073, 4188, 5049 | 6 (extra cross-satellite set) |
| FUNCUBE-1 | 22 | 7 | 6 | 1763, 4806 | 5 |

- FUNCUBE-1 test: only 1763 and 4806 meet decision 30, so there was no choice. All 3 observations of
  1763 are timing-unreliable (batched writes), so 3 of the 5 FUNCUBE-1 test frame observations have NaN
  timing features. Calib uses stations 2107 and 4272 only.

## Phase C — positive control rerun (decision 55, 2026-10-30)

| satellite / obs | interferer | −10 dB | 0 dB | +10 dB | +20 dB | +30 dB |
|---|---|---|---|---|---|---|
| TECHSAT 1B 15034285 | tone 4 kHz | flag | flag | flag | – | – |
| TECHSAT 1B 15034285 | noise 7 kHz, 150 Hz | **flag** (−3 dB width 141 Hz, run 258 Hz) | flag | flag | – | – |
| FUNCUBE-1 15123656 | tone 6 kHz | flag | flag | flag | flag | – |
| FUNCUBE-1 15123656 | noise 3 kHz, 150 Hz | **flag** (−3 dB width 164 Hz, run 328 Hz) | flag | flag | – | – |
| FUNCUBE-1 15123656 | tone 11.65 kHz (inside TRAIN downlink) | in_signal_band | in_signal_band | in_signal_band | – | – |

SNR now uses P_ref (decision 56). TRAIN downlink regions (`data/downlink_bands.json`): FUNCUBE-1
10.90–12.40 kHz (15 of 19 TRAIN audio observations), TECHSAT 1B none (its FSK signal fills the band).
QC rerun: still 0 flagged and 0 in_signal_band lines on clean data. Problem 9 is closed.

## Phase F — results (2026-10-30)

- `attacks/` (common, replay, sequence, interference, benign, apply), `detect/counters.py`,
  `data/make_recipes.py` → 1017 recipes (989 before decision 62 added 2 SNR-grid values); `data/recipes/report.json`.

| satellite | split | clean | benign | interference | snrgrid | replay | sequence | combo | evasion |
|---|---|---|---|---|---|---|---|---|---|
| FUNCUBE-1 | train | 22 | 57 | 76 | – | 33 | 40 | 27 | – |
| FUNCUBE-1 | calib | 7 | 21 | 21 | – | 10 | 10 | 10 | – |
| FUNCUBE-1 | test | 6 | 18 | 18 | 60 | 10 | 10 | 50 | 12 |
| TECHSAT 1B | train | 26 | 78 | 104 | – | – | – | – | – |
| TECHSAT 1B | calib | 8 | 24 | 24 | – | – | – | – | – |
| TECHSAT 1B | test | 8 | 24 | 24 | 80 | 12 | 12 | 60 | 15 |

- Skipped by design (not available): frame groups on TECHSAT 1B train/calib (fragments), audio groups
  on the 3 frame-only FUNCUBE-1 observations, frame groups on observations below 10 frames.
- Skipped because no free time holds the copies: 7 replay + 7 combo on FUNCUBE-1 TRAIN (passes with frames
  from start to end), 1 cross_pass evasion on TECHSAT 1B.
- Sequence subtypes (all groups): FUNCUBE-1 deletion 18, swap 31, duplicate 32, forged 29, timing 16,
  counter_jump 21; TECHSAT 1B 15 / 14 / 12 / 12 / 14 / 5. Timing only on timing-reliable observations.
- Replay audio paste on 158 of 213 replays (the others are frame-only: no audio QC or unreliable timing).

## Phase G — dry run on 2 observations (2026-10-30, 1 worker, this machine)

| obs | satellite | audio | variants | total | per variant | peak memory |
|---|---|---|---|---|---|---|
| 15229495 | FUNCUBE-1 (test) | 486 s | 31 | 204 s | 6.5 s | 860 MB |
| 15080837 | TECHSAT 1B (test) | 906 s | 31 | 361 s | 11.5 s | 1474 MB |

- Clean variant of both = Phase D tables, byte-identical (frames and windows).
- Projection (made for 989 recipes; 1017 after decision 62, ≈ 3 % more, ≈ 2.0 h with 1 worker) (time per variant ∝ audio length, 13 ms per audio second): **≈ 1.9 h with
  1 worker** (TECHSAT 1B 67 min, FUNCUBE-1 45 min), ≈ 1 h with 2 workers. Peak ≈ 1.5 GB per worker
  (≈ 1.6 MB per audio second; longest observation 914 s), more for cross_pass (second observation loaded).
  Output ≈ 160 MB of CSV.

### Problems found
10. **Closed by decision 62.** Near-threshold evasion SNR was inverted in the spec: with snr_db = 10 log10(P_ref / P_interf),
    −35…−25 dB is the STRONGEST interference (25–35 dB above the reference), not a near-threshold one.
    The positive control places the screen's limit around +10…+20 dB. Recipes follow the spec for now
    (`attacks.evasion.near_threshold_snr_db`); 3 FUNCUBE-1 test variants are affected. Fixed: all SNR ranges re-centred for an SIR.
11. 14 FUNCUBE-1 TRAIN replay/combo variants cannot be built (no free time in the pass).

## Phase H — validation (2026-10-31)

First full run (by the user): 6 of 7 checks passed; the pooled shortcut test failed (max AUC 0.7624,
random forest, TECHSAT 1B, 5-fold by observation).

Diagnosis (cached cues only, nothing rerun):
- Duration and n_windows are identical to the variant's own clean in all 1002 audio variants: no
  structure trace from the chain. Their pooled AUC (up to 0.78 for TECHSAT 1B evasion) is a
  **composition confound**: test observations carry 25–27 attacked variants against 3–9 elsewhere,
  and TECHSAT 1B test passes are longer (median 870 s against 518–533 s); Spearman ρ(duration,
  attacked fraction) 0.34 / 0.31.
- Level cues follow physics: interference at snr_db [−10, −5) raises the file RMS by +3.2 dB
  (FUNCUBE-1 +7.8 dB, RMS AUC 0.98); from +15 dB up the AUC is back near 0.5. Pure sequence variants
  have a level delta of exactly 0 dB, pure replay at most +0.02 dB (pasted audio).
- A second confound: 75 % of not-attacked variants carry a benign condition, 14 % of attacked ones;
  benign conditions change the level by design.

Revised test (decisions 63–65), rerun with `python data/validate.py --only shortcut`:
- Structure cues: weighted AUC exactly 0.5 everywhere; classifiers on structure cues 0.5.
- Level cues, benign-matched (both satellites pooled): replay 0.48–0.50, sequence 0.46–0.49,
  power-neutral attacked 0.50–0.51, interference snr_db ≥ +15 0.50–0.51; classifiers on all cues,
  power-neutral variants 0.46–0.54.
- Reported as physical effects: per-SNR level AUCs up to 0.89 (TECHSAT 1B peak, [−10, −5)) and 0.97
  (FUNCUBE-1 RMS, [−10, −5)); classifiers on all cues and all variants up to 0.68.
- **5 documented limitations** (rule failures that vanish without benign conditions, not chain
  traces; not iterated on, decision 65), all TECHSAT 1B, 12 variants on 6 observations:
  replay rms_db AUC 0.357 (pure stratum 0.486; 2 replay variants carry a benign condition) and
  sequence peak / rms / mean power 0.335 / 0.325 / 0.370 (pure stratum 0.500; 5 variants carry one).
  Attacked variants with a benign condition carry exactly 1 such condition, benign variants
  1.48 on average, so the benign stratum is not fully matched on this small sample.
- Amplitude (decision 67): post-chain peak |x| up to 2.09 on clean variants, 18.3 on one benign
  variant; processing is in float, nothing is clipped.

## Decisions (with reason)

| # | Decision | Reason |
|---|---|---|
| 1 | Redo the ranking from the API; do not reuse the earlier draft in `data/explore/` | Its counts were estimates (`exact: false`) and the Network API filter it may rely on is unreliable (see 2) |
| 2 | Network API: filter by `norad_cat_id`, never `satellite__norad_cat_id`; the script fails if any returned observation does not match the requested satellite, transmitter and status | `satellite__norad_cat_id` is silently ignored (a test query returned 15 different satellites) |
| 3 | Use `status=good` for the spec's `vetted_status=good` | Current API field name; same meaning (vetting outcome) |
| 4 | DB satellite status filter is `in orbit` | The DB has no `alive` status value (values: in orbit / re-entered / future) |
| 5 | Rate limit: 1 request every 3 s, exponential backoff on 429/5xx/connection errors (honours `Retry-After`), every response cached on disk | Spec Phase B (stricter than CLAUDE.md's 2 s); reruns cost no API calls |
| 6 | Client practices follow a public satnogs-id client (cache, throttle, backoff, Link-header pagination, token in gitignored `.env`); our code is written independently | Requested; avoids tripping SatNOGS rate limits |
| 7 | Selection window fixed: 2026-08-30 .. 2026-10-29 UTC (60 days) | Reproducible results (spec principle 6) |
| 8 | Allowed modes: AFSK 1k2, BPSK 1k2, FSK 9k6, GMSK 9k6. GFSK and MSK excluded. Prefix match for 9k6 (`FSK AX.25 G3RUH`, `FSK AX.100 Mode 5`, `GMSK USP` count), exact match for 1k2 | Requested mode list |
| 9 | gr-satellites support required; list from the public satyaml files of release v5.9.0 (tarball SHA-256 pinned in config), or an installed gr-satellites if present | gr-satellites is not installed on the dev machine; pinning keeps the list reproducible |
| 10 | DB GMSK 9600 is matched to gr-satellites FSK 9600; DB BPSK 1200 to gr-satellites BPSK or DBPSK 1200 | gr-satellites has no separate GMSK modulation (its FSK demodulator decodes GMSK) |
| 11 | Counts capped at 20 pages (500 obs) per transmitter; capped counts reported as lower bounds | Bound the API load; thresholds are ">=", so a lower bound is enough |
| 12 | Stop the full 45-candidate ranking; focus on TECHSAT 1B (9k6) + FUNCUBE-1 + 2 best other 1k2 | Save time before the deadline |
| 13 | "Best other 1k2" = highest count of good observations with demoddata (then stations) on the first page (25 most recent good obs) of each transmitter | `prefilter.json` carries no quality numbers; one request per transmitter is the cheapest data-based choice |
| 14 | TECHSAT 1B keeps its exact full count (early stop only for 1k2) | Its pages were already cached: 0 extra requests |
| 15 | Burst period from demoddata filename timestamps (1 s resolution), new burst when gap > 2 s, period = median burst-start interval; "regular" = share of intervals within 1.5 s of a multiple of the period; the 3 observations with the most frames | No audio download needed; missed bursts give multiples of the period, which are still regular |
| 16 | Counter fields: field ids in the satnogs-decoders `.ksy` struct(s) listed by the DB for the satellite, matched by name (counter, cnt, uptime, seq, frame no.) | Spec asks only whether a decoder exposes one; values are not checked until Phase D |
| 17 | `.h5` artifacts: optional, need a SatNOGS DB token (`SATNOGS_DB_TOKEN` in gitignored `.env`); audio stays the primary data | The DB artifacts endpoint returns HTTP 401 without a token |
| 18 | Local runs use a project `.venv` (Python 3.14) on Windows | No WSL on the dev machine; the jury setup (`setup.sh`, Ubuntu) is unchanged |
| 19 | 9k6 satellite: TECHSAT 1B (NORAD 25397) | Passes all thresholds (290 obs with demod, 24 stations), regular 30 s rhythm |
| 20 | Filename timestamp parser accepts any `_<letters><digits>` suffix | Real suffixes seen: `_1`, `_0015`, `_g0` |
| 21 | Counter fields also looked up in gr-satellites telemetry parsers | The DB lists no satnogs-decoders struct for the 4 satellites; gr-satellites is the decoder we will use anyway |
| 22 | **1k2 satellite: FUNCUBE-1 (NORAD 39444), station threshold lowered from 10 to 7 for the 1k2 class** (`selection.min_stations_by_class`) | No 1k2 candidate reaches 10 stations (best 8). FUNCUBE-1 has the only real frame counter (`seqnumber`) and the most regular rhythm |
| 23 | **2 FUNCUBE-1 stations reserved for test only** (`download.test_only_stations`; which 2 is chosen in Phase E) | Keeps the test split station-disjoint with only 7 stations |
| 24 | Download caps: FUNCUBE-1 all eligible observations, at most 12 per station (36 obs, 7 stations); TECHSAT 1B 40 observations, at most 6 per station (24 stations) | Approved choice; FUNCUBE-1 has few stations, so a higher cap is needed |
| 25 | Download selection: per-station shuffle with seed (`seed + NORAD`), cap, then round-robin over a shuffled station order | Deterministic, spreads observations over stations, no bias toward strong passes |
| 26 | Observation records for `metadata.json` come from the cached Phase A list pages (same record as the detail endpoint) | Saves one API request per observation |

| 27 | TECHSAT 1B download raised to 60 observations; Phase C keeps the first 40 that pass (plan order). The 10 s duration rule is unchanged | Approved option 1: buffer against short audio |
| 28 | Fallback (only if fewer than 30 TECHSAT 1B observations pass, and only after telling the user): accept short audio when burst times match the FM-quieting noise dips within 1 s; report the alignment offset per observation | Approved fallback; not applied automatically |
| 29 | **Metadata policy**: `data/raw/` is kept exactly as received and never distributed. Derived tables, docs and reports use station ids only, never usernames or coordinates. `data/sample/` gets a sanitized `metadata.json` (no usernames, no coordinates) and DATA.md states what was removed | Approved; anonymity rule + provenance |
| 30 | Phase E: the 2 FUNCUBE-1 test-only stations must not be 2107 or 4272; pick two stations with 3 to 6 observations each | Approved |
| 31 | **FUNCUBE-1 counter**: `seqnumber` = 24-bit big-endian at frame bytes 50..52, steps by 1 when the frame type (low 6 bits of byte 0) wraps 23 → 0. Per-frame counter = seqnumber × 24 + frame type | Found by search on pilot frames (81/81 steps consistent); the gr-satellites layout is shifted by 1 byte in SatNOGS files. The naive offset (byte 49) gave a constant value |
| 32 | QC "fewer than 10 frames" counts **canonical** frames (identical timestamp + SHA-256 counted once) | `_g0` / `_g1` files are byte-identical copies, not separate frames |
| 33 | f_edge: fixed-point iteration (reference median over [100 Hz, f_edge], recomputed until f_edge stops changing) | The spec's "in-band median" depends on the edge; a single pass over the full band overestimates the edge (14 kHz for noise low-passed at 6 kHz) |
| 34 | `timing_reliable`: at most 5% of occupied seconds hold more frames than the satellite can send (FUNCUBE-1: 1 per second, as a frame lasts 4.3 s), and for FUNCUBE-1 at least 90% of counter steps agree with timestamps (±1.5 s) | A strict "no overfull second" rule rejected a pilot pass with one late write while its counter was perfect |
| 35 | Narrowband-line screening runs only on observations that pass the hard checks; flagged lines are reported with frequency range and width | Saves time; width lets the reviewer tell a satellite signal from a narrow RFI line |
| 36 | **Option C, extended**: FUNCUBE-1 is the primary satellite for all three attacks (replay, sequence, interference), frames and audio. No search for another 9k6 satellite | TECHSAT 1B cannot supply 30 observations with ≥ 10 canonical frames: only 7/60 downloaded qualify, and only 20 in the whole window (17 from one station) |
| 37 | **QC split by task**. *Audio QC* (readable, 48 kHz mono, duration rule) decides use for audio tasks: interference, benign variants, audio features. *Frame QC* (≥ 10 canonical frames) decides use for frame tasks: replay and sequence. Each observation gets both flags | Lets the wideband TECHSAT 1B audio be used even though its passes decode few frames |
| 38 | **TECHSAT 1B roles**: observations that pass audio QC are a second, wideband 9k6 audio domain for interference and benign variants (24 stations, enough for station-disjoint testing). The 7 observations with ≥ 10 canonical frames form a small extra cross-satellite test set for replay and sequence. Observations with fewer frames are kept but flagged as fragments | Uses all usable data without lowering the frame rule |
| 39 | **TECHSAT 1B frame validity**: beacon check (length 44 bytes + ASCII marker `TechSat`) replaces the AX.25 check. Counter = uint32 little-endian seconds counter at bytes 6..9 (+30 s per beacon) | TECHSAT 1B frames are not AX.25 framed; analysis of 239 canonical frames (200 valid beacons) |
| 40 | **Narrowband-line QC flag**: flag only lines at most 200 Hz wide (−3 dB / contiguous-bin width as reported). Wider lines are reported but do not flag | FUNCUBE-1's own BPSK signal (~1.3 kHz wide, fixed audio frequency ~11–12 kHz) is not RFI; both pilot passes were wrongly flagged |
| 41 | **Phases D and G, interference features**: must not fire on the satellite's own signal. Add a peak-width feature (−3 dB width of the strongest line), and compute spectral deviations against a per-satellite clean spectral template built from TRAIN observations only, not only against the observation's own median | Avoids false alarms on the downlink signal; train-only template prevents leakage |
| 42 | Phase C implementation: TECHSAT 1B counter-time consistency is reported, not used in timing_reliable (decision 34 applies the counter rule to FUNCUBE-1 only); the burst remeasure picks the 3 observations with the most valid beacons from 3 stations, without requiring frame QC | Follows decision 34; most TECHSAT 1B passes fail frame QC |
| 43 | **Narrowband-line screen, local baseline** (both satellites): each bin is normalized per window by the median PSD over ±500 Hz around it, excluding ±50 Hz; then the same +10 dB, 30 % of windows and ≤ 200 Hz width rules. **f_edge**: the spec's 20 dB rule, searched only below the steepest drop of the median PSD (median-filtered over 1 kHz); f_edge and f_drop are reported per satellite | The global band median hid lines inside the TECHSAT 1B FSK band; near-zero codec bins above ~12 kHz pulled TECHSAT 1B f_edge to Nyquist |
| 44 | **in_signal_band**: a narrow line inside the observation's own signal band (±200 Hz) is tagged in_signal_band, listed, not flagged. The 7 FUNCUBE-1 flags of the first run are reviewed ok (`data/qc_review.yaml`) | User checked the waterfalls of 14934136 and 15192857: the lines are part of the satellite signal |
| 45 | **Rewrite merge** (`detect/canonical.py`, `canonical.max_rewrite_delay_s: 10`): byte-identical frames at most 10 s after the first copy are one canonical frame, with `n_copies` and `copy_delays_s`. Same function in QC, Phase D and the detectors (applied to every input recording before replay detection). Frame QC is recounted with it; 15305494 (8 frames) is accepted as failing frame QC | Some stations write a second copy 1–4 s later; it is not a separate frame |
| 46 | **timing_reliable = False** observations stay in the frame set; their timing features (dt_prev_s, dt_burst_s) are NaN, and FUNCUBE-1 frames are ordered by counter (implemented in Phase D) | Batched writes make timestamps meaningless, the counter is still valid |
| 47 | Implementation of 43/44: f_edge keeps the spec's 20 dB rule below f_drop (TECHSAT 1B: ~10.15 kHz, not the 11.07 kHz drop itself). Signal band = runs wider than 200 Hz that exceed the per-window in-band median by +10 dB in more than 5 % of windows (`qc.signal_band_min_frac`) | Spec definition kept; 2 % picked spurious bands, 10 % missed weak passes (probe on 7 FUNCUBE-1 obs) |
| 48 | **Bands**: f_edge (TECHSAT 1B ~10.15 kHz) stays the signal-band limit for SNR (P_signal over [f_low, f_edge]). Window features and injected interference frequencies use the full non-silent band [f_low, f_drop - 200 Hz] (`tables.feature_band_top_margin_hz`), so no audible part is blind | Phase C approval, point 1 |
| 49 | Signal-band threshold 5 % of windows (decision 47) accepted | Phase C approval, point 2 |
| 50 | **Rewrite merge window per satellite** (`canonical.max_rewrite_delay_s`): FUNCUBE-1 0 s (same-second branches only, no rewrites observed), TECHSAT 1B 5 s (observed delays 1–4 s). Merged copies are kept as `n_copies` / `copy_delays_s`; the replay detector treats them as "probable rewrite" events with a low but non-zero replay score | Phase C approval, point 3; closes the 10 s evasion gap for FUNCUBE-1 and narrows it for TECHSAT 1B |
| 51 | `data/qc_review.yaml` comment: "Superseded by the local-baseline line screen (decision 43); these observations are no longer flagged." The earlier claim of a manual check of 14934136 / 15192857 was removed because it could not be confirmed in the session | Phase C approval, point 5 |
| 52 | **Line-screen positive control on real audio** (`data/qc_positive_control.py`, `data/qc_positive_control.json`): tones and 150 Hz noise bands injected at SNR −10…+30 dB into one real observation per satellite (TECHSAT 1B 15034285, FUNCUBE-1 15123656) | Phase C approval, point 4. Result in "Phase C — positive control" |
| 53 | **Phase D implementation**: the clean variant goes through the common chain (×0.5, Ogg Vorbis compression level 0.4, decode; decoded audio is bit-identical between runs) so Phase G reproduces it. Table universe = Phase C `selected` ∪ frame-QC passes; windows only for audio-QC passes. dt_burst_s = start of the frame's burst − start of the previous burst. When timing_reliable is False, burst_active is NaN as well as dt_prev_s / dt_burst_s. Features in `detect/features.py` (shared with the detectors); the TRAIN-only clean template of decision 41 is built in Phase G, after the splits | Spec 6.1 (same chain for every variant); decision 46 extended to burst_active because batched timestamps make it meaningless |
| 54 | **Phase E implementation**: universe as in 53. FUNCUBE-1 test = all observations of the 2 stations not in {2107, 4272} with 3–6 universe observations: exactly 1763 and 4806 qualify (5155 has 2). TECHSAT 1B: the 6 frame-QC observations are test only, so their stations (1664, 4188, 5049) are test stations; one more station is added if that brings test closer to 20 %. Rest: seeded shuffle, calib = round(0.2 × universe). Seed = config seed + NORAD | Decisions 23, 30, 38; station-disjoint test |
| 55 | **Line screen**: line width measured at −3 dB from the line peak of the time-averaged locally normalized profile, not at the threshold. Signal (downlink) band per satellite learned from TRAIN observations only (`data/downlink_bands.py` → `data/downlink_bands.json`: bins inside an observed signal band in ≥ 25 % of TRAIN audio observations); per-observation detection is reported only | A per-observation band let an attacker's noise band pass as downlink; the threshold width of a 150 Hz band was 258–352 Hz |
| 56 | **SNR reference (both satellites)** (snr_db is an SIR, see decision 62): P_ref = median in-band power over ALL windows of the clean observation in [f_low, f_edge]; snr_db = 10 log10(P_ref / P_interf); inr_db kept, P_noise = mean in-band power over non-burst windows (all windows when timing is unreliable). DATASET_SPEC 6.4 updated | Only 2 % of TECHSAT 1B windows are burst_active, and FM-demodulated audio power drops during bursts |
| 57 | 15034285 (and, by the same rule, 15042383) count for audio tasks in test: frame-set observations that pass audio QC get audio = true in `data/splits.json`. Split assignments unchanged | Keep-first-40 only limited volume; their stations are test-only |
| 58 | **Evaluation**: FUNCUBE-1 test set accepted (1763 + 4806). The timing sequence subtype is reported only on timing-reliable test observations, with its count. Standard-combo variants per test observation raised to 10. Confidence intervals by bootstrap at observation level | Small test set; 1763 timestamps are batched |
| 59 | Replay audio segment = [t − airtime − margin, t + airtime + margin] around every copied frame (airtime FUNCUBE-1 4.33 s, TECHSAT 1B 0.1 s; margin 0.5 s); copies at least 10 s from the source (> any rewrite-merge window); copy filenames follow the target observation and new time (no naming cue) | Spec's ±0.5 s would cut a 4.3 s FUNCUBE-1 frame; it is unknown whether timestamps mark the start or the end of a frame |
| 60 | **Replay placement**: copies keep their spacing and are shifted by one integer delta so that every copied frame lands in time free of the target's frames (interleaving for sparse beacons); the audio is replaced only around each copy. Later placements preferred, earlier as fallback. In combos, L ≤ n − 3 so the sequence anomaly has free frames; sequence ops never touch replayed source frames; if a subtype has no position the next one (seeded order) is used | TECHSAT 1B beacons every 30 s leave no gap long enough for 5–20 beacons (59 test variants were impossible) |
| 61 | Phase G per observation in its own process (spawn); peak memory = peak working set (Windows) / ru_maxrss (Linux). Vorbis encoding in 4096-sample blocks | Measured dry run; Windows stack overflow on one large Vorbis write |
| 62 | **SNR ranges re-centred for an SIR**: snr_db = 10 log10(P_ref / P_interf) is a signal-to-interference ratio (SIR), so low snr_db = strong interference; column names unchanged, inr_db kept. Random interference snr_db ~ U[−10, +30] dB; Pd-vs-SNR grid −10…+35 dB in 5 dB steps (10 values, test snrgrid 8 → 10 per observation); near-threshold evasion U[+20, +30] dB. Config, DATASET_SPEC 6.4/6.6 updated, recipes regenerated (1017). Phase G gets `--resume` (per-observation `done.json` with the recipes' SHA-256; progress in `data/features/runtime.json`) | The spec ranges [−25, +10] and [−35, −25] were written for an interference-to-signal sense: under an SIR they put most variants far above the signal and the “near-threshold” evasion was the strongest case; the positive control places the screen's limit around +10…+20 dB |
| 63 | **Shortcut test within observation** (`data/diag_shortcut.py`, used by `data/validate.py`; `--only shortcut` reruns it from the cue cache): every observation carries the same weight, half on attacked and half on not-attacked variants; weighted fit and weighted ROC AUC; paired deltas against the own clean variant; composition table (attack density, duration per split) in the report | The pooled test (max AUC 0.7624) learned which observations carry many attacks (composition confound), not a processing trace: duration and n_windows equal the own clean in 1002/1002 variants |
| 64 | **Rule \|AUC − 0.5\| < 0.1** (`validation.shortcut_auc_dev_max`). Structure cues (duration, n_windows): every comparison and SNR bin. Level cues (peak, RMS, mean power_db): replay, sequence, power-neutral attacks, interference with snr_db ≥ +15 dB (`validation.shortcut_level_min_snr_db`). Level cues of interference below +15 dB (per-SNR table) and of benign vs clean are reported as expected physical effects. Symmetric rule (not AUC < 0.6) | The spec called level cues “non-physical”, but strong interference raises the file level and benign conditions change it by design; a one-sided rule would hide AUCs far below 0.5 |
| 65 | **Benign-matched strata** (observation × has-a-benign-condition), and **stop rule**: pass = no processing-chain trace (structure cues equal to the own clean everywhere, and every rule failure disappears on the stratum without benign conditions). Failures that disappear there (composition, small samples) are documented limitations with their numbers and are not iterated on; only a real chain trace justifies changing the dataset | 75 % of not-attacked vs 14 % of attacked variants carry a benign condition; matched on it, pure sequence = 0.500 exactly. Approved stop rule (avoid iterating the test until it passes) |
| 66 | **Detector phase (binding)**: per-observation sample weights balancing attacked and not-attacked samples inside each observation; metrics per observation, then averaged (pooled values for reference); no duration, n_windows or observation-level metadata (station, split, satellite-level counts, observation id) as features; after training, report feature importances and check that absolute-level features (noise_floor_db, power_db) do not dominate. DATASET_SPEC section 8 | The dataset has the composition confounds of decisions 63 and 65; a detector must not learn them |
| 67 | **Amplitude**: processing is in float and nothing is clipped; decoded SatNOGS audio is not bounded to ±1 (post-chain clean peak up to 2.09, one benign variant 18.3). Documented as a known property; the report's “no clipping” wording replaced | Detection features are mostly scale-invariant dB ratios; absolute-level features are covered by decision 66 |

## Notes for later phases (approved)

1. **Phase D, TECHSAT 1B `_g0` / `_g1` pairs**: build a canonical frame stream. Identical
   (timestamp, sha256) pairs collapse into one frame with a `decoder_branches` column;
   non-identical frames are both kept. Measure the natural duplicate rate. Attacks are generated
   on the canonical stream.
2. **Phase C, FUNCUBE-1 batched files** (many files in the same second): add a `timing_reliable`
   flag per observation. For FUNCUBE-1, order frames by `seqnumber` when it can be decoded.
3. **Phase C**: remeasure the TECHSAT 1B burst period on 3 observations from 3 different stations.
4. **Phases D and G**: peak-width feature and per-satellite TRAIN-only clean spectral template
   for interference features (decision 41).
5. **Phase C changes to implement before the run**: audio-QC and frame-QC flags per observation
   (decision 37), TechSat beacon check and counter (39), 200 Hz line-width limit (40), fragment
   flag for TECHSAT 1B observations below 10 canonical frames (38). The keep-first-40 rule for
   TECHSAT 1B applies to audio QC.
6. **Detector phase**: decision 66 (per-observation weights and metrics, no structure or
   observation-level features, feature-importance check on absolute-level features).

## Incidents

- First full ranking (206 candidates, before the pre-filter): stopped by request after 18 candidates.
- Second full ranking (45 candidates): stopped by the host because the machine was low on
  memory (not a script error) after 1 candidate. Cached responses were kept, and the plan
  then changed to the focused scan.
- Full download: stopped by the host (low memory) after 67 of 96 observations. No partial
  files left; the manifest makes the rerun skip completed files. The user finished the
  remaining 29 outside Claude Code; the manifest covers all 96 observations.

## Open items / needs a decision

- `.h5` check skipped (no DB token); audio is the primary data.
- Phase G evasion risk of decision 45: a replay of a frame within 10 s of its original is merged away
  before detection. To test in Phase G (the replay attack spec uses older frames).

---------------------------------------------------------------------------------------------

# Detector phases (docs/DETECTOR_SPEC.md)

## Phase J — Replay detector: test lock (rule 10)

Frozen and signed on 2026-11-01 (10:54 UTC) BEFORE any evaluation on test. Lock file `models/replay/lock.json`
(Ed25519, pinned public key `keys/public/ed25519_public.pem`; private key in gitignored `keys/private/`),
SHA-256 of lock.json `01ecc240475872bab3979eba933431b0810ce747c16cde276b62cedfebcd5b99`.
Test results are produced only by `eval/replay_eval.py`, which refuses to run on test unless the lock verifies.

| file | SHA-256 |
|---|---|
| `models/replay/params.json` | `9d36f6e3f72210300054436a5eb92113c905c9b43c8ffd47a9a8aef4162e2abd` |
| `models/replay/threshold.json` | `8b5e2a74641d303ed73370990a6bf70cbea6519dce28d1866beb304d641bf998` |
| `models/replay/hash_store.json` | `4877fbabfa88d594fa52cc87a11780b6a173fc287589e18e0bf147161acb1a60` |
| `detect/replay_det.py` | `ba6d220b003b31456881f637d041aa9617234e024fb755d560734cbcb3f9fdf9` |
| `detect/frames_io.py` | `dbb4785535dbdfe7b0c27254be6dbc3c696ad129e9817ea299bdef0b18c95da3` |
| `detect/hash_store.py` | `3eeb1474ca6cf91c47c58cfface3cafcdeed1a20c024f8cc56042470d6ae45b0` |
| `detect/canonical.py` | `96523031243787dded35e5f214e4cd6f4f154f4b5d4a447621592aea8c2a9667` |
| `detect/counters.py` | `5a569f16b24817e38c3f9a0b6b7aae73f4300d0b4787af6f6c7c327e1f00e1dd` |

## Phase J — Replay detector: results (2026-11-01)

Files: `detect/frames_io.py` (raw demoddata or canonical rows -> canonical stream, no labels),
`detect/hash_store.py` (archive of clean frames, earlier passes only), `detect/replay_det.py` (CLI on a raw
recording folder), `trust/manifest.py` (Ed25519 manifests), `eval/lock.py` (test lock), `eval/metrics.py`,
`eval/replay_eval.py` (fit / score / calibrate / report). Tests: `tests/test_replay_det.py`,
`tests/test_trust_lock_metrics.py` (21 tests; full suite 104 pass). Reports: `eval/out/replay/report_{train,calib,test}.{json,md}`.

TRAIN statistics (`models/replay/params.json`): natural intra-pass repeat rate 0 (both satellites); natural
rewrite rate TECHSAT 1B 16.7 %, FUNCUBE-1 0; 33 clean cross-pass collisions, all the same pass heard by two
stations (|dt| <= 6 s); 0.33 % of FUNCUBE-1 clean frames off the counter-time line (2 frames, -224 s and -380 s).

Threshold: tau = 0.35 (alert if score >= tau; calib alone gave > 0.15, the train guard raised it, decision 72);
strict point (0.1 %) tau > 0.35. Calib FPR 0; train FPR 0.34 % (the 2 natural off-line frames, once per variant).

| split | obs | replay frames | not attacked | per-obs F1 [95 % CI] | pooled FPR | EER | events detected | median delay |
|---|---|---|---|---|---|---|---|---|
| train (dev) | 48 | 681 | 7586 | 0.982 [0.957, 1.000] | 0.0034 | 0.000 | 60 / 60 | 0 s (max 228 s) |
| calib | 15 | 228 | 1648 | 1.000 | 0.0000 | 0.000 | 20 / 20 | 0 s |
| **test** | 14 | 1424 | 5793 | **1.000 [1.000, 1.000]** | **0.0000** (clean 0, benign 0) | **0.000** | **133 / 133** | 0 s (alert on the 1st copy) |

Test per satellite: TECHSAT 1B 618 / 618 replay frames, 0 / 2963 false alarms; FUNCUBE-1 806 / 806, 0 / 2830.
Subtypes: intra_pass 132 / 132 events, cross_pass (evasion) 1 / 1. Online (ever alerted) FPR on test 0.
Fires on other attacks (test): sequence duplicate 100 % (a duplicate IS a one-frame repeat; fusion must name
it), forged 21 % (counter equal to its source and off the line), swap / deletion / timing / counter_jump 0 %.

Baselines and ablations on test (thresholds from calib + train guard, same target FPR):
- energy: recall 0.005, EER 0.70 (replayed audio has the same level: replay is invisible to energy).
- n_earlier_same_bytes: at the 1 % rule tau > 1 -> detects nothing (concurrent receptions and copies placed
  before their originals give > 1 % false alarms on train); at its natural tau = 1 it is also perfect on test.
  **Honest reading: on this test set the exact-hash repeat does almost all the work**; attribution, the
  concurrency rule and runs are what keep the false-alarm rule satisfied on train-like cases.
- counter_le_max: tau > 1 -> nothing; at tau = 1 recall 0.929, FPR 1.47 % (counter-jump aftermath).
- ablations: without intra-pass repeats recall 0.472 (counter regression alone); without the store 0.989;
  without attribution / runs / regression / rewrite: unchanged on test.
- No absolute-level features (rule 1 check not applicable: the detector uses bytes, counters and times only).

Limitations: test frames are exact byte copies, so hash evidence is decisive; a replay with re-encoded or
modified bytes falls to counter evidence (recall 0.47 in the ablation) or to the sequence detector. TECHSAT 1B
replays exist only in test (no TECHSAT 1B replay was seen before the lock; logic covered by synthetic tests).
Only 1 cross-pass replay event in test. Copies placed BEFORE their originals are blamed on the originals
transiently while the pass arrives (train online FPR 1.3 %, 6 variants); final scores are correct.
Development note: the CLI smoke test was run once on the raw clean folder of a test observation (no labels,
no metrics, all scores 0).

## Detector decisions (with reason)

| # | Decision | Reason |
|---|---|---|
| 68 | Cross-pass evidence only if the archived reception is more than `concurrent_tol_s` away (60 s when the counter-time line fits, 1800 s with batched timestamps); closer = same pass heard by another station, reported as corroboration | 33 natural clean collisions in the archive, all concurrent receptions (|dt| <= 6 s); batched writes can move file times by up to a pass length; another pass is >= one orbit away |
| 69 | Copy vs original: the member off the counter-time line is the copy; else first seen; an unresolved pair follows the shift of its line-attributed block. Line = mode of counter * s_per_count - t (singletons weigh 2) | Some replays are placed before their originals (5 of 80 train+calib); a mostly replayed pass (10 of 12 frames) left too few singletons for a median; undecodable counters exist |
| 70 | A run = copies sharing one time shift (+-2 s), not adjacent frames | TECHSAT 1B copies interleave with the pass's own beacons (decision 60) |
| 71 | Counter regression against the running max of line-consistent frames, only when the line fits | One forward counter jump otherwise makes every later clean frame a regression (baseline counter_le_max: 1.1-1.5 % FPR) |
| 72 | Threshold = max(tau on CALIB not-attacked frames, tau on TRAIN not-attacked frames), each pooled and per satellite; strict 0.1 % point reported | Calib has no TECHSAT 1B replays and few rewrites; train shows the natural rewrite rate (16.7 %) |
| 73 | `line_tol_s` = 5.5 s | Train clean residuals are 0, 1 or 5 s; must stay below half the 10 s minimum copy shift for the mode window |
| 74 | Causal scoring (prefix of the stream) for detection delay and online FPR; final per-frame score with the full pass | Delay must be measured as a station would see it |
| 75 | Minimal Ed25519 manifest (`trust/manifest.py`) and test lock (`eval/lock.py`) built in Phase J; Phase M extends `trust/`. `cryptography` installed in `.venv` (allowed dependency) | Rule 10 requires signing before the first test evaluation |
| 76 | `DETECTOR_SPEC.md` moved from the repository root to `docs/DETECTOR_SPEC.md` (2026-11-01, after Phase J); the frozen Phase J files cite it by name only and were not edited | Housekeeping; editing locked files would break the lock |
| 77 | **Phase J approved. Operating threshold tau = 0.35** (the rule fixed before the lock). The strict point (tau > 0.35) is reported only as an alternative and is not adopted | Choosing it after seeing test results would be post-hoc tuning |
| 78 | **CLI smoke test on test data recorded**: before the lock, `detect/replay_det.py --obs-dir` was run once on the raw clean folder of test observation 15229495, without labels and without metrics (all scores 0). Nothing was tuned on it | Transparency (rule 10) |
| 79 | **Note for Phase O (frozen detector unchanged)**: evaluate a near-copy replay evasion (1 byte of each replayed frame changed outside the counter field, so the hash no longer matches). Any detector change it motivates is a new version with a new lock; both versions are reported | Phase J test frames are exact copies, so the hash evidence was decisive |

## Phase K — Sequence detector: test lock (rule 10)

Frozen and signed on 2026-11-01 (20:06 UTC) BEFORE any evaluation on test. Lock file `models/sequence/lock.json`
(same pinned Ed25519 key), SHA-256 of lock.json `43ee3dc01b835bf1b92ec384952de263cb7c6773ed02aeb9ac6359e7a2b924fa`.
Operating threshold tau = 2.3054 (score = -log10 p >= tau; calib 2.3054, train guard 2.2893, decision 72 rule);
strict point 2.7825 reported as an alternative only. The naming rule for fusion (`detect/naming.py`) is locked with it.

| file | SHA-256 |
|---|---|
| `models/sequence/profile.json` | `bfd568b29ad55e1516d94a46705b533763023d79ca854db7d684f5f4b830051f` |
| `models/sequence/threshold.json` | `98af1c1168bbb02265633385025f558019b7ebc5b2303afcf468207d339480c3` |
| `detect/sequence_det.py` | `36b270eaf7266ccd784a9784d768654bd0528054b9effa71eea0ca0fec1a4f83` |
| `detect/naming.py` | `f64f9ccf57bcb3bb3347806720ecef4eb3c8259941dd56ef39fa2f5c32b5e74e` |
| shared, unchanged since the Phase J lock | `detect/replay_det.py`, `frames_io.py`, `hash_store.py`, `canonical.py`, `counters.py` (hashes as in Phase J) |

## Phase K — Sequence detector: results (2026-11-01), version 1 (official)

Files: `detect/sequence_det.py`, `detect/naming.py`, `eval/sequence_eval.py`, `tests/test_sequence_det.py`
(11 tests; full suite 115 pass). Reports `eval/out/sequence/report_{train,calib,test}.{json,md}`.
TRAIN natural statistics (FUNCUBE-1, 605 frames): counter-time deviation 0 s 83 %, 1 s 15 %, exactly one count
(5 s) 1.7 %, 3 s 1 frame, beyond 10 s 2 frames; 1-3 missing frames on 7 % of steps; 2 inversions, 0 swap
patterns, 0 duplicates, 0 same-counter clashes. TECHSAT 1B: 36 train frames only.

| split | seq. frames | per-obs recall [95 % CI] | per-obs F1 | pooled FPR (clean / benign) | events |
|---|---|---|---|---|---|
| train (dev) | 80 | 0.862 [0.772, 0.938] | 0.770 | 0.0078 (0.0075 / 0.0049) | 57 / 67 |
| calib | 22 | 0.820 [0.660, 0.960] | 0.844 | 0.0012 (0 / 0) | 16 / 20 |
| **test** | 162 | **0.291 [0.102, 0.499]** | **0.267** | **0.0126** (0.0109 / 0.0109) | **41 / 132** |

Test recall per subtype (timing on timing-reliable observations only), FUNCUBE-1 / TECHSAT 1B:
deletion 0/6, 0/15; swap 0.44 (n=32), 0 (n=28); duplicate 1.00 (10), 0 (12); forged 0.82 (17), 0 (12);
timing 0.75 (4), 0 (14); counter_jump 1.00 (7), 0 (5). Median detection delay 0 s (max 45 s).
Baselines on test: energy recall 0 (AUC 0.46); counter_step 0.037; abs_time_dev 0.235 at FPR 0 (per-obs F1 0.344,
above the detector's 0.267). Ablations: without content FPR 0.0016 and per-obs F1 0.328; every other group
lowers recall.

Why test is far below calib (diagnosed after the test run; nothing retuned):
1. **TECHSAT 1B can never alert (design error, decision 81).** p = max(own, pooled) lets the Laplace prior
   of its 36 train frames dominate: any never-seen event gets p >= 1/37, score <= 1.57 < tau 2.31. The unit
   test only checked that pooling was active, not that a rare event could still reach tau.
2. **Content evidence does not transfer across stations.** 70 of 73 test false alarms are content, all on
   2 observations of test station 1763; positions constant in TRAIN passes vary there. The
   leave-one-OBSERVATION-out estimate hid the station shift. Test FPR 1.26 % is above the 1 % target.
3. **Swap in batched passes** (timing not usable, 1763): recall 0.25 against 1.00 when timing is usable;
   the file order within a batched second does not follow the counters.
4. Deletion 0 / 21: indistinguishable from natural losses, as expected.

Fusion view (frozen detectors, either one alerting, test): duplicates 22 / 22 (TECHSAT 1B duplicates are
caught by the replay detector's single-repeat evidence), forged FUNCUBE-1 0.82 / TECHSAT 1B 0.33; FPR 1.26 %.

Naming (`detect/naming.py`, locked; test): name accuracy 0.994 on alerted attacked frames. 803 frames
flagged by both detectors: 790 replay -> "replay", 10 duplicates -> "duplicate", 2 forged -> "forged",
1 cross-pass -> "replay_cross_pass". Rule: a block of copies sharing one shift (or an earlier pass) is a
replay; ONE repeated frame is a sequence duplicate; intra-pass evidence takes precedence over a match with
another station's recording of the same pass.

## Detector decisions, continued

| # | Decision | Reason |
|---|---|---|
| 80 | Sequence score = -log10(min p) over 6 evidence groups, p = Laplace-smoothed natural probability from TRAIN clean frames; time deviation scored by its exact 1 s bin; swap-pattern rate separate from generic inversions; order analysis without frames already explained as copies; same threshold rule as decision 72 | Natural deviations are 0, 1 or one count; tail probabilities would make every timing shift look natural; one jumped counter must not flag its neighbours |
| 81 | **Error, recorded**: small-satellite pooling p = max(own, pooled) caps TECHSAT 1B at score 1.57 < tau, so version 1 cannot alert on TECHSAT 1B | Found on test; version 1 stays the official result |
| 82 | Naming for fusion (`detect/naming.py`) frozen with Phase K; timing vs counter_jump from the phase on the pass rhythm and whether the deviation is a whole number of >= 2 counts | Train naming accuracy 0.992 |
| 83 | Phase K CLI smoke test on a TRAIN folder (15123656), not test | Lesson from decision 78 |
| 84 | **Version 2 proposed, not built** (needs approval): (a) pooled p, raised to the satellite's own p only for values it actually shows (count > 0); (b) content profile checked leave-one-STATION-out on train, positions kept only if constant across stations; (c) nothing else. Any v2 is post hoc: new lock, reported next to version 1, version 1 stays the official result | Test results motivated these changes |
| 85 | **Sequence detector: version 1 is the reported result; version 2 is a planned fix, not built for Phase 1.** Diagnosis (Phase K results): (1) small-satellite pooling p = max(own, pooled) caps TECHSAT 1B at score 1.57 < tau 2.31, so TECHSAT 1B can never alert (decision 81); (2) content positions constant in TRAIN vary at test station 1763 (70 of 73 test false alarms; leave-one-observation-out hid the station shift), test FPR 1.26 % > 1 % target; (3) swap in batched passes is weak without timing; (4) deletion is indistinguishable from natural loss. Planned v2 = (a) pooled p, raised to the satellite's own p only for values it actually shows (count > 0), fixing (1); (b) content profile checked leave-one-STATION-out on train, keeping only positions constant across stations, fixing (2). If ever built: new lock, reported next to v1, v1 stays official | Organizers' email (2026-11-01): Phase 1 deliverables are an anonymous technical report with preliminary validation, the GitHub repository, a working proof-of-concept prototype and documentation; time goes to Phase L and the prototype. The report states the v1 numbers and this diagnosis |
| 86 | Prototype entry point `detect/run.py`: runs the detectors on ONE raw recording folder (audio.ogg, demoddata/, metadata.json), no labels; Phase L wires the interference detector into it, later phases add the others | Deliverables now include a working proof-of-concept prototype (decision 85) |
| 87 | Phase L design: features in feature space (template = per-satellite TRAIN clean median / MAD of window features, peak-frequency rarity, within-recording deviations); level features only in the `with_level` ablation; grid on every 4th TRAIN window, learning rate fixed at 0.1, 8 combinations, max_iter 200 (first run with 16 combinations on every 2nd window: ~8 min per combination, stopped); threshold from CALIB only (no train guard: in-sample train scores of a learned model are meaningless, out-of-fold scores come from other models), OOF train FPR at tau reported (2.7 %); smoothing chosen on calib by TPR among (1,1) (2,2) (2,3) (3,4) (3,5) at <= 1 % smoothed FPR | Frozen dataset stores features, not spectra; CPU budget before the deadline |
| 88 | Main model keeps no level features although `with_level` has higher calib AUC (0.902 vs 0.862): power_db and noise_floor_db rank 2nd and 3rd in its importances (they would dominate), and gain changes are benign | Rule 1 / benign robustness |
| 89 | **Phase L version 1 is the reported result; its threshold does not transfer to new stations (diagnosed after the test run; nothing retuned).** 6 of 14 test observations carry almost all false alarms (FPR 0.09–0.99 each; the other 8 <= 0.5 %): stations 4188 / 4073 (TECHSAT 1B) and 1763 (FUNCUBE-1) record a wider audio band, so occupied_bw_hz is 9.42 kHz vs 9.23 kHz in TRAIN (tz 5.4) and 19.6 kHz vs 17.2 kHz (tz 14), and tz_occupied_bw_hz is the top feature; one 4806 pass has a persistent line (tz persistence 10). The template scale (MAD of a quantized feature) is too small. Grouped CV by observation hid the station shift (as in decision 84). Evidence: ablation without the shape group: test FPR 4.4 %, per-obs F1 0.746 | Same lesson as Phase K |
| 90 | **Version 2 proposed, not built** (needs approval): (a) drop occupied_bw_hz / tz_occupied_bw_hz (band-edge dependent, a receiver property); (b) floor the template scale at one frequency bin / a fixed fraction of the TRAIN spread; (c) grid and threshold checked leave-one-STATION-out on train+calib; nothing else. Post hoc: new lock, reported next to version 1 | Test results motivated these changes |
| 91 | Phase L smoke test of `detect/run.py` on TRAIN raw folder 14915678: 1041 windows, 0 alerts, 11.4 s, 489 MB peak traced memory. The Phase L lock includes `detect/run.py`; Phase N will extend run.py as a new version (the Phase L test report is already produced) | Prototype deliverable (decision 86) |

## Phase L — Interference detector: test lock (rule 10)

Frozen and signed on 2026-11-01 (20:36 UTC) BEFORE any evaluation on test. Lock `models/interference/lock.json`
(pinned Ed25519 key), SHA-256 `1d1ee6f984cc17cb30f08bd51da5904a63dd4dfc56222d7aec3592cdb6e36bc9`.
tau = 0.6650 (raw score; strict 0.9864); smoothing 3 of 5 at tau 0.6085; calibration isotonic (CV Brier 0.0738 vs Platt 0.0753).

| file | SHA-256 |
|---|---|
| `models/interference/model.pkl` | `e96f2ce9b3d513c3325c0dd63225fd1963d3f50322633457ff823881ae9871e5` |
| `models/interference/extras.pkl` | `014fbc4fb38090833c213d9b5c7078757c8be23d588d3545678f4298c8244aad` |
| `models/interference/threshold.json` | `51ea074451ee822bc94ca9f262987edeb5bc468a0659799ea80ee12ab996142c` |
| `models/interference/template.json` | `717106d176a35ebe314d8bcb80dcc0cc351ff89fb094570c127ccec1d0ec926c` |
| `detect/interference_det.py` | `95d2fa3f02ede9f35c960490eee1bbae75b3a16853133fd6cf12dfb85ce61b2f` |
| `detect/audio_front.py` | `80cdcfc44d0a7c096e7ec553e6b41fcba611e68d14cc02037a2bcbb0ac80758d` |
| `detect/run.py` | `466eeb319841d4cbe148ab89e9966e8d961a83cd85ce87af4dae0fb26ab5191d` |
| `detect/features.py` | `3fa455a14c7edffa72a9e4b0bf7405e25a301367c53241e612d7bc3b321a6e1a` |
| `detect/frames_io.py` | unchanged since Phase J |

## Phase L — Interference detector: results (2026-11-01), version 1 (official)

Files: `detect/interference_det.py`, `detect/audio_front.py` (raw audio -> windows, reproduces the frozen features to
5e-5), `detect/run.py` (prototype), `eval/interference_eval.py`, `tests/test_interference_det.py` (5 tests; suite 120 pass).
Reports `eval/out/interference/report_{calib,test}.{json,md}`, `fit_report.json`, `reliability_{calib,test}.png`.
Grid best: 15 leaves, min_samples_leaf 400, l2 0 (CV pAUC@1 % 0.840; one fold 0.72).

| split | AUC | TPR@1 % FPR (curve) | TPR at tau | FPR clean / benign | per-obs F1 | events | ECE |
|---|---|---|---|---|---|---|---|
| calib | 0.862 | 0.644 | 0.633 | 0.0052 / 0.0050 | 0.731 [0.576, 0.857] | 44 / 55 | (in-sample) |
| **test** | **0.844** | **0.564** | 0.774 | **0.364 / 0.340** | 0.629 [0.533, 0.734] | 269 / 292 | 0.164 |
| test, strict tau | | | 0.632 | 0.045 / 0.046 | 0.710 [0.613, 0.799] | 196 / 292 | |

Test smoothed (3 of 5): TPR 0.786, FPR clean 0.384, median delay 1 s. Per shape TPR noise 0.80, tone 0.76; per satellite
TECHSAT 1B 0.87, FUNCUBE-1 0.60. Pd vs snr_db (test, raw tau): <= 0 dB 1.00, 5 dB 0.93, 10 dB 0.88, 20 dB 0.73,
30 dB 0.32; min detectable snr_db 5 dB (inflated by the FPR). Evasion TPR (smoothed) 0.84 / 0.83.
Baselines and others on test (AUC; TPR@1 % FPR on the curve): energy 0.529 / 0.033; peak_to_median_db 0.796 / 0.161;
model B IsolationForest 0.853 / 0.345 (FPR 0 at its tau, TPR 0.04); leave-one-shape-out: noise held out TPR 0.60, tone
held out 0.73; ablations: with_level 0.848, no_peak 0.800, no_shape 0.843 (FPR 4.4 %, best F1 0.746), no_deviation
0.841, no_template 0.832, no_obs_relative 0.845. Calib importances (permutation): tz_occupied_bw_hz, spectral_entropy,
tz_peak_persistence, peak_rarity.
Honest reading: ranking power transfers (AUC 0.84, above both baselines), the operating threshold does not (decision 89).
| 92 | **Interference v2 built (post hoc, after the v1 diagnosis; rules fixed before any v2 test look).** (a) no occupied-bandwidth features; features whose per-recording clean median follows the receiver band edge (\|Spearman\| >= 0.75 on train + calib: peak_width, flatness, entropy, kurtosis) only as within-recording deviations d_<f>; (b) template scale >= max(window MAD, between-station SD of clean medians, 1 dB / 1 window / 1 bin); (c) station-grouped CV on train + calib (GroupKFold by station, 6 folds instead of 22 single-station folds, for CPU; 22 stations), every window scored by a model that never saw its station; these out-of-fold scores give calibration (isotonic, Brier 0.124 vs Platt 0.184), the 1 % threshold (tau 0.99985) and smoothing (3 of 5); v1 hyperparameters, models on every 2nd window. Gate fixed in advance (lower worst-station FPR, fewer stations > 2 %, TPR@1 % within 0.05 of v1), on the station-out-of-fold scores: v1 AUC 0.847, TPR@1 % 0.502, worst station 12.2 % (4011), 1 station > 2 %; v2 AUC 0.827, TPR@1 % 0.514, worst 1.85 %, 0 stations > 2 % -> **passed**; lock signed, test run once | User decision after the Phase L STOP |
| 93 | **Main finding: cross-station generalization is the main difficulty** (sequence content profile, decision 85; interference threshold, decisions 89 and 92). Observation-grouped validation hides it; station-grouped validation exposes it. v2 trades recall for false alarms: on test it removes the station false alarms but misses full-pass interference (d_ features cancel when the interferer lasts the whole recording; Pd at -10 dB 0.77, noise TPR 0.35). **Phase 2 fix: a per-station template built from the station's own earlier clean passes** (as the replay hash archive, decision 68), so absolute shape features can be compared with the same receiver's normal spectrum and full-pass interference becomes visible again | Recorded for the report |
| 94 | Report-code fix after the test runs: snrgrid variants are named `snrgrid_+20` etc., the filter `kind == "snrgrid"` matched none, so the v1 Pd-vs-SNR curve had used all positives in 5 dB bins. Fixed to `startswith("snrgrid")` in both eval scripts and both test reports regenerated (no model, threshold or locked file changed) | Transparency |

## Phase L — Interference detector v2: test lock (rule 10)

Frozen and signed on 2026-11-01 (21:19 UTC) BEFORE its test run. Lock `models/interference_v2/lock.json`, SHA-256
`02cdfcb8c423031ac085877b73ca0922510448581db577017697a3c1fcb01da4`. Prototype: `detect/run.py --model models/interference_v2/model.pkl`.

| file | SHA-256 |
|---|---|
| `models/interference_v2/model.pkl` | `2412d06aaf39af367304d9ada291d6c14f2fea87b5e2a1ac366364a642c89696` |
| `models/interference_v2/threshold.json` | `c8ae4698d7c21e9b1a92ef9459794cf651bb9162d0817831e2ba38a949fdc67d` |
| `models/interference_v2/template.json` | `2e4e6b39474fe9b70a498627988af495c7d4c014d51188b3d2d1229e746ab329` |
| detect/interference_det.py, audio_front.py, features.py, run.py | unchanged since the v1 lock |

## Phase L — v1 (official) vs v2 (post hoc, after diagnosis) on test

Files: `eval/interference_v2.py`, `tests/test_interference_v2.py` (2 tests; suite 122 pass), `eval/out/interference_v2/{loso_report,report_test}.{json,md}`.

| version | AUC | TPR@1 % FPR (curve) | TPR at tau | FPR clean / benign | per-obs F1 | events | ECE | evasion TPR | min detectable snr_db |
|---|---|---|---|---|---|---|---|---|---|
| v1 | 0.844 | 0.564 | 0.774 | 0.364 / 0.340 | 0.629 [0.533, 0.734] | 269 / 292 | 0.164 | 0.84 | 10 |
| v2 | 0.864 | 0.580 | 0.484 | **0.0014 / 0.0016** | 0.582 [0.457, 0.694] | 154 / 292 | 0.056 | 0.44 | none (Pd -10 dB 0.77) |

Per-station test FPR (raw tau), v1 -> v2: 1664 0.27 % -> 0; 1763 14.4 % -> 0; 4073 92.7 % -> 0; 4188 87.4 % -> 0;
4806 24.5 % -> 0.66 %; 5049 0.12 % -> 0. TPR by shape v1 noise 0.80 / tone 0.76; v2 noise 0.35 / tone 0.59.
Pd vs snr_db (snrgrid), v1: -10..0 dB 1.00, 5 dB 0.97, 10 dB 0.93, 20 dB 0.82, 30 dB 0.20; v2: -10 dB 0.77, 0 dB 0.90,
10 dB 0.84, 20 dB 0.25, >= 25 dB ~0. v2 meets the false-alarm target on every test station; v1 has the higher recall
only because its threshold sits inside the false-alarm cloud of new stations. Sequence v2 not built (usage).

## Phase N — Fusion, line screen and standard test: test lock (rule 10)

Final lock signed on 2026-11-02 (08:07:04 UTC) after the sequence v2 check (decision 100) and BEFORE the reported test
run. Lock `models/fusion/lock.json` (pinned Ed25519 key), SHA-256
`535df513cb4d6da87ff9fd1cd32afb6a2124399b2e1b5b1a8bbc6e012f2b708c`. It covers `models/fusion/fusion.json`
(`9ea7831dd6b190837177fe8db5ab9e10e97cbded867f011db51b8559d2b766fd`, sequence content evidence disabled), `detect/{fusion,line_screen,run,naming,interference_det,audio_front,features,replay_det,sequence_det,
frames_io,hash_store,canonical,counters}.py`, the interference v2, replay and sequence model / threshold files (hashes
unchanged since their locks), `eval/{run_standard_test,fusion_dev,fusion_eval}.py`. `config.yaml` is not in the lock
(packaging may edit it); SHA-256 at lock time `75365476b1900b44fc94c59279933d4814a603dfd07f3b26ba7f76bb87abab3d`.
`detect/run.py` (prototype) now verifies this lock before running (`--no-verify` for development).

## Phase N — development evaluation on train + calib (2026-11-02)

Files: `detect/line_screen.py`, `detect/fusion.py`, `detect/run.py` (new version), `eval/fusion_eval.py` (line screen on
every train + calib audio variant, cached in `eval/out/fusion/lines/`), `eval/fusion_dev.py`, `eval/run_standard_test.py`;
tests `tests/test_line_screen.py`, `tests/test_fusion.py` (+ updated `test_interference_det.py`). Report
`eval/out/fusion/report_dev.{json,md}`; 598 recordings (243 not attacked), interference v2 on station-out-of-fold scores.

| line-screen cap | false-line rate, recordings without interference | worst station | line recall | full pass |
|---|---|---|---|---|
| none (feature band) | 0.118 | 1.000 | 0.317 | 0.701 |
| **f_edge − 100 Hz (chosen; Phase C band)** | **0.000** | **0.000** | 0.267 | 0.590 |
| feature band − 500 Hz | 0.118 | 1.000 | 0.305 | 0.675 |

| interference | n | any tier | quarantine tier | window model | line screen |
|---|---|---|---|---|---|
| segment (5–60 s) | 145 | 0.393 | 0.214 | 0.393 | 0.007 |
| full pass | 117 | 0.786 | 0.726 | 0.701 | 0.590 |

(Table numbers above are with the content evidence on; with it off (final, decision 100) the line-screen and
interference rows are unchanged, sequence recall 0.851, quarantine tier 0.690.)
Not-attacked recordings: not accepted 13.6 % (flag tier: interference model at station 4272, 75 recordings, and sequence
scores just above tau at 4272 / 5155 / 4434); **quarantined 1.2 %** (only 4272, 6.3 %); all other 20 stations 0.
Replay recall 1.00 (quarantine), sequence 0.86 (quarantine tier 0.75). Recording ECE 0.168. Sequence false alerts on
train are in-sample (profile fitted on train), so the train part of this estimate is optimistic.

| # | Decision | Reason |
|---|---|---|
| 95 | **Fusion design (Phase N)**: frames named by the locked naming rule; interference = v2 events + line screen, merged when the line holds the event's peak (±100 Hz); per-alert confidence = isotonic map (train + calib, alert correct vs not; v2 on OOF scores), line = dev precision (Laplace, 15/15 correct); recording confidence = 1 − Π(1 − p) | User decisions 1–3 |
| 96 | **Line screen band = Phase C band [f_low, f_edge − 100 Hz]**, chosen on dev by the fixed rule (highest line recall among caps with false-line rate ≤ 1 % and worst station ≤ 2 %): only f_edge qualified. Limit: TECHSAT 1B interference between f_edge (~10.15 kHz) and the feature-band top (10.9–18.5 kHz) is invisible to the screen | Band-edge roll-off makes a persistent "line" on every recording of some stations |
| 97 | **Two-tier verdict**: quarantine = replay, line screen, or a sequence / interference-model alert whose evidence ≥ a recording-level point chosen on dev for ≤ 1 % of recordings with a false alert of that family (sequence score ≥ 2.4893, v2 max probability ≥ 0.9906); flag = other alerts (review); accept = none. Replaces a fixed 0.9-confidence rule written before the dev numbers | A 1 % per-window / per-frame FPR gives 16 % (interference) and 5 % (sequence) of recordings with a false alert; a single-tier recording threshold would cost interference recall 0.50 → 0.32 |
| 98 | Standard test = the 10 frozen `combo_*` recipes per test observation (drawn before any detector, own seed each) + the clean and benign test variants as negatives, materialized as raw folders (audio.ogg in 4096-sample Vorbis blocks, demoddata with branch / rewrite copies, metadata with satellite, start, id) and run through `detect/run.py`. End-to-end check on a TRAIN combo: same v2 decision as on the frozen features (window score correlation 0.97: the written audio.ogg adds one Vorbis pass) | User decision 4, DETECTOR_SPEC 5 |
| 99 | **Incident, recorded**: the first standard-test launch stopped at its lock check, before reading any test data: the interference v2 lock lists the Phase L `detect/run.py`, replaced in Phase N (decision 91). Fix: for that lock only `detect/run.py` may differ (current run.py and all v2 model files are in the fusion lock); `eval/run_standard_test.py` changed, so the fusion lock was deleted and re-signed (07:39:06 → 07:39:42 UTC, no test result in between) | Transparency (rule 10) |
| 100 | **Sequence v2 (changes (a) and (b) only, user request 2026-11-02) failed its gate; v1 kept, content evidence disabled in the fusion.** `detect/sequence_v2.py`: (a) small satellite: pooled timestamp statistics, raised to its own p only for values it shows; (b) constant byte positions only for frame types seen at >= 2 stations, violation tail leave-one-station-out. `eval/sequence_v2.py loso` (train + calib, 23 station folds, each version's own OOF 1 % threshold): v1 worst station 2.65 %, FUNCUBE-1 recall 0.765; v2 14.35 % (5155), 0.726 -> gate (worst station lower AND FUNCUBE-1 recall not lower, fixed in advance) **failed**. (b) leaves no content position (no type constant at >= 2 train stations), so v2 = v1 without content plus (a). Fallback check under the same folds: own OOF tau 14.35 % / 0.726 (fails both criteria); at v1's frozen tau 2.3054: pooled FPR 1.87 % -> 1.19 %, stations > 2 % 3 -> 2, worst station 14.35 % unchanged (5155: gap / swap / timing evidence), FUNCUBE-1 recall 0.814 -> 0.765 (`eval/out/sequence_v2/loso_report.md`, `loso_at_v1_tau.json`). As instructed for a failed gate, the fusion uses v1 with the content evidence off (`sequence_disabled_evidence` in fusion.json, passed by `detect/run.py`); its check did not meet the worst-station criterion either, recorded here. v2 not locked, not tested | User decision; TECHSAT 1B still cannot reach tau with v1 (decision 81) |
| 101 | **Incident, recorded**: the user's sequence v2 request arrived after the first fusion lock (07:39:42 UTC) and during its standard-test run. The run was stopped after 4 of 11 test observations had been written; those files were moved UNREAD to `eval/out/unread_fusion_v1_partial/` and the superseded config / lock to `models/fusion_v1_superseded/`. Nothing was read from them; the reported test is the fresh run with the 08:07:04 lock | Transparency (rule 10) |

## Phase N — standard test on TEST (run once, 2026-11-02, lock 08:07:04 UTC)

`eval/run_standard_test.py run` then `report` -> `eval/out/standard_test/report.{json,md}`, `recordings.csv`. 11 test observations
with frames and audio x 10 frozen standard-scenario seeds = 110 attacked recordings (replay 5-20 frames + one sequence anomaly
+ narrowband interference at a hidden snr_db ~ U[-10, +30] dB), plus their 11 clean and 33 benign variants as negatives,
each written as a raw folder and run through `detect/run.py`. Per-observation mean [95 % bootstrap CI over observations]:

| metric | value |
|---|---|
| **false alarm: clean / benign recording not accepted** | **0 / 44** (0.000 [0.000, 0.000]); every station 0 |
| attacked recording quarantined | 110 / 110 |
| replay detected | 1.000 [1.000, 1.000]; frames P / R / F1 0.999 / 1.000 / 1.000; delay 0 s |
| sequence anomaly detected / named correctly | 0.309 [0.182, 0.446] / 0.273 [0.145, 0.400] |
| interference detected | 0.582 [0.464, 0.709] (segment 0.515, full pass 0.690) |
| all three detected | 0.136 [0.064, 0.209] |
| recording ECE | 0.001 (every attacked recording carries a replay alert, so confidence is near 0 or 1) |

- Full-pass interference: window model 0.571, line screen 0.548, either 0.690; segments: model only (line screen 0).
  By snr_db: [-10, 0) 0.89, [0, 10) 0.69, [10, 20) 0.66, [20, 30] 0.04. Tone 0.59, noise 0.57. Per satellite: TECHSAT 1B 0.72, FUNCUBE-1 0.42.
- Sequence by subtype (detected): duplicate 0.58, forged 0.54, counter_jump 0.44, swap 0.16, timing 0.13, deletion 0.
  TECHSAT 1B 0.17 (v1 cannot alert on its own evidence, decision 81; duplicates come from the replay detector), FUNCUBE-1 0.48.
- False alerts inside attacked recordings: 0.027 frame alerts and 0 interference alerts per recording.
- Latency: replay alert on the first copy (0 s); interference window model median 9 s (max 226 s); line screen decides at the end
  of the pass. Runtime per recording median 28.9 s (audio front 20.5 s, line screen 7.7 s, frame detectors < 0.2 s), max 85 s
  (4 workers in parallel); peak traced memory max 563 MB.
- Limits: only 11 test observations from 5 stations; the 0 / 44 false alarms bound the recording false-alarm rate at about
  6.6 % (95 % one-sided, rule of three), not at 1 %. Interference above +20 dB SIR is not detected.

## Phase M — trust layer: per-frame envelope (2026-11-02)

`trust/envelope.py` (seal / verify / stream file / bench CLI), `tests/test_envelope.py` (9 tests). Envelope = magic, 16 B session
id, u64 session counter, u64 timestamp (ms), SHA-256 of the previous envelope (chain), u16 length, frame, HMAC-SHA256 tag
over everything before it. Strict in-order verifier; a rejected envelope leaves the state unchanged. Deterministic results:
replay -> `replay`; reorder -> `gap` for the early frame, the stream resumes when it is re-sent in order; deletion -> `gap` for
every later frame; one flipped payload or header bit -> `bad_tag`; forgery without the key -> `bad_tag`; other session ->
`wrong_session`; valid tag but wrong chain -> `chain`.
Overhead (`eval/out/trust/overhead.json`, 20 000 frames, Python 3.14, idle machine): **102 bytes per frame** (70 header + 32 tag;
232 % of a 44-byte TECHSAT 1B beacon, 40 % of a 256-byte FUNCUBE-1 frame); seal 4.2 µs, verify 4.4–4.7 µs per frame;
Ed25519 manifest sign 50 µs, verify 99 µs (64-byte signature). `detect/run.py` refuses to run when the signed fusion lock does
not verify (`--no-verify` for development). Losses before the station are the detectors' job; after the envelope the
transport must be lossless, so a gap is rejected.

## Phase O — near-copy replay evasion and results table (2026-11-02)

`eval/evasion_near_copy.py` (run once on TEST, locked fusion, nothing retuned), `attacks.replay.near_copy`, `tests/test_evasion_near_copy.py`.
The 133 TEST recipes containing a replay (110 combos, 22 replay, 1 cross-pass evasion) applied twice: exact copies (control) and
near-copies (one seeded byte changed per replayed frame outside the counter field: FUNCUBE-1 byte 0 and seqnumber kept, TECHSAT 1B
counter and ASCII marker kept). Frames-only raw folders through `detect/run.py`. Report `eval/out/evasion_near_copy/report.md`.

| mode | replay detected (named) | any alert on a replayed frame | recording not accepted |
|---|---|---|---|
| exact | 1.000 (133 / 133) | 1.000 | 1.000 |
| near-copy | **0.000 (0 / 133)** | 0.455 [0.182, 0.727] | 0.523 [0.265, 0.780] (FUNCUBE-1 1.00, TECHSAT 1B 0.12) |

| # | Decision | Reason |
|---|---|---|
| 102 | **Near-copy finding (diagnosed after the run, nothing retuned)**: without hash matches the copies enter the counter-time line fit; when they are a large share of the pass the line fails (`line_ok` False, e.g. 56 % on line), which disables counter-regression evidence. Phase 2 fix: robust line (dominant line + lower-counter off-line cluster = copies); new version, new lock | Decision 79 |
| 103 | `eval/results.{md,json}`: one table of every challenge metric from the existing reports; count CIs Clopper-Pearson 95 %, others per-observation bootstrap; 0 / 44 bound = rule of three 6.8 %, exact 6.6 % | Phase O request |

## Phase P — packaging (2026-11-02)

Files: `attacks/run_attack.py` (replay / sequence --subtype / interference --shape --f0 --snr-db / standard on a raw folder ->
attacked folder, separate `<out>.truth.json`, before / after spectrogram PNG), `scripts/demo.py` (= `make demo`), `data/make_sample.py`
-> `data/sample/{14932721 (FUNCUBE-1, train), 15080917 (TECHSAT 1B, test)}`, `requirements.txt`, `environment.yml`, `setup.sh`,
`Makefile`, `README.md`, `eval/results.py`, `.gitattributes`; tests `tests/test_packaging.py`, `tests/test_results.py` (suite 151 pass).
Demo (fresh venv from requirements.txt, Python 3.14, Windows): both clean passes accept, both attacked passes quarantined, 111 s.

| # | Decision | Reason |
|---|---|---|
| 104 | **Pins for Python 3.11–3.14**: numpy 2.4.6, scipy 1.17.1, contourpy 1.3.3 instead of the dev venv's numpy 2.5.4, scipy 1.18.1, contourpy 1.4.0 (those need Python >= 3.12); scikit-learn stays 1.9.1 (the model pickles). Checked: `pip download --only-binary` resolves 35 manylinux wheels for each of 3.11, 3.12, 3.13, 3.14; a fresh 3.14 venv with these exact pins runs the demo and the tests, model.pkl loads without a version warning. Not executed on a Linux machine (none available here) | The jury's Kali / Ubuntu Python may be 3.11–3.13 |
| 105 | **`.gitattributes`: every file listed in a `models/*/lock.json`, the locks, `data/splits.json`, `data/raw/manifest.csv` and `data/recipes/**` are `-text`** (stored and checked out byte-exact) | With `core.autocrlf=true` the working tree had CRLF files while git stored LF: on a Linux clone 13 of the 24 fusion-lock files would fail their SHA-256 and `detect/run.py` / `make demo` would refuse to run. No file content or lock changed; staged blobs now match the active locks (fusion, replay, sequence) and the DATA.md fingerprints. The 5 remaining mismatches are the known ones (superseded fusion v1 lock; Phase L `detect/run.py` in the interference locks, decisions 91 and 99) |
| 106 | **Sample**: smallest audio + frame observation per satellite; TECHSAT 1B has frame-QC observations only in test (decision 54), so its sample is test observation 15080917 (demo only, no metric uses it). Sanitized metadata = allowlist (ids, times, transmitter, numeric station id, TLE); removed field names listed in the file | Decision 29 |

**Anonymity scan (2026-11-02)**, all tracked + new files and the full git history (`git log --all -p`), for: usernames, emails,
absolute paths (`C:\Users`, `/home/`), the local machine name, and all 106 observer / station names and coordinates found in
`data/raw/*/metadata.json`. Files: no finding (only the rule text in CLAUDE.md and metadata field NAMES in the sanitizer, its test
and the sample's `removed_fields` list). History content and commit messages: no finding. **Finding: every commit's author and
committer is a GitHub username with its users.noreply.github.com email** (4 commits). Not rewritten, as instructed; future
commits can use an anonymous identity (`git config user.name` / `user.email`), and the anonymous submission should be an export
without history (e.g. `git archive`) or a fresh repository.
