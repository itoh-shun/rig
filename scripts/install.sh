#!/usr/bin/env bash
# rig-wb installer — detects pip/pipx and installs via git+URL.
#
# Usage:
#   scripts/install.sh                             # interactive mode (recommended)
#   scripts/install.sh --yes                       # install without confirmation
#   scripts/install.sh --ref <branch|tag|sha>      # pin a specific ref (default: master)
#   scripts/install.sh --check                     # only check installability, then exit
#   scripts/install.sh --uninstall                 # remove rig-workbench
#
# Strategy (in order of preference):
#   1. If pipx is available, `pipx install` (isolated venv, single CLI on PATH; recommended)
#   2. If pip is available, `pip install --user` (on PEP 668, prompt for explicit --break-system-packages)
#   3. Error if neither exists
#
# Idempotent: skips if `rig-wb version` already succeeds (--force reinstalls).
set -euo pipefail

REPO_URL="git+https://github.com/itoh-shun/rig.git"
DEFAULT_REF="master"

# ── flag parsing ────────────────────────────────────────────────────────
YES=0
REF="$DEFAULT_REF"
CHECK_ONLY=0
UNINSTALL=0
FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    -y|--yes) YES=1; shift ;;
    --ref) REF="${2:-}"; shift 2 ;;
    --check) CHECK_ONLY=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    --force) FORCE=1; shift ;;
    -h|--help)
      sed -n '1,20p' "$0"
      exit 0
      ;;
    *)
      echo "[ERROR] Unknown flag: $1" >&2
      exit 2
      ;;
  esac
done

# ── existing install check ──────────────────────────────────────────────
if command -v rig-wb >/dev/null 2>&1; then
  CURRENT=$(rig-wb version 2>/dev/null || echo "?")
  if [ "$UNINSTALL" -eq 1 ]; then
    echo "◇ Uninstalling: currently $CURRENT"
    if command -v pipx >/dev/null 2>&1; then
      pipx uninstall rig-workbench || pip3 uninstall -y rig-workbench || true
    else
      pip3 uninstall -y rig-workbench || true
    fi
    echo "✓ Uninstall complete"
    exit 0
  fi
  if [ "$FORCE" -eq 0 ]; then
    echo "✓ rig-wb is already installed: $CURRENT"
    echo "  Use --force to reinstall, --uninstall to remove."
    exit 0
  fi
fi

if [ "$UNINSTALL" -eq 1 ]; then
  echo "rig-wb is not installed. Nothing to do."
  exit 0
fi

# ── env detection ───────────────────────────────────────────────────────
HAS_PIPX=0
HAS_PIP=0
HAS_UV=0
PYTHON_CMD=""
command -v pipx >/dev/null 2>&1 && HAS_PIPX=1
command -v uv >/dev/null 2>&1 && HAS_UV=1
if command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON_CMD=python
fi
if [ -n "$PYTHON_CMD" ] && "$PYTHON_CMD" -m pip --version >/dev/null 2>&1; then
  HAS_PIP=1
fi

# Decision logic: pipx > uv > pip
METHOD=""
if [ "$HAS_PIPX" -eq 1 ]; then
  METHOD="pipx"
elif [ "$HAS_UV" -eq 1 ]; then
  METHOD="uv"
elif [ "$HAS_PIP" -eq 1 ]; then
  METHOD="pip"
fi

echo "◇ Environment detection"
echo "  python:     ${PYTHON_CMD:-none}"
echo "  pipx:       $([ "$HAS_PIPX" -eq 1 ] && echo yes || echo no)"
echo "  uv:         $([ "$HAS_UV" -eq 1 ] && echo yes || echo no)"
echo "  pip:        $([ "$HAS_PIP" -eq 1 ] && echo yes || echo no)"
echo "  install method: ${METHOD:-none}"

if [ "$CHECK_ONLY" -eq 1 ]; then
  [ -n "$METHOD" ] && exit 0 || exit 1
fi

if [ -z "$METHOD" ]; then
  cat >&2 <<'EOF'
[ERROR] None of pip / pipx / uv found. Install one of them first:

  # Recommended: pipx (installs the CLI standalone in an isolated venv)
  # Debian/Ubuntu:
  sudo apt install pipx && pipx ensurepath

  # macOS:
  brew install pipx && pipx ensurepath

  # Generic:
  python3 -m pip install --user pipx && python3 -m pipx ensurepath
EOF
  exit 1
fi

# ── confirm ─────────────────────────────────────────────────────────────
SPEC="${REPO_URL}@${REF}"
if [ "$YES" -eq 0 ]; then
  echo ""
  echo "◇ About to run"
  case "$METHOD" in
    pipx) echo "  pipx install \"$SPEC\"" ;;
    uv)   echo "  uv tool install \"$SPEC\"" ;;
    pip)  echo "  $PYTHON_CMD -m pip install --user \"$SPEC\"" ;;
  esac
  echo ""
  read -r -p "Continue? [y/N] " ANS
  case "$ANS" in
    y|Y|yes|Yes) ;;
    *) echo "Aborted."; exit 0 ;;
  esac
fi

# ── install ─────────────────────────────────────────────────────────────
echo ""
echo "◇ Installing..."
case "$METHOD" in
  pipx)
    if [ "$FORCE" -eq 1 ]; then
      pipx install --force "$SPEC"
    else
      pipx install "$SPEC"
    fi
    ;;
  uv)
    if [ "$FORCE" -eq 1 ]; then
      uv tool install --force "$SPEC"
    else
      uv tool install "$SPEC"
    fi
    ;;
  pip)
    # PEP 668 environments (Debian family) may require --break-system-packages.
    # Try a plain --user first and show guidance if it fails.
    if ! $PYTHON_CMD -m pip install --user "$SPEC" 2>&1; then
      cat >&2 <<'EOF'

[HINT] pip install --user was rejected. On a PEP 668 environment, try:

  # Install pipx (recommended; guidance above)
  # Or, as a one-off:
  python3 -m pip install --user --break-system-packages "$SPEC"

EOF
      exit 1
    fi
    ;;
esac

# ── verify ──────────────────────────────────────────────────────────────
echo ""
echo "◇ Verifying"
if ! command -v rig-wb >/dev/null 2>&1; then
  cat >&2 <<'EOF'
[WARN] rig-wb not found on PATH. Try the following:

  # If installed via pipx:
  pipx ensurepath && exec "$SHELL"

  # If installed via pip --user:
  export PATH="$HOME/.local/bin:$PATH"
  # or add it to `.bashrc` / `.zshrc`

EOF
  exit 1
fi
INSTALLED=$(rig-wb version)
echo "✓ Install complete: $INSTALLED"
echo ""
echo "Usage:"
echo "  rig-wb --help          # list sub-commands"
echo "  rig-wb wb board        # workbench status"
echo "  rig-wb runs --html /tmp/rig.html   # HTML dashboard"
