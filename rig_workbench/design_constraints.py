#!/usr/bin/env python3
"""check_design_constraints — 宣言したデザイン制約を、成果物に機械的に突き合わせるゲート。

デザイン仕様書は「ブランドカラーを使う」と書けてしまいます。書いた通りに使われたかは、
読んでも分かりません。このスクリプトは、プロジェクトが宣言したトークン・コンポーネント・
禁止表現を読み、成果物の中の生値・トークン参照・コンポーネント参照・禁止表現を突き合わせ、
一致しないものを位置つきで報告します。

    python3 scripts/check_design_constraints.py docs/design/login.md
    python3 scripts/check_design_constraints.py --constraints path/to/c.json --json artifacts/

規則の正本は `skills/engine/facets/policies/design-constraint-rules.md`、制約ファイルの形は
`skills/engine/manifests/design-constraints.schema.json` です。

検出するのは次の4クラスだけです:

    raw-value              宣言トークンに解決しない色・長さ・フォントが書かれている
    unknown-token          存在しないトークン名を参照している
    unknown-component      インベントリに無いコンポーネントを参照している
    prohibited-expression  禁止表現が出現している

トークン名も生値も書かずに散文で指示したもの（「ブランドの青を使う」）は**構造的に
検出クラスの外**です。捕れません。捕れたことにしません——レビュアの判定に回します。

use と mention も区別しません。「#0A84FF から移行した」という理由書きも、未一致の生値として
報告します。向きを読むのはセンサーの仕事ではありません。再現率はここが、適合率はレビュアが
担当します。

状態は3つあります。「宣言が無い」と「宣言はあるが検査できなかった」は別物です。

    checked         制約ファイルを読み、成果物を突き合わせた。
    unchecked       **宣言はあるのに検査が成立しなかった。** [要記入] が残っている /
                    JSON が壊れている / 明示指定した制約ファイルが無い / 成果物が読めない。
    not-configured  そもそも宣言が無い（--if-configured 指定時に、既定パスが不在）。
                    合格でも未検査でもない第三の状態です。verdict に必ず転記します。

終了コード:

    0   checked かつ違反なし。または not-configured。
    1   checked かつ違反あり。
    2   unchecked。走らなかったことを合格として扱わないための区別です。

`--report <path>` は、どの状態でも JSON 報告を書きます。orchestrate の checks 実行系
(`_run_step_checks`) は stdout を捨てるため、**報告の本体はこのファイルです。**
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata

UNFILLED = "[要記入]"
DEFAULT_CONSTRAINTS = os.path.join(".claude", "design-constraints.json")

# 走査するテキスト拡張子。ディレクトリを渡されたときにだけ使う。
TEXT_SUFFIXES = (
    ".md", ".markdown", ".txt", ".css", ".scss", ".sass", ".less",
    ".html", ".htm", ".vue", ".svelte", ".js", ".jsx", ".ts", ".tsx", ".json",
)

# 生値。色は長い表記から順に試す（#aabbccdd が #aabbcc + dd に割れないように）。
RE_HEX = re.compile(
    r"#(?P<d>[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})(?![0-9a-fA-F])"
)
RE_RGB = re.compile(
    r"\brgba?\(\s*(\d{1,3})\s*[,\s]\s*(\d{1,3})\s*[,\s]\s*(\d{1,3})\s*"
    r"(?:[,/]\s*(\d*\.?\d+\s*%?)\s*)?\)",
    re.IGNORECASE,
)
# 長さ。`--sp-16px` のような識別子の一部を拾わないよう直前を制限する。
RE_LENGTH = re.compile(r"(?<![\w.$#-])(\d+(?:\.\d+)?)(px|rem|em)\b", re.IGNORECASE)
RE_FONT_DECL = re.compile(r"font-family\s*:\s*([^;\n}]+)", re.IGNORECASE)
# `font:` ショートハンドでサイズ／行送りが占める場所。ここより後ろが書体。
RE_FONT_SIZE_SLOT = re.compile(
    r"var\([^)]*\)|\d+(?:\.\d+)?(?:px|rem|em|pt|%)|/\s*[\d.]+", re.IGNORECASE
)

# 参照構文。policy 5 と 7 が正本。
RE_TOKEN_REF = re.compile(r"\btoken\(\s*([A-Za-z0-9_.\-]+)\s*\)")
RE_CSS_VAR = re.compile(r"\bvar\(\s*--([A-Za-z0-9_\-]+)\s*[,)]")
# JSX の `<` は識別子文字の後に来ない。TypeScript の型引数（`Map<String, Int>`・`f<T>`）は
# 必ず識別子の直後に来る。さらに TSX のアロー関数ジェネリクス `<T,>(x) => x` は識別子の
# 後ろではないので、名前の直後の `,` で分ける——**JSX の要素名の直後に `,` は来ない**。
RE_COMPONENT = re.compile(
    r"(?<![A-Za-z0-9_$])<([A-Z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)\b"
    r"(?!\s*,)(?!\s+extends\b)"
)
# JSX を書けない拡張子。ここでコンポーネント検査をすると、型引数を要素と読み違える。
NO_JSX_SUFFIXES = (".ts", ".css", ".scss", ".sass", ".less", ".json")

# CSS 宣言。名前付き色とフォントは、散文で同じ語が出るため宣言の中でだけ読む
# （「エラーは red で示す」を色の生値として上げない）。
# 属性値（`<div style="color: red">`）の先頭宣言にも当たるよう、引用符を許す。
RE_DECL = re.compile(r"""(?:^|[;{,"'])\s*([-a-zA-Z]+)\s*:\s*([^;{}\n]+)""")
COLOUR_PROPERTIES = ("color", "background", "border", "outline", "shadow", "fill", "stroke")

# Unicode の `Default_Ignorable_Code_Point`——**描画時に無視されるべきと Unicode 自身が
# 定めた集合**。ここを自分で見立てると必ず外す。最初はゼロ幅5文字を列挙して U+00AD で破られ、
# 次に「カテゴリ `Cf`」に広げて U+3164 HANGUL FILLER（カテゴリ `Lo`＝**文字**なのに
# 描画されない）と U+FE0F（`Mn`）で破られた。**カテゴリ列挙も列挙である。**
# 自分の見立てではなく、「無視されるべき」を定義している側の集合を使う。
DEFAULT_IGNORABLE = (
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160),
    (0x17B4, 0x17B5), (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E),
    (0x2060, 0x206F), (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)


def is_invisible(ch: str) -> bool:
    """表示に現れないのに、部分文字列としては一致を壊す文字か。

    `Cf`（書式制御）は将来の追加も拾えるようカテゴリで、それ以外は
    `Default_Ignorable_Code_Point` の範囲で判定する。

    **双方向制御は落とすが、並べ替えは元に戻さない。** RLO で表示上だけ禁止語になる文章は
    検出しない——「落としている」と「無効化している」は違う。後者は主張しない。
    """
    if unicodedata.category(ch) == "Cf":
        return True
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in DEFAULT_IGNORABLE)


# `regex: true` の照合に置く上限。**破滅的バックトラックは1回の `re.search` が返ってこない**
# ので、同じプロセス内で経過時間を測っても止められない（`^(a+)+$` は 41 文字で無限に近い）。
# 別プロセスに出して壁時計で殺す。超えたら合格ではなく未検査にする。
REGEX_TIME_BUDGET = 5.0
REGEX_MAX_CHARS = 200_000

# 正規表現だけを走らせる最小のワーカ。shell は使わない。入力は stdin の JSON。
_REGEX_WORKER = """
import json, re, sys
d = json.load(sys.stdin)
out = []
for r in d["rules"]:
    text = d["raw"] if r["cs"] else d["flat"]
    m = re.search(r["pattern"], text, 0 if r["cs"] else re.IGNORECASE)
    out.append(m.start() if m else None)
json.dump(out, sys.stdout)
"""


def regex_hits(rules: list[dict], raw: str, flat: str, where: str) -> list[int | None]:
    """正規表現の照合を別プロセスで行い、壁時計で打ち切る。

    パターンはプロジェクトが書き、本文は監査モードでは外部サイトの DOM である。
    プロセス内で回すと、破滅的なパターン1つでゲートが返ってこなくなる——
    **返ってこないゲートは、通らないのではなく何も守っていない。**
    """
    payload = json.dumps({
        "raw": raw, "flat": flat,
        "rules": [{"pattern": r["pattern"], "cs": bool(r.get("case_sensitive"))} for r in rules],
    })
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _REGEX_WORKER], input=payload,
            capture_output=True, text=True, timeout=REGEX_TIME_BUDGET, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise Unchecked(
            f"禁止表現（正規表現）の照合が {REGEX_TIME_BUDGET} 秒を超えた: {where} — "
            f"破滅的バックトラックの可能性。パターンを見直す"
        ) from exc
    except OSError as exc:
        raise Unchecked(f"正規表現の照合プロセスを起動できない: {exc}") from exc
    if proc.returncode != 0:
        raise Unchecked(
            f"正規表現の照合が失敗した: {where}: {proc.stderr.strip().splitlines()[-1:]}"
        )
    return json.loads(proc.stdout)

