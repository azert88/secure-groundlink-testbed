"""List the connected Plutos (no transmission).  python -m bench.probe [--config bench/config.yaml]

Prints each IIO context URI and, when pyadi-iio is installed, the default settings of each Pluto.
Devices configured with a "fake:" URI (rehearsal, bench/fake_pluto.py) are listed too.
Copy the URIs into bench/config.yaml (devices) and the serials / firmware into docs/BENCH_LOG.md.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess

from bench.safety import DEFAULT_CONFIG, load_config


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="List the connected Plutos (no transmission)")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = ap.parse_args(argv)
    from bench.pluto import describe, open_pluto
    for role, uri in (load_config(args.config).get("devices") or {}).items():
        if str(uri).startswith("fake:"):
            print(f"{role}\t{uri}\tFAKE (no hardware)\n   ", describe(open_pluto(uri)))
    exe = shutil.which("iio_info")
    if exe:
        r = subprocess.run([exe, "-s"], capture_output=True, text=True)
        print(r.stdout or r.stderr)
    else:
        print("iio_info not found: install libiio-utils (apt) first")
    try:
        import iio
        ctxs = iio.scan_contexts()
        for uri, desc in ctxs.items():
            print(f"{uri}\t{desc}")
            try:
                print("   ", describe(open_pluto(uri)))
            except Exception as e:
                print(f"    could not open with pyadi-iio: {e}")
    except ImportError:
        print("python bindings 'iio' not installed: pip install -r requirements-bench.txt")


if __name__ == "__main__":
    main()
