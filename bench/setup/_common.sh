#!/usr/bin/env bash
# Shared steps of setup_rpi.sh and setup_kali.sh. Sourced from the cloned repo, never run directly.
# RECEIVE-ONLY: nothing here opens a TX. bench.probe only lists / opens the Plutos without transmitting.
# Package names marked [UNVERIFIED] were not checked on the target distribution; each has a fallback.

PHASE1_GR_SATELLITES="5.9.0"

log()  { printf '\n=== %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; WARNINGS+=("$*"); }

# Candidate version of an apt package ("" if none). Output captured first, then parsed: piping apt-cache into
# 'grep -q' made apt-cache die of SIGPIPE, and under 'set -o pipefail' every package looked unavailable.
apt_candidate() {
  local out
  out="$(LC_ALL=C apt-cache policy "$1" 2>/dev/null || true)"
  awk '$1 == "Candidate:" && $2 != "(none)" { print $2; exit }' <<<"$out"
}

# Install a package only if apt has a candidate for it; otherwise warn and continue.
apt_try() {
  local p c
  for p in "$@"; do
    c="$(apt_candidate "$p")"
    if [ -n "$c" ]; then
      echo "apt: $p candidate $c"
      sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "$p" || warn "apt install $p failed"
    else
      warn "apt package '$p' not available on this distribution"
    fi
  done
}

apt_base() {
  log "apt packages"
  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y git python3 python3-venv python3-pip usbutils
  # libiio command-line tools (iio_info, iio_attr) and the C library headers.
  apt_try libiio-utils libiio-dev
  # Python bindings of libiio from apt (module 'iio'); used as fallback if the pip binding fails. [UNVERIFIED name]
  apt_try python3-libiio
  if [ "${NO_GNURADIO:-0}" = "1" ]; then
    warn "gnuradio / gr-satellites skipped: this machine cannot decode frames (not needed for TX-only roles)"
  else
    apt_try gnuradio
    # gr-satellites: Debian package name 'gr-satellites' [UNVERIFIED on Bookworm / Kali]. Fallback: build from
    # source, tag v5.9.0 (Phase 1 pin): https://github.com/daniestevez/gr-satellites (not done automatically).
    apt_try gr-satellites
  fi
}

clone_repo() {
  log "repository ${REPO_URL} branch ${REPO_BRANCH} -> ${REPO_DIR}"
  if [ -d "${REPO_DIR}/.git" ]; then
    git -C "${REPO_DIR}" fetch origin
    git -C "${REPO_DIR}" checkout "${REPO_BRANCH}"
    git -C "${REPO_DIR}" pull --ff-only origin "${REPO_BRANCH}" || warn "git pull --ff-only failed (local changes kept)"
  else
    git clone --branch "${REPO_BRANCH}" "${REPO_URL}" "${REPO_DIR}"
  fi
}

python_venv() {
  log "Python virtual environment"
  cd "${REPO_DIR}"
  local py
  py="$(command -v python3)"
  "$py" -c 'import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] <= (3, 14) else 1)' \
    || { echo "python3 must be 3.11-3.14 (found $($py --version))" >&2; exit 1; }
  [ -d .venv ] || "$py" -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  # Binary wheels only, as setup.sh: building scipy / scikit-learn from source on a Pi would exhaust its RAM.
  .venv/bin/python -m pip install --only-binary=:all: -r requirements.txt
  # Bench extras: pyadi-iio (pure Python) + pylibiio (libiio ctypes binding). pylibiio 1.x needs libiio 1.x:
  # with a 0.x system libiio, constrain pylibiio<1.
  local libiio_ver cons
  libiio_ver="$(system_libiio_version)"
  cons="$(mktemp)"
  case "$libiio_ver" in
    0.*) echo "pylibiio<1" > "$cons"; echo "system libiio $libiio_ver (0.x): installing pylibiio<1" ;;
    "")  : > "$cons"; warn "system libiio version unknown: pylibiio left unconstrained" ;;
    *)   : > "$cons"; echo "system libiio $libiio_ver" ;;
  esac
  if ! .venv/bin/python -m pip install -c "$cons" -r requirements-bench.txt; then
    warn "pip install -r requirements-bench.txt failed; retrying pyadi-iio without its pylibiio dependency"
    .venv/bin/python -m pip install --no-deps pyadi-iio || warn "pyadi-iio install failed"
  fi
  rm -f "$cons"
  echo "libiio (system): ${libiio_ver:-unknown}   pylibiio (venv): $(pylibiio_version)"
  iio_binding_fallback
}

# Upstream version of the installed libiio runtime package (libiio0 or libiio1), e.g. "0.26"; "" if none.
system_libiio_version() {
  local v
  v="$(dpkg-query -W -f='${Version}\n' libiio1 libiio0 2>/dev/null | grep -v '^$' | head -1 || true)"
  v="${v#*:}"; echo "${v%%[-+]*}"
}

pylibiio_version() {
  .venv/bin/python -m pip show pylibiio 2>/dev/null | awk '$1 == "Version:" { print $2 }' | grep . || echo "not installed"
}

# If 'import iio' fails in the venv (pylibiio missing or mismatched with the system libiio), link the apt binding.
# The apt module is a pure-Python ctypes wrapper of the installed libiio, so it matches the system library.
iio_binding_fallback() {
  if .venv/bin/python -c 'import iio; iio.scan_contexts' >/dev/null 2>&1; then
    return
  fi
  local sys_iio site
  sys_iio="$(/usr/bin/python3 -c 'import iio, os; print(os.path.abspath(iio.__file__))' 2>/dev/null || true)"
  site="$(.venv/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
  if [ -n "$sys_iio" ]; then
    .venv/bin/python -m pip uninstall -y pylibiio >/dev/null 2>&1 || true
    ln -sf "$sys_iio" "$site/iio.py"
    echo "linked apt binding $sys_iio -> $site/iio.py"
  fi
  .venv/bin/python -c 'import iio' 2>/dev/null || warn "Python module 'iio' unavailable: bench.probe will only run iio_info"
}