# CSS Color Module Level 4 の名前付き色。数値記法だけを色と見なすと、
# `dodgerblue` と書くだけで宣言集合の検査を抜けられる。
CSS_NAMED_COLOURS = {
    "aliceblue": "#f0f8ff", "antiquewhite": "#faebd7", "aqua": "#00ffff",
    "aquamarine": "#7fffd4", "azure": "#f0ffff", "beige": "#f5f5dc", "bisque": "#ffe4c4",
    "black": "#000000", "blanchedalmond": "#ffebcd", "blue": "#0000ff",
    "blueviolet": "#8a2be2", "brown": "#a52a2a", "burlywood": "#deb887",
    "cadetblue": "#5f9ea0", "chartreuse": "#7fff00", "chocolate": "#d2691e",
    "coral": "#ff7f50", "cornflowerblue": "#6495ed", "cornsilk": "#fff8dc",
    "crimson": "#dc143c", "cyan": "#00ffff", "darkblue": "#00008b", "darkcyan": "#008b8b",
    "darkgoldenrod": "#b8860b", "darkgray": "#a9a9a9", "darkgreen": "#006400",
    "darkgrey": "#a9a9a9", "darkkhaki": "#bdb76b", "darkmagenta": "#8b008b",
    "darkolivegreen": "#556b2f", "darkorange": "#ff8c00", "darkorchid": "#9932cc",
    "darkred": "#8b0000", "darksalmon": "#e9967a", "darkseagreen": "#8fbc8f",
    "darkslateblue": "#483d8b", "darkslategray": "#2f4f4f", "darkslategrey": "#2f4f4f",
    "darkturquoise": "#00ced1", "darkviolet": "#9400d3", "deeppink": "#ff1493",
    "deepskyblue": "#00bfff", "dimgray": "#696969", "dimgrey": "#696969",
    "dodgerblue": "#1e90ff", "firebrick": "#b22222", "floralwhite": "#fffaf0",
    "forestgreen": "#228b22", "fuchsia": "#ff00ff", "gainsboro": "#dcdcdc",
    "ghostwhite": "#f8f8ff", "gold": "#ffd700", "goldenrod": "#daa520", "gray": "#808080",
    "green": "#008000", "greenyellow": "#adff2f", "grey": "#808080",
    "honeydew": "#f0fff0", "hotpink": "#ff69b4", "indianred": "#cd5c5c",
    "indigo": "#4b0082", "ivory": "#fffff0", "khaki": "#f0e68c", "lavender": "#e6e6fa",
    "lavenderblush": "#fff0f5", "lawngreen": "#7cfc00", "lemonchiffon": "#fffacd",
    "lightblue": "#add8e6", "lightcoral": "#f08080", "lightcyan": "#e0ffff",
    "lightgoldenrodyellow": "#fafad2", "lightgray": "#d3d3d3", "lightgreen": "#90ee90",
    "lightgrey": "#d3d3d3", "lightpink": "#ffb6c1", "lightsalmon": "#ffa07a",
    "lightseagreen": "#20b2aa", "lightskyblue": "#87cefa", "lightslategray": "#778899",
    "lightslategrey": "#778899", "lightsteelblue": "#b0c4de", "lightyellow": "#ffffe0",
    "lime": "#00ff00", "limegreen": "#32cd32", "linen": "#faf0e6", "magenta": "#ff00ff",
    "maroon": "#800000", "mediumaquamarine": "#66cdaa", "mediumblue": "#0000cd",
    "mediumorchid": "#ba55d3", "mediumpurple": "#9370db", "mediumseagreen": "#3cb371",
    "mediumslateblue": "#7b68ee", "mediumspringgreen": "#00fa9a",
    "mediumturquoise": "#48d1cc", "mediumvioletred": "#c71585", "midnightblue": "#191970",
    "mintcream": "#f5fffa", "mistyrose": "#ffe4e1", "moccasin": "#ffe4b5",
    "navajowhite": "#ffdead", "navy": "#000080", "oldlace": "#fdf5e6", "olive": "#808000",
    "olivedrab": "#6b8e23", "orange": "#ffa500", "orangered": "#ff4500",
    "orchid": "#da70d6", "palegoldenrod": "#eee8aa", "palegreen": "#98fb98",
    "paleturquoise": "#afeeee", "palevioletred": "#db7093", "papayawhip": "#ffefd5",
    "peachpuff": "#ffdab9", "peru": "#cd853f", "pink": "#ffc0cb", "plum": "#dda0dd",
    "powderblue": "#b0e0e6", "purple": "#800080", "rebeccapurple": "#663399",
    "red": "#ff0000", "rosybrown": "#bc8f8f", "royalblue": "#4169e1",
    "saddlebrown": "#8b4513", "salmon": "#fa8072", "sandybrown": "#f4a460",
    "seagreen": "#2e8b57", "seashell": "#fff5ee", "sienna": "#a0522d", "silver": "#c0c0c0",
    "skyblue": "#87ceeb", "slateblue": "#6a5acd", "slategray": "#708090",
    "slategrey": "#708090", "snow": "#fffafa", "springgreen": "#00ff7f",
    "steelblue": "#4682b4", "tan": "#d2b48c", "teal": "#008080", "thistle": "#d8bfd8",
    "tomato": "#ff6347", "turquoise": "#40e0d0", "violet": "#ee82ee", "wheat": "#f5deb3",
    "white": "#ffffff", "whitesmoke": "#f5f5f5", "yellow": "#ffff00",
    "yellowgreen": "#9acd32",
}
# 色ではなく指示語。宣言集合と突き合わせる意味がない。
COLOUR_NON_VALUES = frozenset({"inherit", "initial", "unset", "revert", "none",
                               "transparent", "currentcolor", "auto"})


