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
# Also offers rig's optional companion: the `gh` binary plus the github/gh-stack
# extension, which add stacked-PR publishing. They are NOT required — rig runs
# without them — and neither is authentication (reported only; it matters at
# push/submit/sync time). `gh` itself is a system package and has to be installed
# by hand; the extension is offered here. Nothing in this section can fail the
# install.
#
# Idempotent: skips if `rig-wb version` already succeeds (--force reinstalls).
# Exit: 0=ready / 1=no install method / 2=bad flag
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
      sed -n '1,24p' "$0"
      exit 0
      ;;
    *)
      echo "[ERROR] Unknown flag: $1" >&2
      exit 2
      ;;
  esac
done

# ── gh + gh-stack (optional) ────────────────────────────────────────────
# `gh` plus the github/gh-stack extension add stacked-PR publishing. They are
# optional: rig's own worktree flow does not use them (see
# rig_workbench/gh_requirement.py for the measurement behind that), so this
# section only ever reports and offers — it never fails the install. Auth is
# reported too, and never required. Detection is inline bash on purpose: this
# runs *before* rig-wb exists, so it cannot call `rig-wb gh-check`. States and
# remedies mirror rig_workbench/gh_requirement.py.
#
# RIG_SKIP_GH_CHECK is deliberately not honoured here: it silences the one-line
# note inside rig runs, and `/rig:setup` is someone explicitly asking to be told
# about their environment.
GH_STATE="ok"       # ok | gh-missing | extension-missing
GH_VERSION=""
GH_STACK_VERSION=""
GH_AUTH="unknown"   # informational only: yes | no | unknown

detect_gh() {
  GH_VERSION=""
  GH_STACK_VERSION=""
  GH_AUTH="unknown"
  if ! command -v gh >/dev/null 2>&1; then
    GH_STATE="gh-missing"
    return 0
  fi
  # No `| head` anywhere in this function: `head` exits after the first line,
  # `gh` takes SIGPIPE writing the second, and `set -o pipefail` turns that into
  # an exit 141 that kills the whole installer. Capture first, slice in bash.
  GH_VERSION_OUT=$(gh --version 2>/dev/null || true)
  GH_VERSION=$(printf '%s' "${GH_VERSION_OUT%%$'\n'*}" | awk '{print $3}')
  if [ -z "$GH_VERSION" ]; then
    GH_STATE="gh-missing"
    return 0
  fi
  # Informational: never changes GH_STATE.
  if gh auth status >/dev/null 2>&1; then GH_AUTH="yes"; else GH_AUTH="no"; fi
  # Reads the local extension dir — no auth, no remote.
  GH_EXT_LIST=$(gh extension list 2>/dev/null || true)
  GH_STACK_LINE=$(printf '%s' "$GH_EXT_LIST" | grep -i "gh-stack" || true)
  GH_STACK_LINE=${GH_STACK_LINE%%$'\n'*}
  if [ -z "$GH_STACK_LINE" ]; then
    GH_STATE="extension-missing"
    return 0
  fi
  GH_STACK_VERSION=$(printf '%s' "$GH_STACK_LINE" | awk '{print $NF}')
  GH_STATE="ok"
}

gh_remedy() {
  case "$GH_STATE" in
    gh-missing)
      echo "    macOS:          brew install gh"
      echo "    Debian/Ubuntu:  sudo apt install gh"
      echo "    other:          https://github.com/cli/cli#installation"
      echo "    then:           gh extension install github/gh-stack"
      ;;
    extension-missing)
      echo "    gh extension install github/gh-stack"
      ;;
  esac
}

# Auth line: reported, never a failure. `gh stack` only needs it to reach a remote.
report_gh_auth() {
  case "$GH_AUTH" in
    yes) echo "  auth:       authenticated" ;;
    no)  echo "  auth:       not authenticated (only needed for push/submit/sync)" ;;
  esac
}

report_gh() {
  echo "◇ GitHub CLI (optional — adds stacked-PR publishing)"
  case "$GH_STATE" in
    ok)
      echo "  gh:         $GH_VERSION"
      echo "  gh-stack:   ${GH_STACK_VERSION:-installed}"
      report_gh_auth
      ;;
    gh-missing)
      echo "  gh:         NOT INSTALLED"
      ;;
    extension-missing)
      echo "  gh:         $GH_VERSION"
      echo "  gh-stack:   NOT INSTALLED"
      report_gh_auth
      ;;
  esac
  if [ "$GH_STATE" != "ok" ]; then
    echo "  fix:"
    gh_remedy
  fi
}

# Offer the extension when that is the only thing missing. `gh` itself stays
# manual (system package). Authentication is never prompted for or performed.
# Every path here returns 0: declining, or having no gh at all, is a legitimate
# way to run rig. --yes skips the prompt, --force reinstalls, --check never
# reaches here (detection only).
ensure_gh_stack() {
  if [ "$GH_STATE" = "ok" ]; then
    if [ "$FORCE" -eq 1 ]; then
      echo ""
      echo "◇ Reinstalling gh-stack (--force)"
      if gh extension install --force github/gh-stack; then
        detect_gh
      else
        echo "  gh-stack reinstall failed; keeping what is already there (rig runs without it)."
      fi
    fi
    return 0
  fi
  if [ "$GH_STATE" != "extension-missing" ]; then
    # Only gh-missing reaches here. Installing gh is a system-package step and
    # the whole thing is optional, so say so once and get on with the install.
    echo "  skipping gh-stack: install gh first if you want it (rig runs without it)."
    return 0
  fi
  if [ "$YES" -eq 0 ]; then
    echo ""
    echo "◇ About to run"
    echo "  gh extension install github/gh-stack"
    echo ""
    read -r -p "Continue? [y/N] " GH_ANS
    case "$GH_ANS" in
      y|Y|yes|Yes) ;;
      *) echo "Skipped gh-stack (optional — rig runs without it)."; return 0 ;;
    esac
  fi
  echo ""
  echo "◇ Installing gh-stack..."
  # `set -e` is on: an unguarded failure here (network, auth, a bad extension
  # release — Codex reproduced exit 23) would abort the whole rig install before
  # it ever reaches the pip step. Optional means optional in both directions.
  if gh extension install github/gh-stack; then
    detect_gh
  else
    echo "  gh-stack install failed; continuing without it (rig runs without it)."
  fi
}

if [ "$UNINSTALL" -eq 0 ]; then
  detect_gh
  report_gh
  # --check detects only; anything else ensures the extension before touching pip.
  if [ "$CHECK_ONLY" -eq 0 ]; then
    ensure_gh_stack
  fi
  echo ""
fi

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
  # exit 0 = an install method exists. The gh section above is reported, never
  # graded: missing gh / gh-stack does not make an environment un-installable.
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
