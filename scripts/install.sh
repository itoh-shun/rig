#!/usr/bin/env bash
# rig-wb installer — pip/pipx を検知して git+URL 経由で導入する。
#
# 呼び方:
#   scripts/install.sh                             # 対話モード（推奨）
#   scripts/install.sh --yes                       # 確認なしで install
#   scripts/install.sh --ref <branch|tag|sha>      # 特定 ref を指定（既定 master）
#   scripts/install.sh --check                     # インストール可否だけ確認して終了
#   scripts/install.sh --uninstall                 # rig-workbench を外す
#
# 戦略（優先順）:
#   1. pipx が使えれば `pipx install`（隔離 venv・PATH に単発 CLI・推奨）
#   2. pip があれば `pip install --user`（PEP 668 の場合は明示 --break-system-packages を促す）
#   3. どちらも無ければエラー
#
# 冪等: 既に `rig-wb version` が通ればスキップ（--force で再インストール）。
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
      echo "[ERROR] 未知のフラグ: $1" >&2
      exit 2
      ;;
  esac
done

# ── existing install check ──────────────────────────────────────────────
if command -v rig-wb >/dev/null 2>&1; then
  CURRENT=$(rig-wb version 2>/dev/null || echo "?")
  if [ "$UNINSTALL" -eq 1 ]; then
    echo "◇ アンインストール: 現在 $CURRENT"
    if command -v pipx >/dev/null 2>&1; then
      pipx uninstall rig-workbench || pip3 uninstall -y rig-workbench || true
    else
      pip3 uninstall -y rig-workbench || true
    fi
    echo "✓ アンインストール完了"
    exit 0
  fi
  if [ "$FORCE" -eq 0 ]; then
    echo "✓ rig-wb は既にインストール済み: $CURRENT"
    echo "  再インストールするには --force、外すには --uninstall。"
    exit 0
  fi
fi

if [ "$UNINSTALL" -eq 1 ]; then
  echo "rig-wb は未インストール。何もしません。"
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

# 決定ロジック: pipx > uv > pip
METHOD=""
if [ "$HAS_PIPX" -eq 1 ]; then
  METHOD="pipx"
elif [ "$HAS_UV" -eq 1 ]; then
  METHOD="uv"
elif [ "$HAS_PIP" -eq 1 ]; then
  METHOD="pip"
fi

echo "◇ 環境検知"
echo "  python:     ${PYTHON_CMD:-なし}"
echo "  pipx:       $([ "$HAS_PIPX" -eq 1 ] && echo 有り || echo なし)"
echo "  uv:         $([ "$HAS_UV" -eq 1 ] && echo 有り || echo なし)"
echo "  pip:        $([ "$HAS_PIP" -eq 1 ] && echo 有り || echo なし)"
echo "  install 方法: ${METHOD:-なし}"

if [ "$CHECK_ONLY" -eq 1 ]; then
  [ -n "$METHOD" ] && exit 0 || exit 1
fi

if [ -z "$METHOD" ]; then
  cat >&2 <<'EOF'
[ERROR] pip / pipx / uv のいずれも見つかりません。以下のどれかを先に入れてください:

  # 推奨: pipx（隔離 venv で CLI を単発インストール）
  # Debian/Ubuntu:
  sudo apt install pipx && pipx ensurepath

  # macOS:
  brew install pipx && pipx ensurepath

  # 汎用:
  python3 -m pip install --user pipx && python3 -m pipx ensurepath
EOF
  exit 1
fi

# ── confirm ─────────────────────────────────────────────────────────────
SPEC="${REPO_URL}@${REF}"
if [ "$YES" -eq 0 ]; then
  echo ""
  echo "◇ 実行内容"
  case "$METHOD" in
    pipx) echo "  pipx install \"$SPEC\"" ;;
    uv)   echo "  uv tool install \"$SPEC\"" ;;
    pip)  echo "  $PYTHON_CMD -m pip install --user \"$SPEC\"" ;;
  esac
  echo ""
  read -r -p "続行しますか？ [y/N] " ANS
  case "$ANS" in
    y|Y|yes|Yes) ;;
    *) echo "中止しました。"; exit 0 ;;
  esac
fi

# ── install ─────────────────────────────────────────────────────────────
echo ""
echo "◇ インストール中..."
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
    # PEP 668 環境（Debian 系）では --break-system-packages が要る場合あり。
    # ここでは素直に --user を試し、失敗したらメッセージで案内する。
    if ! $PYTHON_CMD -m pip install --user "$SPEC" 2>&1; then
      cat >&2 <<'EOF'

[HINT] pip install --user が拒否されました。PEP 668 環境なら以下を試してください:

  # pipx を入れる（推奨・上に案内あり）
  # または一時的に:
  python3 -m pip install --user --break-system-packages "$SPEC"

EOF
      exit 1
    fi
    ;;
esac

# ── verify ──────────────────────────────────────────────────────────────
echo ""
echo "◇ 検証"
if ! command -v rig-wb >/dev/null 2>&1; then
  cat >&2 <<'EOF'
[WARN] rig-wb が PATH に見つかりません。以下を試してください:

  # pipx 経由なら:
  pipx ensurepath && exec "$SHELL"

  # pip --user 経由なら:
  export PATH="$HOME/.local/bin:$PATH"
  # または `.bashrc` / `.zshrc` に追記

EOF
  exit 1
fi
INSTALLED=$(rig-wb version)
echo "✓ インストール完了: $INSTALLED"
echo ""
echo "使い方:"
echo "  rig-wb --help          # サブコマンド一覧"
echo "  rig-wb wb board        # workbench の状態"
echo "  rig-wb runs --html /tmp/rig.html   # HTML dashboard"