def fold(text: str) -> str:
    r"""全角と半角を同じ参照として読む。

    `token（color.brand）` は日本語 IME の自動変換で日常的に起きる。丸括弧が全角だと
    `token\(` に当たらず、参照そのものが**見えなくなる**——違反が「無い」のではなく
    「検査されていない」状態になる。列は報告しない（行だけ）ので、NFKC で潰してよい。
    """
    return _normalize(text, collapse_space=False)[0]


class Unchecked(Exception):
    """宣言はあるのに検査が成立しなかった。合格でも不合格でもない。"""


class NotConfigured(Exception):
    """そもそも宣言が無い。未検査とは別の状態——強制すべき制約が存在しない。"""


# ── 正規化 ────────────────────────────────────────────────────────────────────
def normalize_hex(digits: str) -> str:
    h = digits.lower()
    if len(h) in (3, 4):
        h = "".join(c * 2 for c in h)
    if len(h) == 8 and h.endswith("ff"):
        h = h[:6]
    return "#" + h


def normalize_rgb(r: str, g: str, b: str, a: str | None) -> str | None:
    try:
        vals = [int(r), int(g), int(b)]
    except ValueError:
        return None
    if any(v > 255 for v in vals):
        return None
    out = "#%02x%02x%02x" % tuple(vals)
    if a is not None:
        raw_alpha = a.strip()
        try:
            alpha = float(raw_alpha.rstrip("%").strip())
            if raw_alpha.endswith("%"):
                alpha /= 100.0  # `/ 50%` と `, 0.5` は同じ色。片方だけ不透明扱いにしない
        except ValueError:
            return out
        if alpha <= 1.0 and abs(alpha - 1.0) > 1e-9:
            out += "%02x" % max(0, min(255, round(alpha * 255)))
    return out


def normalize_length(number: str, unit: str) -> str:
    value = float(number)
    text = ("%f" % value).rstrip("0").rstrip(".") if "." in number else str(int(value))
    return f"{text}{unit.lower()}"


def normalize_font(raw: str) -> str:
    first = raw.split(",")[0]
    return first.strip().strip("'\"").strip().lower()


def css_prop(prop: str) -> str:
    """JSX の `fontFamily` を CSS の `font-family` と同じものとして読む。

    キャメルケースを見ないと、`style={{ fontFamily: "Comic Sans MS" }}` が丸ごと
    すり抜ける。同じ宣言を書き方で見分けるのは、検査ではなく偶然になる。
    """
    return re.sub(r"(?<!^)(?=[A-Z])", "-", prop).lower()


def _declaration_spans(text: str) -> list[tuple[int, int]]:
    """`prop: value` の value が占める範囲。数字だけの16進を色と見なす条件に使う。"""
    return [(m.start(2), m.end(2)) for m in RE_DECL.finditer(text)]


