"""Within-observation, benign-matched shortcut test (DATASET_SPEC.md 8.3, decisions 63-65).

The test asks whether a model could tell attacked from not-attacked variants from cues that a
processing trace would leave (file duration, number of windows, post-chain peak and RMS, mean
window power_db), WITHOUT knowing which observation a file comes from.

Why within-observation: attacks are not spread evenly over observations (test observations carry
many more attacked variants, and TECHSAT 1B test passes are longer), so a pooled comparison
learns "which observation" instead of "which processing". Benign conditions (gain, noise rise,
fading, edge attenuation) change the level by design and are spread unevenly too (most
not-attacked variants carry one, few attacked variants do). Weights are therefore balanced inside
each stratum (observation x has-a-benign-condition): every stratum that holds both classes gets
total weight 1, half on its attacked and half on its not-attacked variants. Every variant is also
compared with the clean variant of its own observation (paired deltas).

Rules (pass if |AUC - 0.5| < validation.shortcut_auc_dev_max for every scope):
  (a) structure cues (duration_s, n_windows): every comparison and every SNR bin;
  (b) level cues (peak_abs, rms_db, mean_power_db), benign-matched: replay, sequence,
      interference with snr_db >= validation.shortcut_level_min_snr_db, power-neutral attacked
      vs not attacked (negatives = clean + benign variants of the same stratum);
  (c) reported as expected physical effects, not rules: level cues of interference below that SIR
      (strong interference raises the file level) and of benign vs clean (benign conditions
      change the level by design).
Classifiers (logistic regression, random forest; benign-matched weighted fit and weighted AUC;
train+calib -> test and 5-fold by observation): structure cues on all variants and all cues on
power-neutral variants are rules; all cues on all variants is reported.

CLI: reads only cached inputs (validation.cues_dir, Phase G windows.csv, recipes) and writes JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

NOT_ATTACK_GROUPS = ("clean", "benign")
BENIGN_OPS = ("gain", "noise_rise", "fading", "edge_attenuation")
STRUCTURE_CUES = ("duration_s", "n_windows")
LEVEL_CUES = ("peak_abs", "rms_db", "mean_power_db")


# ---------------------------------------------------------------- inputs
def load_recipes(recipes_dir: Path) -> dict:
    """{(obs_id, variant_id): recipe} for every recipe JSON under recipes_dir/<obs_id>/."""
    out = {}
    for d in sorted(p for p in recipes_dir.iterdir() if p.is_dir()):
        for p in sorted(d.glob("*.json")):
            out[(int(d.name), p.stem)] = json.loads(p.read_text(encoding="utf-8"))
    return out


def load_cached_cues(cues_dir: Path, recipes_dir: Path, digest) -> pd.DataFrame:
    """Cached post-chain cues; fails if a cache file was built from other recipes."""
    frames = []
    for d in sorted(p for p in recipes_dir.iterdir() if p.is_dir()):
        p = cues_dir / f"{d.name}.csv"
        if not p.exists():
            continue                                  # frame-only observation: no audio variants
        df = pd.read_csv(p)
        if not (df.recipes_sha256 == digest(d)).all():
            raise SystemExit(f"stale cue cache {p.name}: recipes changed, run data/validate.py in full")
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def window_aggregates(features_dir: Path, obs_ids) -> pd.DataFrame:
    """n_windows and mean power_db per (obs_id, variant_id) from the Phase G window tables."""
    w = pd.concat([pd.read_csv(features_dir / str(i) / "windows.csv", usecols=["obs_id", "variant_id", "win_idx", "power_db"])
                   for i in sorted(set(obs_ids))], ignore_index=True)
    return w.groupby(["obs_id", "variant_id"]).agg(n_windows=("win_idx", "size"), mean_power_db=("power_db", "mean"))


def assemble(cues: pd.DataFrame, wagg: pd.DataFrame, recipes: dict) -> pd.DataFrame:
    """One row per audio variant: cues, recipe info, y, min_snr_db, has_benign, paired deltas."""
    sc = cues.drop(columns=["recipes_sha256"], errors="ignore").join(wagg, on=["obs_id", "variant_id"])
    recs = [recipes[(i, v)] for i, v in zip(sc.obs_id, sc.variant_id)]
    sc["group"] = [r["group"] for r in recs]
    sc["split"] = [r["split"] for r in recs]
    sc["norad"] = [r["norad"] for r in recs]
    sc["y"] = (~sc.group.isin(NOT_ATTACK_GROUPS)).astype(int)
    snr = [[o["snr_db"] for o in r["operations"] if o["type"] == "interference"] for r in recs]
    sc["min_snr_db"] = [min(s) if s else np.nan for s in snr]
    sc["replay_audio"] = [any(o["type"] == "replay" and o.get("audio") for o in r["operations"]) for r in recs]
    sc["has_benign"] = [any(o["type"] in BENIGN_OPS for o in r["operations"]) for r in recs]
    clean = sc[sc.group == "clean"].set_index("obs_id")
    missing = set(sc.obs_id) - set(clean.index)
    if missing:
        raise ValueError(f"no clean variant for observations {sorted(missing)}")
    for c in STRUCTURE_CUES + LEVEL_CUES:
        sc["d_" + c] = sc[c].to_numpy(float) - clean.loc[sc.obs_id, c].to_numpy(float)
    return sc


def power_class(sc: pd.DataFrame, min_snr: float) -> pd.Series:
    """none = adds no interference power; high_sir = all interference >= min_snr; low_sir otherwise."""
    return pd.Series(np.where(sc.min_snr_db.isna(), "none", np.where(sc.min_snr_db >= min_snr, "high_sir", "low_sir")),
                     index=sc.index)


# ---------------------------------------------------------------- weighted AUC
def strata(d: pd.DataFrame, matched: bool = True) -> np.ndarray:
    """Weighting stratum: observation x has-a-benign-condition (or the observation alone)."""
    o = d.obs_id.to_numpy().astype(np.int64)
    return o * 2 + d.has_benign.to_numpy().astype(np.int64) if matched else o


def balanced_weights(stratum, y) -> np.ndarray:
    """Per stratum: attacked and not-attacked variants each carry weight 1/2 (total 1 per stratum).

    Strata with only one class get weight 0: they say nothing about attacked vs not."""
    df = pd.DataFrame({"o": np.asarray(stratum), "y": np.asarray(y)})
    n = df.groupby(["o", "y"]).y.transform("size").to_numpy(float)
    both = df.groupby("o").y.transform("nunique").to_numpy() == 2
    return np.where(both, 0.5 / n, 0.0)


def weighted_auc(y, score, w) -> float | None:
    y, score, w = np.asarray(y), np.asarray(score, float), np.asarray(w, float)
    m = (w > 0) & ~np.isnan(score)
    if len(set(y[m])) < 2:
        return None
    return round(float(roc_auc_score(y[m], score[m], sample_weight=w[m])), 4)


def comparisons(sc: pd.DataFrame, min_snr: float) -> list[tuple[str, pd.Series, pd.Series, bool, bool]]:
    """(name, positives, negatives, level cues are a rule, benign-matched strata)."""
    g, pc = sc.group, power_class(sc, min_snr)
    clean, notatk = g == "clean", g.isin(NOT_ATTACK_GROUPS)
    return [
        ("replay vs not attacked", g == "replay", notatk, True, True),
        ("replay (audio pasted) vs not attacked", (g == "replay") & sc.replay_audio, notatk, True, True),
        ("sequence vs not attacked", g == "sequence", notatk, True, True),
        (f"interference snr_db >= {min_snr:+.0f} vs not attacked", pc == "high_sir", notatk, True, True),
        ("power-neutral attacked vs not attacked", (sc.y == 1) & pc.isin(["none", "high_sir"]), notatk, True, True),
        ("interference vs not attacked", g == "interference", notatk, False, True),
        ("snrgrid vs not attacked", g == "snrgrid", notatk, False, True),
        ("evasion vs not attacked", g == "evasion", notatk, False, True),
        ("combo vs not attacked", g == "combo", notatk, False, True),
        ("attacked vs not attacked", sc.y == 1, notatk, False, True),
        ("benign vs clean (physical, not matched)", g == "benign", clean, False, False),
    ]


def cue_aucs(d: pd.DataFrame, pos: pd.Series, neg: pd.Series, cues, matched: bool = True) -> dict:
    m = (pos | neg).to_numpy()
    sub, y = d[m], pos.to_numpy()[m].astype(int)
    w = balanced_weights(strata(sub, matched), y)
    return {"n_pos": int(y.sum()), "n_pos_used": int(((w > 0) & (y == 1)).sum()),
            "n_obs": int(sub.obs_id[w > 0].nunique()), **{c: weighted_auc(y, sub[c], w) for c in cues}}


def deltas(d: pd.DataFrame) -> dict:
    """Paired deltas vs the variant's own clean: structure = variants that differ; level = p5, median, p95."""
    out = {f"n_{c}_differs": int((d["d_" + c] != 0).sum()) for c in STRUCTURE_CUES}
    for c in LEVEL_CUES:
        v = d["d_" + c].to_numpy(float)
        out[f"d_{c}"] = [round(float(np.quantile(v, q)), 4) for q in (0.05, 0.5, 0.95)] if len(v) else None
    return out


