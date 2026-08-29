from __future__ import annotations

import argparse
import json
import datetime as dt
import pathlib
import sys

from rig_workbench import __version__

from .doctor import diagnose
from .manifest import PACK_SCHEMA_VERSION, canonical
from .model import ASSET_DIRS, PACK_TYPES, PackError
from .resolver import pack_roots
from .sources import SOURCE_SCHEMES, read_sources, verify_pin, write_sources
from .validation import validate_pack, validate_tiered_collection


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rig-wb pack")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("id")
    init.add_argument("--kind", choices=["core", "official", "domain", "project"], default="project")
    init.add_argument("--type", dest="type_", choices=list(PACK_TYPES), required=True)
    init.add_argument("--root", default=".rig/packs")
    validate = sub.add_parser("validate")
    validate.add_argument("path", nargs="?")
    validate.add_argument("--global", dest="global_", action="store_true")
    doctor = sub.add_parser("doctor")
    doctor.add_argument("path", nargs="?")
    doctor.add_argument("--json", action="store_true")
    sync = sub.add_parser("sync")
    sync.add_argument("path", nargs="?")
    source = sub.add_parser("source")
    source_sub = source.add_subparsers(dest="source_command", required=True)
    source_sub.add_parser("list")
    source_add = source_sub.add_parser("add")
    source_add.add_argument("name")
    source_add.add_argument("--scheme", choices=list(SOURCE_SCHEMES), required=True)
    source_add.add_argument("--url", required=True,
                            help="URL template containing {pack}")
    source_remove = source_sub.add_parser("remove")
    source_remove.add_argument("name")
    export = sub.add_parser("export")
    export.add_argument("path")
    export.add_argument("--to", required=True)
    verify_sources = sub.add_parser("verify-sources")
    verify_sources.add_argument("--scope", choices=["project", "user", "org"],
                                default="project")
    verify_sources.add_argument("--root")
    for name in ("list", "outdated"):
        parser_ = sub.add_parser(name)
        parser_.add_argument("--scope", choices=["project", "user", "org"], default="project")
        parser_.add_argument("--root")
    for name in ("info", "explain"):
        parser_ = sub.add_parser(name)
        parser_.add_argument("pack")
        parser_.add_argument("--scope", choices=["project", "user", "org"], default="project")
        parser_.add_argument("--root")
        parser_.add_argument("--json", action="store_true")
    knowledge = sub.add_parser("knowledge")
    knowledge.add_argument("--topic", action="append", default=[], dest="topics",
                           help="repeatable; a pack matches if it declares any of them")
    knowledge.add_argument("--scope", action="append", default=[], dest="scopes",
                           help="repeatable; a bare dimension (`product`) matches every "
                                "value under it, a valued one (`product:x`) is exact")
    knowledge.add_argument("--scope-filter", choices=["project", "user", "org"],
                           default="project", dest="scope")
    knowledge.add_argument("--root")
    knowledge.add_argument("--json", action="store_true")
    update = sub.add_parser("update")
    update.add_argument("pack")
    update.add_argument("--to", required=True)
    update.add_argument("--scope", choices=["project", "user", "org"], default="project")
    update.add_argument("--root")
    update.add_argument("--allow-unverified", action="store_true")
    install = sub.add_parser("install")
    install.add_argument("source")
    install.add_argument("--scope", choices=["project", "user", "org"], default="project")
    install.add_argument("--root")
    install.add_argument("--allow-unverified", action="store_true")
    test = sub.add_parser("test")
    test.add_argument("pack")
    test.add_argument("--provider", choices=["mock", "codex"])
    test.add_argument("--model")
    test.add_argument("--judge-provider", choices=["mock", "codex"])
    test.add_argument("--judge-model")
    test.add_argument("--command", dest="provider_command")
    test.add_argument("--judge-command")
    test.add_argument("--timeout", type=float, default=30)
    test.add_argument("--result-dir")
    test.add_argument("--allow-paid-provider", action="store_true")
    test.add_argument("--json", action="store_true")
    import_results = sub.add_parser("import-results")
    import_results.add_argument("pack")
    import_results.add_argument("--result-dir", required=True)
    sign = sub.add_parser("sign")
    sign.add_argument("pack")
    sign.add_argument("--private-key", required=True)
    sign.add_argument("--key-id", required=True)
    sign.add_argument("--signer", required=True)
    keygen = sub.add_parser("keygen")
    keygen.add_argument("--private-key", required=True)
    keygen.add_argument("--trust-roots", required=True)
    keygen.add_argument("--key-id", required=True)
    keygen.add_argument("--signer", required=True)
    remove = sub.add_parser("remove")
    remove.add_argument("id")
    remove.add_argument("--scope", choices=["project", "user", "org"], default="project")
    remove.add_argument("--root")
    remove.add_argument("--yes", action="store_true")
    invoke = sub.add_parser("invoke")
    invoke.add_argument("entrypoint")
    invoke.add_argument("args", nargs=argparse.REMAINDER)
    return parser


