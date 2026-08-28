from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import re
from typing import Any

from rig_workbench.eval.safety import unsafe_key_reason, unsafe_text_reason
from rig_workbench.workbench.destructive import scan_line as destructive_scan_line
from rig_workbench.workbench.injection import scan_line as injection_scan_line

from .model import ASSET_DIRS, PACK_TYPES, TYPE_ASSETS, CapabilityRefused, PackError

PACK_BASE_FIELDS = {
    "pack_schema_version", "id", "type", "version", "kind", "engine", "dependencies",
    "assets", "hashes", "provenance",
}
#: Bumped from 1 when `type` became required. A manifest without a type is refused rather
#: than defaulted: guessing a type is guessing a permission, and the safe-looking guess
#: (`knowledge`) breaks working packs while the permissive one (`skill`) hands out reach
#: nobody granted. There is no third answer that is not one of those two in disguise.
PACK_SCHEMA_VERSION = 2
PACK_CATALOG_FIELDS = {
    "display_name", "description", "capabilities", "entrypoints", "references", "resources",
}
PACK_FIELDS = PACK_BASE_FIELDS | PACK_CATALOG_FIELDS

#: The knowledge declaration (#533): what a pack's contents are *about*, as opposed to what
#: they are. Optional, and permitted on any type — this is description, not permission, and
#: `type` vs `kind` already records what folding those two together costs. A `reviewer` pack
#: whose personas encode a product's domain has the same thing to declare as a `knowledge`
#: one, and refusing it there would only teach people to mislabel their type to get the field.
PACK_KNOWLEDGE_FIELDS = {"knowledge"}
#: The four manifest shapes. The field set is exact rather than a minimum, which is what makes
#: a typo'd key a refusal instead of a silently ignored line; an optional block therefore costs
#: a shape rather than a default.
PACK_SHAPES = frozenset({
    frozenset(PACK_BASE_FIELDS),
    frozenset(PACK_BASE_FIELDS | PACK_KNOWLEDGE_FIELDS),
    frozenset(PACK_FIELDS),
    frozenset(PACK_FIELDS | PACK_KNOWLEDGE_FIELDS),
})
#: Every key the knowledge block carries, all of them required once the block is present. The
#: block is what is optional; a half-filled one is not. `reviewed_at` is the key that argues
#: for this: a knowledge declaration with no review date is exactly the one that goes stale
#: without anybody noticing, and it would be the first field dropped if dropping were allowed.
KNOWLEDGE_FIELDS = {"scope", "topics", "owner", "evidence", "reviewed_at"}
#: A scope is a bare dimension (`company`) or a dimension with a value (`product:northwind-one`).
#: Selection compares the dimension, so the split has to be in the syntax rather than left to
#: whoever reads the string.
KNOWLEDGE_SCOPE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}(?::[a-z0-9][a-z0-9-]{0,63})?$")
KNOWLEDGE_TOPIC = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
COMPAT_FIELDS = {"compatibility_schema_version", "pack_id", "pack_version", "engine", "platforms"}
PACK_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
RESERVED_PACK_IDS = frozenset({"rig-core"})
REFERENCE_ID = re.compile(r"^[a-z0-9][a-z0-9/_-]{0,127}$")
PROMPT_REFERENCE_KINDS = frozenset({
    "agent", "command", "instruction", "output-contract", "pattern", "persona",
    "policy", "recipe", "wiki",
})
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?$")
RANGE = re.compile(r"^(?:\*|(?:>=|<=|>|<|==)?[0-9]+\.[0-9]+\.[0-9]+(?:\s*,\s*(?:>=|<=|>|<|==)[0-9]+\.[0-9]+\.[0-9]+)*)$")
_BARE_RM_RECURSIVE_FORCE = re.compile(
    r"\brm\s+-(?:[A-Za-z]*[rR][A-Za-z]*[fF][A-Za-z]*|[A-Za-z]*[fF][A-Za-z]*[rR][A-Za-z]*)\b"
)


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"


def read_json_yaml(path: pathlib.Path) -> tuple[str, dict]:
    """Read the documented JSON-compatible YAML subset, without executing YAML tags."""
    try:
        raw = path.read_text(encoding="utf-8")
        value = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(
            PackError(f"non-finite number in {path.name}: {value}")))
    except PackError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PackError(f"{path.name} must be JSON-compatible canonical YAML: {exc}") from exc
    if not isinstance(value, dict):
        raise PackError(f"{path.name} must contain an object")
    return raw, value


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_relative(value: object) -> pathlib.PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise PackError("asset path must be a non-empty POSIX relative path")
    path = pathlib.PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise PackError(f"unsafe asset path: {value}")
    return path


