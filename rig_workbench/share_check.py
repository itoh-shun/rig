#!/usr/bin/env python3
"""share_check — 共有する 1 枚の HTML を、公開する前に機械で検査するセンサー（stdlib のみ）。

エージェントに作らせた HTML は、書いた本人が開いて眺めても壊れ方が見えません。外部の画像を
1 枚だけ読んでいる、ダークモードで文字が背景に沈む、`{{TITLE}}` が残っている、API キーが
表の中に紛れている。どれも公開してから気づきます。このセンサーは、`/rig:share` が Artifact
として公開する前の 1 枚を読み、Artifact のページ契約と共有の規則に照らして位置つきで
報告します。

    rig-wb share-check share/weekly-report.html
    rig-wb share-check --report .rig/share-check-report.json share/

規則の正本は `skills/engine/facets/policies/share-page-rules.md` です。ここで捕るのは、
構文から機械的に決まるものだけです。内容が正しいか、読み手に合っているかは捕れません。
捕れたことにせず、reviewer の判定に回します。

    error  公開してはいけない状態。1 件でもあれば exit 1。
           title が無いか先頭 8 KB より後ろにある、doctype・<html>・<head>・<body> を自分で
           書いている（Artifact は公開時にこれらと viewport を外から被せる）、許可外の外部
           資源を読む、相対パスの資源を読む（1 枚で完結しない）、ダークモードの定義が無い、
           :root の色トークンが無い、body の背景が無い、プレースホルダーが残っている、
           秘密情報の形をした文字列がある、alt の無い画像、外部へ送信するフォーム、
           meta refresh、16 MB 超。
    warn   直したほうがよいが、公開は止めない。--strict で exit 1 に数える。
           1 MB 超、図が 1 つも無い、見出しの段が飛ぶ・h1 が 1 つでない、テーマ切り替えの
           属性セレクタが無い、狭い画面で横スクロールを生む固定幅、長い title。

状態は 2 つあります。「検査して問題がなかった」と「検査できなかった」は別物です。

    checked     HTML を読み、規則を当てた。
    unchecked   パスが無い、HTML が 1 枚も見つからない、読めない。走らなかったことを
                合格として扱わないための区別です。

終了コード:

    0   checked かつ error なし（--strict では warn もなし）。
    1   checked かつ error あり。
    2   unchecked。

`--report <path>` は、どの状態でも JSON 報告を書きます。orchestrate の checks 実行系は
stdout を捨てるため、reviewer が根拠に引くのはこのファイルです。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from html.parser import HTMLParser

from .ports.local import CONSOLE

MAX_BYTES = 16 * 1024 * 1024
WARN_BYTES = 1024 * 1024
TITLE_SCAN_BYTES = 8 * 1024

#: Artifact のページ契約が外部から読むことを許すホスト。script はこの 5 つ、stylesheet は
#: Google Fonts だけ。それ以外はすべてページの中へ埋め込む。
SCRIPT_HOSTS = (
    "cdnjs.cloudflare.com/",
    "cdn.jsdelivr.net/npm/",
    "unpkg.com/",
    "cdn.tailwindcss.com",
    "code.jquery.com/",
)
STYLE_HOSTS = ("fonts.googleapis.com/", "fonts.gstatic.com/")

#: 資源として読み込まれる属性。`<a href>` は遷移なので対象外。
RESOURCE_ATTRS = {
    "script": ("src",),
    "img": ("src", "srcset"),
    "iframe": ("src",),
    "video": ("src", "poster"),
    "audio": ("src",),
    "source": ("src", "srcset"),
    "track": ("src",),
    "embed": ("src",),
    "object": ("data",),
    "input": ("src",),
    "image": ("href", "xlink:href"),  # SVG の <image>
}
#: `<link rel=...>` のうち、ページが読み込むもの。
LINK_LOADING_RELS = {"stylesheet", "icon", "preload", "modulepreload", "manifest", "apple-touch-icon"}

RE_PLACEHOLDER = re.compile(r"\{\{\s*[A-Za-z_][\w.-]*\s*\}\}|\[要記入\]")
RE_CSS_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)
RE_CSS_IMPORT = re.compile(r"@import\s+(?:url\(\s*)?(['\"])(.*?)\1", re.IGNORECASE)
RE_CSS_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
RE_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
RE_FIXED_WIDTH = re.compile(r"(?<![\w-])(min-width|width)\s*:\s*(\d+(?:\.\d+)?)px", re.IGNORECASE)
FIXED_WIDTH_LIMIT = 480


def _is_remote(url: str) -> bool:
    lowered = url.strip().lower()
    return lowered.startswith(("http://", "https://", "//"))


def _is_inline(url: str) -> bool:
    lowered = url.strip().lower()
    return (not lowered or lowered.startswith(("data:", "#", "blob:", "about:"))
            or lowered.startswith("javascript:"))


def _host_path(url: str) -> str:
    stripped = url.strip()
    for prefix in ("https://", "http://", "//"):
        if stripped.lower().startswith(prefix):
            return stripped[len(prefix):]
    return stripped


def _allowed(url: str, hosts: tuple[str, ...]) -> bool:
    target = _host_path(url).lower()
    return any(target.startswith(host) for host in hosts)


class _Page(HTMLParser):
    """1 枚の HTML から、規則が見るものだけを行番号つきで拾う。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.title_line: int | None = None
        self._in_title = False
        self._in_style = False
        self._in_script = False
        self.skeleton: list[tuple[int, str]] = []  # line, what
        self._svg_depth = 0  # <svg> の中の <title> は図の代替テキストで、ページの名前ではない
        self.styles: list[tuple[int, str]] = []
        self._style_buf: list[str] = []
        self._style_line = 0
        self.resources: list[tuple[int, str, str, tuple[str, ...]]] = []  # line, tag, url, allowed hosts
        self.images_without_alt: list[int] = []
        self.figures = 0
        self.headings: list[tuple[int, int]] = []  # line, level
        self.external_forms: list[tuple[int, str]] = []
        self.meta_refresh: list[int] = []
        self.inline_style_attrs: list[tuple[int, str]] = []

    def handle_starttag(self, tag, attrs):
        line = self.getpos()[0]
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in ("html", "head", "body"):
            self.skeleton.append((line, f"<{tag}>"))
        elif tag == "svg":
            self._svg_depth += 1
        elif tag == "title" and not self._svg_depth and self.title_line is None:
            self._in_title = True
            self.title_line = line
        elif tag == "style":
            self._in_style = True
            self._style_buf = []
            self._style_line = line
        elif tag == "script":
            self._in_script = True
        elif tag == "meta":
            if a.get("http-equiv", "").lower() == "refresh":
                self.meta_refresh.append(line)
        elif tag == "link":
            rels = set(a.get("rel", "").lower().split())
            if rels & LINK_LOADING_RELS and "href" in a:
                hosts = STYLE_HOSTS if "stylesheet" in rels or "preload" in rels else ()
                self.resources.append((line, "link", a["href"], hosts))
        elif tag == "form":
            action = a.get("action", "")
            if _is_remote(action):
                self.external_forms.append((line, action))
        if tag in ("svg", "img", "canvas", "picture"):
            self.figures += 1
        if tag == "img" and "alt" not in a:
            self.images_without_alt.append(line)
        if re.fullmatch(r"h[1-6]", tag):
            self.headings.append((line, int(tag[1])))
        for attr in RESOURCE_ATTRS.get(tag, ()):
            if attr not in a:
                continue
            hosts = SCRIPT_HOSTS if tag == "script" else ()
            if attr == "srcset":
                for candidate in a[attr].split(","):
                    url = candidate.strip().split(" ")[0]
                    if url:
                        self.resources.append((line, tag, url, hosts))
            else:
                self.resources.append((line, tag, a[attr], hosts))
        if "style" in a:
            self.inline_style_attrs.append((line, a["style"]))

    def handle_decl(self, decl):
        if decl.lower().startswith("doctype"):
            self.skeleton.append((self.getpos()[0], "<!doctype>"))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag == "title":
            self._in_title = False

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "svg" and self._svg_depth:
            self._svg_depth -= 1
        elif tag == "style" and self._in_style:
            self._in_style = False
            self.styles.append((self._style_line, "".join(self._style_buf)))
        elif tag == "script":
            self._in_script = False

    def handle_data(self, data):
        if self._in_title:
            self.title_parts.append(data)
        elif self._in_style:
            self._style_buf.append(data)


