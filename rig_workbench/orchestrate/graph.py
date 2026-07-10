"""orchestrate graph: brick graph + cmd_graph (split from scripts/orchestrate.py)."""

import sys
import re
import json
import pathlib

from . import config
from .recipes import parse_frontmatter

# ── ブリック・グラフ（型付き関係の導出＝オントロジー層・#graph）───────────────
# 「概念間の関係を型として明示する」オントロジーを、rig は**手で書かずコードで導出**する。
# source of truth は各ブリックの frontmatter / steps: 定義そのもの＝グラフは腐らない。
# オントロジー5要素との対応: クラス=kind / インスタンス=node / プロパティ=path /
# 関係=typed edge（11種）/ 制約=validate.py check_graph（CI）。

_WIKI_LINK_RE = re.compile(r"\[\[([a-z0-9-]+)(?:\|[^\]]*)?\]\]")


def _graph_body_links(path: pathlib.Path) -> list[str]:
    """本文＋frontmatter links: から [[slug]] 参照を重複なしで抽出する。"""
    text = path.read_text(encoding="utf-8")
    seen: list[str] = []
    for slug in _WIKI_LINK_RE.findall(text):
        if slug not in seen:
            seen.append(slug)
    return seen


def build_brick_graph() -> dict:
    """shipped ブリック群の既存メタデータから型付きグラフを導出する（純関数・決定論）。

    nodes: {id, kind, path} / edges: {from, rel, to, resolved}
    rel 語彙（固定11種）: extends / injects / links-to / uses-instruction / uses-pattern /
    gated-by / applies-policy / emits-contract / uses-persona / references / mirrors
    """
    skills = config.RIG_HOME / "skills" / "rig"
    facets = skills / "facets"
    dirs = {
        "persona": facets / "personas",
        "instruction": facets / "instructions",
        "pattern": skills / "patterns",
        "policy": facets / "policies",
        "contract": facets / "output-contracts",
        "wiki": facets / "knowledge" / "wiki",
        "recipe": skills / "recipes",
        "agent": config.RIG_HOME / "agents",
        "command": config.RIG_HOME / "commands",
    }
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def add_edge(src: str, rel: str, dst: str) -> None:
        e = {"from": src, "rel": rel, "to": dst}
        if e not in edges:
            edges.append(e)

    for kind, d in dirs.items():
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*.md")):
            stem = str(f.relative_to(d).with_suffix(""))
            if stem.startswith("_") or "/" in stem and stem.split("/")[-1].startswith("_"):
                continue
            nodes[f"{kind}:{stem}"] = {"id": f"{kind}:{stem}", "kind": kind,
                                       "path": str(f.relative_to(config.RIG_HOME))}

    # basename → persona id（recipe の personas: は basename 指定を許容）
    persona_base: dict[str, list[str]] = {}
    for nid in nodes:
        if nid.startswith("persona:"):
            persona_base.setdefault(nid.split("/")[-1].split(":")[-1], []).append(nid)

    def persona_id(name: str) -> str:
        if f"persona:{name}" in nodes:
            return f"persona:{name}"
        hits = persona_base.get(name, [])
        return hits[0] if len(hits) == 1 else f"persona:{name}"

    # persona → wiki（injects）
    for nid, n in list(nodes.items()):
        if n["kind"] != "persona":
            continue
        fm = parse_frontmatter(config.RIG_HOME / n["path"])
        for entry in (fm.get("inject") or []):
            m = _WIKI_LINK_RE.fullmatch(str(entry))
            if m:
                add_edge(nid, "injects", f"wiki:{m.group(1)}")

    # wiki → wiki（links-to・frontmatter links: ＋ 本文 [[slug]]）
    for nid, n in list(nodes.items()):
        if n["kind"] != "wiki":
            continue
        for slug in _graph_body_links(config.RIG_HOME / n["path"]):
            if f"wiki:{slug}" != nid:
                add_edge(nid, "links-to", f"wiki:{slug}")

    # recipe → 各ブリック（steps: の生定義＝著者が書いた関係）
    for nid, n in list(nodes.items()):
        if n["kind"] != "recipe":
            continue
        fm = parse_frontmatter(config.RIG_HOME / n["path"])
        if fm.get("extends"):
            add_edge(nid, "extends", f"recipe:{fm['extends']}")
        for s in (fm.get("steps") or []):
            if not isinstance(s, dict):
                continue
            if s.get("instruction"):
                add_edge(nid, "uses-instruction", f"instruction:{s['instruction']}")
            if s.get("pattern"):
                add_edge(nid, "uses-pattern", f"pattern:{s['pattern']}")
            if s.get("gate") not in (None, "—", "-"):
                add_edge(nid, "gated-by", f"pattern:{s['gate']}")
            for p_ in (s.get("personas") or []):
                add_edge(nid, "uses-persona", persona_id(str(p_)))
            for pol in (s.get("policies") or []):
                add_edge(nid, "applies-policy", f"policy:{pol}")
            if s.get("output_contract"):
                add_edge(nid, "emits-contract", f"contract:{s['output_contract']}")

    # agent → persona（mirrors・native-first の対）
    for nid, n in list(nodes.items()):
        if n["kind"] != "agent":
            continue
        stem = nid.split(":", 1)[1]
        cand = [stem]
        if stem.endswith("-reviewer"):
            cand.append(stem[: -len("-reviewer")])
        dst = next((persona_id(c) for c in cand
                    if persona_id(c) in nodes), persona_id(cand[-1]))
        add_edge(nid, "mirrors", dst)

    # command → instruction（本文の `facets/instructions/<name>` 明示参照のみ＝散文推測しない）
    ref_re = re.compile(r"facets/instructions/([a-z0-9-]+)")
    for nid, n in list(nodes.items()):
        if n["kind"] != "command":
            continue
        text = (config.RIG_HOME / n["path"]).read_text(encoding="utf-8")
        for name in sorted(set(ref_re.findall(text))):
            add_edge(nid, "references", f"instruction:{name}")

    for e in edges:
        e["resolved"] = e["to"] in nodes
    return {
        "nodes": sorted(nodes.values(), key=lambda x: (x["kind"], x["id"])),
        "edges": sorted(edges, key=lambda x: (x["from"], x["rel"], x["to"])),
    }