def _reject_unsafe(value: object, where: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if unsafe_key_reason(key):
                raise PackError(f"secret-like manifest field: {where}.{key}")
            _reject_unsafe(item, f"{where}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_unsafe(item, f"{where}[{index}]")
    elif isinstance(value, str):
        if unsafe_text_reason(value):
            raise PackError(f"unsafe manifest text: {where}")
        injection = injection_scan_line(value, where, 1)
        destructive = destructive_scan_line(value, where, 1)
        if injection or destructive or _BARE_RM_RECURSIVE_FORCE.search(value):
            raise PackError(f"unsafe manifest instruction: {where}")


def _frontmatter_scalar(raw: str, path: pathlib.Path, lineno: int) -> Any:
    value = raw.strip()
    if not value:
        raise PackError(f"empty frontmatter scalar: {path.name}:{lineno}")
    if value in {"true", "false"}:
        return value == "true"
    if value[0] in "[{":
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            if value[0] == "{":
                raise PackError(
                    f"inline frontmatter objects must be JSON: {path.name}:{lineno}"
                ) from None
            if not value.endswith("]"):
                raise PackError(f"invalid inline frontmatter list: {path.name}:{lineno}")
            inner = value[1:-1].strip()
            if not inner:
                return []
            # The shipped YAML subset uses bare scalar lists. Nested flow values
            # must be JSON so commas and object boundaries remain unambiguous.
            if any(char in inner for char in "[{"):
                raise PackError(
                    f"nested inline frontmatter values must be JSON: {path.name}:{lineno}"
                )
            return [_frontmatter_scalar(item, path, lineno) for item in inner.split(",")]
        if not isinstance(parsed, (dict, list)):
            raise PackError(f"invalid inline frontmatter value: {path.name}:{lineno}")
        return parsed
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise PackError(f"invalid quoted frontmatter scalar: {path.name}:{lineno}") from exc
        if not isinstance(parsed, str):
            raise PackError(f"frontmatter scalar must be text: {path.name}:{lineno}")
        return parsed
    if value.startswith("'"):
        if not value.endswith("'") or len(value) < 2:
            raise PackError(f"invalid quoted frontmatter scalar: {path.name}:{lineno}")
        return value[1:-1].replace("''", "'")
    if value.endswith(("'", '"')) or value in {"|", ">", "|-", ">-", "|+", ">+"}:
        raise PackError(f"unsupported frontmatter scalar: {path.name}:{lineno}")
    return value


def _frontmatter_pair(raw: str, path: pathlib.Path, lineno: int) -> tuple[str, str]:
    match = re.fullmatch(r"([a-z_][a-z0-9_-]*):(?:\s*(.*))?", raw)
    if not match:
        raise PackError(f"unsupported frontmatter syntax: {path.name}:{lineno}")
    return match.group(1), (match.group(2) or "").strip()


def _parse_frontmatter_yaml(block: str, path: pathlib.Path) -> dict[str, Any]:
    tokens: list[tuple[int, str, int]] = []
    for lineno, raw in enumerate(block.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        tokens.append((indent, raw[indent:], lineno))
    if not tokens:
        return {}

    def parse_node(index: int, indent: int) -> tuple[Any, int]:
        if index >= len(tokens) or tokens[index][0] != indent:
            line = tokens[index][2] if index < len(tokens) else tokens[-1][2]
            raise PackError(f"malformed frontmatter indentation: {path.name}:{line}")
        if tokens[index][1] == "-" or tokens[index][1].startswith("- "):
            return parse_list(index, indent)
        return parse_mapping(index, indent)

    def parse_mapping(index: int, indent: int) -> tuple[dict[str, Any], int]:
        result: dict[str, Any] = {}
        while index < len(tokens):
            current_indent, content, lineno = tokens[index]
            if current_indent < indent:
                break
            if current_indent != indent or content == "-" or content.startswith("- "):
                raise PackError(f"malformed frontmatter indentation: {path.name}:{lineno}")
            key, raw_value = _frontmatter_pair(content, path, lineno)
            if key in result:
                raise PackError(f"duplicate frontmatter key: {path.name}:{lineno}")
            index += 1
            if raw_value:
                result[key] = _frontmatter_scalar(raw_value, path, lineno)
            elif index < len(tokens) and tokens[index][0] > indent:
                result[key], index = parse_node(index, tokens[index][0])
            else:
                result[key] = None
        return result, index

    def parse_list(index: int, indent: int) -> tuple[list[Any], int]:
        result: list[Any] = []
        while index < len(tokens):
            current_indent, content, lineno = tokens[index]
            if current_indent < indent:
                break
            if current_indent != indent:
                raise PackError(f"malformed frontmatter indentation: {path.name}:{lineno}")
            if content == "-":
                index += 1
                if index >= len(tokens) or tokens[index][0] <= indent:
                    raise PackError(f"empty frontmatter list item: {path.name}:{lineno}")
                item, index = parse_node(index, tokens[index][0])
                result.append(item)
                continue
            if not content.startswith("- "):
                break
            raw_item = content[2:].strip()
            try:
                key, raw_value = _frontmatter_pair(raw_item, path, lineno)
            except PackError:
                result.append(_frontmatter_scalar(raw_item, path, lineno))
                index += 1
                continue
            item: dict[str, Any] = {}
            index += 1
            item_indent = indent + 2
            if raw_value:
                item[key] = _frontmatter_scalar(raw_value, path, lineno)
            elif index < len(tokens) and tokens[index][0] > item_indent:
                item[key], index = parse_node(index, tokens[index][0])
            else:
                item[key] = None
            if index < len(tokens) and tokens[index][0] > indent:
                continuation_indent = tokens[index][0]
                continuation, index = parse_mapping(index, continuation_indent)
                duplicate = set(item) & set(continuation)
                if duplicate:
                    raise PackError(
                        f"duplicate frontmatter key: {path.name}:{tokens[index - 1][2]}"
                    )
                item.update(continuation)
            result.append(item)
        return result, index

    if tokens[0][0] != 0:
        raise PackError(f"frontmatter must start at column 1: {path.name}:{tokens[0][2]}")
    value, index = parse_node(0, 0)
    if index != len(tokens) or not isinstance(value, dict):
        lineno = tokens[index][2] if index < len(tokens) else tokens[-1][2]
        raise PackError(f"frontmatter must be a mapping: {path.name}:{lineno}")
    return value


def parse_frontmatter_subset(path: pathlib.Path) -> dict:
    """Parse the safe frontmatter subset needed for Rig reference validation.

    Supports scalar mappings, inline lists, and indented multiline lists. YAML
    tags, anchors, aliases, tabs, folded blocks, and malformed indentation fail.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PackError(f"cannot read frontmatter: {path.name}") from exc
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        raise PackError(f"unterminated frontmatter: {path.name}")
    block = text[4:end]
    if block.strip().startswith("{"):
        try:
            value = json.loads(block)
        except json.JSONDecodeError as exc:
            raise PackError(f"invalid JSON frontmatter: {path.name}") from exc
        if not isinstance(value, dict):
            raise PackError(f"frontmatter must be an object: {path.name}")
        _reject_unsafe(value, f"frontmatter.{path.name}")
        return value
    if "\t" in block or re.search(r"(?:^|\s)[!&*][A-Za-z]", block):
        raise PackError(f"unsafe frontmatter syntax: {path.name}")
    return _parse_frontmatter_yaml(block, path)


def _validate_knowledge(value: dict) -> None:
    """The `knowledge:` block: what this pack's contents are about (#533).

    Every list is sorted and unique for the same reason the asset lists are — two manifests
    describing the same pack must be the same bytes, or the digest stops meaning anything.

    `evidence` is deliberately not spelled `sources`, which the issue proposing this block
    used. `sources` already means *where a pack is installed from* across `pack source
    add/list/remove`, `verify-sources`, and the lock's `source` entries, and one word with two
    meanings in one CLI is a defect to introduce rather than inherit. The issue's own
    acceptance criterion writes "source/evidence", so the other half of its vocabulary is
    used here. These are human labels for the documents an answer rests on — "運用設計書" —
    not paths and not URLs; nothing resolves them, and this refuses to imply otherwise.
    """
    knowledge = value["knowledge"]
    if not isinstance(knowledge, dict) or set(knowledge) != KNOWLEDGE_FIELDS:
        raise PackError(
            f"pack knowledge must declare exactly {', '.join(sorted(KNOWLEDGE_FIELDS))}")
    for field, pattern in (("scope", KNOWLEDGE_SCOPE), ("topics", KNOWLEDGE_TOPIC)):
        items = knowledge[field]
        if (not isinstance(items, list) or not items or items != sorted(set(items))
                or any(not isinstance(item, str) or not pattern.fullmatch(item)
                       for item in items)):
            raise PackError(f"pack knowledge {field} must be a sorted unique slug list")
    if not isinstance(knowledge["owner"], str) or not knowledge["owner"].strip():
        raise PackError("pack knowledge owner is invalid")
    evidence = knowledge["evidence"]
    # Unique, but deliberately *not* sorted, unlike every other list in this file. Those are
    # slugs, where order carries nothing and leaving it free would only admit undetectable
    # noise. These are the titles of documents a person wrote, in whatever language they
    # wrote them, and two things follow. Codepoint order over prose is not an order any
    # author can predict — "運用設計書" sorts after "情報セキュリティ規程" for a reason no
    # human reading either would guess — so the rule could only be obeyed by trial and error.
    # And the order is information: a citation list puts the document the answer chiefly
    # rests on first, and sorting would throw that away to buy nothing.
    if (not isinstance(evidence, list) or not evidence
            or len(evidence) != len(set(evidence))
            or any(not isinstance(item, str) or not item.strip() for item in evidence)):
        raise PackError("pack knowledge evidence must be a non-empty list of distinct labels")
    reviewed_at = knowledge["reviewed_at"]
    if not isinstance(reviewed_at, str):
        raise PackError("pack knowledge reviewed_at is invalid")
    try:
        timestamp = dt.datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PackError("pack knowledge reviewed_at is invalid") from exc
    if timestamp.tzinfo is None:
        raise PackError("pack knowledge reviewed_at requires timezone")


def validate_manifest_shape(value: dict) -> None:
    _reject_unsafe(value, "pack")
    fields = set(value)
    if (frozenset(fields) not in PACK_SHAPES
            or value.get("pack_schema_version") != PACK_SCHEMA_VERSION):
        raise PackError("pack manifest schema fields/version are invalid")
    if "knowledge" in fields:
        _validate_knowledge(value)
    if not isinstance(value.get("id"), str) or not PACK_ID.fullmatch(value["id"]):
        raise PackError("pack id is invalid")
    if value["id"] in RESERVED_PACK_IDS:
        raise PackError(f"pack id is reserved: {value['id']}")
    if not isinstance(value.get("version"), str) or not VERSION.fullmatch(value["version"]):
        raise PackError("pack version must be semver")
    if value.get("kind") not in {"core", "official", "domain", "project"}:
        raise PackError("pack kind is invalid")
    if value.get("type") not in PACK_TYPES:
        raise PackError(f"pack type must be one of {', '.join(PACK_TYPES)}")
    catalog_manifest = PACK_CATALOG_FIELDS <= fields
    if catalog_manifest:
        for field in ("display_name", "description"):
            if not isinstance(value.get(field), str) or not value[field].strip():
                raise PackError(f"pack {field} is invalid")
        capabilities = value.get("capabilities")
        if (not isinstance(capabilities, list) or not capabilities
                or capabilities != sorted(set(capabilities))
                or any(not isinstance(item, str) or not PACK_ID.fullmatch(item)
                       for item in capabilities)):
            raise PackError("pack capabilities must be a sorted unique slug list")
    if not isinstance(value.get("engine"), str) or not RANGE.fullmatch(value["engine"]):
        raise PackError("pack engine range is invalid")
    deps = value.get("dependencies")
    if not isinstance(deps, list):
        raise PackError("pack dependencies must be a list")
    seen: set[str] = set()
    for dep in deps:
        if (not isinstance(dep, dict) or set(dep) != {"id", "range"}
                or not isinstance(dep["id"], str) or not PACK_ID.fullmatch(dep["id"])
                or not isinstance(dep["range"], str) or not RANGE.fullmatch(dep["range"])):
            raise PackError("pack dependency is invalid")
        if dep["id"] == value["id"] or dep["id"] in seen:
            raise PackError("pack dependency is duplicate or self-referential")
        seen.add(dep["id"])
    assets = value.get("assets")
    asset_fields = set(assets) if isinstance(assets, dict) else set()
    legacy_asset_fields = set(ASSET_DIRS) - {"resource"}
    if (not isinstance(assets, dict)
            or frozenset(asset_fields) not in {
                frozenset(legacy_asset_fields), frozenset(ASSET_DIRS)
            }
            or (catalog_manifest and asset_fields != set(ASSET_DIRS))):
        raise PackError("pack assets must declare every asset kind")
    permitted = TYPE_ASSETS[value["type"]]
    declared: set[str] = set()
    for kind, paths in assets.items():
        if not isinstance(paths, list) or paths != sorted(paths) or len(paths) != len(set(paths)):
            raise PackError(f"pack assets.{kind} must be a sorted unique list")
        if paths and kind not in permitted:
            # `validate_pack` refuses any file the manifest does not declare and checks every
            # declared file's hash, so this list is the pack's whole contents. That is what
            # makes a manifest check enough here: dropping `commands/` from the declaration to
            # slip past this raises asset-declaration drift instead.
            raise CapabilityRefused(
                f"a {value['type']} pack may not carry {kind} assets "
                f"(permitted: {', '.join(sorted(permitted))})")
        prefix = pathlib.PurePosixPath(ASSET_DIRS[kind])
        for item in paths:
            rel = safe_relative(item)
            if rel == prefix or prefix not in rel.parents:
                raise PackError(f"asset crosses ownership boundary: {item}")
            if item in declared:
                raise PackError(f"asset has duplicate ownership: {item}")
            declared.add(item)
    hashes = value.get("hashes")
    if (not isinstance(hashes, dict) or set(hashes) != declared
            or any(not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v)
                   for v in hashes.values())):
        raise PackError("pack hashes must exactly cover declared assets")
    provenance = value.get("provenance")
    if (not isinstance(provenance, dict) or set(provenance) != {"source", "created_at"}
            or not all(isinstance(provenance[k], str) and provenance[k] for k in provenance)):
        raise PackError("pack provenance is invalid")
    try:
        timestamp = dt.datetime.fromisoformat(provenance["created_at"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise PackError("pack provenance created_at is invalid") from exc
    if timestamp.tzinfo is None:
        raise PackError("pack provenance created_at requires timezone")
    if not catalog_manifest:
        return
    references = value.get("references")
    if not isinstance(references, list):
        raise PackError("pack references must be a list")
    reference_keys: list[tuple[str, str, str]] = []
    reference_targets: list[tuple[str, str]] = []
    for reference in references:
        if (not isinstance(reference, dict) or set(reference) != {"kind", "id", "pack"}
                or reference.get("kind") not in PROMPT_REFERENCE_KINDS
                or not isinstance(reference.get("id"), str)
                or not REFERENCE_ID.fullmatch(reference["id"])
                or not isinstance(reference.get("pack"), str)
                or not PACK_ID.fullmatch(reference["pack"])):
            raise PackError("pack typed reference is invalid")
        reference_keys.append((reference["pack"], reference["kind"], reference["id"]))
        reference_targets.append((reference["kind"], reference["id"]))
    if reference_keys != sorted(set(reference_keys)):
        raise PackError("pack references must be sorted and unique")
    if len(reference_targets) != len(set(reference_targets)):
        raise PackError("pack typed reference owner is ambiguous")
    entrypoints = value.get("entrypoints")
    if not isinstance(entrypoints, list):
        raise PackError("pack entrypoints must be a list")
    entry_ids: list[str] = []
    for entrypoint in entrypoints:
        if (not isinstance(entrypoint, dict) or set(entrypoint) != {"id", "kind", "target"}
                or not isinstance(entrypoint.get("id"), str)
                or not PACK_ID.fullmatch(entrypoint["id"])
                or entrypoint.get("kind") not in {"command", "recipe"}
                or not isinstance(entrypoint.get("target"), str)
                or not REFERENCE_ID.fullmatch(entrypoint["target"])):
            raise PackError("pack entrypoint is invalid")
        entry_ids.append(entrypoint["id"])
    if entry_ids != sorted(set(entry_ids)):
        raise PackError("pack entrypoints must be sorted and unique")
    resources = value.get("resources")
    declared_resources = set(assets.get("resource", []))
    if not isinstance(resources, dict) or set(resources) != declared_resources:
        raise PackError("pack resources must exactly cover resource assets")
    for path, metadata in resources.items():
        if (not isinstance(metadata, dict)
                or set(metadata) != {"media_type", "size", "sha256"}
                or metadata.get("sha256") != hashes.get(path)):
            raise PackError("pack resource metadata is invalid")


def validate_compatibility(value: dict, manifest: dict) -> None:
    _reject_unsafe(value, "compatibility")
    if set(value) != COMPAT_FIELDS or value.get("compatibility_schema_version") != 1:
        raise PackError("compatibility schema fields/version are invalid")
    if value.get("pack_id") != manifest["id"] or value.get("pack_version") != manifest["version"]:
        raise PackError("compatibility identity does not match pack")
    if value.get("engine") != manifest["engine"]:
        raise PackError("compatibility engine does not match pack")
    if (not isinstance(value.get("platforms"), list) or not value["platforms"]
            or value["platforms"] != sorted(set(value["platforms"]))
            or any(item not in {"any", "linux", "macos", "windows"} for item in value["platforms"])):
        raise PackError("compatibility platforms are invalid")
