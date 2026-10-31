# Progress log — dataset build (DATASET_SPEC.md)

Last update: 2026-10-31 (Phase H shortcut test revised, decisions 63–67).

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