# Non-root access to the Pluto over USB (libiio 'usb:' backend). ADI product IDs: b673 = normal, b674 = DFU.
# Rule adapted from ADI's 53-adi-plutosdr-usb.rules [UNVERIFIED wording; IDs as published by ADI].
udev_rule() {
  log "udev rule for the ADALM-Pluto"
  local rule=/etc/udev/rules.d/53-adi-plutosdr-usb.rules
  if ls /etc/udev/rules.d /lib/udev/rules.d /usr/lib/udev/rules.d 2>/dev/null | grep -qi pluto; then
    echo "a Pluto udev rule already exists: kept"
  else
    sudo tee "$rule" >/dev/null <<'EOF'
# ADALM-Pluto (Analog Devices): libiio USB access for non-root users (bench machines only)
SUBSYSTEM=="usb", ATTRS{idVendor}=="0456", ATTRS{idProduct}=="b673", MODE="0666"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0456", ATTRS{idProduct}=="b674", MODE="0666"
EOF
    echo "written $rule"
  fi
  sudo udevadm control --reload-rules && sudo udevadm trigger || warn "udev reload failed: unplug / replug the Pluto"
}

run_tests() {
  log "unit tests (offline, no hardware)"
  cd "${REPO_DIR}"
  if [ -f data/tables/frames.csv ]; then
    .venv/bin/python -m pytest -q tests/test_bench.py tests/test_link_signed.py || warn "unit tests FAILED"
  else
    # data/tables/ is git-ignored; the attack scenarios of test_link_signed.py read real frames from it.
    warn "data/tables/frames.csv missing (git-ignored): attack tests deselected. Copy it from the PC (scp) and rerun"
    .venv/bin/python -m pytest -q tests/test_bench.py tests/test_link_signed.py -k "not test_attack_gives_expected_rejection" \
      || warn "unit tests FAILED"
  fi
}

run_probe() {
  log "Pluto probe (receive side only, nothing transmitted)"
  cd "${REPO_DIR}"
  lsusb 2>/dev/null | grep -i '0456:b67' || warn "no Pluto on USB (lsusb 0456:b673 / b674 not found)"
  .venv/bin/python -m bench.probe || warn "bench.probe failed"
  # Context attributes: hw_serial, fw_version, model. [UNVERIFIED: 'iio_attr -C' flags on the installed libiio]
  if command -v iio_attr >/dev/null 2>&1; then
    local uri
    for uri in $(iio_info -s 2>/dev/null | grep -o '\[usb:[^]]*\]' | tr -d '[]'); do
      echo "--- context attributes of $uri"
      iio_attr -u "$uri" -C 2>/dev/null || iio_info -u "$uri" 2>/dev/null | head -25
    done
  fi
}

# Version block to paste into docs/BENCH_LOG.md.
print_versions() {
  cd "${REPO_DIR}"
  local grs
  echo
  echo "=================== paste into docs/BENCH_LOG.md ==================="
  echo "Machine:        ${MACHINE_ROLE}   host $(hostname)   $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "OS:             $(. /etc/os-release && echo "$PRETTY_NAME")   kernel $(uname -r)   arch $(uname -m)"
  [ -r /proc/device-tree/model ] && echo "Board:          $(tr -d '\0' < /proc/device-tree/model)"
  echo "Virtualisation: $(systemd-detect-virt 2>/dev/null || echo unknown)"
  echo "RAM:            $(free -m | awk '/^Mem:/ {print $2 " MiB"}')"
  echo "Repo:           $(git rev-parse --abbrev-ref HEAD) $(git rev-parse --short HEAD)"
  echo "Python (venv):  $(.venv/bin/python --version 2>&1)"
  echo "pip packages:   $(.venv/bin/python -m pip list --format=freeze 2>/dev/null \
      | grep -iE '^(numpy|scipy|scikit-learn|soundfile|cryptography|pyadi-iio|pylibiio)==' | tr '\n' ' ')"
  echo "libiio:         system $(system_libiio_version | grep . || echo unknown)   pylibiio (venv) $(pylibiio_version)"
  echo "iio (Python):   $(.venv/bin/python -c 'import iio; print(iio.version)' 2>/dev/null || echo 'not importable')"
  echo "apt packages:"
  dpkg-query -W -f='                ${Package} ${Version}\n' \
      libiio0 libiio-utils libiio-dev python3-libiio gnuradio gr-satellites 2>/dev/null || true
  echo "GNU Radio:      $(gnuradio-config-info --version 2>/dev/null || echo 'not installed')"
  grs="$(gr_satellites --version 2>/dev/null | head -1 || true)"
  echo "gr-satellites:  ${grs:-not installed}   (Phase 1 pin v${PHASE1_GR_SATELLITES})"
  case "$grs" in *"${PHASE1_GR_SATELLITES}"*|"") ;; *) echo "                ^ differs from the Phase 1 pin: record it";; esac
  echo "Plutos (iio_info -s):"
  iio_info -s 2>/dev/null | sed 's/^/                /' || echo "                iio_info not installed"
  echo "====================================================================="
  if [ "${#WARNINGS[@]}" -gt 0 ]; then
    echo "Warnings (${#WARNINGS[@]}):"
    printf '  - %s\n' "${WARNINGS[@]}"
  else
    echo "No warnings."
  fi
}
