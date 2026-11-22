#!/usr/bin/env bash
# One-command install of the bench Kali VM ("attacker", Pluto #2 by USB pass-through).
# Target: Kali Linux rolling (amd64) in VirtualBox or VMware. Needs sudo and network. RECEIVE-ONLY: transmits nothing.
#
#   wget -O setup_kali.sh https://raw.githubusercontent.com/azert88/secure-groundlink-testbed/phase2/bench/setup/setup_kali.sh
#   bash setup_kali.sh                     # attacker role: no GNU Radio / gr-satellites (VM has 2.8 GB RAM)
#   bash setup_kali.sh --with-gnuradio     # also install gnuradio + gr-satellites (Kali offers gr-satellites 5.8.0)
#
# Options (environment): REPO_DIR (default ~/secure-groundlink-testbed), REPO_BRANCH (phase2).
# Full log: ~/bench_setup_<host>_<UTC>.log. Rerunnable.
#
# ---------------------------------------------------------------------------------------------------------------
# USB pass-through of the Pluto to the VM (do this on the HOST before running the probe) [UNVERIFIED menu wording]
# The Pluto enumerates as USB 0456:b673 (normal) and re-enumerates as 0456:b674 in DFU / after a firmware update:
# give the VM BOTH IDs, otherwise it falls back to the host after a reboot of the Pluto.
#
# VirtualBox: install the Extension Pack matching the VirtualBox version (needed for the USB 2.0/3.0 controllers).
#   VM powered off -> Settings -> USB -> enable "USB 2.0 (EHCI)" or "USB 3.0 (xHCI)" -> "+" (add filter) ->
#   pick "Analog Devices Inc. PlutoSDR"; add a second filter with Vendor 0456, Product b674.
#   Linux host only: add the host user to the 'vboxusers' group and log in again.
# VMware Workstation / Player: VM -> Settings -> USB Controller -> USB compatibility 2.0 or 3.1;
#   with the VM running: VM -> Removable Devices -> "Analog Devices PlutoSDR" -> Connect (Disconnect from host).
#   Answer "connect to the virtual machine" if VMware asks when the Pluto is plugged in.
# Check inside the VM: `lsusb | grep 0456` must list the Pluto; then `iio_info -s` must show a usb: context.
# Alternative without pass-through: the Pluto's USB network interface on the host (default ip:192.168.2.1),
#   reachable from a NAT VM through the host's routing [UNVERIFIED on this host]; use "ip:192.168.2.1" as URI.
# ---------------------------------------------------------------------------------------------------------------
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/azert88/secure-groundlink-testbed.git}"
REPO_BRANCH="${REPO_BRANCH:-phase2}"
REPO_DIR="${REPO_DIR:-$HOME/secure-groundlink-testbed}"
MACHINE_ROLE="Kali VM"
WARNINGS=()
NO_GNURADIO=1                            # attacker role does not decode; opt in with --with-gnuradio
for a in "$@"; do
  case "$a" in
    --with-gnuradio) NO_GNURADIO=0 ;;
    *) echo "unknown option: $a (only --with-gnuradio)" >&2; exit 2 ;;
  esac
done

LOG="$HOME/bench_setup_$(hostname)_$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$LOG") 2>&1
echo "log: $LOG"

. /etc/os-release
[ "${ID:-}" = "kali" ] || echo "NOTE: expected Kali, found '${ID:-?}' (continuing)"
virt="$(systemd-detect-virt 2>/dev/null || echo none)"
echo "virtualisation: $virt"

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
if ! lsusb 2>/dev/null | grep -qi '0456:b67'; then
  warn "Pluto not visible in the VM ($virt): set up USB pass-through (see the header of bench/setup/setup_kali.sh)"
fi
run_probe
print_versions
echo "log: $LOG"
echo "Next: copy the URI into bench/config.yaml (devices.attacker), fill docs/BENCH_LOG.md. Do NOT transmit before the attenuators are wired."