def numeric_colors_in(text: str, anywhere: bool = False) -> list[str]:
    """16進と rgb() を読む。

    **数字だけの3桁・4桁は CSS 宣言の中でだけ色と見なす。** `fixes #123` / `#1234` の
    Issue 番号が色に化けていた。6桁・8桁は Issue 番号としては現実的でないので散文でも拾う
    ——使用トークン表の `| navy | #003366 |` を落とすほうが害が大きい。英字を含むもの
    （`#f00`・`#fff`）は色としか読めないので、桁数によらず拾う。
    `anywhere=True` は宣言側（値であることが確定している）用。
    """
    spans = [] if anywhere else _declaration_spans(text)
    found: list[str] = []
    for m in RE_HEX.finditer(text):
        digits = m.group("d")
        if not anywhere and digits.isdigit() and len(digits) in (3, 4):
            if not any(lo <= m.start() < hi for lo, hi in spans):
                continue
        found.append(normalize_hex(digits))
    for m in RE_RGB.finditer(text):
        value = normalize_rgb(*m.groups())
        # 範囲外（rgb(300,0,0)）は None。落とすと「見えない」＝「無い」になるので、
        # 解決できない生値として字面のまま報告に載せる。
        found.append(value if value is not None else re.sub(r"\s+", "", m.group(0)).lower())
    return found


def colors_in(text: str) -> list[str]:
    return numeric_colors_in(text) + named_colors_in(text)


def classify_declared(value: str) -> tuple[list[str], list[str], str | None]:
    """宣言された値ひとつを、**成果物側とまったく同じ抽出器**で振り分ける。

    ここが成果物側とずれると、**宣言した値そのものが違反として上がる。**
    `{"surface": "white"}` がフォント扱いになり、`{"focus": "2px solid red"}` の
    `red` と `{"body": "16px/1.5 Inter, sans-serif"}` の `Inter` が宣言から落ちて、
    それを使った成果物が違反になっていた。

    名前付き色と書体は CSS 宣言の中でしか読まない（散文の誤検出を避けるため）ので、
    宣言された値を**合成した宣言文に載せてから**同じ関数に渡す。宣言された値は
    「値であること」が確定しているので、この合成は嘘をつかない。
    """
    folded = fold(value)
    colors = numeric_colors_in(folded, anywhere=True)
    lengths = lengths_in(folded)
    bare = folded.strip().strip("'\"").strip().lower()
    if bare in CSS_NAMED_COLOURS:
        return [*colors, CSS_NAMED_COLOURS[bare]], lengths, None

    # ここで「枠線ショートハンドか書体か」を**当てにいかない**。プロパティ名が無い以上
    # `Solid Grotesk`（実在書体）と `2px solid red` は語彙では分けられず、線種キーワード
    # 表で分けたら実在書体が宣言から丸ごと落ちた。判別器を賢くしても次の書体で破れる。
    #
    # 代わりに宣言側の**非対称性**を使う。過少宣言は「ユーザーが宣言した値そのもの」を
    # 違反として上げる（致命的）。過剰宣言はその文字列の検出だけを緩める。だから
    # **曖昧なら書体としても登録する**——書体名の過剰宣言はその名前1つにしか波及しない。
    #
    # 色は同じ扱いにできない。`#000000` を余分に宣言すると、その色の**あらゆる**使用が
    # 検出されなくなる。名前付き色は枠線ショートハンドの形（末尾が色語 ∧ 長さを含む）の
    # ときだけ読む。`700 24px/1.2 Black Han Sans` の `black` を色にしないのはこのため。
    # **ここで CSS プロパティを推測しない。** 裸の値から「枠線か影か書体か」を当てる
    # 判別器は3代続けて実値に破れた——線種キーワード表は `Solid Grotesk` に、末尾語は
    # `red 2px solid` に、語数は `0 1px 2px black` に。次の判別器は `inset 0 1px 0 white`
    # で破れる。長さを含む値の中の色語は**すべて**色として宣言する。
    #
    # 代償は `16px Display Black` のような書体指定が `#000000` を宣言してしまうこと。
    # 過剰宣言はその色のあらゆる使用を通すので、**黙って**起きてはならない——どの
    # トークンがどの色を宣言したかを報告の `composite_declarations` に載せ、レビュアが
    # 読めるようにする。過少宣言（宣言した影が違反になる）には同等の救済が無い。
    if lengths:
        colors = colors + named_colors_in(f"color: {folded};")

    shorthand = fonts_in(f"font: {folded};")
    family = shorthand[0] if shorthand else (
        normalize_font(folded) if not colors and not lengths else None
    )
    if family and (
        family in CSS_NAMED_COLOURS or numeric_colors_in(fold(family), anywhere=True)
    ):
        family = None  # `2px red` の `red` は色であって書体ではない
    return colors, lengths, family


def named_colors_in(text: str) -> list[str]:
    """名前付き色は CSS 宣言の中でだけ読む。

    散文の「エラーは red で示す」を生値として上げないため、単語の総当たりはしない。
    """
    found: list[str] = []
    for prop, value in RE_DECL.findall(text):
        if not any(k in css_prop(prop) for k in COLOUR_PROPERTIES):
            continue
        for word in re.findall(r"[A-Za-z]{3,20}", value):
            low = word.lower()
            if low in COLOUR_NON_VALUES:
                continue
            if low in CSS_NAMED_COLOURS:
                found.append(CSS_NAMED_COLOURS[low])
    return found


def fonts_in(text: str) -> list[str]:
    """`font-family:` と `font:` ショートハンドの両方から書体名を読む。

    ショートハンドは長さ（`16px` / `16px/1.5`）の後ろが書体なので、長さが無い値
    （`font: inherit`）は書体を特定できない——特定できないものは報告しない。
    """
    found: list[str] = []
    for prop, value in RE_DECL.findall(text):
        low = css_prop(prop)
        if low == "font-family":
            family = normalize_font(value)
        elif low == "font":
            # ショートハンドの書体は、サイズ／行送りより後ろにある。サイズは
            # `16px` とは限らず `var(--font-size-heading)` のこともあるので、
            # 長さリテラルだけを目印にすると空振りする。
            m = list(RE_FONT_SIZE_SLOT.finditer(value))
            if not m:
                continue
            family = normalize_font(value[m[-1].end():].lstrip("/0123456789. "))
        else:
            continue
        if family and not family.startswith("var(") and family not in COLOUR_NON_VALUES:
            found.append(family)
    return found


