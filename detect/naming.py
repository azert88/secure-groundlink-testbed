"""Names of frame alerts when the replay and sequence detectors both look at a frame (used by fusion, Phase N).

A replayed BLOCK (several copies sharing one time shift, or frames of an earlier pass) is a replay; a single
repeated frame is a sequence 'duplicate', since a replay of the standard test is 5-20 frames; the other
sequence evidence is named after the anomaly it reveals. First matching rule wins:
  1 replay intra_run or regress_run            -> replay   (an intra-pass twin is the direct explanation, even
                                                              when another station's recording also matches)
  2 replay cross_run / cross_single            -> replay_cross_pass
  3 sequence dup_payload or replay intra_single -> duplicate
  4 sequence same_counter (near copy)          -> forged
  5 sequence inversion (swap pattern / generic) -> swap
  6 sequence time_dev, order_spike or counter_clash -> timing or counter_jump (timing_or_jump)
  7 sequence gap                               -> deletion
  8 sequence content                           -> forged
  9 replay regress_single                      -> counter_anomaly
Only evidence of a detector that alerted on the frame is used. No alert -> None.
"""

from __future__ import annotations

PHASE_MIN_S = 2.0      # frame time off the pass rhythm by this much: the time moved
MULT_TOL_S = 1.0       # deviation within this of a whole number of counter counts


def timing_or_jump(phase_dev_s: float | None, dev_s: float | None, s_per_count: float | None) -> str:
    """A timing shift moves the frame time (phase on the pass rhythm, or a deviation that is not a whole number
    of >= 2 counter counts); a counter jump moves the counter by whole counts and leaves the time on the rhythm."""
    if phase_dev_s is not None and abs(phase_dev_s) >= PHASE_MIN_S:
        return "timing"
    if dev_s is None or s_per_count is None:
        return "counter_jump"
    if s_per_count >= 2:
        k = round(abs(dev_s) / s_per_count)
        if k < 2 or abs(abs(dev_s) - k * s_per_count) > MULT_TOL_S:
            return "timing"
    return "counter_jump"


def name_frame(replay_alert: bool, replay_kinds: list[str], seq_alert: bool, seq_kinds: list[str],
               phase_dev_s: float | None = None, dev_s: float | None = None,
               s_per_count: float | None = None) -> str | None:
    r = set(replay_kinds) if replay_alert else set()
    q = set(seq_kinds) if seq_alert else set()
    if not (r or q):
        return None
    if r & {"intra_run", "regress_run"}:
        return "replay"
    if r & {"cross_run", "cross_single"}:
        return "replay_cross_pass"
    if "dup_payload" in q or "intra_single" in r:
        return "duplicate"
    if "same_counter" in q:
        return "forged"
    if "inversion" in q:
        return "swap"
    if q & {"time_dev", "order_spike", "counter_clash"}:
        return timing_or_jump(phase_dev_s, dev_s, s_per_count)
    if "gap" in q:
        return "deletion"
    if "content" in q:
        return "forged"
    if "regress_single" in r:
        return "counter_anomaly"
    return "anomaly"
