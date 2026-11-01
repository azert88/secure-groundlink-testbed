"""Test lock (DETECTOR_SPEC rule 10).

Before ANY evaluation on test, the models, thresholds, calibration files and detector code of a phase are
frozen: their SHA-256 go into models/<name>/lock.json, signed with Ed25519 (trust/manifest.py). Eval
scripts call require_lock() before touching test labels: it fails if the lock is missing, the signature
does not verify with the pinned public key, or any locked file changed since.

CLI:
  python eval/lock.py freeze --name replay --files models/replay/params.json ...
  python eval/lock.py check --name replay
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from trust import manifest as tm  # noqa: E402


class LockError(RuntimeError):
    pass


def lock_path(name: str, models_dir: str = "models") -> Path:
    return Path(models_dir) / name / "lock.json"


def freeze(name: str, files: list[Path], models_dir: str = "models", priv: Path = tm.DEFAULT_PRIV) -> dict:
    p = lock_path(name, models_dir)
    if p.exists():
        raise LockError(f"{p.as_posix()} already exists: a frozen phase is never re-frozen")
    m = tm.sign(tm.make_manifest(files, label=f"test lock: {name}"), priv)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return m


def require_lock(name: str, models_dir: str = "models", pub: Path = tm.DEFAULT_PUB) -> dict:
    p = lock_path(name, models_dir)
    if not p.exists():
        raise LockError(f"no test lock {p.as_posix()}: freeze models and thresholds first (rule 10)")
    m = json.loads(p.read_text(encoding="utf-8"))
    if not tm.verify(m, pub):
        raise LockError(f"{p.as_posix()}: signature does not verify with the pinned public key")
    bad = tm.file_mismatches(m)
    if bad:
        raise LockError(f"{p.as_posix()}: locked files changed since freezing: {bad}")
    return m


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Freeze / check a phase's test lock")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--name", required=True)
    f.add_argument("--files", nargs="+", required=True)
    c = sub.add_parser("check")
    c.add_argument("--name", required=True)
    a = ap.parse_args(argv)
    try:
        m = freeze(a.name, [Path(x) for x in a.files]) if a.cmd == "freeze" else require_lock(a.name)
    except LockError as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1
    print(json.dumps({"ok": True, "lock": lock_path(a.name).as_posix(), "created_utc": m["created_utc"],
                      "files": m["files"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
