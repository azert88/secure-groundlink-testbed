#!/usr/bin/env bash
# Set up the project on a fresh Ubuntu / Kali VM: Python virtual environment + pinned dependencies.
# Supported Python: 3.11, 3.12, 3.13, 3.14 (CPU only). Usage:  ./setup.sh   then   make demo
set -euo pipefail
cd "$(dirname "$0")"

# 1. pick a Python >= 3.11 (override with PYTHON=/path/to/python ./setup.sh)
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for c in python3.13 python3.12 python3.11 python3.14 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
      PY="$c"; break
    fi
  done
fi
if [ -z "$PY" ]; then
  echo "Python >= 3.11 not found. On Ubuntu / Kali: sudo apt install python3 python3-venv" >&2
  exit 1
fi
echo "Using $($PY --version) ($PY)"

# 2. the venv module is a separate package on Debian-based systems
if ! "$PY" -m venv --help >/dev/null 2>&1; then
  echo "python venv module missing. Run: sudo apt install python3-venv   (or python3.X-venv)" >&2
  exit 1
fi

# 3. virtual environment + pinned dependencies (binary wheels only: no compiler needed)
"$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/python -m pip install --only-binary=:all: -r requirements.txt

# 4. check: imports, signed model lock, unit tests are available
.venv/bin/python - <<'EOF'
import sys
sys.path.insert(0, ".")
import numpy, scipy, sklearn, soundfile, cryptography
from detect.run import verify_models
ok, msg = verify_models()
print(f"Python {sys.version.split()[0]}, numpy {numpy.__version__}, scipy {scipy.__version__}, scikit-learn {sklearn.__version__}")
print(f"signed model lock: {msg}")
sys.exit(0 if ok else 1)
EOF
echo "Setup done. Next: make demo   (or: .venv/bin/python scripts/demo.py)"
