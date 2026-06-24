#!/usr/bin/env python3
"""
Rigsino — a dev-themed 6号機風 AT/ART パチスロ実機シミュレータ（永続メダル管理つき）。

rig humor pack `slot` の実エンジン（Native-first：薄い instruction が委譲する実装本体）。
参考実機: 6号機 AT/ART 機（通常時 → CZ → AT の状態機械・押し順ベル・天井・設定1〜6・純増）。

⚠️ 遊び。メダルは架空（fake medals）、現金は一切絡まない。実ギャンブルではない。

ウォレット: 既定 ~/.claude/rig/rigsino/wallet.json（環境変数 RIGSINO_WALLET / --wallet で上書き）
依存なし（Python 標準ライブラリのみ）。

使い方:
  rigsino.py spin [--order L|C|R]   # 1 ゲーム（レバーON＋ストップ）。通常時の押し順ベルは
                                    #   --order で第1停止を指定（無指定=ランダム押し）。AT 中はナビ自動
  rigsino.py auto [N]               # N ゲーム自動消化（既定 50。AT はナビ的中で回す）
  rigsino.py status                 # 手持ちメダル・現在状態・戦績
  rigsino.py reset [--setting 1-6]  # 台移動（メダル維持・状態/設定リセット。設定無指定はランダム＝看破要素）
  rigsino.py cashin <N>             # 架空メダルを補充（追加投資）
  rigsino.py payouts                # 小役・配当・状態の早見表

終了コード: 0=正常 / 1=入力エラー（メダル不足・不正値など）
"""

import argparse
import json
import os
import pathlib
import random
import sys
from datetime import datetime, timezone

RNG = random.SystemRandom()

# ── シンボル（dev テーマ）────────────────────────────────────────────────────
S_REPLAY = "🔄"   # リプレイ（再ビルド＝再遊技・投入なし）
S_BELL   = "🔔"   # 押し順ベル（CI ベル）
S_GREEN  = "🟢"   # ベル/ハズレ目
S_COFFEE = "☕"   # 弱レア（チェリー相当）
S_BUG    = "🐛"   # チャンス目（弱）
S_FIRE   = "🔥"   # 強レア（強チャンス目）
S_SHIP   = "🚀"   # AT 図柄
S_REL    = "💎"   # 確定役（AT 直撃確定）

BET = 3           # 規定投入枚数
BELL_PAY = 9      # 押し順ベル正解の払い出し
BELL_SPILL = 1    # 不正解こぼし
CHERRY_PAY = 2    # 弱レア払い出し
CEILING = 800     # 天井（通常ハマり G で CZ 当選）

# ── 役抽選テーブル（通常時・1/x、確率の分母）────────────────────────────────
# AT 初当たりに効く部分は設定で変える（_setting_param 参照）。小役自体は共通。
ROLE_ODDS = {
    "replay": 7.3,
    "bell":   1.9,    # 押し順ベル（第1停止 3 択・正解 1/3 で BELL_PAY、外れこぼし）
    "cherry": 49.0,   # ☕ 弱レア（CZ/AT 抽選の弱トリガ）
    "chance": 110.0,  # 🐛 チャンス目（中トリガ）
    "strong": 240.0,  # 🔥 強チャンス（AT 期待大）
    "kakutei": 8192.0,# 💎 確定役（AT 直撃）
}

# ── 設定差（1〜6）：AT 初当たり率・CZ 成功率・継続率・上乗せに効く ──────────
def setting_param(setting: int) -> dict:
    s = max(1, min(6, setting))
    t = (s - 1) / 5.0  # 0(設定1)〜1(設定6)
    # 値は 50 万 G シミュレーションで実機相当に調整（機械割 設定1≈95% / 設定6≈115%、
    # AT 初当たり 1/337→1/233、AT 占有 17→36%、単調増加）。
    return {
        # レア役からの AT/CZ 当選率（高設定ほど甘い）
        "cz_from_cherry": 0.04 + 0.04 * t,
        "cz_from_chance": 0.18 + 0.12 * t,
        "at_from_strong": 0.25 + 0.18 * t,
        "cz_success":     0.33 + 0.20 * t,   # CZ 中の AT 当選率
        "at_continue":    0.40 + 0.16 * t,   # AT セット継続率（設定1=40%・設定6=56%）
        "soft_ceiling":   0.006 + 0.009 * t, # 各 G の微小な直撃抽選（高設定の引き戻し感）
    }

