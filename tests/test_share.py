"""/rig:share and the sensor that stands between a page and its publication.

The recipe writes one HTML file and the parent session publishes it as an Artifact. What
has to hold here is the part a machine can decide: that the shipped template, once filled,
passes the sensor it is checked by; that each error rule fires on the shape it names and
stays quiet on the honest one; and that "nothing was checked" never reads as a pass.
"""

import json
import pathlib
import re

from rig_workbench import share_check

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATE = REPO_ROOT / "skills" / "engine" / "manifests" / "share-page.template.html"


def _filled_template() -> str:
    return re.sub(r"\{\{\s*\w+\s*\}\}", "サンプル", TEMPLATE.read_text(encoding="utf-8"))


def _rules(text: str, severity: str | None = None) -> set[str]:
    return {f["rule"] for f in share_check.check_text(text, "page.html")
            if severity is None or f["severity"] == severity}


def test_the_filled_template_is_clean():
    findings = share_check.check_text(_filled_template(), "page.html")
    assert findings == [], findings


def test_the_unfilled_template_does_not_pass():
    """The template ships with its placeholders, so copying it without filling it in fails."""
    assert "placeholder" in _rules(TEMPLATE.read_text(encoding="utf-8"), "error")


def test_the_gate_resolves_from_core_without_installing_anything(monkeypatch, tmp_path):
    from rig_workbench.packs.resolver import resolve_asset

    monkeypatch.setenv("RIG_HOME", str(REPO_ROOT))
    monkeypatch.setenv("RIG_USER_HOME", str(tmp_path / "user-home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("RIG_ORG_HOME", raising=False)
    for kind, name in (
        ("recipe", "share"),
        ("persona", "share-composer"),
        ("persona", "share-page-reviewer"),
        ("policy", "share-page-rules"),
        ("output-contract", "share-page-verdict"),
        ("instruction", "share-compose"),
        ("instruction", "share-review"),
    ):
        resolved = resolve_asset(kind, name, project=tmp_path)
        assert resolved is not None, f"{kind}:{name} does not resolve"
        assert resolved.tier == "core", f"{kind}:{name} resolved at {resolved.tier}"
    assert (REPO_ROOT / "commands" / "share.md").is_file()


def test_the_recipe_checks_with_the_shipped_sensor_and_nothing_else():
    from rig_workbench.packs.manifest import parse_frontmatter_subset

    recipe = parse_frontmatter_subset(REPO_ROOT / "skills/engine/recipes/share.md")
    checks = [check for step in recipe["steps"] for check in step.get("checks", [])]
    assert checks == ["rig-wb share-check --report .rig/share-check-report.json share"]


def _with(head: str = "", body: str = "") -> str:
    """The filled template with something added, so each probe changes one thing."""
    page = _filled_template()
    return page.replace("</style>", "</style>" + head, 1).replace("</main>", body + "</main>", 1)


def test_outside_resources_are_refused_except_the_hosts_artifacts_allow():
    assert "external-resource" in _rules(_with(body='<script src="https://evil.example/x.js"></script>'))
    assert "external-resource" in _rules(_with(head='<link rel="stylesheet" href="https://example.com/a.css">'))
    assert "external-resource" in _rules(_with(body='<img alt="x" src="https://example.com/a.png">'))
    assert "external-resource" in _rules(_with(
        body='<svg viewBox="0 0 1 1"><image href="https://example.com/a.png"/></svg>'))
    allowed = _with(
        head='<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter">',
        body='<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>'
             '<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js"></script>',
    )
    assert "external-resource" not in _rules(allowed)
    # A link the reader follows is not a resource the page loads.
    assert "external-resource" not in _rules(_with(body='<a href="https://example.com/">出典</a>'))


def test_a_relative_resource_is_refused_because_only_one_file_is_published():
    assert "local-reference" in _rules(_with(body='<img alt="図" src="images/flow.png">'))
    assert "local-reference" in _rules(_with(head="<style>.x{background:url(bg.png)}</style>"))
    assert "local-reference" not in _rules(_with(body='<img alt="図" src="data:image/png;base64,iVBORw0KGgo=">'))


def test_the_page_contract_basics_are_errors():
    bare = "<h1>x</h1><svg></svg>"
    assert {"no-title", "no-dark-mode", "no-color-tokens",
            "no-body-background"} <= _rules(bare, "error")


def test_a_page_that_brings_its_own_skeleton_is_refused():
    """Artifacts wrap the page in their own doctype, head and viewport at publish time."""
    whole = ("<!doctype html><html lang=ja><head><meta charset=utf-8></head><body>"
             + _filled_template() + "</body></html>")
    findings = [f for f in share_check.check_text(whole, "page.html") if f["rule"] == "own-skeleton"]
    assert [f["message"].split(" ")[0] for f in findings] == ["<!doctype>", "<html>", "<head>", "<body>"]


def test_a_title_past_the_first_8_kb_is_not_found():
    page = "<style>/*" + "x" * 9000 + "*/</style>" + _filled_template()
    assert "title-late" in _rules(page, "error")


def test_content_that_must_not_go_out_is_an_error():
    assert "placeholder" in _rules(_with(body="<p>{{OWNER}}</p>"))
    assert "placeholder" in _rules(_with(body="<p>[要記入]</p>"))
    assert "secret" in _rules(_with(body="<p>AKIAABCDEFGHIJKLMNOP</p>"))
    assert "img-no-alt" in _rules(_with(body='<img src="data:image/png;base64,iVBORw0KGgo=">'))
    assert "external-form" in _rules(_with(body='<form action="https://collect.example/"></form>'))
    assert "meta-refresh" in _rules(_with(head='<meta http-equiv="refresh" content="0;url=https://x.example">'))


def test_an_embedded_image_is_not_mistaken_for_a_secret():
    """Embedding is the right shape for a shared page, so long base64 must stay quiet."""
    blob = "A" * 30000 + "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo" * 400
    page = _with(body=f'<img alt="図" src="data:image/png;base64,{blob}">')
    assert "secret" not in _rules(page)


def test_warnings_do_not_fail_the_check_unless_strict(tmp_path):
    page = tmp_path / "share" / "wide.html"
    page.parent.mkdir()
    page.write_text(_with(head="<style>.wide{width:960px}</style>"), encoding="utf-8")
    report = share_check.run([str(page.parent)])
    assert report["status"] == "checked"
    assert report["errors"] == 0 and report["warnings"] >= 1
    assert share_check.exit_code(report) == 0
    assert share_check.exit_code(share_check.run([str(page.parent)], strict=True)) == 1


def test_nothing_to_check_is_unchecked_not_a_pass(tmp_path):
    (tmp_path / "share").mkdir()
    for report in (share_check.run([]),
                   share_check.run([str(tmp_path / "share")]),
                   share_check.run([str(tmp_path / "absent")])):
        assert report["status"] == "unchecked"
        assert share_check.exit_code(report) == 2


def test_the_report_is_written_in_every_state(tmp_path):
    out = tmp_path / ".rig" / "share-check-report.json"
    assert share_check.main(["--report", str(out), str(tmp_path / "absent")]) == 2
    assert json.loads(out.read_text(encoding="utf-8"))["status"] == "unchecked"


def test_a_commented_selector_still_counts():
    page = ("<title>t</title><style>/* tokens */ :root{--bg:#fff}"
            "@media (prefers-color-scheme: dark){:root:not([data-theme=light]){--bg:#000}}"
            ":root[data-theme=dark]{--bg:#000} /* ground */ body{background:var(--bg)}</style>"
            "<h1>x</h1><svg></svg>")
    assert share_check.check_text(page, "page.html") == []


def test_a_figure_title_is_not_the_page_name():
    """The template's own diagram carries an SVG <title>; it must not be read as the page's."""
    page = _filled_template().replace("<title>サンプル</title>", "<title>週報</title>", 1)
    svg = '<svg viewBox="0 0 1 1"><title>' + "長い図の説明" * 20 + "</title></svg>"
    assert "long-title" not in _rules(page.replace("</main>", svg + "</main>", 1))
    assert "no-title" in _rules("<style></style><svg><title>図</title></svg>")


def test_the_allowlist_compares_hosts_not_prefixes():
    for url in ("https://cdn.tailwindcss.com.evil.example/x.js",
                "https://cdn.tailwindcss.com@evil.example/x.js",
                "https://unpkg.com.evil.example/x.js",
                "https://cdn.jsdelivr.net/npm/../gh/user/repo/x.js",
                "https://cdn.jsdelivr.net/gh/user/repo/x.js"):
        assert "external-resource" in _rules(_with(body=f'<script src="{url}"></script>')), url
    for url in ("https://cdn.tailwindcss.com", "https://CDNJS.cloudflare.com/ajax/libs/x/1/x.js"):
        assert "external-resource" not in _rules(_with(body=f'<script src="{url}"></script>')), url
    assert "external-resource" not in _rules(
        _with(head='<link rel="modulepreload" href="https://unpkg.com/lit@3/index.js">'))
    assert "external-resource" in _rules(_with(head='<link rel="prefetch" href="https://evil.example/x">'))


def test_css_image_set_and_comments_are_read_the_way_the_browser_reads_them():
    assert "external-resource" in _rules(
        _with(head='<style>.x{background-image:image-set("https://evil.example/a.png" 1x)}</style>'))
    assert "external-resource" in _rules(
        _with(body='<div style="background-image:image-set(\'https://evil.example/a.png\' 1x)"></div>'))
    fake = ("<title>t</title><style>:root{--bg:#fff} body{background:var(--bg)}"
            "/* prefers-color-scheme: dark data-theme */</style><h1>x</h1><svg></svg>")
    assert "no-dark-mode" in _rules(fake, "error")


def test_srcset_data_uris_and_self_closing_tags_do_not_confuse_the_parser():
    data = "data:image/png;base64,iVBORw0KGgo="
    page = _with(body=f'<img alt="a" src="{data}" srcset="{data} 1x, {data} 2x">')
    assert "local-reference" not in _rules(page)
    assert "local-reference" in _rules(_with(body=f'<img alt="a" src="{data}" srcset="a.png 1x, {data} 2x">'))
    early = "<svg/><title/>" + _filled_template()
    assert "no-title" not in _rules(early)


def test_title_late_measures_the_real_title_not_a_decoy():
    decoy = "<svg><title>c</title></svg><!-- <title> -->"
    page = decoy + "<style>/*" + "x" * 9000 + "*/</style>" + _filled_template()
    assert "title-late" in _rules(page, "error")
