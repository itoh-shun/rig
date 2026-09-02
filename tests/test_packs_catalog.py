"""The check that catches a pack listed in SKILL.md §2 with no detail row in PACKS.md (#573).

§2 states the rule itself — a new pack gets a one-line row in §2 *and* a detail row in
`PACKS.md` — and `PACKS.md` opens by repeating it. Nothing checked the pair:
`check_catalog_drift` compares §2 against the files on disk, and the pair of documents was
left to people. Ten packs went into §2 with no detail row while the rule stood.

Every branch below is exercised with a pair of documents the check must object to, not only
with a pair it must accept, and the last test measures the shipped files as they are.
"""

import pytest

from rig_workbench.validation.catalog import (
    CATALOG_SECTION,
    PACK_TABLE_HEADER,
    PACKS_TABLE_HEADER,
    pack_table_ids,
    packs_catalog_drift,
)
from rig_workbench.validation.config import SKILLS


def skill_row(pack, entry="`/rig:x`"):
    return f"> | **{pack}**（{entry}） | 一行要旨 | `facets/instructions/{pack}` |"


def packs_row(pack, entry="`/rig:x`"):
    return f"| **{pack}**（{entry}） | instruction `facets/instructions/{pack}`（詳細） |"


def skill(*packs, before="", after="", table_header=PACK_TABLE_HEADER):
    """A SKILL.md whose §2 holds a pack table listing `packs`, plus text on either side."""
    rows = "\n".join(skill_row(pack) for pack in packs)
    return (f"# rig\n\n## 1. Overview\n\n{before}\n\n{CATALOG_SECTION[0]}\n\n"
            f"> **pack 追加分** — 下表は要旨のみ。\n>\n{table_header}\n> |---|---|---|\n{rows}\n\n"
            f"{CATALOG_SECTION[1]}\n\n{after}\n")


def packs(*packs, table_header=PACKS_TABLE_HEADER):
    """A PACKS.md whose pack table lists `packs`."""
    rows = "\n".join(packs_row(pack) for pack in packs)
    return (f"# rig — pack 詳細目録\n\n## Extension Catalog（opt-in）\n\n| id | 説明 |\n|---|---|\n"
            f"| `sales` | 営業 |\n\n## pack 一覧（engine 不変で上乗せ）\n\n{table_header}\n|---|---|\n"
            f"{rows}\n\n## 後段\n\nprose\n")


# ── the omission the check exists to catch ───────────────────────────────────
def test_a_section_2_pack_with_no_detail_row_is_reported():
    missing, stale, blind = packs_catalog_drift(skill("talk", "evidence"), packs("talk"))
    assert missing == ["evidence"] and stale == [] and blind == []


def test_a_detail_row_for_a_pack_section_2_does_not_list_is_reported():
    """The other direction: a pack removed from §2 that kept its long description. A detail
    row nobody can reach from the catalogue is not documentation, it is rot."""
    missing, stale, blind = packs_catalog_drift(skill("talk"), packs("talk", "gone"))
    assert missing == [] and stale == ["gone"] and blind == []


def test_matching_tables_report_nothing():
    missing, stale, blind = packs_catalog_drift(skill("talk", "goal"), packs("goal", "talk"))
    assert missing == [] and stale == [] and blind == []


# ── what a row is ────────────────────────────────────────────────────────────
def test_an_id_with_spaces_is_one_id_and_the_entry_point_is_not_part_of_it():
    """`intent / assurance target` is one pack. The bold is the id; the parenthesised entry
    point that follows it in the same cell is not."""
    md = skill("intent / assurance target")
    assert pack_table_ids(md, PACK_TABLE_HEADER) == ["intent / assurance target"]
    row = "> | **assurance**（`rig-wb wb {receipt,import,contract}`・utility） | x | `y` |"
    assert pack_table_ids(f"{PACK_TABLE_HEADER}\n> |---|---|---|\n{row}",
                          PACK_TABLE_HEADER) == ["assurance"]


def test_the_table_ends_at_the_first_line_that_is_not_a_row():
    """A bold id in prose under the table, or in the next table, is not a row of this one."""
    md = skill("talk", after="| **hooks** | not this table |\n\n> | **later** | nor this |")
    assert pack_table_ids(md, PACK_TABLE_HEADER) == ["talk"]


def test_a_pack_table_outside_section_2_is_not_the_catalogue():
    """§2 is the brick catalogue; a table of the same shape elsewhere in SKILL.md is not
    where the rule says a pack is listed."""
    stray = f"{PACK_TABLE_HEADER}\n> |---|---|---|\n{skill_row('stray')}"
    missing, stale, blind = packs_catalog_drift(skill("talk", after=stray), packs("talk"))
    # Two copies of the header now exist in the document, one inside §2 and one outside.
    # Inside §2 there is still exactly one, and that is the one read.
    assert missing == [] and stale == [] and blind == []
    missing, stale, blind = packs_catalog_drift(skill("talk", after=stray), packs("stray"))
    assert missing == ["talk"] and stale == ["stray"] and blind == []


# ── a check that found nothing to check has not passed ───────────────────────
@pytest.mark.parametrize("skill_md,packs_md,says", [
    ("# rig\n\nno headings here\n", packs("talk"), "exactly one section bounded by"),
    (skill("talk") + f"\n{CATALOG_SECTION[0]}\n\n{CATALOG_SECTION[1]}\n", packs("talk"),
     "exactly one section bounded by"),
    (skill("talk", table_header="> | pack | summary | bricks |"), packs("talk"),
     "does not hold exactly one line"),
    (skill("talk", after=""), packs("talk", table_header="| pack | details |"),
     "does not hold exactly one line"),
    (skill("talk"), packs("talk") + f"\n{PACKS_TABLE_HEADER}\n|---|---|\n",
     "does not hold exactly one line"),
    (skill(), packs("talk"), "has no `| **<id>** |` rows"),
    (skill("talk"), packs(), "has no `| **<id>** |` rows"),
])
def test_it_goes_blind_rather_than_reporting_an_empty_drift(skill_md, packs_md, says):
    missing, stale, blind = packs_catalog_drift(skill_md, packs_md)
    assert missing == [] and stale == []
    assert blind and any(says in why for why in blind), blind


# ── the shipped files, as they are ───────────────────────────────────────────
def test_the_shipped_documents_are_readable_by_this_check():
    """Whatever the drift is today, the check must be able to locate both tables in the
    files this repository ships; otherwise `--validate` FAILs on every run."""
    skill_md = (SKILLS / "SKILL.md").read_text(encoding="utf-8")
    packs_md = (SKILLS / "PACKS.md").read_text(encoding="utf-8")
    _, _, blind = packs_catalog_drift(skill_md, packs_md)
    assert blind == []
    assert "talk" in pack_table_ids(packs_md, PACKS_TABLE_HEADER)