def _finding(severity: str, rule: str, path: str, line: int, message: str) -> dict:
    return {"severity": severity, "rule": rule, "path": path, "line": line, "message": message}


def _css_rules(css: str):
    css = RE_CSS_COMMENT.sub("", css)
    for m in RE_CSS_RULE.finditer(css):
        yield m.group(1).strip(), m.group(2), m.start()


def _line_of(text: str, offset: int, base: int) -> int:
    return base + text.count("\n", 0, offset)


def check_text(text: str, path: str, size: int | None = None) -> list[dict]:
    """1 枚の HTML 本文に規則を当て、所見を返す。"""
    findings: list[dict] = []
    size = len(text.encode("utf-8")) if size is None else size
    if size > MAX_BYTES:
        findings.append(_finding("error", "too-large", path, 1,
                                 f"{size} bytes。Artifact は 16 MB を超えるページを公開できない"))
    elif size > WARN_BYTES:
        findings.append(_finding("warn", "large-page", path, 1,
                                 f"{size} bytes。埋め込み画像の枚数を先に減らす（解像度より先）"))

    page = _Page()
    page.feed(text)
    page.close()
    if page._in_style:  # 閉じていない <style> も検査の対象に入れる
        page.styles.append((page._style_line, "".join(page._style_buf)))

    title = " ".join("".join(page.title_parts).split())
    if not title:
        findings.append(_finding("error", "no-title", path, page.title_line or 1,
                                 "<title> が無いか空。一覧で見分けられる名前を付ける"))
    elif len(title) > 40:
        findings.append(_finding("warn", "long-title", path, page.title_line or 1,
                                 f"title が {len(title)} 文字。名前は短くし、説明は description へ回す"))
    if title:
        start = text.lower().find("<title")
        if start >= 0 and len(text[:start].encode("utf-8")) > TITLE_SCAN_BYTES:
            findings.append(_finding("error", "title-late", path, page.title_line or 1,
                                     "<title> が先頭 8 KB より後ろにある。Artifact は名前を見つけられない"))
    for line, what in page.skeleton:
        findings.append(_finding("error", "own-skeleton", path, line,
                                 f"{what} を書いている。Artifact は公開時に doctype・head・viewport を"
                                 "外から被せるので、<title> と <style> から書き始める"))

    for line, tag, url, hosts in page.resources:
        if _is_inline(url):
            continue
        if _is_remote(url):
            if hosts and _allowed(url, hosts):
                continue
            findings.append(_finding("error", "external-resource", path, line,
                                     f"<{tag}> が許可外の外部資源を読む: {url[:120]}"))
        else:
            findings.append(_finding("error", "local-reference", path, line,
                                     f"<{tag}> が相対パスの資源を読む: {url[:120]}。"
                                     "公開するのは 1 枚だけなので data: で埋め込む"))

    css_all = ""
    for base, css in page.styles:
        css_all += css + "\n"
        for m in RE_CSS_IMPORT.finditer(css):
            url = m.group(2)
            if _is_remote(url) and _allowed(url, STYLE_HOSTS):
                continue
            rule = "external-resource" if _is_remote(url) else "local-reference"
            findings.append(_finding("error", rule, path, _line_of(css, m.start(), base),
                                     f"@import が 1 枚の外を読む: {url[:120]}"))
        for m in RE_CSS_URL.finditer(css):
            url = m.group(2)
            if _is_inline(url) or (_is_remote(url) and _allowed(url, STYLE_HOSTS)):
                continue
            rule = "external-resource" if _is_remote(url) else "local-reference"
            findings.append(_finding("error", rule, path, _line_of(css, m.start(), base),
                                     f"CSS の url() が 1 枚の外を読む: {url[:120]}"))
        for m in RE_FIXED_WIDTH.finditer(css):
            if float(m.group(2)) > FIXED_WIDTH_LIMIT:
                findings.append(_finding("warn", "fixed-width", path, _line_of(css, m.start(), base),
                                         f"{m.group(1)}: {m.group(2)}px。390px 幅で横スクロールが出る。"
                                         "max-width か % にする"))
    for line, style in page.inline_style_attrs:
        for m in RE_CSS_URL.finditer(style):
            url = m.group(2)
            if not _is_inline(url):
                rule = "external-resource" if _is_remote(url) else "local-reference"
                findings.append(_finding("error", rule, path, line,
                                         f"style 属性の url() が 1 枚の外を読む: {url[:120]}"))

    if "prefers-color-scheme" not in css_all or not re.search(r"prefers-color-scheme\s*:\s*dark", css_all):
        findings.append(_finding("error", "no-dark-mode", path, 1,
                                 "@media (prefers-color-scheme: dark) が無い。ダークモードの閲覧者には"
                                 "色が反転しないまま届く"))
    elif "data-theme" not in css_all:
        findings.append(_finding("warn", "no-theme-guard", path, 1,
                                 ':root:not([data-theme="light"]) と :root[data-theme="dark"] の'
                                 "切り替えが無い。閲覧側のテーマ指定に追従しない"))
    root_tokens = any(":root" in selector and re.search(r"--[\w-]+\s*:", body)
                      for selector, body, _ in _css_rules(css_all))
    if not root_tokens:
        findings.append(_finding("error", "no-color-tokens", path, 1,
                                 ":root に色トークン（--name: 値）が無い。ライトとダークで同じ名前を"
                                 "定義し直す前提が崩れる"))
    body_background = any(
        any(part.strip().split(":")[0].split("[")[0] in ("body", "html body") for part in selector.split(","))
        and re.search(r"(?<![\w-])background(?:-color)?\s*:", body)
        for selector, body, _ in _css_rules(css_all)
    )
    if not body_background:
        findings.append(_finding("error", "no-body-background", path, 1,
                                 "body に background が無い。埋め込み先の背景色が透けて文字が沈む"))

    for line in page.images_without_alt:
        findings.append(_finding("error", "img-no-alt", path, line, "<img> に alt が無い"))
    for line, action in page.external_forms:
        findings.append(_finding("error", "external-form", path, line,
                                 f"<form> が外部へ送信する: {action[:120]}。共有ページで入力を集めない"))
    for line in page.meta_refresh:
        findings.append(_finding("error", "meta-refresh", path, line,
                                 '<meta http-equiv="refresh"> で別の場所へ飛ばさない'))

    if page.figures == 0:
        findings.append(_finding("warn", "no-figure", path, 1,
                                 "<svg> も <img> も無い。日程・手順・構造は図のほうが速く読める"))
    h1 = [line for line, level in page.headings if level == 1]
    if len(h1) != 1:
        findings.append(_finding("warn", "h1-count", path, h1[1] if len(h1) > 1 else 1,
                                 f"h1 が {len(h1)} 個。ページの主題は 1 つにする"))
    previous = 0
    for line, level in page.headings:
        if previous and level > previous + 1:
            findings.append(_finding("warn", "heading-skip", path, line,
                                     f"h{previous} の次が h{level}。見出しの段を飛ばさない"))
        previous = level

    for lineno, line in enumerate(text.splitlines(), start=1):
        for m in RE_PLACEHOLDER.finditer(line):
            findings.append(_finding("error", "placeholder", path, lineno,
                                     f"埋め忘れのプレースホルダー: {m.group(0)}"))

    findings.extend(_secret_findings(text, path))
    findings.sort(key=lambda f: (f["line"], f["severity"] != "error", f["rule"]))
    return findings


