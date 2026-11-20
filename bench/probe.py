"""List the connected Plutos (no transmission).  python -m bench.probe

Prints each IIO context URI and, when pyadi-iio is installed, the default settings of each Pluto.
Copy the URIs into bench/config.yaml (devices) and the serials / firmware into docs/BENCH_LOG.md.
"""

from __future__ import annotations

import shutil
import subprocess


def main() -> None:
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
                from bench.pluto import describe, open_pluto
                print("   ", describe(open_pluto(uri)))
            except Exception as e:
                print(f"    could not open with pyadi-iio: {e}")
    except ImportError:
        print("python bindings 'iio' not installed: pip install -r requirements-bench.txt")


if __name__ == "__main__":
    main()