def snr_bins(lo: float, hi: float, width: float) -> list[tuple[float, float]]:
    edges = np.arange(np.floor(lo / width) * width, hi + width, width)
    return [(float(a), float(b)) for a, b in zip(edges[:-1], edges[1:])]


# ---------------------------------------------------------------- classifiers
def _fit(model, X, y, w):
    if hasattr(model, "steps"):
        return model.fit(X, y, **{f"{model.steps[-1][0]}__sample_weight": w})
    return model.fit(X, y, sample_weight=w)


def classifier_test(d: pd.DataFrame, cues, seed: int, n_trees: int = 300) -> list[dict]:
    """Benign-matched weighted fit and weighted AUC; train+calib -> test and 5-fold GroupKFold by observation."""
    models = {"logreg": lambda: make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)),
              "forest": lambda: RandomForestClassifier(n_estimators=n_trees, min_samples_leaf=5, random_state=seed, n_jobs=1)}
    X, y = d[list(cues)].to_numpy(float), d.y.to_numpy()
    w = balanced_weights(strata(d), y)
    out = []
    for name, make in models.items():
        tr = d.split.isin(["train", "calib"]).to_numpy() & (w > 0)
        te = (d.split == "test").to_numpy() & (w > 0)
        if len(set(y[tr])) == 2 and len(set(y[te])) == 2:
            p = _fit(make(), X[tr], y[tr], w[tr]).predict_proba(X[te])[:, 1]
            out.append({"model": name, "protocol": "train+calib -> test", "n": int(te.sum()), "auc": weighted_auc(y[te], p, w[te])})
        keep = w > 0
        Xk, yk, wk, gk = X[keep], y[keep], w[keep], d.obs_id.to_numpy()[keep]
        if len(set(gk)) >= 5:
            oof = np.full(len(yk), np.nan)
            for a, b in GroupKFold(5).split(Xk, yk, gk):
                oof[b] = _fit(make(), Xk[a], yk[a], wk[a]).predict_proba(Xk[b])[:, 1]
            out.append({"model": name, "protocol": "5-fold by observation", "n": int(keep.sum()), "auc": weighted_auc(yk, oof, wk)})
    return out