def cmd_graph(args):
    """graph [--json] [--focus <name>]: 型付きブリック・グラフを導出して表示する。"""
    g = build_brick_graph()
    if "--json" in args:
        print(json.dumps(g, ensure_ascii=False, indent=2))
        return
    if "--focus" in args:
        name = args[args.index("--focus") + 1]
        ids = {n["id"] for n in g["nodes"] if n["id"] == name or n["id"].split(":", 1)[-1] == name
               or n["id"].split(":", 1)[-1].split("/")[-1] == name}
        if not ids:
            print(f"[graph] focus に一致するノードがありません: {name}")
            sys.exit(1)
        for nid in sorted(ids):
            print(f"◈ {nid}")
            for e in g["edges"]:
                if e["from"] == nid:
                    print(f"  → {e['rel']} → {e['to']}" + ("" if e["resolved"] else "  (未解決)"))
            for e in g["edges"]:
                if e["to"] == nid:
                    print(f"  ← {e['rel']} ← {e['from']}")
        return
    kinds: dict[str, int] = {}
    for n in g["nodes"]:
        kinds[n["kind"]] = kinds.get(n["kind"], 0) + 1
    rels: dict[str, int] = {}
    unresolved = [e for e in g["edges"] if not e["resolved"]]
    for e in g["edges"]:
        rels[e["rel"]] = rels.get(e["rel"], 0) + 1
    print("ブリック・グラフ（型付き・frontmatter/steps から導出＝手書きしない）")
    print(f"  nodes: {len(g['nodes'])}  (" + " / ".join(f"{k} {v}" for k, v in sorted(kinds.items())) + ")")
    print(f"  edges: {len(g['edges'])}  (" + " / ".join(f"{k} {v}" for k, v in sorted(rels.items())) + ")")
    print(f"  未解決エッジ: {len(unresolved)}")
    for e in unresolved:
        print(f"    ✗ {e['from']} → {e['rel']} → {e['to']}")
    print("  1ホップ探索: graph --focus <name> ／ 機械可読: graph --json")

