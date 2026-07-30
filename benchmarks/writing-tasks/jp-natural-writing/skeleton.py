#!/usr/bin/env python3
"""Extract a content-free structural score from one human article, to be filled by a model.

Implements the workflow agent's recovered design (its session had no working tools, so this
is the first implementation). The idea: every arm's uniformity is the model distributing
its own structure; a skeleton transplanted from one real donor gives it a *particular
uneven profile* instead. There is no uniform satisfier for "paragraph 4 has sentences of
38, 9, 52, 11, 44 chars" — compliance is a match, not a maximum, so the gradient the
generator rode to beat every quota does not exist.

The hard rule is that nothing semantic transfers. The score holds only counts, lengths,
positions and categories — no heading text, no vocabulary, no n-grams. assert_no_leak()
makes that mechanical: no substring of the donor longer than 3 chars may appear in the
rendered score (ASCII digits/labels excepted, since lengths are numbers).

Deliberately omitted from the original design: the defect budget (reproduce the donor's
typo count at the donor's positions). Detecting typos in human text mechanically is
unreliable, and a wrong defect inventory would instruct the model to fabricate flaws at
stated positions — the exact quota failure this benchmark has already paid for twice.
"""

import hashlib
import json
import re
from pathlib import Path

SENT_SPLIT = re.compile(r"(?<=[。！？])")
POLITE = re.compile(r"(です|ます|でした|ました|ません)[。！？]?$")
PLAIN = re.compile(r"(だ|である|だった|た|る|い)[。！？]?$")


def _heading_type(text: str) -> str:
    stripped = text.strip("# ").strip()
    if "？" in stripped or "?" in stripped:
        return "疑問"
    if re.search(r"[一-鿿ァ-ヴーA-Za-z0-9]$", stripped):
        return "名詞句"
    return "文"


def _final_form(sentence: str) -> str:
    s = sentence.strip().rstrip("」）)")
    if not s:
        return "他"
    if s.endswith(("？", "?")):
        return "疑問"
    if POLITE.search(s):
        return "丁寧"
    if re.search(r"[一-鿿ァ-ヴー]$", s.rstrip("。！")):
        return "体言"
    if PLAIN.search(s):
        return "常体"
    return "他"


def extract(md: str) -> dict:
    """Structural score of a markdown article. Counts and categories only."""
    # replace fenced code with placeholders so prose stats exclude listings but positions survive
    code_sizes = [len(m.group(0).splitlines()) - 2 for m in re.finditer(r"```.*?```", md, re.DOTALL)]
    body = re.sub(r"```.*?```", "\x00CODE\x00", md, flags=re.DOTALL)

    blocks = [b for b in re.split(r"\n\s*\n", body) if b.strip()]
    sections: list[dict] = []
    current = {"heading_len": 0, "heading_type": "なし", "paras": [], "code": 0,
               "list_lines": 0, "links": 0, "images": 0}

    for block in blocks:
        stripped = block.strip()
        if re.match(r"^#{1,6} ", stripped):
            if current["paras"] or current["heading_len"]:
                sections.append(current)
            head = stripped.splitlines()[0]
            current = {"heading_len": len(head.lstrip("# ")), "heading_type": _heading_type(head),
                       "paras": [], "code": 0, "list_lines": 0, "links": 0, "images": 0}
            stripped = "\n".join(stripped.splitlines()[1:]).strip()
            if not stripped:
                continue
        current["code"] += stripped.count("\x00CODE\x00")
        current["images"] += len(re.findall(r"!\[", stripped))
        # counted directly, not as url-matches minus images — that arithmetic double-counted
        # image URLs and went negative (a rendered score once said リンク-8件)
        current["links"] += (len(re.findall(r"(?<!!)\[[^\]]*\]\(", stripped))
                             + len(re.findall(r"(?<![(\]])https?://", stripped)))
        list_lines = len(re.findall(r"(?m)^\s*[-*+\d]+[.)]?\s+\S", stripped))
        current["list_lines"] += list_lines
        prose = re.sub(r"\x00CODE\x00|!\[[^\]]*\]\([^)]*\)|\[[^\]]*\]\([^)]*\)", "", stripped)
        if list_lines or not prose.strip():
            continue
        sents = [s for s in SENT_SPLIT.split(prose) if s.strip()]
        if sents:
            current["paras"].append({
                "sent_lens": [len(s.strip()) for s in sents],
                "finals": [_final_form(s) for s in sents],
            })
    sections.append(current)

    last_prose = ""
    for section in reversed(sections):
        if section["paras"]:
            last_prose = "x"  # marker: prose exists
            tail_sents = section["paras"][-1]["sent_lens"]
            break
    ends_open = bool(last_prose) and not md.rstrip().endswith(("。", "！", "？", "```", ")", "）"))

    return {"sections": sections, "code_sizes": code_sizes, "ends_open": ends_open}


