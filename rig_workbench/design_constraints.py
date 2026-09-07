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

検出するのは次の3クラスだけです:

    raw-value              宣言トークンに解決しない色・長さ・フォントが書かれている
    unknown-token          存在しないトークン名を参照している
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
    r"(?:[,/]\s*(\d*\.?\d+)\s*%?\s*)?\)",
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
RE_COMPONENT = re.compile(r"<([A-Z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)\b")

# CSS 宣言。名前付き色とフォントは、散文で同じ語が出るため宣言の中でだけ読む
# （「エラーは red で示す」を色の生値として上げない）。
RE_DECL = re.compile(r"(?:^|[;{,])\s*([-a-zA-Z]+)\s*:\s*([^;{}\n]+)")
COLOUR_PROPERTIES = ("color", "background", "border", "outline", "shadow", "fill", "stroke")

# 表示上は同じで、部分文字列としては一致しない文字。禁止表現の検査前に落とす。
ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)

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
    return unicodedata.normalize("NFKC", text)


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
        try:
            alpha = float(a)
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


def colors_in(text: str) -> list[str]:
    found = [normalize_hex(m.group("d")) for m in RE_HEX.finditer(text)]
    for m in RE_RGB.finditer(text):
        value = normalize_rgb(*m.groups())
        if value is not None:
            found.append(value)
    found.extend(named_colors_in(text))
    return found


def named_colors_in(text: str) -> list[str]:
    """名前付き色は CSS 宣言の中でだけ読む。

    散文の「エラーは red で示す」を生値として上げないため、単語の総当たりはしない。
    """
    found: list[str] = []
    for prop, value in RE_DECL.findall(text):
        if not any(k in prop.lower() for k in COLOUR_PROPERTIES):
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
        low = prop.lower()
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
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
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


def validate_constraints(data: object, path: str) -> None:
    """スキーマの構造だけを手で見る（jsonschema に依存しない）。"""
    if not isinstance(data, dict):
        raise Unchecked(f"制約ファイルがオブジェクトでない: {path}")
    if data.get("version") != 1:
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
            for key in ("pattern", "why"):
                if not isinstance(rule.get(key), str) or not rule[key]:
                    raise Unchecked(f"prohibited[{i}].{key} が空でない文字列でない: {path}")
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
        for entries in self.tokens.values():
            for value in entries.values():
                found_colors = colors_in(value)
                found_lengths = lengths_in(value)
                # 複合値（"1px solid #ccc"）は色も長さも宣言する。elif にすると
                # 後ろの種別が黙って落ち、宣言済みの値が違反として上がる。
                self.colors.update(found_colors)
                self.lengths.update(found_lengths)
                if not found_colors and not found_lengths:
                    self.fonts.add(normalize_font(value))
        self.components: list[str] | None = data.get("components")
        self.prohibited: list[dict] = data.get("prohibited") or []

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

    if decl.components is not None:
        allowed = set(decl.components)
        for name in RE_COMPONENT.findall(line):
            if name not in allowed:
                add("unknown-component", name, f"インベントリに無いコンポーネント: <{name}>")

    return out


def _flatten(text: str, drop_all_space: bool) -> tuple[str, list[int]]:
    """照合用に本文を潰し、潰した各文字が元の何文字目から来たかを覚えておく。

    全角を半角に、ゼロ幅を除去し、小文字化する。`drop_all_space` は空白を全部落とす
    （日本語の行折り返し用）。落とさない側は空白の連続を1つに詰める（英語の行折り返し用）。
    """
    out: list[str] = []
    idx: list[int] = []
    prev_space = False
    for i, ch in enumerate(text):
        if ord(ch) in ZERO_WIDTH:
            continue
        for c in unicodedata.normalize("NFKC", ch):
            if c.isspace():
                if drop_all_space or prev_space:
                    continue
                out.append(" ")
                idx.append(i)
                prev_space = True
                continue
            prev_space = False
            out.append(c.lower())
            idx.append(i)
    return "".join(out), idx


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

    out: list[dict] = []
    for rule in decl.prohibited:
        pattern = rule["pattern"]
        hit_at: int | None = None
        if rule.get("regex"):
            flags = 0 if rule.get("case_sensitive") else re.IGNORECASE
            m = re.search(pattern, text, flags)
            if m:
                hit_at = m.start()
        elif rule.get("case_sensitive"):
            at = text.find(pattern)
            hit_at = at if at >= 0 else None
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


def collect_artifacts(paths: list[str], skip: set[str]) -> list[str]:
    files: list[str] = []
    for path in paths:
        if os.path.isdir(path):
            for root, _dirs, names in os.walk(path):
                for name in sorted(names):
                    if name.lower().endswith(TEXT_SUFFIXES):
                        files.append(os.path.join(root, name))
        elif os.path.isfile(path):
            files.append(path)
        else:
            raise Unchecked(f"成果物が無い: {path}")
    return [f for f in files if os.path.realpath(f) not in skip]


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
    files: list[str] = []
    violations: list[dict] = []
    try:
        if not args.artifacts:
            raise Unchecked("成果物が指定されていない")
        if args.if_configured and not explicit and not os.path.isfile(constraints_path):
            raise NotConfigured(
                f"制約ファイルが無い: {constraints_path} — 宣言が無いので強制すべき制約も無い"
            )
        data = load_constraints(constraints_path)
        decl = Declared(data)
        # 報告ファイルは成果物ではない。走査対象に入れると、前回の報告に載っている
        # 違反値（#ff0000 等）を今回の違反として拾う。
        skip = {os.path.realpath(constraints_path)}
        if args.report:
            skip.add(os.path.realpath(args.report))
        files = collect_artifacts(args.artifacts, skip)
        if not files:
            raise Unchecked("検査対象のテキスト成果物が1件も無い")
        for path in files:
            violations.extend(scan_file(path, decl))
    except NotConfigured as exc:
        status, reason = "not-configured", str(exc)
    except Unchecked as exc:
        status, reason = "unchecked", str(exc)

    report = {
        "status": status,
        "reason": reason,
        "scope": SCOPE_NOTE,
        "constraints": constraints_path,
        "constraints_sha256": file_sha256(constraints_path),
        "artifacts": files,
        "violations": violations,
    }
    if args.report:
        write_report(args.report, report)

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