AT_SET_GAMES = 33          # AT 1 セットのゲーム数
CZ_GAMES = 5               # CZ のゲーム数
# AT 中の上乗せ（実機の機械割に合わせてシミュで調整した値）
AT_UPLIFT_STRONG = [10, 20]   # 強チャンス/確定役での上乗せ G
AT_UPLIFT_CHANCE = [10]       # チャンス目での上乗せ G
AT_UPLIFT_CHANCE_PROB = 0.25  # チャンス目が上乗せに化ける確率

DEFAULT_WALLET = {
    "medals": 1000,
    "mode": "normal",          # normal | cz | at
    "setting": 0,              # 0 = 未設定（reset で確定）
    "since_bonus": 0,          # 天井カウンタ（通常 G 数）
    "cz_remain": 0,
    "at_games": 0,
    "at_sets": 0,
    "at_payout": 0,            # 現在 AT の累計獲得
    "stats": {
        "games": 0, "invested": 0, "payout": 0,
        "at_hits": 0, "cz_hits": 0, "best_at": 0, "best_medals": 1000,
        "max_set": 0,
    },
}

# ── ウォレット永続化 ─────────────────────────────────────────────────────────
def wallet_path(arg_path: str | None) -> pathlib.Path:
    p = arg_path or os.environ.get("RIGSINO_WALLET")
    if p:
        return pathlib.Path(p).expanduser()
    return pathlib.Path.home() / ".claude" / "rig" / "rigsino" / "wallet.json"