def render(skel: dict) -> str:
    """Compact text score for the prompt. Numbers and category labels only."""
    lines = []
    for i, sec in enumerate(skel["sections"], 1):
        head = (f"見出し{sec['heading_len']}字/{sec['heading_type']}"
                if sec["heading_len"] else "見出しなし")
        lines.append(f"§{i} {head}")
        for para in sec["paras"]:
            forms = "".join({"丁寧": "丁", "常体": "常", "体言": "体", "疑問": "疑", "他": "他"}[f]
                            for f in para["finals"])
            lines.append(f"  段落: {len(para['sent_lens'])}文 {para['sent_lens']} 文末[{forms}]")
        extras = []
        if sec["code"]:
            extras.append(f"コードブロック{sec['code']}個")
        if sec["list_lines"]:
            extras.append(f"箇条書き{sec['list_lines']}行")
        if sec["links"]:
            extras.append(f"リンク{sec['links']}件")
        if sec["images"]:
            extras.append(f"画像{sec['images']}枚")
        if extras:
            lines.append("  " + "、".join(extras))
    if skel["ends_open"]:
        lines.append("末尾: 文の途中で終わっている")
    return "\n".join(lines)


# render() writes from this fixed vocabulary and nothing else. The leak check strips these
# before matching — a donor that happens to contain 「リンク」 must not trip it (it did).
_TEMPLATE_VOCAB = ("見出し", "なし", "名詞句", "疑問", "段落", "文末", "コードブロック",
                   "箇条書き", "リンク", "画像", "末尾", "文の途中で終わっている",
                   "字", "文", "個", "行", "件", "枚")


def assert_no_leak(donor_md: str, rendered: str) -> None:
    """No 4+ char substring of the donor may survive into the score.

    Checked against the render with the template's own vocabulary removed, so only
    donor-originated content can match.
    """
    stripped = rendered
    for token in _TEMPLATE_VOCAB:
        stripped = stripped.replace(token, "\x00")
    ascii_ok = re.compile(r"^[\x00-\x7f]+$")
    for size in (8, 6, 4):
        for start in range(0, max(0, len(donor_md) - size), 3):
            chunk = donor_md[start:start + size]
            if ascii_ok.match(chunk) or not chunk.strip():
                continue
            if chunk in stripped:
                raise AssertionError(f"donor content leaked into skeleton: {chunk!r}")


def diff(target: dict, actual: dict, top: int = 5) -> list[str]:
    """Largest numeric deltas, for the mechanical round-2 feedback. No style advice."""
    deltas: list[tuple[float, str]] = []
    t_secs, a_secs = target["sections"], actual["sections"]
    if len(t_secs) != len(a_secs):
        deltas.append((abs(len(t_secs) - len(a_secs)) * 100,
                       f"節の数: 指定 {len(t_secs)}、現状 {len(a_secs)}"))
    for i, (ts, as_) in enumerate(zip(t_secs, a_secs), 1):
        if len(ts["paras"]) != len(as_["paras"]):
            deltas.append((abs(len(ts["paras"]) - len(as_["paras"])) * 20,
                           f"§{i} の段落数: 指定 {len(ts['paras'])}、現状 {len(as_['paras'])}"))
        for j, (tp, ap) in enumerate(zip(ts["paras"], as_["paras"]), 1):
            if len(tp["sent_lens"]) != len(ap["sent_lens"]):
                deltas.append((abs(len(tp["sent_lens"]) - len(ap["sent_lens"])) * 10,
                               f"§{i}段落{j}の文数: 指定 {len(tp['sent_lens'])}、現状 {len(ap['sent_lens'])}"))
            else:
                for k, (tl, al) in enumerate(zip(tp["sent_lens"], ap["sent_lens"]), 1):
                    # ±30% band: without it the model pads sentences to hit a char count
                    if tl and abs(al - tl) / tl > 0.30:
                        deltas.append((abs(al - tl),
                                       f"§{i}段落{j}文{k}の長さ: 目安 {tl}字、現状 {al}字"))
    deltas.sort(key=lambda d: -d[0])
    return [d[1] for d in deltas[:top]]


def pick_donor(corpus: Path, topic: str, seed: str, lo: int = 1500, hi: int = 2600) -> tuple[str, str]:
    """One donor body in the length band, deterministic per (topic, seed)."""
    index = json.loads((corpus / "index.json").read_text())
    fitting = [e for e in index if lo <= e["chars"] <= hi] or index
    h = int(hashlib.sha1(f"{topic}:{seed}".encode()).hexdigest(), 16)
    entry = fitting[h % len(fitting)]
    return entry["file"], (corpus / entry["file"]).read_text()
