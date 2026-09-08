#!/usr/bin/env python3
"""ja_textlint — textlint-ja 相当の日本語校正センサー（stdlib のみ、形態素解析なし）。

textlint-ja（textlint-rule-preset-ja-technical-writing / preset-ja-spacing /
ja-hiragana-* / prh）が JavaScript と kuromoji で行っている検査のうち、**辞書と正規表現で
決定論的に再現できる部分だけ**を Python 標準ライブラリで実装したものです。

    rig-wb ja-lint docs/ README.ja.md
    rig-wb ja-lint --config .claude/ja-textlint.json --report .rig/ja-lint-report.json
    cat draft.md | rig-wb ja-lint -

規則の正本は `skills/engine/facets/policies/japanese-textlint-rules.md`、設定ファイルの形は
`skills/engine/manifests/ja-textlint.schema.json` です。

## 主張してよい範囲

textlint-ja の多くのルールは kuromoji の品詞情報に依存します（助詞の連続、ら抜き、
い抜き、敬体/常体の混在、同じ語の連続）。ここではそれらを**表層の近似**で再現しています。
近似であるルールは既定の severity を `warning` にし、exit code に影響させません。
文字と辞書で正確に決まるルール（文の長さ、読点の数、半角カナ、二重否定の定型、冗長表現の
辞書、誤用の辞書、括弧の対応、用語）だけを `error` にしています。

捕れないものは捕れたことにしません。どのルールが近似で、何を取りこぼすかは policy に
一覧があり、`tests/fixtures/ja-textlint/` の種で実測しています。

## 状態と終了コード

    checked         成果物を読み、検査した。
    unchecked       検査が成立しなかった（設定 JSON が壊れている / 成果物が読めない /
                    検査対象が 1 件も無い）。合格ではない。
    not-configured  `--if-configured` 指定時に設定ファイルも引数も無い。検査していない。

    0   checked かつ error 0 件。または not-configured。
    1   checked かつ error 1 件以上（`--strict` では warning も数える）。
    2   unchecked。走らなかったことを合格として扱わないための区別。

`--report <path>` はどの状態でも JSON 報告を書きます。orchestrate の checks 実行系
（`_run_step_checks`）は stdout を捨てるため、**報告の本体はこのファイルです。**
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata

DEFAULT_CONFIG = os.path.join(".claude", "ja-textlint.json")
TEXT_SUFFIXES = (".md", ".markdown", ".mdx", ".txt", ".rst")
STDIN_NAME = "<stdin>"
MAX_INPUT_BYTES = 8 * 1024 * 1024

SCOPE_NOTE = (
    "textlint-ja 相当の表層検査。形態素解析は行わず、辞書と正規表現で決まる範囲だけを "
    "error、品詞の近似に頼る範囲を warning として報告する。"
)

# ── 文字クラス ────────────────────────────────────────────────────────────────
HIRA = "ぁ-ゖ"
KATA = "ァ-ヺー"
KANJI = "一-鿿㐀-䶿々〆ヶ"
JA = HIRA + KATA + KANJI
RE_JA_CHAR = re.compile(f"[{JA}]")
MASK = "\ue000"  # インラインコード・URL・リンク先を潰す私用文字。長さは保つ。

PRESETS = {
    "technical": [
        "sentence-length", "max-ten", "max-kanji-continuous-len", "no-mix-dearu-desumasu",
        "ja-no-mixed-period", "no-double-negative-ja", "no-dropped-i", "no-dropping-the-ra",
        "no-doubled-conjunctive-particle-ga", "no-doubled-conjunction", "no-doubled-joshi",
        "no-nfd", "no-invalid-control-character", "no-zero-width-spaces",
        "no-exclamation-question-mark", "no-hankaku-kana", "ja-no-weak-phrase",
        "ja-no-successive-word", "ja-no-abusage", "ja-no-redundant-expression",
        "ja-unnatural-alphabet", "no-unmatched-pair",
    ],
    "spacing": [
        "ja-space-between-half-and-full-width", "ja-no-space-around-parentheses",
        "ja-nakaguro-or-halfwidth-space-between-katakana",
    ],
    "hiragana": ["ja-hiragana-keishikimeishi", "ja-hiragana-fukushi", "ja-hiragana-hojodoushi"],
    "style": ["ja-no-orthographic-variants", "no-zenkaku-alnum"],
}
DEFAULT_PRESETS = ["technical", "spacing"]
# `prh` は terms が宣言されたときだけ意味を持つので preset に入れない。常に有効。

# 既定の severity。近似ルール（品詞が要るもの）は warning。
DEFAULT_SEVERITY = {
    "sentence-length": "error",
    "max-ten": "error",
    "max-kanji-continuous-len": "warning",
    "no-mix-dearu-desumasu": "warning",
    "ja-no-mixed-period": "error",
    "no-double-negative-ja": "error",
    "no-dropped-i": "warning",
    "no-dropping-the-ra": "warning",
    "no-doubled-conjunctive-particle-ga": "error",
    "no-doubled-conjunction": "error",
    "no-doubled-joshi": "warning",
    "no-nfd": "error",
    "no-invalid-control-character": "error",
    "no-zero-width-spaces": "error",
    "no-exclamation-question-mark": "error",
    "no-hankaku-kana": "error",
    "ja-no-weak-phrase": "warning",
    "ja-no-successive-word": "warning",
    "ja-no-abusage": "error",
    "ja-no-redundant-expression": "error",
    "ja-unnatural-alphabet": "warning",
    "no-unmatched-pair": "error",
    "ja-space-between-half-and-full-width": "warning",
    "ja-no-space-around-parentheses": "error",
    "ja-nakaguro-or-halfwidth-space-between-katakana": "error",
    "ja-hiragana-keishikimeishi": "warning",
    "ja-hiragana-fukushi": "warning",
    "ja-hiragana-hojodoushi": "warning",
    "ja-no-orthographic-variants": "warning",
    "no-zenkaku-alnum": "warning",
    "prh": "error",
}
ALL_RULES = tuple(DEFAULT_SEVERITY)
CONFIG_KEYS = {"_readme", "version", "paths", "presets", "rules", "terms", "ignore"}


class Unchecked(Exception):
    """宣言や成果物はあるのに検査が成立しなかった。合格にも不合格にもしない。"""


class NotConfigured(Exception):
    """設定も引数も無い。検査すべきものが指定されていない。"""


# ── 文書モデル ────────────────────────────────────────────────────────────────
class Line:
    __slots__ = ("col0", "kind", "no", "raw", "text")

    def __init__(self, no: int, raw: str, text: str, kind: str, col0: int):
        self.no = no          # 1-based
        self.raw = raw        # 元の行
        self.text = text      # マーカーを落とし、コード/URL を MASK した本文
        self.kind = kind      # prose / heading / list / quote / table / code / blank / meta
        self.col0 = col0      # raw の中で text が始まる位置（列の復元用）


class Paragraph:
    """連続する本文行のまとまり。文は段落を跨がない。"""

    __slots__ = ("kind", "lines", "starts", "text")

    def __init__(self, lines: list[Line], kind: str):
        self.lines = lines
        self.kind = kind
        parts: list[str] = []
        self.starts: list[int] = []
        pos = 0
        for ln in lines:
            self.starts.append(pos)
            parts.append(ln.text)
            pos += len(ln.text) + 1
        self.text = "\n".join(parts)

    def locate(self, offset: int) -> tuple[int, int]:
        """段落内オフセット → (行番号, 1-based 列)。"""
        idx = 0
        for i, start in enumerate(self.starts):
            if start <= offset:
                idx = i
            else:
                break
        ln = self.lines[idx]
        return ln.no, ln.col0 + (offset - self.starts[idx]) + 1


class Sentence:
    __slots__ = ("para", "start", "text")

    def __init__(self, text: str, start: int, para: Paragraph):
        self.text = text
        self.start = start
        self.para = para

    def locate(self, rel: int = 0) -> tuple[int, int]:
        return self.para.locate(self.start + rel)


RE_FENCE = re.compile(r"^\s*(```|~~~)")
RE_HEADING = re.compile(r"^(\s{0,3}#{1,6}\s+)")
RE_LIST = re.compile(r"^(\s*(?:[-*+]|\d{1,3}[.)])\s+(?:\[[ xX]\]\s+)?)")
RE_QUOTE = re.compile(r"^(\s*>\s?)")
RE_TABLE = re.compile(r"^\s*\|")
RE_HR = re.compile(r"^\s*([-*_]\s*){3,}$")
RE_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
RE_INLINE_CODE = re.compile(r"(`+)([^`]|[^`][\s\S]*?[^`])\1(?!`)")
RE_URL = re.compile(r"(?:https?|ftp)://[^\s<>）)\]」]+")
RE_LINK_TARGET = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
RE_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
RE_AUTOLINK = re.compile(r"<(?:https?|mailto):[^>]*>")
# 強調記号。落とすと列がずれるので MASK に置く。`_` は識別子に多いので `__` だけ。
RE_EMPHASIS = re.compile(r"\*{1,3}|__|~~")


def _mask(text: str) -> str:
    def repl(m: re.Match) -> str:
        return MASK * len(m.group(0))

    text = RE_HTML_COMMENT.sub(repl, text)
    text = RE_INLINE_CODE.sub(repl, text)
    text = RE_IMAGE.sub(repl, text)
    text = RE_AUTOLINK.sub(repl, text)
    text = RE_URL.sub(repl, text)
    # `[text](url)` — 表示されるのは text だけ。
    text = RE_LINK_TARGET.sub(lambda m: "]" + MASK * (len(m.group(0)) - 1), text)
    text = RE_EMPHASIS.sub(repl, text)
    return text


def parse_document(source: str) -> tuple[list[Line], list[Paragraph]]:
    """Markdown / プレーンテキストを行と段落に分ける。"""
    raw_lines = source.split("\n")
    lines: list[Line] = []
    in_fence = False
    fence_mark = ""
    in_front = False
    # 複数行に跨るインラインコードは扱わない。行ごとにマスクする。
    for i, raw in enumerate(raw_lines, start=1):
        if i == 1 and raw.strip() == "---":
            in_front = True
            lines.append(Line(i, raw, "", "meta", 0))
            continue
        if in_front:
            lines.append(Line(i, raw, "", "meta", 0))
            if raw.strip() == "---":
                in_front = False
            continue
        m = RE_FENCE.match(raw)
        if m and not in_fence:
            in_fence, fence_mark = True, m.group(1)
            lines.append(Line(i, raw, "", "code", 0))
            continue
        if in_fence:
            lines.append(Line(i, raw, "", "code", 0))
            if raw.strip().startswith(fence_mark):
                in_fence = False
            continue
        if not raw.strip():
            lines.append(Line(i, raw, "", "blank", 0))
            continue
        if RE_HR.match(raw):
            lines.append(Line(i, raw, "", "meta", 0))
            continue
        if raw.startswith(("    ", "\t")):
            # インデントコードブロック（リスト継続行も含むが、本文検査からは外す）
            lines.append(Line(i, raw, "", "code", 0))
            continue
        if RE_TABLE.match(raw):
            lines.append(Line(i, raw, _mask(raw), "table", 0))
            continue
        kind, col0, body = "prose", 0, raw
        for pat, k in ((RE_HEADING, "heading"), (RE_LIST, "list"), (RE_QUOTE, "quote")):
            mm = pat.match(raw)
            if mm:
                kind, col0, body = k, len(mm.group(1)), raw[len(mm.group(1)):]
                break
        if kind == "heading":
            body = re.sub(r"\s+#+\s*$", "", body)
        lines.append(Line(i, raw, _mask(body), kind, col0))

    paragraphs: list[Paragraph] = []
    buf: list[Line] = []
    for ln in lines:
        if ln.kind == "prose":
            buf.append(ln)
            continue
        if buf:
            paragraphs.append(Paragraph(buf, "prose"))
            buf = []
        if ln.kind in ("heading", "list", "quote"):
            paragraphs.append(Paragraph([ln], ln.kind))
    if buf:
        paragraphs.append(Paragraph(buf, "prose"))
    return lines, paragraphs


# 「。！？」に加えて、空白か行末が続く ASCII ピリオドも文末にする（英文段落のため）。
RE_SENTENCE_END = re.compile(f"[。！？!?]+[」』）)”\"'{MASK}]*|\\.(?=\\s|$)[」』）)”\"'{MASK}]*")


def split_sentences(para: Paragraph) -> list[Sentence]:
    text = para.text
    out: list[Sentence] = []
    pos = 0
    for m in RE_SENTENCE_END.finditer(text):
        end = m.end()
        seg = text[pos:end]
        if seg.strip():
            lead = len(seg) - len(seg.lstrip())
            out.append(Sentence(seg[lead:], pos + lead, para))
        pos = end
    tail = text[pos:]
    if tail.strip():
        lead = len(tail) - len(tail.lstrip())
        out.append(Sentence(tail[lead:], pos + lead, para))
    return out


# ── 報告の器 ──────────────────────────────────────────────────────────────────
class Finding(dict):
    def __init__(self, rule: str, file: str, line: int, col: int, message: str,
                 text: str = "", fix: str | None = None):
        super().__init__(rule=rule, severity="", file=file, line=line, column=col,
                         message=message, text=text[:120])
        if fix:
            self["fix"] = fix


class Context:
    def __init__(self, file: str, source: str, options: dict):
        self.file = file
        self.source = source
        self.options = options
        self.lines, self.paragraphs = parse_document(source)
        # 日本語を含む文だけが検査対象。英文の段落に文の長さや読点の規則は当てない。
        self.sentences: list[Sentence] = []
        self.body_sentences: list[Sentence] = []
        for p in self.paragraphs:
            ss = [x for x in split_sentences(p) if RE_JA_CHAR.search(x.text)]
            self.sentences.extend(ss)
            if p.kind == "prose":
                self.body_sentences.extend(ss)
        self.findings: list[Finding] = []

    def opt(self, rule: str, key: str, default):
        val = self.options.get(rule)
        if isinstance(val, dict) and key in val:
            return val[key]
        return default

    def add(self, rule: str, where: tuple[int, int], message: str,
            text: str = "", fix: str | None = None) -> None:
        line, col = where
        self.findings.append(Finding(rule, self.file, line, col, message, text, fix))

    def scan_lines(self, kinds=("prose", "heading", "list", "quote", "table")):
        for ln in self.lines:
            if ln.kind in kinds:
                yield ln

    @staticmethod
    def at(ln: Line, idx: int) -> tuple[int, int]:
        return ln.no, ln.col0 + idx + 1


# ── ルール ────────────────────────────────────────────────────────────────────
def _visible_len(s: str) -> int:
    return sum(1 for ch in s if ch != MASK and ch != "\n")


def rule_sentence_length(ctx: Context) -> None:
    limit = int(ctx.opt("sentence-length", "max", 100))
    for s in ctx.sentences:
        n = _visible_len(s.text)
        if n > limit:
            ctx.add("sentence-length", s.locate(),
                    f"一文が {n} 文字あります（上限 {limit}）。文を分けてください。", s.text)


def rule_max_ten(ctx: Context) -> None:
    limit = int(ctx.opt("max-ten", "max", 3))
    for s in ctx.sentences:
        n = s.text.count("、") + s.text.count("，") + len(re.findall(r"(?<=[^\d]),", s.text))
        if n > limit:
            ctx.add("max-ten", s.locate(),
                    f"一文に読点が {n} 個あります（上限 {limit}）。文を分けてください。", s.text)


def rule_max_kanji(ctx: Context) -> None:
    limit = int(ctx.opt("max-kanji-continuous-len", "max", 5))
    allow = set(ctx.opt("max-kanji-continuous-len", "allow", []))
    pat = re.compile(f"[{KANJI}]{{{limit + 1},}}")
    for s in ctx.sentences:
        for m in pat.finditer(s.text):
            if m.group(0) in allow or any(a in m.group(0) for a in allow):
                continue
            ctx.add("max-kanji-continuous-len", s.locate(m.start()),
                    f"漢字が {len(m.group(0))} 文字連続しています（上限 {limit}）。", m.group(0))


RE_DESUMASU = re.compile(
    r"(です|ます|ません|でした|ました|ましょう|ませんでした|でしょう|ください|下さい|なさい|ませ|ございます|ございません|"
    r"ですね|ますね|ですか|ますか|ますよ|ですよ)"
    r"[。！？!?]*[」』）)]*$"
)
RE_DEARU = re.compile(
    r"(だ|である|だった|であった|だろう|であろう|ではない|た|る|う|く|す|つ|ぬ|む|ぐ|ぶ|ない|たい|しい|い)"
    r"[。！？!?]+[」』）)]*$"
)


def _register(sentence: str) -> str | None:
    t = sentence.rstrip()
    if RE_DESUMASU.search(t):
        return "desumasu"
    if RE_DEARU.search(t) and t[-1] in "。！？!?」』）)":
        return "dearu"
    return None


def rule_dearu_desumasu(ctx: Context) -> None:
    prefer = ctx.opt("no-mix-dearu-desumasu", "prefer", "auto")
    tagged = [(s, _register(s.text)) for s in ctx.body_sentences]
    counts = {"desumasu": 0, "dearu": 0}
    for _, r in tagged:
        if r:
            counts[r] += 1
    if not counts["desumasu"] or not counts["dearu"]:
        return
    if prefer in counts:
        majority = prefer
    else:
        majority = "desumasu" if counts["desumasu"] >= counts["dearu"] else "dearu"
    label = {"desumasu": "敬体（です・ます）", "dearu": "常体（だ・である）"}
    for s, r in tagged:
        if r and r != majority:
            ctx.add("no-mix-dearu-desumasu", s.locate(),
                    f"本文は{label[majority]}が主ですが、この文は{label[r]}です。", s.text)


RE_PERIOD_OK = re.compile(r"[。！？!?][」』）)”\"]*$|[」』）)][。]?$|[:：]$")
RE_TRAILING_MASK = re.compile(f"[{MASK} ]+$")


def rule_mixed_period(ctx: Context) -> None:
    for p in ctx.paragraphs:
        if p.kind != "prose":
            continue
        text = RE_TRAILING_MASK.sub("", p.text.rstrip())
        if not RE_JA_CHAR.search(text):
            continue
        last_line = p.lines[-1]
        # 行末がすべて MASK（画像・リンクだけの段落）は本文ではない。
        if set(text.replace("\n", "")) <= {MASK, " "}:
            continue
        if text.endswith(".") and RE_JA_CHAR.search(text[-4:-1] or ""):
            ctx.add("ja-no-mixed-period", ctx.at(last_line, len(last_line.text) - 1),
                    "文末がピリオドです。日本語の文は「。」で終えてください。", last_line.text)
            continue
        if RE_PERIOD_OK.search(text):
            continue
        if text.endswith(MASK):
            continue
        ctx.add("ja-no-mixed-period", ctx.at(last_line, max(len(last_line.text) - 1, 0)),
                "段落の最後の文が「。」で終わっていません。", last_line.text)


DOUBLE_NEGATIVES = [
    "ないことはない", "ないことはありません", "ないことはなかった", "ないわけではない",
    "ないわけではありません", "ないわけではなかった", "なくはない", "なくはありません",
    "ないとは限らない", "ないとは限りません", "ないでもない", "ないでもありません",
    "なくもない", "なくもありません", "ないとはいえない", "ないとは言えない",
    "ないとは言えません", "ないはずがない", "ないはずはない", "ないものはない",
]
RE_DOUBLE_NEG = re.compile("|".join(map(re.escape, DOUBLE_NEGATIVES)))


def rule_double_negative(ctx: Context) -> None:
    for s in ctx.sentences:
        for m in RE_DOUBLE_NEG.finditer(s.text):
            ctx.add("no-double-negative-ja", s.locate(m.start()),
                    f"二重否定「{m.group(0)}」です。肯定で言い換えてください。", m.group(0))


I_DAN_E_DAN = "いきしちにひみりぎじぢびぴえけせてねへめれげぜでべぺっん"
RE_DROPPED_I = re.compile(
    f"(?<=[{I_DAN_E_DAN}])(て|で)(る|た|ます|ない|て|れば|ん)(?![{HIRA}]*てる坊主)"
)
DROPPED_I_ALLOW = ("愛でる", "秀でる", "めでる", "てるてる")


def rule_dropped_i(ctx: Context) -> None:
    allow = tuple(DROPPED_I_ALLOW) + tuple(ctx.opt("no-dropped-i", "allow", []))
    for s in ctx.sentences:
        for m in RE_DROPPED_I.finditer(s.text):
            window = s.text[max(0, m.start() - 2): m.end() + 2]
            if any(a in window for a in allow):
                continue
            hit = s.text[m.start() - 1: m.end()]
            ctx.add("no-dropped-i", s.locate(m.start()),
                    f"い抜き言葉「{hit}」の可能性があります。「〜ている」の形にしてください。",
                    hit)


ICHIDAN_STEMS = (
    "見|観|食べ|来|出|寝|着|起き|降り|借り|決め|受け|覚え|考え|教え|答え|変え|伝え|開け|生き|"
    "感じ|信じ|調べ|比べ|続け|助け|逃げ|投げ|上げ|下げ|付け|見つけ|集め|止め|辞め|始め|進め|"
    "認め|得|捨て|育て|建て|立て|当て|分け|避け|届け|閉め|締め|入れ|忘れ|超え|越え|終え|"
    "迎え|与え|加え|支え|抑え|数え|抜け|預け|載せ|乗せ|見せ|任せ|寄せ|混ぜ|並べ|浮かべ|"
    "みれ|たべれ|これ|でれ|ねれ|きれ|おきれ"
)
RE_DROPPING_RA = re.compile(f"(?:{ICHIDAN_STEMS})(れ)(る|ます|ない|た|て|ん)")


def rule_dropping_ra(ctx: Context) -> None:
    allow = ctx.opt("no-dropping-the-ra", "allow", [])
    for s in ctx.sentences:
        for m in RE_DROPPING_RA.finditer(s.text):
            hit = m.group(0)
            # 「これる」は「来れる」のら抜きにも「これ+る」にも読めるが、後者は語にならない。
            if hit in allow:
                continue
            ctx.add("no-dropping-the-ra", s.locate(m.start()),
                    f"ら抜き言葉「{hit}」の可能性があります。「〜られる」の形にしてください。",
                    hit)


RE_GA_CONJ = re.compile(r"(?<=[すますだるたいうくむぶぐつぬん])が[、，,]")


def rule_doubled_ga(ctx: Context) -> None:
    for s in ctx.sentences:
        hits = list(RE_GA_CONJ.finditer(s.text))
        if len(hits) >= 2:
            ctx.add("no-doubled-conjunctive-particle-ga", s.locate(hits[1].start()),
                    "一文に接続助詞「〜が、」が二回あります。文を分けてください。", s.text)


CONJUNCTIONS = (
    "しかし|また|そして|さらに|ただし|なお|つまり|したがって|そのため|ところが|一方|例えば|"
    "たとえば|まず|次に|最後に|しかも|または|あるいは|すなわち|ゆえに|だから|でも|けれども|"
    "ちなみに|さて|ところで|もっとも|むしろ|なぜなら|とはいえ|ともかく|いずれにせよ|要するに|"
    "結局|そこで|それで|それから|そのうえ|加えて|なおかつ|よって|もちろん|逆に|反対に|同様に|"
    "具体的には|ようするに|それでも|それでは|では|なので|ですが|だが"
)
RE_CONJ_HEAD = re.compile(f"^[「『（(\"]*({CONJUNCTIONS})[、，,]")


def rule_doubled_conjunction(ctx: Context) -> None:
    prev: str | None = None
    prev_para: Paragraph | None = None
    for s in ctx.sentences:
        m = RE_CONJ_HEAD.match(s.text)
        cur = m.group(1) if m else None
        if cur and cur == prev and s.para is prev_para:
            ctx.add("no-doubled-conjunction", s.locate(),
                    f"接続詞「{cur}」が連続する文の頭で繰り返されています。", s.text)
        prev, prev_para = cur, s.para


PRONOUNS = ("これ", "それ", "あれ", "どれ", "ここ", "そこ", "あそこ", "どこ", "こちら", "そちら",
            "あちら", "どちら", "こと", "もの", "ため", "よう", "とき", "ところ", "わけ", "うち",
            "ほう", "だれ", "なに", "いつ", "すべて", "みんな", "あなた", "わたし", "ぼく")
JOSHI_DEFAULT = ["は", "が", "を", "に", "へ", "で"]
RE_JOSHI = re.compile(f"(?<=[{KANJI}{KATA}A-Za-z0-9０-９）)」』])([はがをにへでもとのやか])(?![{HIRA}]*[{KANJI}]{{0}})")


def _particles(sentence: str) -> list[tuple[str, int, bool]]:
    """表層で助詞と読める位置。(文字, 位置, 高確度) の列。

    非ひらがな（漢字・カナ・英数・閉じ括弧）または代名詞的な語の直後にある一文字は
    高確度の助詞。ひらがなの直後にあるもの（「まで」の「で」、「たの」の「の」）は語の
    一部かもしれないので低確度とし、**間隔の計算にだけ**使う。報告するのは高確度のみ。
    低確度を間隔に入れないと「結果が表示されるまでの待ち時間が長い」の「が…が」が
    隣接と数えられ、kuromoji が「まで」「の」を助詞と数える textlint と食い違う。"""
    out: list[tuple[str, int, bool]] = []
    for i, ch in enumerate(sentence):
        if ch not in "はがをにへでもとのやか":
            continue
        prev = sentence[i - 1] if i else ""
        if not prev or prev == "\n":
            continue
        if re.match(f"[{KANJI}{KATA}A-Za-z0-9０-９）)」』{MASK}]", prev):
            out.append((ch, i, True))
            continue
        head = sentence[max(0, i - 4): i]
        if any(head.endswith(p) for p in PRONOUNS):
            out.append((ch, i, True))
        elif re.match(f"[{HIRA}]", prev):
            out.append((ch, i, False))
    return out


def rule_doubled_joshi(ctx: Context) -> None:
    targets = set(ctx.opt("no-doubled-joshi", "joshi", JOSHI_DEFAULT))
    min_interval = int(ctx.opt("no-doubled-joshi", "min_interval", 1))
    for s in ctx.sentences:
        seq = _particles(s.text)
        last: dict[str, int] = {}
        for idx, (ch, pos, sure) in enumerate(seq):
            if sure and ch in last and ch in targets and idx - last[ch] <= min_interval:
                ctx.add("no-doubled-joshi", s.locate(pos),
                        f"一文の中で助詞「{ch}」が続けて使われています。", s.text)
            if sure:
                last[ch] = idx


RE_NFD = re.compile("[\u3099\u309a\u0300-\u036f]")


def rule_nfd(ctx: Context) -> None:
    for ln in ctx.scan_lines():
        for m in RE_NFD.finditer(ln.text):
            ctx.add("no-nfd", ctx.at(ln, m.start()),
                    "結合文字（NFD）が使われています。NFC に正規化してください。",
                    ln.text[max(0, m.start() - 3): m.end() + 1])


RE_ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")
RE_CONTROL = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")


def rule_zero_width(ctx: Context) -> None:
    for ln in ctx.lines:
        for m in RE_ZERO_WIDTH.finditer(ln.raw):
            if ln.no == 1 and m.start() == 0 and m.group(0) == "\ufeff":
                continue  # BOM
            ctx.add("no-zero-width-spaces", (ln.no, m.start() + 1),
                    f"ゼロ幅文字 U+{ord(m.group(0)):04X} が含まれています。", ln.raw[:40])


def rule_control_chars(ctx: Context) -> None:
    for ln in ctx.lines:
        for m in RE_CONTROL.finditer(ln.raw):
            ctx.add("no-invalid-control-character", (ln.no, m.start() + 1),
                    f"制御文字 U+{ord(m.group(0)):04X} が含まれています。", ln.raw[:40])


RE_EXCLAIM = re.compile(r"[！？!?]")


def rule_exclamation(ctx: Context) -> None:
    for s in ctx.sentences:
        for m in RE_EXCLAIM.finditer(s.text):
            ctx.add("no-exclamation-question-mark", s.locate(m.start()),
                    f"感嘆符・疑問符「{m.group(0)}」は使いません。", s.text)


RE_HANKAKU_KANA = re.compile(r"[｡-ﾟ]+")


def rule_hankaku_kana(ctx: Context) -> None:
    for ln in ctx.scan_lines():
        for m in RE_HANKAKU_KANA.finditer(ln.text):
            ctx.add("no-hankaku-kana", ctx.at(ln, m.start()),
                    f"半角カナ「{m.group(0)}」は全角にしてください。", m.group(0),
                    unicodedata.normalize("NFKC", m.group(0)))


WEAK_PHRASES = [
    "かもしれない", "かもしれません", "かも知れない", "かも知れません", "と思います", "と思う",
    "と思われます", "と思われる", "気がします", "気がする", "ような気が", "ではないでしょうか",
    "のではないか", "ではないかと",
]
RE_WEAK = re.compile("|".join(map(re.escape, WEAK_PHRASES)))


def rule_weak_phrase(ctx: Context) -> None:
    for s in ctx.sentences:
        for m in RE_WEAK.finditer(s.text):
            ctx.add("ja-no-weak-phrase", s.locate(m.start()),
                    f"弱い表現「{m.group(0)}」です。断定できるなら言い切ってください。", m.group(0))


RE_SUCC_JOSHI = re.compile("(はは|がが|をを|にに|でで|とと|のの|へへ|もも)")
RE_SUCC_KANJI = re.compile(f"([{KANJI}]{{2,}})\\1")
SUCC_ALLOW_RE = re.compile(r"^[一二三四五六七八九十百千万]\S[一二三四五六七八九十百千万]\S$")


def rule_successive_word(ctx: Context) -> None:
    allow = set(ctx.opt("ja-no-successive-word", "allow", []))
    for s in ctx.sentences:
        for m in RE_SUCC_JOSHI.finditer(s.text):
            prev = s.text[m.start() - 1] if m.start() else ""
            head = s.text[max(0, m.start() - 4): m.start()]
            if not (re.match(f"[{KANJI}{KATA}A-Za-z0-9]", prev or " ")
                    or any(head.endswith(p) for p in PRONOUNS)):
                continue
            ctx.add("ja-no-successive-word", s.locate(m.start()),
                    f"同じ助詞「{m.group(1)[0]}」が連続しています。", m.group(0))
        for m in RE_SUCC_KANJI.finditer(s.text):
            whole = m.group(0)
            if whole in allow or SUCC_ALLOW_RE.match(whole):
                continue
            ctx.add("ja-no-successive-word", s.locate(m.start()),
                    f"同じ語「{m.group(1)}」が連続しています。", whole)


ABUSAGE = [
    ("汚名挽回", "汚名返上"), ("的を得", "的を射"), ("一つ返事", "二つ返事"),
    ("押しも押されぬ", "押しも押されもせぬ"), ("間が持たない", "間が持てない"),
    ("取り付く暇", "取り付く島"), ("怒り心頭に達", "怒り心頭に発"), ("上へ下へ", "上を下へ"),
    ("熱にうなされ", "熱に浮かされ"), ("濡れ手に粟", "濡れ手で粟"), ("口先三寸", "舌先三寸"),
    ("愛想を振りま", "愛嬌を振りま"), ("寸暇を惜しまず", "寸暇を惜しんで"),
    ("足元をすくわれ", "足をすくわれ"), ("出る釘は打たれる", "出る杭は打たれる"),
    ("明るみになる", "明るみに出る"), ("采配を振るう", "采配を振る"), ("論戦を張る", "論陣を張る"),
    ("眉をひそめる", "眉をひそめる"), ("青田刈り", "青田買い"), ("極め付け", "極め付き"),
    ("にやける", "にやける"), ("汚名を晴らす", "汚名をそそぐ"), ("鳥肌が立つ思い", "鳥肌が立つ思い"),
]
ABUSAGE = [(a, b) for a, b in ABUSAGE if a != b]
RE_ABUSAGE = re.compile("|".join(re.escape(a) for a, _ in ABUSAGE))
ABUSAGE_MAP = dict(ABUSAGE)


def rule_abusage(ctx: Context) -> None:
    for s in ctx.sentences:
        for m in RE_ABUSAGE.finditer(s.text):
            ctx.add("ja-no-abusage", s.locate(m.start()),
                    f"誤用「{m.group(0)}」です。「{ABUSAGE_MAP[m.group(0)]}」が本来の形です。",
                    m.group(0), ABUSAGE_MAP[m.group(0)])


SAHEN_NOUNS = (
    "実行|検討|確認|設定|作成|削除|更新|追加|変更|導入|実施|処理|対応|検証|説明|比較|分析|調査|"
    "準備|開発|運用|管理|保存|登録|検索|修正|改善|移行|評価|判断|決定|計算|測定|表示|入力|出力|"
    "送信|受信|接続|生成|定義|適用|参照|初期化|認証|承認|公開|報告|議論|検査|実装|構築|配置|"
    "起動|停止|再起動|操作|設計|翻訳|変換|圧縮|展開|監視|記録|通知|選択|指定|統合|分割|同期|"
    "検出|解析|解決|提供|依頼|購入|注文|予約|申請|案内|発表|発行|提出|回答|集計|分類|抽出|"
    "復旧|復元|確定|調整|点検|整理|把握|共有|周知|連携|検収|納品|支払|請求|契約|採用|教育|"
    "訓練|練習|準備|想定|予測|推定|推測|比較|判定|審査|検討|協議|相談|交渉|投票|選挙|測定"
)
REDUNDANT = [
    (re.compile(r"することが(出来|でき)(る|ます|ません|ない|た|ました)"), "「〜できる」で足ります。"),
    (re.compile(r"することが可能(です|だ|である|になります|となります)"), "「〜できる」で足ります。"),
    (re.compile(f"(?<![{KANJI}])({SAHEN_NOUNS})を(行|おこな)(う|い|っ|わ|え)"), "「〜する」で足ります。"),
    (re.compile(r"まず最初に"), "「まず」か「最初に」のどちらかで足ります。"),
    (re.compile(r"一番最初|一番最後"), "「最初」「最後」で足ります。"),
    (re.compile(r"各[^ごと、。\n]{1,8}ごと"), "「各〜」か「〜ごと」のどちらかで足ります。"),
    (re.compile(r"約[0-9０-９.．]+[^、。\n]{0,4}(ほど|くらい|ぐらい|程度)"), "「約」と「ほど」は同じ意味です。"),
    (re.compile(r"頭痛が痛|馬から落馬|後で後悔|あとで後悔|返事を返|違和感を感じ|犯罪を犯|被害を被|炎天下の下|過半数を超え|今現在|いまだ未定|予め予約|日本に来日|射程距離|存亡の危機|従来から|内定が決ま"),
     "意味が重なっています。"),
]


def rule_redundant(ctx: Context) -> None:
    for s in ctx.sentences:
        for pat, why in REDUNDANT:
            for m in pat.finditer(s.text):
                ctx.add("ja-no-redundant-expression", s.locate(m.start()),
                        f"冗長な表現「{m.group(0)}」です。{why}", m.group(0))


# 小文字だけ。「提案A」「主張B」のような大文字一字のラベルは自然で、IME の打ち損ないは
# ひらがなの中に小文字で現れる（「こnにちは」）。片側にひらがなを要求する。
RE_UNNATURAL_ALPHA = re.compile(f"(?<=[{HIRA}])[a-z](?=[{JA}])|(?<=[{JA}])[a-z](?=[{HIRA}])")


def rule_unnatural_alphabet(ctx: Context) -> None:
    allow = set(ctx.opt("ja-unnatural-alphabet", "allow", []))
    for s in ctx.sentences:
        for m in RE_UNNATURAL_ALPHA.finditer(s.text):
            if m.group(0) in allow:
                continue
            ctx.add("ja-unnatural-alphabet", s.locate(m.start()),
                    f"日本語の中に半角英字「{m.group(0)}」が一文字だけあります。入力ミスの可能性があります。",
                    s.text[max(0, m.start() - 3): m.end() + 3])


PAIRS = [("（", "）"), ("「", "」"), ("『", "』"), ("【", "】"), ("(", ")"), ("［", "］"), ("〈", "〉"), ("《", "》")]


def rule_unmatched_pair(ctx: Context) -> None:
    for p in ctx.paragraphs:
        text = p.text
        for open_, close in PAIRS:
            depth = 0
            first_open = -1
            for i, ch in enumerate(text):
                if ch == open_:
                    if depth == 0:
                        first_open = i
                    depth += 1
                elif ch == close:
                    if depth == 0:
                        ctx.add("no-unmatched-pair", p.locate(i),
                                f"閉じ括弧「{close}」に対応する「{open_}」がありません。", text[:60])
                    else:
                        depth -= 1
            if depth > 0:
                ctx.add("no-unmatched-pair", p.locate(first_open),
                        f"「{open_}」が閉じられていません。", text[:60])


# 数字は数えない。「9月7日 14時05分」の「日 14」と「9月」を同じ流儀の投票に入れると、
# 日付の書き方が文体を決めてしまう。数字と単位の間は日本語ではほぼ常に詰めるので、
# 英字と日本語の間だけを見る。
RE_SPACE_HALF_FULL = re.compile(f"(?<=[A-Za-z])[ ]+(?=[{JA}])|(?<=[{JA}])[ ]+(?=[A-Za-z])")
RE_NOSPACE_HALF_FULL = re.compile(f"(?<=[A-Za-z])(?=[{JA}])|(?<=[{JA}])(?=[A-Za-z])")


def rule_space_half_full(ctx: Context) -> None:
    """`space`: always / never / auto（既定）。auto は文書内で混在しているときだけ少数派を報告する。

    textlint の既定は never だが、日本語の技術文書では「全角 半角」の間に半角スペースを
    置く流儀が広く使われている（rig 自身の docs もそう）。どちらかを既定にすると、
    半分の文書で一行おきに error が出る。auto は「宣言していない流儀を押しつけない、
    ただし一つの文書の中で揺れているなら指摘する」という妥協で、textlint からの意図した逸脱。"""
    mode = ctx.opt("ja-space-between-half-and-full-width", "space", "auto")
    if mode == "ignore":
        return
    lines = list(ctx.scan_lines(("prose", "heading", "list", "quote")))
    spaced = [(ln, m) for ln in lines for m in RE_SPACE_HALF_FULL.finditer(ln.text)]
    unspaced = [(ln, m) for ln in lines for m in RE_NOSPACE_HALF_FULL.finditer(ln.text)]
    if mode == "auto":
        if not spaced or not unspaced:
            return
        mode = "never" if len(unspaced) >= len(spaced) else "always"
        why = "この文書では{}が多数です。".format("入れない書き方" if mode == "never" else "入れる書き方")
    else:
        why = ""
    if mode == "always":
        for ln, m in unspaced:
            ctx.add("ja-space-between-half-and-full-width", ctx.at(ln, m.start()),
                    "全角と半角英数字の間にスペースを入れてください。" + why,
                    ln.text[max(0, m.start() - 4): m.start() + 4])
    else:
        for ln, m in spaced:
            ctx.add("ja-space-between-half-and-full-width", ctx.at(ln, m.start()),
                    "全角と半角英数字の間にスペースを入れないでください。" + why,
                    ln.text[max(0, m.start() - 4): m.end() + 4])


RE_PAREN_SPACE = re.compile(r"(?<=\S)[ 　]+(?=[（「『【])|(?<=[）」』】])[ 　]+(?=\S)")


def rule_space_around_parentheses(ctx: Context) -> None:
    for ln in ctx.scan_lines(("prose", "heading", "list", "quote")):
        for m in RE_PAREN_SPACE.finditer(ln.text):
            ctx.add("ja-no-space-around-parentheses", ctx.at(ln, m.start()),
                    "全角括弧の前後にスペースを入れないでください。",
                    ln.text[max(0, m.start() - 4): m.end() + 4])


RE_KATA_NAKAGURO = re.compile(f"[{KATA}]+・[{KATA}]+")
RE_KATA_SPACE = re.compile(f"[{KATA}]+ [{KATA}]+")


def rule_nakaguro_or_space(ctx: Context) -> None:
    prefer = ctx.opt("ja-nakaguro-or-halfwidth-space-between-katakana", "prefer", "auto")
    naka: list[tuple[Line, re.Match]] = []
    space: list[tuple[Line, re.Match]] = []
    for ln in ctx.scan_lines(("prose", "heading", "list", "quote")):
        naka.extend((ln, m) for m in RE_KATA_NAKAGURO.finditer(ln.text))
        space.extend((ln, m) for m in RE_KATA_SPACE.finditer(ln.text))
    if not naka or not space:
        return
    if prefer == "nakaguro":
        minority = space
    elif prefer == "space":
        minority = naka
    else:
        minority = space if len(naka) >= len(space) else naka
    for ln, m in minority:
        ctx.add("ja-nakaguro-or-halfwidth-space-between-katakana", ctx.at(ln, m.start()),
                "カタカナ語の区切りが「・」と半角スペースで混在しています。どちらかに統一してください。",
                m.group(0))


RE_ZENKAKU_ALNUM = re.compile(r"[Ａ-Ｚａ-ｚ０-９．]+")


def rule_zenkaku_alnum(ctx: Context) -> None:
    for ln in ctx.scan_lines():
        for m in RE_ZENKAKU_ALNUM.finditer(ln.text):
            if not re.search(r"[Ａ-Ｚａ-ｚ０-９]", m.group(0)):
                continue
            ctx.add("no-zenkaku-alnum", ctx.at(ln, m.start()),
                    f"全角英数字「{m.group(0)}」は半角にしてください。", m.group(0),
                    unicodedata.normalize("NFKC", m.group(0)))


VERB_TAIL = "たるいうくすつぬむぐぶの"
KEISHIKI = [
    (re.compile(f"(?<=[{VERB_TAIL}])事(?=[がはをにもでと、。])|(?<=ない)事(?=[がはをにもでと、。])"), "事", "こと"),
    (re.compile(f"(?<=[{VERB_TAIL}])時(?=[には、。も])|(?<=ない)時(?=[には、。も])"), "時", "とき"),
    (re.compile(f"(?<=[{VERB_TAIL}])為(?=[にの、。]|です|だ)|(?<=ない)為(?=[にの、。])"), "為", "ため"),
    (re.compile(f"(?<=[{VERB_TAIL}])訳(?=[でがはに、。])|(?<=ない)訳(?=[でがはに、。])"), "訳", "わけ"),
    (re.compile(f"(?<=[{VERB_TAIL}])物(?=[をがはにもで、。])"), "物", "もの"),
    (re.compile(f"(?<=[{VERB_TAIL}])所(?=[がでにをは、。])"), "所", "ところ"),
    (re.compile(f"(?<=[{VERB_TAIL}])通り(?=[に、。]|です|だ)"), "通り", "とおり"),
    (re.compile(r"(?<=[たる])上(?=[で、])"), "上", "うえ"),
]


def rule_hiragana_keishikimeishi(ctx: Context) -> None:
    for s in ctx.sentences:
        for pat, word, kana in KEISHIKI:
            for m in pat.finditer(s.text):
                ctx.add("ja-hiragana-keishikimeishi", s.locate(m.start()),
                        f"形式名詞「{word}」はひらがな「{kana}」で書きます。",
                        s.text[max(0, m.start() - 3): m.end() + 2], kana)


FUKUSHI = [
    ("予め", "あらかじめ"), ("殆ど", "ほとんど"), ("殆んど", "ほとんど"), ("更に", "さらに"), ("既に", "すでに"),
    ("是非", "ぜひ"), ("概ね", "おおむね"), ("但し", "ただし"), ("或いは", "あるいは"), ("即ち", "すなわち"),
    ("凡そ", "およそ"), ("僅か", "わずか"), ("暫く", "しばらく"), ("敢えて", "あえて"), ("沢山", "たくさん"),
    ("丁度", "ちょうど"), ("一寸", "ちょっと"), ("何処", "どこ"), ("何故", "なぜ"), ("何れ", "いずれ"),
    ("極めて", "きわめて"), ("全て", "すべて"), ("大体", "だいたい"), ("益々", "ますます"), ("度々", "たびたび"),
    ("屡々", "しばしば"), ("偶々", "たまたま"), ("段々", "だんだん"), ("折角", "せっかく"), ("流石", "さすが"),
    ("兎に角", "とにかく"), ("取り敢えず", "とりあえず"), ("直ぐに", "すぐに"), ("未だ", "いまだ"),
    ("尤も", "もっとも"), ("若しくは", "もしくは"), ("何時も", "いつも"), ("成る程", "なるほど"),
    ("迄", "まで"), ("及び", "および"), ("並びに", "ならびに"), ("又は", "または"), ("若し", "もし"),
    ("例えば", "たとえば"), ("尚、", "なお、"), ("又、", "また、"), ("先ず", "まず"), ("殊に", "ことに"),
    ("却って", "かえって"), ("恐らく", "おそらく"), ("勿論", "もちろん"), ("如何に", "いかに"),
    ("何時でも", "いつでも"), ("矢張り", "やはり"), ("辛うじて", "かろうじて"), ("直ちに", "ただちに"),
]
RE_FUKUSHI = re.compile("|".join(re.escape(k) for k, _ in FUKUSHI))
FUKUSHI_MAP = dict(FUKUSHI)


def rule_hiragana_fukushi(ctx: Context) -> None:
    allow = set(ctx.opt("ja-hiragana-fukushi", "allow", []))
    for s in ctx.sentences:
        for m in RE_FUKUSHI.finditer(s.text):
            word = m.group(0)
            if word in allow:
                continue
            # 直前が漢字なら熟語の一部（「全て」の前が漢字＝「完全て」は無いが、「大体」の前の「拡大体」等）。
            if m.start() and re.match(f"[{KANJI}]", s.text[m.start() - 1]):
                continue
            ctx.add("ja-hiragana-fukushi", s.locate(m.start()),
                    f"副詞「{word}」はひらがな「{FUKUSHI_MAP[word]}」で書きます。", word, FUKUSHI_MAP[word])


HOJODOUSHI = [
    (re.compile(r"(?<=[てで])(下さい|下さる|下され)"), "ください"),
    (re.compile(r"(?<=[てで])(頂く|頂き|頂け|頂い|頂きます|頂いた)"), "いただく"),
    (re.compile(f"(?<=[おご御][{KANJI}])(頂く|頂き|頂け|頂い)|(?<=[おご御][{KANJI}][{KANJI}])(頂く|頂き|頂け|頂い)"), "いただく"),
    (re.compile(r"(?<=[てで])(行く|行き|行っ|行け|行こ)"), "いく"),
    (re.compile(r"(?<=[てで])(来る|来た|来て|来ます|来い|来れ)"), "くる"),
    (re.compile(r"(?<=[てで])(見る|見て|見た|見ます|見よ|見れ)"), "みる"),
    (re.compile(r"(?<=[てで])(置く|置き|置い|置け|置こ)"), "おく"),
    (re.compile(r"(?<=[てで])(欲しい|欲しく|欲しかっ)"), "ほしい"),
    (re.compile(r"(?<=[てで])(貰う|貰い|貰っ|貰え|貰お)"), "もらう"),
    (re.compile(r"(?<=[てで])(仕舞う|仕舞い|仕舞っ|仕舞え)"), "しまう"),
    (re.compile(r"(?<=[てで])(居る|居た|居て|居ます|居れ|居ない)"), "いる"),
    (re.compile(r"(?<=[てで])(上げる|上げ|上げた|上げます)"), "あげる"),
    (re.compile(r"(?<=[てで])御覧"), "ごらん"),
    (re.compile(r"出来(る|ます|ません|ない|た|て|れば|なく|なかっ)"), "でき〜"),
    (re.compile(r"(?<=[がはもにで])(有る|有り|有ります|有った|無い|無く|無かった|無し)"), "ある／ない"),
    (re.compile(r"(?<=[しきぎりみびにちいえけげせてねべめれ])(易い|易く|難い|難く)"), "やすい／にくい"),
]


def rule_hiragana_hojodoushi(ctx: Context) -> None:
    for s in ctx.sentences:
        for pat, kana in HOJODOUSHI:
            for m in pat.finditer(s.text):
                ctx.add("ja-hiragana-hojodoushi", s.locate(m.start()),
                        f"補助動詞「{m.group(0)}」はひらがな「{kana}」で書きます。",
                        s.text[max(0, m.start() - 2): m.end() + 1], kana)


VARIANT_GROUPS = [
    ["サーバー", "サーバ"], ["ユーザー", "ユーザ"], ["コンピューター", "コンピュータ"],
    ["プリンター", "プリンタ"], ["ブラウザー", "ブラウザ"], ["フォルダー", "フォルダ"],
    ["パラメーター", "パラメータ"], ["インターフェース", "インターフェイス", "インタフェース"],
    ["メモリー", "メモリ"], ["ウィンドウ", "ウインドウ"], ["ソフトウェア", "ソフトウエア"],
    ["ハードウェア", "ハードウエア"], ["プロセッサー", "プロセッサ"], ["ディレクトリー", "ディレクトリ"],
    ["ライブラリー", "ライブラリ"], ["エディター", "エディタ"], ["スケジューラー", "スケジューラ"],
    ["行う", "行なう"], ["表す", "表わす"], ["問い合わせ", "問合せ", "問合わせ"],
    ["申し込み", "申込み"], ["取り扱い", "取扱い"], ["引き続き", "引続き"], ["見積もり", "見積り"],
    ["打ち合わせ", "打合せ", "打合わせ"], ["立ち上げ", "立上げ"], ["組み込み", "組込み"],
    ["読み込み", "読込み"], ["書き込み", "書込み"], ["切り替え", "切替え"], ["割り当て", "割当て"],
    ["貼り付け", "貼付け"], ["締め切り", "締切り"], ["分かる", "わかる", "判る", "解る"],
    ["様々", "さまざま"], ["私たち", "私達"], ["子供", "子ども"], ["お客様", "お客さま"],
    ["皆様", "皆さま"], ["一旦", "いったん"], ["致します", "いたします"], ["宜しく", "よろしく"],
    ["有難う", "ありがとう"],
]


def rule_orthographic_variants(ctx: Context) -> None:
    groups = VARIANT_GROUPS + [list(g) for g in ctx.opt("ja-no-orthographic-variants", "groups", [])]
    text_lines = list(ctx.scan_lines())
    for group in groups:
        # 長い形から当てる（「サーバー」の中の「サーバ」を数えない）。
        ordered = sorted(group, key=len, reverse=True)
        pat = re.compile("|".join(re.escape(w) for w in ordered))
        occ: dict[str, list[tuple[Line, int]]] = {w: [] for w in group}
        for ln in text_lines:
            for m in pat.finditer(ln.text):
                occ[m.group(0)].append((ln, m.start()))
        used = [w for w in group if occ[w]]
        if len(used) < 2:
            continue
        majority = max(used, key=lambda w: (len(occ[w]), -group.index(w)))
        for w in used:
            if w == majority:
                continue
            for ln, idx in occ[w]:
                ctx.add("ja-no-orthographic-variants", ctx.at(ln, idx),
                        f"表記ゆれ「{w}」です。この文書では「{majority}」が多数です。", w, majority)


def rule_prh(ctx: Context) -> None:
    terms = ctx.options.get("__terms__") or []
    for term in terms:
        pat = term["_re"]
        expected = term.get("expected", "")
        for ln in ctx.scan_lines():
            for m in pat.finditer(ln.text):
                if m.group(0) == expected:
                    continue
                ctx.add("prh", ctx.at(ln, m.start()),
                        f"用語「{m.group(0)}」は「{expected}」と書きます。", m.group(0), expected)


RULES = {
    "sentence-length": rule_sentence_length,
    "max-ten": rule_max_ten,
    "max-kanji-continuous-len": rule_max_kanji,
    "no-mix-dearu-desumasu": rule_dearu_desumasu,
    "ja-no-mixed-period": rule_mixed_period,
    "no-double-negative-ja": rule_double_negative,
    "no-dropped-i": rule_dropped_i,
    "no-dropping-the-ra": rule_dropping_ra,
    "no-doubled-conjunctive-particle-ga": rule_doubled_ga,
    "no-doubled-conjunction": rule_doubled_conjunction,
    "no-doubled-joshi": rule_doubled_joshi,
    "no-nfd": rule_nfd,
    "no-invalid-control-character": rule_control_chars,
    "no-zero-width-spaces": rule_zero_width,
    "no-exclamation-question-mark": rule_exclamation,
    "no-hankaku-kana": rule_hankaku_kana,
    "ja-no-weak-phrase": rule_weak_phrase,
    "ja-no-successive-word": rule_successive_word,
    "ja-no-abusage": rule_abusage,
    "ja-no-redundant-expression": rule_redundant,
    "ja-unnatural-alphabet": rule_unnatural_alphabet,
    "no-unmatched-pair": rule_unmatched_pair,
    "ja-space-between-half-and-full-width": rule_space_half_full,
    "ja-no-space-around-parentheses": rule_space_around_parentheses,
    "ja-nakaguro-or-halfwidth-space-between-katakana": rule_nakaguro_or_space,
    "ja-hiragana-keishikimeishi": rule_hiragana_keishikimeishi,
    "ja-hiragana-fukushi": rule_hiragana_fukushi,
    "ja-hiragana-hojodoushi": rule_hiragana_hojodoushi,
    "ja-no-orthographic-variants": rule_orthographic_variants,
    "no-zenkaku-alnum": rule_zenkaku_alnum,
    "prh": rule_prh,
}
assert set(RULES) == set(ALL_RULES)


# ── 設定 ──────────────────────────────────────────────────────────────────────
class Settings:
    """有効なルールと severity、オプション、用語。設定ファイルを読んで決める。"""

    def __init__(self, data: dict | None):
        data = data or {}
        unknown = set(data) - CONFIG_KEYS
        if unknown:
            raise Unchecked(f"設定に知らないキーがある: {sorted(unknown)}（誤記なら検査が黙って抜ける）")
        presets = data.get("presets", DEFAULT_PRESETS)
        if presets == "all":
            presets = list(PRESETS)
        if not isinstance(presets, list) or any(p not in PRESETS for p in presets):
            raise Unchecked(f"presets は {sorted(PRESETS)} の配列: {presets!r}")
        self.presets = list(presets)
        enabled: dict[str, str] = {}
        for p in presets:
            for r in PRESETS[p]:
                enabled[r] = DEFAULT_SEVERITY[r]
        self.options: dict[str, dict] = {}
        rules = data.get("rules", {})
        if not isinstance(rules, dict):
            raise Unchecked("rules はオブジェクト")
        for name, val in rules.items():
            if name not in RULES:
                raise Unchecked(f"知らないルール: {name}")
            if val is False:
                enabled.pop(name, None)
            elif val is True:
                enabled[name] = DEFAULT_SEVERITY[name]
            elif val in ("error", "warning"):
                enabled[name] = val
            elif isinstance(val, dict):
                sev = val.get("severity", enabled.get(name, DEFAULT_SEVERITY[name]))
                if sev not in ("error", "warning"):
                    raise Unchecked(f"{name}.severity は error か warning: {sev!r}")
                enabled[name] = sev
                self.options[name] = {k: v for k, v in val.items() if k != "severity"}
            else:
                raise Unchecked(f"{name} の値が読めない: {val!r}")
        terms = data.get("terms", [])
        if not isinstance(terms, list):
            raise Unchecked("terms は配列")
        compiled: list[dict] = []
        for t in terms:
            if not isinstance(t, dict) or "pattern" not in t or "expected" not in t:
                raise Unchecked(f"terms の要素は {{pattern, expected}}: {t!r}")
            try:
                rx = re.compile(t["pattern"] if t.get("regex") else re.escape(t["pattern"]))
            except re.error as exc:
                raise Unchecked(f"terms の正規表現が壊れている: {t['pattern']!r}: {exc}") from exc
            compiled.append({**t, "_re": rx})
        if compiled:
            enabled["prh"] = enabled.get("prh", DEFAULT_SEVERITY["prh"])
            self.options["__terms__"] = compiled
        elif "prh" in enabled:
            enabled.pop("prh")
        ignore = data.get("ignore", [])
        if not isinstance(ignore, list) or any(not isinstance(x, str) for x in ignore):
            raise Unchecked("ignore は文字列の配列")
        try:
            self.ignore = [re.compile(x) for x in ignore]
        except re.error as exc:
            raise Unchecked(f"ignore の正規表現が壊れている: {exc}") from exc
        self.enabled = enabled
        self.paths = data.get("paths", [])
        if not isinstance(self.paths, list):
            raise Unchecked("paths は配列")


def load_config(path: str | None, explicit: bool) -> tuple[dict | None, str | None]:
    """設定ファイルを読む。無ければ None。明示指定したのに無ければ unchecked。"""
    target = path or DEFAULT_CONFIG
    if not os.path.isfile(target):
        if explicit:
            raise Unchecked(f"指定した設定ファイルが無い: {target}")
        return None, None
    try:
        with open(target, encoding="utf-8") as fh:
            data = json.load(fh)
    except UnicodeDecodeError as exc:
        raise Unchecked(f"設定ファイルが UTF-8 でない: {target}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise Unchecked(f"設定ファイルの JSON が壊れている: {target}: {exc}") from exc
    if not isinstance(data, dict):
        raise Unchecked(f"設定ファイルの最上位はオブジェクト: {target}")
    return data, target


# ── 走査 ──────────────────────────────────────────────────────────────────────
def lint_text(source: str, settings: Settings, name: str = STDIN_NAME) -> list[Finding]:
    ctx = Context(name, source, settings.options)
    for rule in settings.enabled:
        RULES[rule](ctx)
    out: list[Finding] = []
    for f in ctx.findings:
        f["severity"] = settings.enabled[f["rule"]]
        if any(rx.search(f.get("text", "")) for rx in settings.ignore):
            continue
        out.append(f)
    out.sort(key=lambda f: (f["line"], f["column"], f["rule"]))
    return out


def collect_artifacts(paths: list[str], skip: set[str]) -> tuple[list[str], list[dict]]:
    files: list[str] = []
    skipped: list[dict] = []
    for p in paths:
        if p == "-":
            files.append("-")
            continue
        if os.path.islink(p):
            skipped.append({"path": p, "why": "symlink"})
            continue
        if os.path.isdir(p):
            for root, dirs, names in os.walk(p):
                dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "node_modules"
                                 and not os.path.islink(os.path.join(root, d)))
                for n in sorted(names):
                    full = os.path.join(root, n)
                    if os.path.islink(full):
                        skipped.append({"path": full, "why": "symlink"})
                    elif n.lower().endswith(TEXT_SUFFIXES):
                        if os.path.realpath(full) not in skip:
                            files.append(full)
                    else:
                        skipped.append({"path": full, "why": "suffix"})
            continue
        if os.path.isfile(p):
            if os.path.realpath(p) in skip:
                continue
            files.append(p)
            continue
        raise Unchecked(f"成果物が無い: {p}")
    return files, skipped


def read_source(path: str) -> str:
    if path == "-":
        data = sys.stdin.buffer.read()
    else:
        with open(path, "rb") as fh:
            data = fh.read()
    if len(data) > MAX_INPUT_BYTES:
        raise Unchecked(f"成果物が大きすぎる（{len(data)} bytes > {MAX_INPUT_BYTES}）: {path}")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Unchecked(f"成果物が UTF-8 でない: {path}: {exc}") from exc
    return text.replace("\r\n", "\n").replace("\r", "\n")


def file_sha256(path: str | None) -> str | None:
    if not path:
        return None
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
        prog="rig-wb ja-lint",
        description="textlint-ja 相当の日本語校正（error があれば exit 1、未検査は exit 2）",
    )
    parser.add_argument("artifacts", nargs="*",
                        help="検査する Markdown / テキスト（ファイル、ディレクトリ、または - で stdin）。"
                             "省略時は設定ファイルの paths を使う")
    parser.add_argument("--config", default=None, help=f"設定ファイル（既定: {DEFAULT_CONFIG}）")
    parser.add_argument("--preset", action="append", default=None, choices=sorted(PRESETS),
                        help="設定ファイルより優先して有効にする preset（複数可）")
    parser.add_argument("--rule", action="append", default=None, metavar="NAME",
                        help="この名前のルールだけを走らせる（複数可）")
    parser.add_argument("--json", action="store_true", help="機械可読な JSON で出力する")
    parser.add_argument("--report", metavar="PATH",
                        help="どの状態でも JSON 報告をこのパスに書く（orchestrate は stdout を捨てるため）")
    parser.add_argument("--strict", action="store_true", help="warning も exit 1 に数える")
    parser.add_argument("--if-configured", action="store_true",
                        help="設定ファイルも引数も無ければ not-configured として exit 0")
    parser.add_argument("--list-rules", action="store_true", help="ルール一覧と既定 severity を出して終わる")
    args = parser.parse_args(argv)

    if args.list_rules:
        for p, rules in PRESETS.items():
            print(f"[{p}]")
            for r in rules:
                print(f"  {r:48s} {DEFAULT_SEVERITY[r]}")
        print("[prh]  (terms が宣言されたときだけ)")
        print(f"  {'prh':48s} {DEFAULT_SEVERITY['prh']}")
        return 0

    status, reason = "checked", ""
    config_path: str | None = None
    files: list[str] = []
    skipped: list[dict] = []
    findings: list[Finding] = []
    settings: Settings | None = None
    try:
        data, config_path = load_config(args.config, args.config is not None)
        if data is not None and args.preset:
            data = dict(data, presets=args.preset)
        elif data is None and args.preset:
            data = {"presets": args.preset}
        settings = Settings(data)
        if args.rule:
            unknown = [r for r in args.rule if r not in RULES]
            if unknown:
                raise Unchecked(f"知らないルール: {unknown}")
            settings.enabled = {r: settings.enabled.get(r, DEFAULT_SEVERITY[r]) for r in args.rule}
        targets = list(args.artifacts) or list(settings.paths)
        if not targets:
            if args.if_configured:
                raise NotConfigured("検査対象が指定されていない（引数も設定の paths も無い）")
            raise Unchecked("検査対象が指定されていない（引数も設定の paths も無い）")
        skip: set[str] = set()
        if config_path:
            skip.add(os.path.realpath(config_path))
        if args.report:
            skip.add(os.path.realpath(args.report))
        files, skipped = collect_artifacts(targets, skip)
        if not files:
            raise Unchecked("検査対象のテキスト成果物が 1 件も無い")
        for path in files:
            source = read_source(path)
            findings.extend(lint_text(source, settings, STDIN_NAME if path == "-" else path))
    except NotConfigured as exc:
        status, reason = "not-configured", str(exc)
    except Unchecked as exc:
        status, reason = "unchecked", str(exc)
    except Exception as exc:  # noqa: BLE001 — 報告を残さず落ちるほうが害が大きい
        status, reason = "unchecked", f"想定外の例外: {type(exc).__name__}: {exc}"

    errors = sum(1 for f in findings if f["severity"] == "error")
    warnings = sum(1 for f in findings if f["severity"] == "warning")
    report = {
        "status": status,
        "reason": reason,
        "scope": SCOPE_NOTE,
        "config": config_path,
        "config_sha256": file_sha256(config_path),
        "presets": settings.presets if settings else [],
        "rules": settings.enabled if settings else {},
        "artifacts": files,
        "skipped": skipped,
        "summary": {"errors": errors, "warnings": warnings, "files": len(files)},
        "findings": findings,
    }
    if args.report:
        try:
            write_report(args.report, report)
        except OSError as exc:
            print(f"未検査: 報告を書けない: {args.report}: {exc}", file=sys.stderr)
            return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif status == "not-configured":
        print(f"未設定: {reason}")
        print(f"検査していない。検査したいなら引数に path を渡すか {DEFAULT_CONFIG} の paths に書く"
              "（雛形: manifests/ja-textlint.template.json）。")
    elif status == "unchecked":
        print(f"未検査: {reason}", file=sys.stderr)
        print("合格ではない。走らなかったことを合格として扱わない。", file=sys.stderr)
    else:
        for f in findings:
            fix = f"  → {f['fix']}" if f.get("fix") else ""
            print(f"{f['file']}:{f['line']}:{f['column']}: {f['severity']} [{f['rule']}] {f['message']}{fix}")
        print(f"error {errors} 件 / warning {warnings} 件 / 検査した成果物 {len(files)} 件"
              f"（presets: {', '.join(report['presets'])}）")

    if status == "not-configured":
        return 0
    if status == "unchecked":
        return 2
    return 1 if errors or (args.strict and warnings) else 0


if __name__ == "__main__":
    sys.exit(main())