# ---------------------------------------------------------------- composition confound
def composition(sc: pd.DataFrame) -> dict:
    """Attack density and benign-condition share per split (the confounds a pooled test would learn)."""
    per_obs = sc.groupby("obs_id").agg(norad=("norad", "first"), split=("split", "first"), duration_s=("duration_s", "first"),
                                       n_attacked=("y", "sum"), n_variants=("y", "size"))
    per_obs["attacked_frac"] = per_obs.n_attacked / per_obs.n_variants
    tab = (per_obs.groupby(["norad", "split"])
           .agg(n_obs=("duration_s", "size"), duration_median_s=("duration_s", "median"),
                attacked_per_obs=("n_attacked", "mean"), not_attacked_per_obs=("n_variants", "mean"),
                attacked_frac=("attacked_frac", "mean")).reset_index())
    tab["not_attacked_per_obs"] -= tab.attacked_per_obs
    ben = (sc.groupby(["norad", "split", "y"]).has_benign.mean().unstack()
           .rename(columns={0: "benign_share_not_attacked", 1: "benign_share_attacked"}))
    tab = tab.join(ben, on=["norad", "split"])
    share = sc.groupby("y").has_benign.mean()
    rho = {str(n): round(float(g.duration_s.corr(g.attacked_frac, method="spearman")), 3) for n, g in per_obs.groupby("norad")}
    return {"per_split": tab.round(3).to_dict(orient="records"), "spearman_duration_vs_attacked_frac": rho,
            "benign_share": {"not_attacked": round(float(share.get(0, np.nan)), 3),
                             "attacked": round(float(share.get(1, np.nan)), 3)}}


