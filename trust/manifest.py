"""Ed25519-signed manifests of files (dataset fingerprints, models, thresholds).

A manifest lists files with their SHA-256; the signature covers the canonical JSON of everything except
the signature itself. The public key is pinned in the repository (keys/public/); the private key is
generated locally and never committed (keys/private/ is gitignored).

CLI:
  python trust/manifest.py keygen
  python trust/manifest.py sign --files a.json b.json --out lock.json [--label replay]
  python trust/manifest.py verify --manifest lock.json
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

DEFAULT_PRIV = Path("keys/private/ed25519_private.pem")
DEFAULT_PUB = Path("keys/public/ed25519_public.pem")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def keygen(priv: Path = DEFAULT_PRIV, pub: Path = DEFAULT_PUB, overwrite: bool = False) -> None:
    if Path(priv).exists() and not overwrite:
        raise FileExistsError(f"{Path(priv).as_posix()} exists; refusing to overwrite")
    k = Ed25519PrivateKey.generate()
    Path(priv).parent.mkdir(parents=True, exist_ok=True)
    Path(pub).parent.mkdir(parents=True, exist_ok=True)
    Path(priv).write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    Path(pub).write_bytes(k.public_key().public_bytes(serialization.Encoding.PEM,
                                                      serialization.PublicFormat.SubjectPublicKeyInfo))


def _canonical(m: dict) -> bytes:
    return json.dumps({k: v for k, v in m.items() if k != "signature"}, sort_keys=True,
                      separators=(",", ":")).encode()


def make_manifest(files: list[Path], label: str = "", extra: dict | None = None) -> dict:
    return {"label": label, "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "files": {Path(f).as_posix(): sha256_file(Path(f)) for f in sorted(files)}, **(extra or {})}


def sign(m: dict, priv: Path = DEFAULT_PRIV) -> dict:
    k = serialization.load_pem_private_key(Path(priv).read_bytes(), password=None)
    return {**m, "signature": base64.b64encode(k.sign(_canonical(m))).decode()}


def verify(m: dict, pub: Path = DEFAULT_PUB) -> bool:
    """True if the signature verifies with the pinned public key."""
    k = serialization.load_pem_public_key(Path(pub).read_bytes())
    if not isinstance(k, Ed25519PublicKey) or "signature" not in m:
        return False
    try:
        k.verify(base64.b64decode(m["signature"]), _canonical(m))
        return True
    except (InvalidSignature, ValueError):
        return False


def file_mismatches(m: dict, root: Path = Path(".")) -> list[str]:
    """Files of the manifest that are missing or whose SHA-256 changed."""
    bad = []
    for f, h in m["files"].items():
        p = Path(root) / f
        if not p.exists() or sha256_file(p) != h:
            bad.append(f)
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ed25519-signed file manifests")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("keygen")
    g.add_argument("--priv", default=str(DEFAULT_PRIV))
    g.add_argument("--pub", default=str(DEFAULT_PUB))
    s = sub.add_parser("sign")
    s.add_argument("--files", nargs="+", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--label", default="")
    s.add_argument("--priv", default=str(DEFAULT_PRIV))
    v = sub.add_parser("verify")
    v.add_argument("--manifest", required=True)
    v.add_argument("--pub", default=str(DEFAULT_PUB))
    a = ap.parse_args(argv)
    if a.cmd == "keygen":
        keygen(Path(a.priv), Path(a.pub))
        print(json.dumps({"public_key": Path(a.pub).as_posix()}))
        return 0
    if a.cmd == "sign":
        m = sign(make_manifest([Path(f) for f in a.files], a.label), Path(a.priv))
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(m, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"out": Path(a.out).as_posix(), "files": m["files"]}, indent=1))
        return 0
    m = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    ok, bad = verify(m, Path(a.pub)), file_mismatches(m)
    print(json.dumps({"signature_ok": ok, "mismatched_files": bad}))
    return 0 if ok and not bad else 1


if __name__ == "__main__":
    sys.exit(main())
