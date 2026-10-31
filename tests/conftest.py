import sys
from pathlib import Path

# Dataset scripts live in data/; the repo root holds the detect/ package.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT))