def pooled_unweighted(sc: pd.DataFrame, cues) -> dict:
    """The original (confounded) pooled single-cue AUCs, kept for reference."""
    return {c: round(float(roc_auc_score(sc.y, sc[c])), 4) for c in cues}


# ---------------------------------------------------------------- full test
def shortcut_report(sc: pd.DataFrame, vc: dict, seed: int) -> dict:
    dev_max, min_snr, width = vc["shortcut_auc_dev_max"], vc["shortcut_level_min_snr_db"], vc["snr_bin_db"]
    cues = list(STRUCTURE_CUES + LEVEL_CUES)
    n_trees = vc.get("shortcut_forest_trees", 300)
    fails, comp_rows, bin_rows, clf_rows = [], [], [], []

    def check(scope, what, cue, auc, rule, n_pos, pure=None):
        """Record a rule failure; pure() gives the same test on the no-benign-condition stratum only."""
        dev = None if auc is None else round(abs(auc - 0.5), 4)
        if rule and dev is not None and dev >= dev_max:
            p = pure() if pure is not None else None
            fails.append({"scope": scope, "test": what, "cue": cue, "auc": auc, "n_pos_used": n_pos, "auc_pure_stratum": p,
                          "chain_trace": not (p is not None and abs(p - 0.5) < dev_max)})
        return dev

    snr_all = sc.min_snr_db.dropna()
    bins = snr_bins(snr_all.min(), snr_all.max(), width) if len(snr_all) else []
    for scope, d in [("both", sc)] + [(str(n), g) for n, g in sc.groupby("norad")]:
        for name, pos, neg, level_rule, matched in comparisons(d, min_snr):
            if not pos.any():
                continue
            r = cue_aucs(d, pos, neg, cues, matched)
            for c in cues:
                check(scope, name, c, r[c], c in STRUCTURE_CUES or level_rule, r["n_pos_used"],
                      lambda c=c, pos=pos, neg=neg: cue_aucs(d, pos & ~d.has_benign, neg & ~d.has_benign, [c])[c])
            comp_rows.append({"scope": scope, "comparison": name, "level_rule": level_rule, **r, **deltas(d[pos])})
        for lo, hi in bins:
            pos = (d.min_snr_db >= lo) & (d.min_snr_db < hi)
            if not pos.any():
                continue
            r = cue_aucs(d, pos, d.group.isin(NOT_ATTACK_GROUPS), cues)
            level_rule = lo >= min_snr
            notatk = d.group.isin(NOT_ATTACK_GROUPS)
            for c in cues:
                check(scope, f"snr_db [{lo:+.0f}, {hi:+.0f})", c, r[c], c in STRUCTURE_CUES or level_rule, r["n_pos_used"],
                      lambda c=c, pos=pos, notatk=notatk: cue_aucs(d, pos & ~d.has_benign, notatk & ~d.has_benign, [c])[c])
            bin_rows.append({"scope": scope, "snr_db_bin": f"[{lo:+.0f}, {hi:+.0f})", "level_rule": level_rule,
                             "status": "rule" if level_rule else "expected physical effect", **r, **deltas(d[pos])})
        pn = d.group.isin(NOT_ATTACK_GROUPS) | power_class(d, min_snr).isin(["none", "high_sir"])
        for set_name, sub, cs, rule in [("structure cues, all variants", d, list(STRUCTURE_CUES), True),
                                        ("all cues, power-neutral variants", d[pn], cues, True),
                                        ("all cues, all variants (reported)", d, cues, False)]:
            rows = classifier_test(sub, cs, seed, n_trees)
            pure_rows = {}
            for row in rows:
                row = {"scope": scope, "set": set_name, "rule": rule, **row}

                def pure(row=row, sub=sub, cs=cs):
                    if not pure_rows:
                        pure_rows.update({(x["model"], x["protocol"]): x["auc"]
                                          for x in classifier_test(sub[~sub.has_benign], cs, seed, n_trees)})
                    return pure_rows.get((row["model"], row["protocol"]))
                row["dev"] = check(scope, f"classifier {set_name} {row['model']} {row['protocol']}", "+".join(cs),
                                   row["auc"], rule, row["n"], pure)
                clf_rows.append(row)
    devs = [abs(r[c] - 0.5) for r in comp_rows + bin_rows for c in cues
            if r[c] is not None and (c in STRUCTURE_CUES or r["level_rule"])]
    devs += [r["dev"] for r in clf_rows if r["rule"] and r["dev"] is not None]
    n_struct = {c: int((sc["d_" + c] != 0).sum()) for c in STRUCTURE_CUES}
    traces = [f for f in fails if f["chain_trace"]]
    # pass = no processing-chain trace: structure cues equal to the own clean everywhere, and every rule failure
    # disappears on the no-benign-condition stratum (then it is a composition / small-sample limitation)
    return {"ok": not traces and not any(n_struct.values()), "rule": f"|AUC - 0.5| < {dev_max}", "max_dev_ruled": round(max(devs), 4) if devs else None,
            "level_min_snr_db": min_snr, "failures": fails, "n_rule_failures": len(fails),
            "n_chain_trace_failures": len(traces), "n_variants": int(len(sc)), "n_attacked": int(sc.y.sum()),
            "n_structure_differs_from_own_clean": n_struct,
            "peak_abs_max": round(float(sc.peak_abs.max()), 4),
            "peak_abs_max_clean": round(float(sc.peak_abs[sc.group == "clean"].max()), 4),
            "comparisons": comp_rows, "snr_bins": bin_rows, "classifiers": clf_rows,
            "composition": composition(sc), "pooled_unweighted_auc_reference": pooled_unweighted(sc, cues)}