def lengths_in(text: str) -> list[str]:
    return [normalize_length(m.group(1), m.group(2)) for m in RE_LENGTH.finditer(text)]


# ── 制約ファイル ──────────────────────────────────────────────────────────────
def load_constraints(path: str) -> dict:
    if not os.path.isfile(path):
        raise Unchecked(f"制約ファイルが無い: {path}")
    try:
        # utf-8-sig: BOM 付きで保存された JSON を恒久 unchecked にしないため。
        with open(path, encoding="utf-8-sig") as fh:
            raw = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        # UnicodeDecodeError を捕らないと traceback で落ち、報告ファイルが書かれない。
        # 「どの状態でも報告を書く」が破れると、design-vet ⓪ が転記する status が消える。
        raise Unchecked(f"制約ファイルが読めない: {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise Unchecked(f"制約ファイルの JSON が壊れている: {path}: {exc}") from exc
    validate_constraints(data, path)
    # `_readme` は制約ではなく説明文で、雛形では本文中に UNFILLED を引用している。
    # 生テキストを走査すると、値を全部埋めた利用者が説明文のせいで永久に未検査になる。
    unfilled = unfilled_fields(data)
    if unfilled:
        raise Unchecked(
            f"制約ファイルに {UNFILLED} が残っている: {path} "
            f"({', '.join(unfilled[:5])}{' ほか' if len(unfilled) > 5 else ''}) "
            f"— 雛形のままでは合格にしない"
        )
    return data


def unfilled_fields(data: dict) -> list[str]:
    """UNFILLED が残っている制約の場所を列挙する。`_readme` は制約ではないので見ない。"""
    out: list[str] = []
    for group, entries in (data.get("tokens") or {}).items():
        for name, value in entries.items():
            if UNFILLED in value:
                out.append(f"tokens.{group}.{name}")
    for i, name in enumerate(data.get("components") or []):
        if UNFILLED in name:
            out.append(f"components[{i}]")
    for i, rule in enumerate(data.get("prohibited") or []):
        for key in ("pattern", "why"):
            if UNFILLED in str(rule.get(key, "")):
                out.append(f"prohibited[{i}].{key}")
    return out


ALLOWED_TOP_KEYS = frozenset({"_readme", "version", "tokens", "components", "prohibited"})
ALLOWED_RULE_KEYS = frozenset({"pattern", "why", "regex", "case_sensitive"})


def validate_constraints(data: object, path: str) -> None:
    """スキーマの構造だけを手で見る（jsonschema に依存しない）。

    **未知のキーは未検査にする。** スキーマは `additionalProperties: false` だが、
    ここが素通りしていたため `prohibited` を `prohibitted` と綴り間違えるだけで
    禁止表現の規則が丸ごと消え、`checked` / 違反 0 件 / exit 0 で緑になっていた。
    宣言したのに守られず、しかもゲートが合格を返す——このセンサーが潰すはずの状態そのもの。
    """
    if not isinstance(data, dict):
        raise Unchecked(f"制約ファイルがオブジェクトでない: {path}")
    extra = sorted(set(data) - ALLOWED_TOP_KEYS)
    if extra:
        raise Unchecked(
            f"スキーマに無いキー: {', '.join(extra)} — 綴りを確認（{path}）。"
            f"知らないキーは黙って無視しない"
        )
    version = data.get("version")
    if isinstance(version, bool) or version != 1:  # True == 1 なので bool を先に弾く
        raise Unchecked(f"未知の version: {data.get('version')!r} ({path})")
    tokens = data.get("tokens")
    if not isinstance(tokens, dict) or not tokens:
        raise Unchecked(f"tokens が無い、または空: {path}")
    for group, entries in tokens.items():
        if not isinstance(entries, dict) or not entries:
            raise Unchecked(f"tokens.{group} がオブジェクトでない、または空: {path}")
        for name, value in entries.items():
            if not isinstance(value, str) or not value:
                raise Unchecked(f"tokens.{group}.{name} が空でない文字列でない: {path}")
    components = data.get("components")
    if components is not None and not (
        isinstance(components, list) and all(isinstance(c, str) and c for c in components)
    ):
        raise Unchecked(f"components が文字列の配列でない: {path}")
    prohibited = data.get("prohibited")
    if prohibited is not None:
        if not isinstance(prohibited, list):
            raise Unchecked(f"prohibited が配列でない: {path}")
        for i, rule in enumerate(prohibited):
            if not isinstance(rule, dict):
                raise Unchecked(f"prohibited[{i}] がオブジェクトでない: {path}")
            unknown = sorted(set(rule) - ALLOWED_RULE_KEYS)
            if unknown:
                raise Unchecked(
                    f"prohibited[{i}] にスキーマに無いキー: {', '.join(unknown)} ({path})"
                )
            for key in ("pattern", "why"):
                if not isinstance(rule.get(key), str) or not rule[key]:
                    raise Unchecked(f"prohibited[{i}].{key} が空でない文字列でない: {path}")
            for key in ("regex", "case_sensitive"):
                # 型を見ないと `"case_sensitive": "false"` が真になり、規則が意図と逆に働く。
                # 宣言と挙動が食い違ったまま checked が返るのは、未知キーの素通りと同じ穴。
                if key in rule and not isinstance(rule[key], bool):
                    raise Unchecked(
                        f"prohibited[{i}].{key} が真偽値でない: {rule[key]!r} ({path})"
                    )
            if rule.get("regex"):
                try:
                    re.compile(rule["pattern"])
                except re.error as exc:
                    raise Unchecked(
                        f"prohibited[{i}].pattern が正規表現として不正: {exc} ({path})"
                    ) from exc


class Declared:
    """宣言集合。値の見た目から種別を決めるので、グループ名には依存しない。"""

    def __init__(self, data: dict) -> None:
        self.tokens: dict[str, dict[str, str]] = data["tokens"]
        self.colors: set[str] = set()
        self.lengths: set[str] = set()
        self.fonts: set[str] = set()
        # 値ひとつが複数の種別を宣言したときの内訳。センサーは裸の値から CSS
        # プロパティを推測しないので、`16px Display Black` は書体と色の両方を宣言する。
        # **その過剰宣言を黙って起こさない**ために、どのトークンが何を宣言したかを残す。
        self.composite: list[dict] = []
        for group, entries in self.tokens.items():
            for name, value in entries.items():
                # 複合値（"1px solid #ccc"）は色も長さも宣言する。片方だけ登録すると
                # 後ろの種別が黙って落ち、宣言済みの値が違反として上がる。
                colors, lengths, font = classify_declared(value)
                self.colors.update(colors)
                self.lengths.update(lengths)
                if font:
                    self.fonts.add(font)
                if len([x for x in (colors, lengths, [font] if font else []) if x]) > 1:
                    self.composite.append({
                        "token": f"{group}.{name}", "value": value,
                        "declared": {"colors": sorted(set(colors)),
                                     "lengths": sorted(set(lengths)),
                                     "font": font},
                    })
        self.components: list[str] | None = data.get("components")
        # `[]` は「未宣言」ではなく「禁止表現は1つも無い」。`components` と同じ読み。
        # 省略（キーが無い）だけが未宣言。両者を分けないと報告の not_declared が嘘をつく。
        self.prohibited: list[dict] | None = data.get("prohibited")

    def resolve_dotted(self, ref: str) -> bool:
        group, _, name = ref.partition(".")
        return bool(name) and name in self.tokens.get(group, {})

    def resolve_var(self, ref: str) -> bool:
        """`--color-brand-primary` を、宣言済みのグループ名を接頭辞として総当たりで解く。"""
        for group, entries in self.tokens.items():
            prefix = group + "-"
            if ref.startswith(prefix) and ref[len(prefix):] in entries:
                return True
        return False


# ── 走査 ──────────────────────────────────────────────────────────────────────
def scan_line(raw_line: str, lineno: int, path: str, decl: Declared) -> list[dict]:
    out: list[dict] = []
    line = fold(raw_line)

    def add(cls: str, value: str, detail: str) -> None:
        out.append(
            {"file": path, "line": lineno, "class": cls, "value": value, "detail": detail}
        )

    for ref in RE_TOKEN_REF.findall(line):
        if not decl.resolve_dotted(ref):
            add("unknown-token", ref, f"宣言に無いトークン名を参照している: token({ref})")
    for ref in RE_CSS_VAR.findall(line):
        if not decl.resolve_var(ref):
            add("unknown-token", ref, f"宣言に無いトークン名を参照している: var(--{ref})")

    for color in colors_in(line):
        if color not in decl.colors:
            add("raw-value", color, "宣言トークンに解決しない色")
    for length in lengths_in(line):
        if length not in decl.lengths:
            add("raw-value", length, "宣言トークンに解決しない長さ")
    for font in fonts_in(line):
        if font not in decl.fonts:
            add("raw-value", font, "宣言トークンに解決しないフォント")

    if decl.components is not None and not path.lower().endswith(NO_JSX_SUFFIXES):
        allowed = set(decl.components)
        for name in RE_COMPONENT.findall(line):
            if name not in allowed:
                add("unknown-component", name, f"インベントリに無いコンポーネント: <{name}>")

    return out


def _normalize(
    text: str, drop_all_space: bool = False, lower: bool = False, collapse_space: bool = True,
) -> tuple[str, list[int]]:
    """本文を**1つの規則で**正規化し、各文字がどの行から来たかを覚えておく。

    NFKC は**行ごとに、その行全体へ**当てる。1文字ずつでも、基底文字＋結合文字の
    まとまり単位でもいけない——NFD で分解されたハングルの jamo は `Lo`（starter）
    なので、どちらの単位でも合成されず、`한글` と NFD の `한글` が別物になる。
    まとまり単位は半角濁点も取りこぼしていた（U+FF9E は NFKC 後にしか `Mn` にならない）。
    **正規化の単位を賢くする試みは3周続けて破れた。単位を分けないのが答え。**

    順序は**破棄 → 合成 → 照合**。不可視文字を先に捨てないと、基底と結合記号の間に
    U+200B を1つ挟むだけで合成が壊れる（表示は変わらないのに一致しなくなる）。

    行に切るのは元の行番号を残すためだけで、改行は合成にも分解にも関与しないので
    行ごとの NFKC は本文全体の NFKC と一致する（テストで固定してある）。これにより
    `fold(x) == fold(NFKC(x))` が表に依存せず成り立つ。

    `fold`（行走査）と `_flatten`（禁止表現の照合）が同じ関数を通る。
    """
    out: list[str] = []
    idx: list[int] = []
    prev_space = False

    def emit(c: str, at: int) -> None:
        # 不可視文字はここに来ない——行を組み立てる前に落としてある。
        nonlocal prev_space
        if c.isspace() and collapse_space:
            if drop_all_space or prev_space:
                return
            out.append(" ")
            idx.append(at)
            prev_space = True
            return
        prev_space = False
        # `"İ".lower()` は2文字。1文字追加を前提にすると out と idx がずれ、
        # 禁止表現の位置引きが IndexError で落ちる（未検査になる）。
        low = c.lower() if lower else c
        out.extend(low)
        idx.extend([at] * len(low))

    pos = 0
    for n, line in enumerate(text.split("\n")):
        if n:
            emit("\n", pos - 1)
        start = pos
        pos += len(line) + 1
        # **捨ててから合成する。** 順序が逆だと、基底と結合記号の間に不可視文字を1つ
        # 挟むだけで合成が壊れる。`ﾀ<ZWSP>ﾞ` は表示上 `ﾀﾞ` と同一なのに `ダ` にならず、
        # 表の中にある文字（U+200B・U+00AD）で禁止表現が素通りしていた。
        # 正規化で長さが変わるので、その行から来たことだけを記録する（行番号は保てる）。
        for c in unicodedata.normalize("NFKC", "".join(
            ch for ch in line if not is_invisible(ch)
        )):
            emit(c, start)
    return "".join(out), idx


def _flatten(text: str, drop_all_space: bool, lower: bool = True) -> tuple[str, list[int]]:
    return _normalize(text, drop_all_space=drop_all_space, lower=lower)


def _line_of(offset: int, line_starts: list[int]) -> int:
    lo, hi = 0, len(line_starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if line_starts[mid] <= offset:
            lo = mid
        else:
            hi = mid - 1
    return lo + 1


def scan_prohibited(text: str, path: str, decl: Declared) -> list[dict]:
    """禁止表現は本文全体に対して照合する。

    行単位だと、日本語の折り返しで「こちらを / クリック」と割れただけで一致しなくなる。
    表示される成果物には禁止表現がそのまま出るので、これを不検出にしてはならない。
    """
    line_starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            line_starts.append(i + 1)

    flat_keep, idx_keep = _flatten(text, drop_all_space=False)
    flat_drop, idx_drop = _flatten(text, drop_all_space=True)
    # `case_sensitive: true` も同じ正規化を通す。生テキストに当てていたため、
    # ゼロ幅を1つ挟むだけで大小を区別する規則だけがすり抜けていた——
    # **同じ禁止表現が、指定の仕方で検出されたりされなかったりする状態**だった。
    cs_keep, cs_idx_keep = _flatten(text, drop_all_space=False, lower=False)
    cs_drop, cs_idx_drop = _flatten(text, drop_all_space=True, lower=False)

    out: list[dict] = []

    # 正規表現は非リテラルと同じ正規化済み本文に当てる（`case_sensitive: true` のときだけ
    # 生テキスト）。生テキストに当てていたため、ゼロ幅・全角・行折り返しの回避が
    # 「リテラル指定には効くが正規表現指定には効かない」＝書き方で検出力が変わる状態だった。
    regex_rules = [r for r in (decl.prohibited or []) if r.get("regex")]
    regex_at: dict[int, int | None] = {}
    if regex_rules:
        if len(flat_keep) > REGEX_MAX_CHARS:
            raise Unchecked(
                f"成果物が大きすぎて正規表現の禁止表現を検査できない: {path} "
                f"({len(flat_keep)} 文字 > {REGEX_MAX_CHARS})"
            )
        # リテラルと同じ**2パス**を与える。空白を残した本文で当たらなければ、空白を
        # 落とした本文にも当てる。これが無いと `regex: true` のときだけ日本語の
        # 折り返しで検出が消え、「本文全体に照合する」という主張が指定方法で変わる。
        found_at = regex_hits(regex_rules, cs_keep, flat_keep, path)
        found_at2 = regex_hits(regex_rules, cs_drop, flat_drop, path)
        for rule, at, at2 in zip(regex_rules, found_at, found_at2):
            cs = rule.get("case_sensitive")
            if at is not None:
                regex_at[id(rule)] = cs_idx_keep[at] if cs else idx_keep[at]
            elif at2 is not None:
                regex_at[id(rule)] = cs_idx_drop[at2] if cs else idx_drop[at2]
            else:
                regex_at[id(rule)] = None

    for rule in decl.prohibited or []:
        pattern = rule["pattern"]
        hit_at: int | None = None
        if rule.get("regex"):
            hit_at = regex_at.get(id(rule))
        elif rule.get("case_sensitive"):
            for flat, idx, drop in ((cs_keep, cs_idx_keep, False), (cs_drop, cs_idx_drop, True)):
                needle, _ = _flatten(pattern, drop_all_space=drop, lower=False)
                at = flat.find(needle) if needle else -1
                if at >= 0:
                    hit_at = idx[at]
                    break
        else:
            for flat, idx, drop in ((flat_keep, idx_keep, False), (flat_drop, idx_drop, True)):
                needle, _ = _flatten(pattern, drop_all_space=drop)
                at = flat.find(needle) if needle else -1
                if at >= 0:
                    hit_at = idx[at]
                    break
        if hit_at is not None:
            out.append({"file": path, "line": _line_of(hit_at, line_starts),
                        "class": "prohibited-expression", "value": pattern,
                        "detail": rule["why"]})
    return out


def _walk_error(exc: OSError) -> None:
    """読めないディレクトリを黙って飛ばさない。

    飛ばすと、その中の成果物を一度も見ていないのに `checked` / 違反 0 件になる。
    ファイルが読めないときは未検査にしているのに、ディレクトリだけ非対称だった。
    """
    raise Unchecked(f"成果物ディレクトリが読めない: {exc}")


def collect_artifacts(paths: list[str], skip: set[str]) -> tuple[list[str], list[dict]]:
    """検査する成果物と、**検査しなかったもの**を返す。

    走査根の外は読まない。シンボリックリンクは辿らない——`docs/design/x.json` が
    `~/.config/gh/hosts.yml` を指していると、その中身の断片が報告 JSON に載る。
    """
    files: list[str] = []
    skipped: list[dict] = []
    for path in paths:
        if os.path.islink(path):
            raise Unchecked(f"成果物がシンボリックリンク: {path} — 実体を指定する")
        if os.path.isdir(path):
            for parent, dirs, names in os.walk(path, onerror=_walk_error, followlinks=False):
                keep = []
                for d in sorted(dirs):
                    full_dir = os.path.join(parent, d)
                    if d.startswith("."):
                        skipped.append({"path": full_dir, "why": "dot-directory"})
                    elif os.path.islink(full_dir):
                        skipped.append({"path": full_dir, "why": "symlink"})
                    else:
                        keep.append(d)
                # 除いたものは黙って消さず `skipped` に残す。`.storybook/` の中の成果物を
                # 一度も見ていないのに「違反 0 件」になるのが、このセンサーが潰す状態そのもの。
                dirs[:] = keep
                for name in sorted(names):
                    full = os.path.join(parent, name)
                    if os.path.islink(full):
                        skipped.append({"path": full, "why": "symlink"})
                        continue
                    if name.lower().endswith(TEXT_SUFFIXES):
                        files.append(full)
                    else:
                        skipped.append({"path": full, "why": "extension"})
        elif os.path.isfile(path):
            files.append(path)
        else:
            raise Unchecked(f"成果物が無い: {path}")
    return [f for f in files if os.path.realpath(f) not in skip], skipped


def scan_file(path: str, decl: Declared) -> list[dict]:
    try:
        with open(path, encoding="utf-8", errors="strict") as fh:
            lines = fh.read().splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise Unchecked(f"成果物が読めない: {path}: {exc}") from exc
    out: list[dict] = []
    for i, line in enumerate(lines, start=1):
        out.extend(scan_line(line, i, path, decl))
    out.extend(scan_prohibited("\n".join(lines), path, decl))
    return out


SCOPE_NOTE = (
    "scope: raw-value / unknown-token / unknown-component / prohibited-expression のみ。"
    " トークン化されていない散文（「ブランドの青を使う」等）は検出クラスの外＝レビュア判定。"
    " use と mention は区別しない（過去の値への言及も報告する）。"
)


def _declared_section(decl: "Declared | None", key: str) -> bool:
    if decl is None:
        return False
    # `components: []` は「検査しない」ではなく「1つも許可しない」（policy 規則）。
    # 宣言されているので not_declared には入れない。
    return getattr(decl, key) is not None


def file_sha256(path: str) -> str | None:
    """報告に制約ファイルの指紋を残す。どの宣言に対して測ったかを後から特定できる。"""
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def write_report(path: str, payload: dict) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="宣言したデザイン制約を成果物に突き合わせる（未検査は exit 2）",
    )
    parser.add_argument("artifacts", nargs="*", help="検査する成果物（ファイルまたはディレクトリ）")
    parser.add_argument(
        "--constraints", default=None,
        help=f"制約ファイル（既定: {DEFAULT_CONSTRAINTS}）",
    )
    parser.add_argument("--json", action="store_true", help="機械可読な JSON で出力する")
    parser.add_argument(
        "--report", metavar="PATH",
        help="どの状態でも JSON 報告をこのパスに書く（orchestrate は stdout を捨てるため）",
    )
    parser.add_argument(
        "--if-configured", action="store_true",
        help="既定パスに制約ファイルが無ければ not-configured として exit 0。"
             " --constraints で明示したパスが無い場合は誤記なので、この指定でも exit 2。",
    )
    args = parser.parse_args(argv)

    constraints_path = args.constraints or DEFAULT_CONSTRAINTS
    explicit = args.constraints is not None

    status = "checked"
    reason = ""
    decl: Declared | None = None
    files: list[str] = []
    skipped: list[dict] = []
    violations: list[dict] = []
    try:
        if not args.artifacts:
            raise Unchecked("成果物が指定されていない")
        if args.if_configured and not explicit and not os.path.isfile(constraints_path):
            raise NotConfigured(
                f"制約ファイルが無い: {constraints_path} — 宣言が無いので強制すべき制約も無い"
            )
        data = load_constraints(constraints_path)
        decl = Declared(data)  # noqa: F841 — 報告の not_declared が読む
        # 報告ファイルは成果物ではない。走査対象に入れると、前回の報告に載っている
        # 違反値（#ff0000 等）を今回の違反として拾う。
        skip = {os.path.realpath(constraints_path)}
        if args.report:
            skip.add(os.path.realpath(args.report))
        files, skipped = collect_artifacts(args.artifacts, skip)
        if not files:
            raise Unchecked("検査対象のテキスト成果物が1件も無い")
        for path in files:
            violations.extend(scan_file(path, decl))
    except NotConfigured as exc:
        status, reason = "not-configured", str(exc)
    except Unchecked as exc:
        status, reason = "unchecked", str(exc)
    except Exception as exc:  # noqa: BLE001 — 報告を残さず落ちるほうが害が大きい
        # 想定外の例外でも報告は書く。書かずに traceback で終わると、design-vet ⓪ が
        # 逐語転記すべき status がどこにも無くなり、「検査していない」ことすら残らない。
        status = "unchecked"
        reason = f"想定外の例外: {type(exc).__name__}: {exc}"

    report = {
        "status": status,
        "reason": reason,
        "scope": SCOPE_NOTE,
        "constraints": constraints_path,
        "constraints_sha256": file_sha256(constraints_path),
        "artifacts": files,
        # 拡張子で外したもの・シンボリックリンク。「対象が黙って欠落した」を可視にする。
        "skipped": skipped,
        # 宣言そのものが省いている検査。policy の「省いたことを報告に書く」を機械側で満たす。
        # 1つの値が複数の種別を宣言したもの。`16px Display Black` が `#000000` を
        # 宣言していることをレビュアが見られるようにする（policy 規則 8・9 と同じ分担）。
        "composite_declarations": decl.composite if (status == "checked" and decl) else [],
        "not_declared": sorted(
            k for k in ("components", "prohibited") if not _declared_section(decl, k)
        ) if status == "checked" else [],
        "violations": violations,
    }
    if args.report:
        try:
            write_report(args.report, report)
        except OSError as exc:
            # 報告が残せないなら、何が起きたか追跡できない。合格にしない。
            print(f"未検査: 報告を書けない: {args.report}: {exc}", file=sys.stderr)
            return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif status == "not-configured":
        print(f"未設定: {reason}")
        print("検査していない。制約を強制したいなら "
              f"{DEFAULT_CONSTRAINTS} を作る（雛形: manifests/design-constraints.template.json）。")
    elif status == "unchecked":
        print(f"未検査: {reason}", file=sys.stderr)
        print("合格ではない。走らなかったことを合格として扱わない。", file=sys.stderr)
    else:
        print(f"design-constraints: {constraints_path}")
        print(SCOPE_NOTE)
        for v in violations:
            print(f"{v['file']}:{v['line']}: [{v['class']}] {v['detail']} — {v['value']}")
        print(f"違反 {len(violations)} 件 / 検査した成果物 {len(files)} 件")

    if status == "not-configured":
        return 0
    if status == "unchecked":
        return 2
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
