#!/usr/bin/env bash
# One-command install of a bench Raspberry Pi (RPi #1 "satellite" or RPi #2 "station").
# Target: Raspberry Pi OS 64-bit (Bookworm, aarch64). Needs sudo and network. RECEIVE-ONLY: transmits nothing.
#
#   wget -O setup_rpi.sh https://raw.githubusercontent.com/azert88/secure-groundlink-testbed/phase2/bench/setup/setup_rpi.sh
#   bash setup_rpi.sh                      # or: NO_GNURADIO=1 bash setup_rpi.sh   (RPi #1 does not decode)
#
# Options (environment): REPO_DIR (default ~/secure-groundlink-testbed), REPO_BRANCH (phase2), NO_GNURADIO=1.
# Full log: ~/bench_setup_<host>_<UTC>.log. Rerunnable: existing clone / venv / udev rule are reused.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/azert88/secure-groundlink-testbed.git}"
REPO_BRANCH="${REPO_BRANCH:-phase2}"
REPO_DIR="${REPO_DIR:-$HOME/secure-groundlink-testbed}"
MACHINE_ROLE="Raspberry Pi"
WARNINGS=()

LOG="$HOME/bench_setup_$(hostname)_$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$LOG") 2>&1
echo "log: $LOG"

# Platform checks: the pinned wheels (numpy, scipy, scikit-learn) exist for aarch64, not for 32-bit armv7l.
arch="$(uname -m)"
if [ "$arch" != "aarch64" ]; then
  echo "This Pi runs '$arch': install Raspberry Pi OS 64-bit (aarch64) first." >&2
  exit 1
fi
. /etc/os-release
[ "${VERSION_CODENAME:-}" = "bookworm" ] || echo "NOTE: tested target is Bookworm, found '${VERSION_CODENAME:-?}' [UNVERIFIED]"
free -m | awk '/^Mem:/ { if ($2 < 1800) print "NOTE: " $2 " MiB RAM; GNU Radio + gr-satellites may be slow (consider NO_GNURADIO=1 on RPi #1)" }'

# git is needed before the shared steps can be read from the clone.
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y git
if [ -d "${REPO_DIR}/.git" ]; then :; else git clone --branch "${REPO_BRANCH}" "${REPO_URL}" "${REPO_DIR}"; fi
# shellcheck source=bench/setup/_common.sh
. "${REPO_DIR}/bench/setup/_common.sh"

apt_base
clone_repo
python_venv
udev_rule
run_tests
run_probe
print_versions
echo "log: $LOG"
echo "Next: copy the URIs into bench/config.yaml (devices), fill docs/BENCH_LOG.md. Do NOT transmit before the attenuators are wired."