# ---------------------------------------------------------------- markdown
def _md(rows: list[dict], cols: list[str]) -> list[str]:
    fmt = lambda x: "" if x is None else (f"{x:.3f}" if isinstance(x, float) else str(x))
    return (["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
            + ["| " + " | ".join(fmt(r.get(c)) for c in cols) + " |" for r in rows] + [""])


def shortcut_markdown(sc: dict, limitations: list[str] | None = None) -> list[str]:
    """Section 3 of data/validation_report.md."""
    cues = list(STRUCTURE_CUES + LEVEL_CUES)
    comp = sc["composition"]
    lim = [f for f in sc["failures"] if not f["chain_trace"]]
    status = "FAIL" if not sc["ok"] else (f"PASS with {len(lim)} documented limitations" if lim else "PASS")
    L = [f"## 3. Shortcut test (within observation, benign-matched) — {status} "
         f"(rule {sc['rule']}, max ruled deviation {sc['max_dev_ruled']})", "",
         "Weights are balanced inside each stratum (observation x has-a-benign-condition): every stratum with both "
         "classes gets total weight 1, half attacked, half not attacked (weighted fit and weighted ROC AUC). The test "
         "therefore cannot learn which observation a file comes from, nor whether it carries a benign condition. "
         f"Rules: structure cues ({', '.join(STRUCTURE_CUES)}) for every comparison and SNR bin; level cues "
         f"({', '.join(LEVEL_CUES)}) for replay, sequence, power-neutral attacks and interference with snr_db >= "
         f"{sc['level_min_snr_db']:+.0f} dB. Reported as expected physical effects, not ruled: level cues of stronger "
         "interference and of benign vs clean (benign conditions change the level by design). "
         "AUC > 0.5 = cue higher in the attacked (or benign) variants. n_pos_used = positives in a stratum that also "
         "holds negatives. Pass = no processing-chain trace: structure cues equal to the variant's own clean "
         "everywhere, and every rule failure disappears on the stratum without benign conditions; such a failure "
         "is a composition / small-sample limitation and is listed with its numbers.", "",
         f"{sc['n_variants']} audio variants, {sc['n_attacked']} attacked. Structure cues that differ from the variant's "
         f"own clean: {sc['n_structure_differs_from_own_clean']}.", "",
         "Amplitude: processing is in float and nothing is clipped. Decoded audio is not bounded to ±1 (known property "
         f"of the SatNOGS audio): post-chain peak |x| up to {sc['peak_abs_max_clean']} on clean variants and "
         f"{sc['peak_abs_max']} over all variants. The detection features are mostly scale-invariant dB ratios "
         "(peak_to_median_db, max_dev_from_obs_median_db, ...); absolute-level features (power_db, noise_floor_db) are "
         "checked in the detector phase (feature importances).", ""]
    tr = [f for f in sc["failures"] if f["chain_trace"]]
    if tr:
        L += ["Failures that may be processing-chain traces:", ""] + [
            f"- {f['scope']} | {f['test']} | {f['cue']}: AUC {f['auc']} (n_pos_used {f['n_pos_used']}, "
            f"pure stratum {f['auc_pure_stratum']})" for f in tr] + [""]
    if lim:
        L += ["Documented limitations (rule failures that vanish without benign conditions; not chain traces):", ""] + [
            f"- {f['scope']} | {f['test']} | {f['cue']}: AUC {f['auc']} (n_pos_used {f['n_pos_used']}); "
            f"no-benign-condition stratum AUC {f['auc_pure_stratum']}" for f in lim] + [""]
    if limitations:
        L += [f"- {x}" for x in limitations] + [""]
    L += ["### 3.1 Composition confounds (why the test is within observation and benign-matched)", "",
          "Attack density and benign-condition share per split. A pooled test confuses observation identity and "
          "benign status with attack status: Spearman ρ(duration, attacked fraction) = "
          + ", ".join(f"{k}: {v}" for k, v in comp["spearman_duration_vs_attacked_frac"].items())
          + f". Share of variants carrying a benign condition: not attacked {comp['benign_share']['not_attacked']}, "
          f"attacked {comp['benign_share']['attacked']}.", ""]
    L += _md(comp["per_split"], ["norad", "split", "n_obs", "duration_median_s", "attacked_per_obs", "not_attacked_per_obs",
                                 "attacked_frac", "benign_share_not_attacked", "benign_share_attacked"])
    L += ["Pooled unweighted single-cue AUC (attacked vs not, confounded, for reference only): "
          + ", ".join(f"{k} {v}" for k, v in sc["pooled_unweighted_auc_reference"].items()) + ".", ""]
    L += ["### 3.2 Single-cue AUC per comparison", "",
          "level_rule = level cues are a rule for this comparison. Paired deltas vs own clean: [p5, median, p95].", ""]
    L += _md(sc["comparisons"], ["scope", "comparison", "level_rule", "n_pos", "n_pos_used", "n_obs", *cues,
                                 "d_rms_db", "d_mean_power_db"])
    L += ["### 3.3 Interference: cue AUC per snr_db bin (variants with interference vs not attacked, benign-matched)", "",
          "snr_db is the lowest SIR of the variant's interference operations.", ""]
    L += _md(sc["snr_bins"], ["scope", "snr_db_bin", "status", "n_pos", "n_pos_used", "n_obs", *cues,
                              "d_peak_abs", "d_rms_db", "d_mean_power_db"])
    L += ["### 3.4 Classifiers (benign-matched weighted fit and weighted AUC)", ""]
    L += _md(sc["classifiers"], ["scope", "set", "rule", "model", "protocol", "n", "auc", "dev"])
    return L


# ---------------------------------------------------------------- CLI
def from_cache(cfg: dict, recipes: dict | None = None) -> pd.DataFrame:
    """Assemble the shortcut table from cached cues and Phase G windows only (no audio processing)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from build_features import recipes_digest  # noqa: E402  (same digest as the cue cache)
    rdir = Path(cfg["attacks"]["recipes_dir"])
    recipes = recipes if recipes is not None else load_recipes(rdir)
    cues = load_cached_cues(Path(cfg["validation"]["cues_dir"]), rdir, recipes_digest)
    return assemble(cues, window_aggregates(Path(cfg["features"]["out_dir"]), cues.obs_id), recipes)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Within-observation shortcut test from cached cues (no audio processing).")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--out", default="data/validation/shortcut.json")
    args = ap.parse_args(argv)
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    rep = shortcut_report(from_cache(cfg), cfg["validation"], cfg["seed"])
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(rep, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"ok": rep["ok"], "max_dev_ruled": rep["max_dev_ruled"], "n_rule_failures": rep["n_rule_failures"],
                      "n_chain_trace_failures": rep["n_chain_trace_failures"], "failures": rep["failures"]}, indent=1))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
