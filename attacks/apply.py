"""Apply a recipe to clean data, in memory (DATASET_SPEC.md 6, principle 5: recipes, not copies).

clean observation dict (see data/build_features.load_clean):
  obs_id, norad, start (datetime), sr, audio (float32 or None), frames (canonical stream, label
  fields set to clean), timing_reliable, f_low, f_edge, f_drop, ref (attacks.interference
  .reference_powers of the clean audio, or None), encode_counter, decode_counter.
recipe: {obs_id, variant_id, split, seed, operations: [{type, ...}, ...]}
Operation types: replay, sequence, interference, gain, noise_rise, fading, edge_attenuation.
Randomness inside operations (noise waveforms) comes from numpy Generator(seed + op index).
Returns {"audio", "frames" (not finalized), "events"}.
"""

from __future__ import annotations

import numpy as np

from attacks import benign
from attacks.interference import apply_interference
from attacks.replay import apply_replay
from attacks.sequence import apply_sequence


def apply_recipe(recipe: dict, clean: dict, load_source=None) -> dict:
    """load_source(obs_id) -> clean dict, needed only for cross_pass replays."""
    audio = clean["audio"]
    frames = [dict(f) for f in clean["frames"]]
    events = []
    sr = clean["sr"]
    for k, op in enumerate(recipe["operations"]):
        rng = np.random.default_rng([recipe["seed"], k])
        t = op["type"]
        if t == "replay":
            source = clean if op.get("source_obs") in (None, clean["obs_id"]) else load_source(op["source_obs"])
            frames, audio, ev = apply_replay(frames, audio, sr, op, clean, source)
        elif t == "sequence":
            frames, ev = apply_sequence(frames, op, clean, clean["encode_counter"], clean["decode_counter"]), None
        elif t == "interference":
            audio, ev = apply_interference(audio, sr, op, clean["ref"], rng)
        elif t == "gain":
            audio, ev = benign.gain(audio, sr, op)
        elif t == "noise_rise":
            audio, ev = benign.noise_rise(audio, sr, op, clean["ref"], clean["f_low"], clean["f_edge"], rng)
        elif t == "fading":
            audio, ev = benign.fading(audio, sr, op)
        elif t == "edge_attenuation":
            audio, ev = benign.edge_attenuation(audio, sr, op)
        else:
            raise ValueError(f"unknown operation {t}")
        if ev is not None:
            events.append(ev)
    return {"audio": audio, "frames": frames, "events": events}