def _secret_findings(text: str, path: str) -> list[dict]:
    """名前つきの秘密情報パターンだけを当てる。

    エントロピー判定は使わない。共有ページは画像やフォントを base64 で埋め込むのが正しい
    形なので、高エントロピーの長い文字列はむしろ正常で、当てると誤検出しか出ない。
    """
    from .workbench.secrets import scan_line

    out = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if len(line) > 20000:  # data: URI の 1 行。名前つきパターンの形をしていない
            line = re.sub(r"data:[\w/+.-]+;base64,[A-Za-z0-9+/=]+", "data:", line)
        for hit in scan_line(line, path, lineno, skip_entropy=True):
            out.append(_finding("error", "secret", path, lineno,
                                f"秘密情報の形をした文字列（{hit['kind']}）: {hit['masked_excerpt']}"))
    return out


def _collect(paths: list[str]) -> tuple[list[pathlib.Path], list[str]]:
    files: list[pathlib.Path] = []
    missing: list[str] = []
    for raw in paths:
        p = pathlib.Path(raw)
        if p.is_dir():
            files.extend(sorted(q for q in p.rglob("*") if q.suffix.lower() in (".html", ".htm") and q.is_file()))
        elif p.is_file():
            files.append(p)
        else:
            missing.append(raw)
    return files, missing