def load_wallet(path: pathlib.Path) -> dict:
    w = {}
    if path.exists():
        try:
            w = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            w = {}
    merged = json.loads(json.dumps(DEFAULT_WALLET))
    for k, v in w.items():
        if k == "stats" and isinstance(v, dict):
            merged["stats"].update(v)
        else:
            merged[k] = v
    if "created" not in merged:
        merged["created"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not merged.get("setting"):
        merged["setting"] = RNG.randint(1, 6)  # 初回は台の設定を抽選
    return merged


def save_wallet(path: pathlib.Path, w: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    w["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    w["stats"]["best_medals"] = max(w["stats"].get("best_medals", 0), w["medals"])
    path.write_text(json.dumps(w, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 役抽選 ───────────────────────────────────────────────────────────────────
def draw_role() -> str:
    """通常時/AT 中共通の小役抽選。確率の高い順に判定。"""
    r = RNG.random()
    cum = 0.0
    for role in ("replay", "bell", "cherry", "chance", "strong", "kakutei"):
        cum += 1.0 / ROLE_ODDS[role]
        if r < cum:
            return role
    return "hazure"


REEL_FACE = {
    "replay": (S_REPLAY, S_REPLAY, S_REPLAY),
    "bell":   (S_BELL, S_BELL, S_BELL),
    "cherry": (S_COFFEE, S_GREEN, S_GREEN),
    "chance": (S_BUG, S_FIRE, S_BUG),
    "strong": (S_FIRE, S_FIRE, S_FIRE),
    "kakutei":(S_REL, S_REL, S_REL),
    "hazure": (S_GREEN, S_BUG, S_COFFEE),
}


def reel_face(role: str, bell_hit: bool) -> tuple:
    if role == "bell" and not bell_hit:
        return (S_BELL, S_BELL, S_GREEN)  # こぼし目
    return REEL_FACE.get(role, REEL_FACE["hazure"])


# ── 1 ゲーム処理 ─────────────────────────────────────────────────────────────
def play_one(w: dict, order: str | None) -> dict:
    """1 ゲームを進め、実況用の dict を返す。w を更新する。"""
    p = setting_param(w["setting"])
    role = draw_role()
    ev = {"role": role, "lines": [], "payout": 0, "invested": 0,
          "mode_before": w["mode"], "navi": None, "lamp": False, "face": None}

    # 投入（リプレイは再遊技＝投入なし）
    if role != "replay":
        if w["medals"] < BET:
            ev["error"] = "medal_short"
            return ev
        w["medals"] -= BET
        ev["invested"] = BET
        w["stats"]["invested"] += BET

    w["stats"]["games"] += 1
    payout = 0
    bell_hit = False

    # ── 押し順ベル ──
    if role == "bell":
        if w["mode"] == "at":
            navi = RNG.choice(["L", "C", "R"])
            ev["navi"] = navi
            bell_hit = True                      # AT 中はナビ通り＝的中
        else:
            correct = RNG.choice(["L", "C", "R"])
            chosen = (order or RNG.choice(["L", "C", "R"])).upper()
            bell_hit = (chosen == correct)
            ev["navi"] = f"押し順 {chosen}（正解 {correct}）"
        payout += BELL_PAY if bell_hit else BELL_SPILL
    elif role == "cherry":
        payout += CHERRY_PAY
    ev["face"] = reel_face(role, bell_hit)

    # ── 状態機械 ──
    if w["mode"] == "normal":
        w["since_bonus"] += 1
        hit_at = False
        # レア役からの抽選
        if role == "kakutei":
            hit_at = True
        elif role == "strong" and RNG.random() < p["at_from_strong"]:
            hit_at = True
        elif role == "chance" and RNG.random() < p["cz_from_chance"]:
            _enter_cz(w, ev)
        elif role == "cherry" and RNG.random() < p["cz_from_cherry"]:
            _enter_cz(w, ev)
        elif RNG.random() < p["soft_ceiling"] / 50.0:
            hit_at = True
        # 天井
        if not hit_at and w["mode"] == "normal" and w["since_bonus"] >= CEILING:
            ev["lines"].append(f"🔧 天井到達（{CEILING}G）！ 救済 CZ 突入")
            _enter_cz(w, ev)
        if hit_at:
            ev["lamp"] = True
            _enter_at(w, ev, reason=("確定役" if role == "kakutei" else "強チャンス"))

    elif w["mode"] == "cz":
        w["cz_remain"] -= 1
        success = role in ("strong", "kakutei") or RNG.random() < p["cz_success"]
        if success:
            ev["lamp"] = True
            ev["lines"].append("🟦 PR REVIEW 通過！ レビュー承認 → AT 当選")
            _enter_at(w, ev, reason="CZ 成功")
        elif w["cz_remain"] <= 0:
            ev["lines"].append("🟥 PR REVIEW 失敗（CHANGES REQUESTED）… 通常へ")
            w["mode"] = "normal"

    elif w["mode"] == "at":
        w["at_games"] -= 1
        if payout > 0:
            ev["lines"].append(f"🚀 SHIP +{payout}枚")
        w["at_payout"] += payout
        # 上乗せ抽選
        if role in ("strong", "kakutei"):
            add = RNG.choice(AT_UPLIFT_STRONG)
            w["at_games"] += add
            ev["lamp"] = True
            ev["lines"].append(f"🎁 コミット上乗せ +{add}G！（残り {w['at_games']}G）")
        elif role == "chance" and RNG.random() < AT_UPLIFT_CHANCE_PROB:
            add = RNG.choice(AT_UPLIFT_CHANCE)
            w["at_games"] += add
            ev["lines"].append(f"🎁 上乗せ +{add}G（残り {w['at_games']}G）")
        # セット終了判定
        if w["at_games"] <= 0:
            if RNG.random() < p["at_continue"]:
                w["at_sets"] += 1
                w["at_games"] = AT_SET_GAMES
                w["stats"]["max_set"] = max(w["stats"]["max_set"], w["at_sets"])
                ev["lines"].append(f"🔁 SHIP RUSH 継続！ 第 {w['at_sets']+1} セット（+{AT_SET_GAMES}G）")
            else:
                ev["lines"].append(f"🏁 SHIP RUSH 終了 — 今回 {w['at_payout']}枚 獲得")
                w["stats"]["best_at"] = max(w["stats"]["best_at"], w["at_payout"])
                w["mode"] = "normal"
                w["since_bonus"] = 0
                w["at_payout"] = 0
                w["at_sets"] = 0

    # 払い出し反映
    w["medals"] += payout
    w["stats"]["payout"] += payout
    ev["payout"] = payout
    ev["mode_after"] = w["mode"]
    return ev


def _enter_cz(w: dict, ev: dict) -> None:
    if w["mode"] != "normal":
        return
    w["mode"] = "cz"
    w["cz_remain"] = CZ_GAMES
    w["stats"]["cz_hits"] += 1
    ev["lines"].append(f"🟦 CZ「PR REVIEW」突入（{CZ_GAMES}G・AT 当選を懸ける）")


def _enter_at(w: dict, ev: dict, reason: str) -> None:
    w["mode"] = "at"
    w["at_games"] = AT_SET_GAMES
    w["at_sets"] = 1
    w["at_payout"] = 0
    w["since_bonus"] = 0
    w["stats"]["at_hits"] += 1
    ev["lines"].append(f"🚀🚀 SHIP RUSH 突入！（{reason}・初期 {AT_SET_GAMES}G・押し順ナビ ON）")


# ── 表示 ─────────────────────────────────────────────────────────────────────
ROLE_LABEL = {
    "replay": "リプレイ（再ビルド）", "bell": "押し順ベル", "cherry": "弱レア ☕",
    "chance": "チャンス目 🐛", "strong": "強チャンス 🔥", "kakutei": "確定役 💎",
    "hazure": "ハズレ",
}
MODE_LABEL = {"normal": "通常", "cz": "CZ・PR REVIEW", "at": "AT・SHIP RUSH"}


def fmt_game(w: dict, ev: dict) -> str:
    if ev.get("error") == "medal_short":
        return (f"[メダル不足] 手持ち {w['medals']}枚 < 投入 {BET}枚。"
                f"`cashin <n>` で補充するか、引き際かも。")
    face = ev["face"]
    L = []
    head = f"🎰 RIGSINO ｜ {MODE_LABEL[ev['mode_before']]}"
    if ev["mode_before"] == "at":
        head += f" 残り{max(w['at_games'],0)}G・第{w['at_sets']}set"
    elif ev["mode_before"] == "cz":
        head += f" 残り{max(w['cz_remain'],0)}G"
    elif ev["mode_before"] == "normal":
        head += f" ｜ ハマり{w['since_bonus']}G/{CEILING}"
    head += f" ｜ 手持ち{w['medals']}枚"
    L.append(head)
    L.append("")
    L.append("   ╔════╤════╤════╗")
    L.append(f"   ║ {face[0]} │ {face[1]} │ {face[2]} ║")
    L.append("   ╚════╧════╧════╝")
    role_line = f"   成立: {ROLE_LABEL.get(ev['role'], ev['role'])}"
    if ev["navi"]:
        role_line += f" ｜ {ev['navi']}"
    if ev["payout"]:
        role_line += f" → +{ev['payout']}枚"
    elif ev["invested"]:
        role_line += f" → こぼし/ハズレ"
    L.append(role_line)
    if ev["lamp"]:
        L.append("   💡✨ DEPLOY ランプ点灯！")
    for ln in ev["lines"]:
        L.append("   " + ln)
    if ev["mode_after"] != ev["mode_before"]:
        L.append(f"   ── 状態: {MODE_LABEL[ev['mode_before']]} → {MODE_LABEL[ev['mode_after']]}")
    L.append(f"手持ち: {w['medals']}枚  [ spin / auto N / status / cash out ]")
    return "\n".join(L)


def fmt_status(w: dict) -> str:
    s = w["stats"]
    inv = s["invested"] or 1
    payout_rate = 100 * s["payout"] / inv
    diff = w["medals"] - DEFAULT_WALLET["medals"]
    cur = MODE_LABEL[w["mode"]]
    if w["mode"] == "at":
        cur += f"（残り{max(w['at_games'],0)}G・第{w['at_sets']}set・今回{w['at_payout']}枚）"
    elif w["mode"] == "cz":
        cur += f"（残り{w['cz_remain']}G）"
    else:
        cur += f"（ハマり{w['since_bonus']}G / 天井{CEILING}）"
    return "\n".join([
        "🎰 RIGSINO 台データ 🎰  ⚠️架空メダル・遊び",
        f"  手持ちメダル : {w['medals']} 枚",
        f"  設定         : {w['setting']}（看破は戦績から推定）",
        f"  現在状態     : {cur}",
        "  ── 生涯戦績 ──",
        f"  総ゲーム数   : {s['games']}",
        f"  投入 / 払出  : {s['invested']} / {s['payout']}   実測機械割: {payout_rate:.1f}%",
        f"  AT 初当たり  : {s['at_hits']} 回（CZ 当選 {s['cz_hits']} 回）",
        f"  最高 AT 獲得 : {s['best_at']} 枚   最高セット: {s['max_set']}",
        f"  最高所持     : {s['best_medals']} 枚   通算差枚: {diff:+d}（開始 {DEFAULT_WALLET['medals']}）",
    ])


def fmt_payouts() -> str:
    return "\n".join([
        "🎰 RIGSINO 早見表（6号機風 AT）🎰  ⚠️架空メダル・遊び",
        "",
        "【通常時】3枚掛けで毎G抽選:",
        f"  🔄 リプレイ        投入なしで再遊技（1/{ROLE_ODDS['replay']:.1f}）",
        f"  🔔 押し順ベル      第1停止 3択・正解 +{BELL_PAY}枚/外れ +{BELL_SPILL}枚（1/{ROLE_ODDS['bell']:.1f}）",
        f"  ☕ 弱レア          +{CHERRY_PAY}枚＋CZ 抽選（1/{ROLE_ODDS['cherry']:.0f}）",
        f"  🐛 チャンス目      CZ 抽選（1/{ROLE_ODDS['chance']:.0f}）",
        f"  🔥 強チャンス      AT 期待大（1/{ROLE_ODDS['strong']:.0f}）",
        f"  💎 確定役          AT 直撃確定（1/{ROLE_ODDS['kakutei']:.0f}）",
        f"  🔧 天井            {CEILING}G ハマりで救済 CZ",
        "",
        "【CZ・PR REVIEW】数G・AT 当選を懸ける（レビュー承認＝当選）。",
        "【AT・SHIP RUSH 🚀】押し順ナビでベル獲得＝純増。強役で G 数上乗せ。",
        f"  セット {AT_SET_GAMES}G・継続率は設定依存（高設定ほど継続）。",
        "",
        "設定1〜6 で AT 初当たり率・CZ 成功率・継続率が変化（機械割が動く）。",
        "押し順: spin --order L|C|R（通常時のみ。AT 中はナビ自動）。",
    ])


# ── サブコマンド ─────────────────────────────────────────────────────────────
def cmd_spin(w, args):
    ev = play_one(w, args.order)
    print(fmt_game(w, ev))
    return 1 if ev.get("error") else 0


def cmd_auto(w, args):
    n = args.n
    start_medals = w["medals"]
    start_games = w["stats"]["games"]
    highlights = []
    for _ in range(n):
        ev = play_one(w, None)
        if ev.get("error"):
            highlights.append("（メダル不足で中断）")
            break
        if ev["lamp"] or ev["mode_after"] != ev["mode_before"] or any("継続" in x or "終了" in x for x in ev["lines"]):
            for ln in ev["lines"]:
                highlights.append(ln)
    played = w["stats"]["games"] - start_games
    diff = w["medals"] - start_medals
    print(f"🎰 RIGSINO オート消化 {played}G  差枚 {diff:+d}枚  手持ち {w['medals']}枚")
    if highlights:
        print("── ハイライト ──")
        for h in highlights[-12:]:
            print("  " + h)
    print(fmt_status(w))
    return 0


def cmd_status(w, args):
    print(fmt_status(w))
    return 0


def cmd_reset(w, args):
    setting = args.setting if args.setting else RNG.randint(1, 6)
    medals = w["medals"]
    fresh = json.loads(json.dumps(DEFAULT_WALLET))
    fresh["created"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    fresh["medals"] = medals             # メダルは台移動でも持ち越す
    fresh["setting"] = setting
    fresh["stats"]["best_medals"] = medals
    w.clear()
    w.update(fresh)
    print(f"🪑 台移動完了。新しい台に座りました（設定は伏せ）。手持ち {medals}枚 で再スタート。")
    return 0


def cmd_cashin(w, args):
    if args.amount < 1:
        print("[ERROR] 補充は 1 以上。", file=sys.stderr)
        return 1
    w["medals"] += args.amount
    print(f"🪙 架空メダル +{args.amount} 補充。手持ち {w['medals']}枚（※遊び・現金ではない）")
    return 0


def cmd_payouts(w, args):
    print(fmt_payouts())
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Rigsino — dev-themed 6号機風 AT slot with persistent medals")
    p.add_argument("--wallet", help="ウォレット JSON パス（既定 ~/.claude/rig/rigsino/wallet.json）")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("spin"); sp.add_argument("--order", choices=list("LCRlcr"))
    ap = sub.add_parser("auto"); ap.add_argument("n", nargs="?", type=int, default=50)
    sub.add_parser("status")
    rp = sub.add_parser("reset"); rp.add_argument("--setting", type=int, choices=range(1, 7))
    cp = sub.add_parser("cashin"); cp.add_argument("amount", type=int)
    sub.add_parser("payouts")

    args = p.parse_args()
    path = wallet_path(args.wallet)
    w = load_wallet(path)
    handlers = {"spin": cmd_spin, "auto": cmd_auto, "status": cmd_status,
                "reset": cmd_reset, "cashin": cmd_cashin, "payouts": cmd_payouts}
    rc = handlers[args.cmd](w, args)
    if rc == 0 and args.cmd not in ("status", "payouts"):
        save_wallet(path, w)
    return rc


if __name__ == "__main__":
    sys.exit(main())