def init_pack(pack_id: str, *, kind: str, type_: str,
              root: pathlib.Path | str) -> pathlib.Path:
    """Scaffold a pack. `type_` has no default on purpose — it decides what the pack may
    carry and run, and a default would hand that decision to whoever forgot to make it."""
    from .manifest import PACK_ID, RESERVED_PACK_IDS
    if type_ not in PACK_TYPES:
        raise PackError(f"pack type must be one of {', '.join(PACK_TYPES)}")
    if not PACK_ID.fullmatch(pack_id):
        raise PackError("pack id is invalid")
    if pack_id in RESERVED_PACK_IDS:
        raise PackError(f"pack id is reserved: {pack_id}")
    destination = pathlib.Path(root).resolve() / pack_id
    if destination.exists():
        raise PackError(f"pack already exists: {destination}")
    try:
        destination.mkdir(parents=True, exist_ok=False)
        for directory in ASSET_DIRS.values():
            (destination / directory).mkdir(parents=True, exist_ok=True)
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        manifest = {
            "pack_schema_version": PACK_SCHEMA_VERSION, "id": pack_id, "type": type_,
            "version": "0.1.0", "kind": kind,
            "engine": f">={__version__}", "dependencies": [],
            "assets": {kind_: [] for kind_ in ASSET_DIRS}, "hashes": {},
            "display_name": pack_id, "description": "New Rig pack",
            "capabilities": ["resource"], "entrypoints": [], "references": [],
            "resources": {},
            "provenance": {"source": "rig-wb pack init", "created_at": now},
        }
        compatibility = {
            "compatibility_schema_version": 1, "pack_id": pack_id,
            "pack_version": "0.1.0", "engine": f">={__version__}", "platforms": ["any"],
        }
        (destination / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
        (destination / "compatibility.yaml").write_text(canonical(compatibility), encoding="utf-8")
    except OSError as exc:
        raise PackError(f"cannot initialize pack: {exc}") from exc
    return destination


def _global_dirs(project: pathlib.Path) -> list[tuple[str, pathlib.Path]]:
    return [(tier, item) for tier, root in pack_roots(project) if root.is_dir()
            for item in sorted(root.iterdir()) if item.is_dir()
            and not item.name.startswith((".", "_"))]


def _resolve_invocation(spec: str, project: pathlib.Path) -> tuple[str, pathlib.Path, dict, dict]:
    pack_id, separator, entry_id = spec.partition(":")
    if not separator or not pack_id or not entry_id:
        raise PackError("pack invoke target must be <pack>:<entry>")
    installed = validate_tiered_collection(_global_dirs(project))
    matches = [(_tier, path, manifest) for _tier, path, manifest in installed
               if manifest["id"] == pack_id]
    if not matches:
        from .catalog import discover_builtin_packs
        matches = [("builtin", path, manifest) for (_kind, candidate_id), (path, manifest)
                   in discover_builtin_packs().items() if candidate_id == pack_id]
    if len(matches) != 1:
        raise PackError(f"pack invoke pack is {'ambiguous' if matches else 'unknown'}: {pack_id}")
    tier, path, manifest = matches[0]
    entries = [entry for entry in manifest["entrypoints"] if entry["id"] == entry_id]
    if len(entries) != 1:
        raise PackError(f"unknown pack entrypoint: {spec}")
    return tier, path, manifest, entries[0]


def invoke_pack(spec: str, forwarded: list[str], *, project: pathlib.Path) -> int:
    from .manifest import parse_frontmatter_subset
    tier, pack, manifest, entry = _resolve_invocation(spec, project)
    kind = entry["kind"]
    if kind not in {"command", "recipe"}:
        raise PackError(f"pack entrypoint kind is not invokable: {kind}")
    prefix = pathlib.PurePosixPath(ASSET_DIRS[kind])
    relative = next((item for item in manifest["assets"][kind]
                     if str(pathlib.PurePosixPath(item).relative_to(prefix).with_suffix(""))
                     == entry["target"]), None)
    if relative is None:
        raise PackError(f"pack entrypoint target is missing: {entry['target']}")
    target = pack / relative
    if tier in {"project", "user", "org"}:
        from .model import ResolvedAsset
        from .trust import ensure_asset_trusted
        ensure_asset_trusted(ResolvedAsset(
            kind, entry["target"], target, tier, str(pack), manifest["id"]
        ))
    args = forwarded[1:] if forwarded[:1] == ["--"] else forwarded
    if kind == "command":
        print(canonical({
            "args": args, "asset": str(target), "entrypoint": spec,
            "mode": "manual-command", "status": "ready",
        }), end="")
        return 0
    frontmatter = parse_frontmatter_subset(target)
    from rig_workbench.orchestrate.gates import validate_executable_recipe
    execution = validate_executable_recipe(frontmatter)
    if not execution["orchestratable"]:
        raise PackError(
            f"entrypoint {spec} is computationally nonexecutable: {execution['reason']}"
        )
    from rig_workbench.orchestrate import commands
    original = commands.resolve_recipe
    commands.resolve_recipe = lambda _name: target
    try:
        commands.cmd_run([entry["target"], *args])
    except SystemExit as exc:
        return int(exc.code or 0)
    finally:
        commands.resolve_recipe = original
    return 0


def cmd_pack(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "init":
            print(init_pack(args.id, kind=args.kind, type_=args.type_, root=args.root))
            return 0
        if args.command == "validate":
            if args.global_:
                records = validate_tiered_collection(_global_dirs(pathlib.Path.cwd()))
                print(f"{len(records)} pack(s) valid")
            else:
                path = pathlib.Path(args.path or ".")
                manifest = validate_pack(path)
                print(f"valid: {manifest['id']}@{manifest['version']}")
            return 0
        if args.command == "sync":
            from .sync import sync_manifest
            result = sync_manifest(args.path or ".")
            for item in result["added"]:
                print(f"  + {item}")
            for item in result["removed"]:
                print(f"  - {item}")
            print(f"pack sync: {result['total']} asset(s) declared and hashed")
            return 0
        if args.command == "export":
            from .exporter import export_pack
            exported = export_pack(args.path, to=args.to)
            print(f"exported: {exported['id']}@{exported['version']} "
                  f"[{exported['type']}] -> {exported['pack_path']}")
            print("next:")
            print(f"  cd {exported['path']} && git init && git add -A && "
                  f"git commit -m '{exported['id']} {exported['version']}'")
            print("  git remote add origin <your repository URL> && git push -u origin main")
            print(f"  git tag {exported['tag']} && git push origin {exported['tag']}")
            return 0
        if args.command == "source":
            project = pathlib.Path.cwd()
            declared = read_sources(project)
            if args.source_command == "list":
                for name in sorted(declared):
                    print(f"{name}\t{declared[name]['scheme']}\t{declared[name]['url']}")
                if not declared:
                    print("no sources declared")
                return 0
            if args.source_command == "add":
                if args.name in declared:
                    raise PackError(f"source already declared: {args.name}")
                declared[args.name] = {"scheme": args.scheme, "url": args.url}
                write_sources(project, declared)
                print(f"declared source: {args.name}")
                return 0
            if args.name not in declared:
                raise PackError(f"source is not declared: {args.name}")
            del declared[args.name]
            write_sources(project, declared)
            print(f"removed source: {args.name}")
            return 0
        if args.command == "verify-sources":
            from .installer import scope_root
            from .lock import read_lock
            project = pathlib.Path.cwd()
            declared = read_sources(project)
            root = scope_root(args.scope, project=project,
                              root=pathlib.Path(args.root) if args.root else None)
            pinned = [item for item in read_lock(root)["packs"]
                      if item["source"]["type"] == "git"]
            if not pinned:
                print("no git-sourced packs are locked in this scope")
                return 0
            worst = 0
            for entry in sorted(pinned, key=lambda item: item["id"]):
                source_id = entry["source"]["source_id"]
                if source_id not in declared:
                    reason, worst = "source-undeclared", 1
                else:
                    reason = verify_pin(declared[source_id], entry)
                    worst = max(worst, 0 if reason == "ok" else 1)
                print(f"{entry['id']}\t{entry['source']['path']}\t{reason}")
            return worst
        if args.command == "knowledge":
            from .installer import scope_root
            from .inventory import knowledge_rows
            project = pathlib.Path.cwd()
            root = scope_root(args.scope, project=project,
                              root=pathlib.Path(args.root) if args.root else None)
            report = knowledge_rows(project, root, topics=tuple(args.topics),
                                    scopes=tuple(args.scopes))
            if args.json:
                print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
                return 0
            if not report["candidates"]:
                print("no installed pack declares knowledge matching that")
                return 0
            for row in report["candidates"]:
                print(f"{row['id']}@{row['version']}\t{'/'.join(row['matched_scope'])}"
                      f"\t{row['owner']}\treviewed {row['reviewed_at']}")
                print(f"  topics: {', '.join(row['topics'])}")
                print(f"  evidence: {', '.join(row['evidence'])}")
                for document in row["documents"]:
                    # A shadowed document is listed rather than hidden, and says who won.
                    # Dropping it would leave a person wondering where their file went; citing
                    # it silently would put an answer behind text that never gets read.
                    state = "" if document["effective"] else (
                        f"  [shadowed by {document['provided_by'] or 'nothing'}]")
                    print(f"  {document['kind']}: {document['uri']}{state}")
            if report["ambiguous"]:
                # Named, not resolved. Which scope was meant is a fact about the asker that
                # no pack contains, so this says what has to be settled and stops there.
                print(f"scope is ambiguous: {', '.join(report['scopes'])} — "
                      f"narrow with --scope before treating any of these as the answer")
            return 0
        if args.command in {"list", "info", "explain", "outdated", "update"}:
            from .installer import scope_root, update_pack
            from .inventory import explain as explain_pack
            from .inventory import info as pack_info
            from .inventory import list_rows, outdated
            project = pathlib.Path.cwd()
            root = scope_root(args.scope, project=project,
                              root=pathlib.Path(args.root) if args.root else None)
            if args.command == "list":
                rows = list_rows(root)
                if not rows:
                    print("no packs installed in this scope")
                    return 0
                for row in rows:
                    print(f"{row['id']}@{row['version']}\t{row['type']}\t{row['kind']}"
                          f"\t{row['origin']}\t{row['verification']}")
                return 0
            if args.command == "info":
                detail = pack_info(root, args.pack)
                if args.json:
                    print(json.dumps(detail, ensure_ascii=False, sort_keys=True, indent=2))
                else:
                    for key, value in detail.items():
                        print(f"{key}\t{value}")
                return 0
            if args.command == "explain":
                rows = explain_pack(project, root, args.pack)
                if args.json:
                    print(json.dumps(rows, ensure_ascii=False, sort_keys=True, indent=2))
                    return 0
                if not rows:
                    print(f"{args.pack} provides no prompt surfaces")
                    return 0
                for row in rows:
                    state = "effective" if row["effective"] else (
                        f"shadowed by {row['provided_by']} [{row['tier']}]")
                    print(f"{row['kind']}:{row['name']}\t{state}")
                return 0
            if args.command == "outdated":
                rows = outdated(project, root)
                if not rows:
                    print("no git-sourced packs are locked in this scope")
                    return 0
                for row in rows:
                    print(f"{row['id']}\t{row['current']}\t{row['latest'] or '-'}"
                          f"\t{row['reason']}")
                return 1 if any(row["reason"] != "ok" for row in rows) else 0
            result = update_pack(
                args.pack, to=args.to, scope=args.scope, project=project,
                root=args.root, allow_unverified=args.allow_unverified,
            )
            print(f"updated: {result.manifest['id']}@{result.manifest['version']} "
                  f"[{result.verification_status}] -> {result.path}")
            return 0
        if args.command == "install":
            from .installer import install_pack
            result = install_pack(
                args.source, scope=args.scope, project=pathlib.Path.cwd(), root=args.root,
                allow_unverified=args.allow_unverified,
            )
            if result.verification_status != "verified-publisher":
                print(
                    f"[WARN] installed publisher-unverified project pack "
                    f"[{result.verification_status}]",
                    file=sys.stderr,
                )
            print(f"installed: {result.manifest['id']}@{result.manifest['version']} "
                  f"[{result.verification_status}] -> {result.path}")
            return 0
        if args.command == "test":
            from .tester import test_pack
            report, code = test_pack(
                args.pack, project=pathlib.Path.cwd(), provider=args.provider,
                model=args.model, judge_provider=args.judge_provider,
                judge_model=args.judge_model, command=args.provider_command,
                judge_command=args.judge_command, timeout=args.timeout,
                result_dir=args.result_dir,
                allow_paid_provider=args.allow_paid_provider,
            )
            if args.json:
                print(canonical(report), end="")
            else:
                print(f"pack test: {report['status']} ({report['pack']})")
                for failure in report["failures"]:
                    print(f"- {failure}")
            return code
        if args.command == "sign":
            from .publisher import sign_pack
            document = sign_pack(
                args.pack, private_key_path=args.private_key,
                key_id=args.key_id, signer=args.signer,
            )
            print(f"signed: {args.pack} [{document['signed']['key_id']}]")
            return 0
        if args.command == "keygen":
            from .publisher import generate_publisher_key
            root = generate_publisher_key(
                private_key_path=args.private_key, trust_roots_path=args.trust_roots,
                key_id=args.key_id, signer=args.signer,
                source_repository=pathlib.Path.cwd(),
            )
            print(f"publisher key registered: {root['key_id']} -> {args.trust_roots}")
            return 0
        if args.command == "import-results":
            from .evidence import import_results
            imported = import_results(
                args.pack, staged=args.result_dir, project=pathlib.Path.cwd(),
            )
            for relative in imported:
                print(f"imported: {relative}")
            return 0
        if args.command == "remove":
            from .remover import remove_pack
            target, removed = remove_pack(
                args.id, scope=args.scope, project=pathlib.Path.cwd(), root=args.root,
                yes=args.yes,
            )
            print(f"{'removed' if removed else 'dry-run remove'}: {target}")
            if not removed:
                print("rerun with --yes to remove this lock-owned pack")
            return 0
        if args.command == "invoke":
            return invoke_pack(args.entrypoint, args.args, project=pathlib.Path.cwd())
        report = diagnose(args.path, project=pathlib.Path.cwd())
        if args.json:
            print(canonical(report), end="")
        else:
            print(f"pack doctor: {report['status']}")
            for finding in report["findings"]:
                print(f"- {finding['code']}: {finding.get('path', finding.get('asset', finding.get('pack', '')))}")
        return 0 if report["status"] == "ok" else 1
    except PackError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