def run(paths: list[str], strict: bool = False) -> dict:
    """検査して、報告の dict を返す。`status` が unchecked なら exit 2。"""
    if not paths:
        return {"status": "unchecked", "reason": "検査するパスが指定されていない", "files": [], "findings": []}
    files, missing = _collect(paths)
    if missing:
        return {"status": "unchecked", "reason": f"見つからないパス: {', '.join(missing)}",
                "files": [], "findings": []}
    if not files:
        return {"status": "unchecked", "reason": "HTML が 1 枚も見つからない", "files": [], "findings": []}
    findings: list[dict] = []
    for f in files:
        try:
            raw = f.read_bytes()
        except OSError as exc:
            return {"status": "unchecked", "reason": f"{f}: 読めない（{exc}）", "files": [], "findings": []}
        findings.extend(check_text(raw.decode("utf-8", errors="replace"), str(f), size=len(raw)))
    errors = sum(1 for f in findings if f["severity"] == "error")
    warnings = len(findings) - errors
    return {
        "status": "checked",
        "reason": "",
        "strict": strict,
        "files": [str(f) for f in files],
        "errors": errors,
        "warnings": warnings,
        "findings": findings,
    }


def exit_code(report: dict) -> int:
    if report["status"] != "checked":
        return 2
    if report["errors"] or (report.get("strict") and report["warnings"]):
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rig-wb share-check",
        description="共有する HTML を公開前に検査する（error があれば exit 1、未検査は exit 2）",
    )
    parser.add_argument("artifacts", nargs="*", help="検査する HTML（ファイルまたはディレクトリ）")
    parser.add_argument("--report", metavar="PATH",
                        help="どの状態でも JSON 報告をこのパスに書く（orchestrate は stdout を捨てるため）")
    parser.add_argument("--strict", action="store_true", help="warn も exit 1 に数える")
    parser.add_argument("--json", action="store_true", help="機械可読な JSON で出力する")
    args = parser.parse_args(argv)

    report = run(args.artifacts, strict=args.strict)
    if args.report:
        out = pathlib.Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.json:
        CONSOLE.out(json.dumps(report, ensure_ascii=False, indent=2))
    elif report["status"] != "checked":
        CONSOLE.err(f"UNCHECKED: {report['reason']}")
    else:
        for f in report["findings"]:
            tag = "NG  " if f["severity"] == "error" else "WARN"
            CONSOLE.out(f"{tag} {f['path']}:{f['line']} [{f['rule']}] {f['message']}")
        verdict = "NG" if exit_code(report) else "OK"
        CONSOLE.out(f"{verdict}: {len(report['files'])} file(s), {report['errors']} error(s), "
              f"{report['warnings']} warning(s)")
    return exit_code(report)


if __name__ == "__main__":
    sys.exit(main())
