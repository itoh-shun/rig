#!/usr/bin/env python3
"""layout-gate pack の参照実装を、実際に走る script から作り直します。

rig の pack は実行できるコードを resource として配れないので、走る実体は
`scripts/layout/` に置き、pack には Markdown の参照実装を同梱しています。二重に
持つと必ずずれるため、片方から機械的に作り、tests/test_layout_gate_pack.py で
一致を検査します。script を直したら、これを実行して pack sync してください。

    python3 scripts/layout/sync-pack-reference.py
    rig-wb pack sync packs/domain/layout-gate
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
RESOURCES = ROOT / "packs" / "domain" / "layout-gate" / "resources"

SPECS = [
    ("layout-fit.js", "js", "layout-fit.reference.md",
     "pptxgenjs のような座標で描く生成器の中で使う、レイアウトの計算的センサーです。"),
    ("check-html-layout.mjs", "js", "check-html-layout.reference.md",
     "HTML で組んだ資料を Chromium で開いて測る、レイアウトのゲートです。"),
]

TEMPLATE = """# reference: {name}

{lead}

rig の pack は、実行できるコードを resource として配れません（`.sh` と `.py` は拡張子で、
`.js` と `.mjs` は MIME で拒否されます）。実行されるものは、導入する側の repository に
置く決まりです。この文書は、その置くべき中身をそのまま載せた参照実装です。

`scripts/layout/{name}` として保存し、`./scripts/layout-gate.sh` から呼んでください。
recipe の `measure` step が実行するのは、その script です。

```{lang}
{code}```
"""


def main() -> None:
    for name, lang, destination, lead in SPECS:
        source = ROOT / "scripts" / "layout" / name
        code = source.read_text(encoding="utf-8")
        if not code.endswith("\n"):
            code += "\n"
            source.write_text(code, encoding="utf-8")
        if "```" in code:
            raise SystemExit(f"{name} contains a fence; the reference cannot embed it")
        out = RESOURCES / destination
        out.write_text(
            TEMPLATE.format(name=name, lang=lang, code=code, lead=lead), encoding="utf-8")
        print(f"wrote {out.relative_to(ROOT)}")
    print("next: rig-wb pack sync packs/domain/layout-gate")


if __name__ == "__main__":
    main()
