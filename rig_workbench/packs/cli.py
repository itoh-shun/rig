from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys

from rig_workbench import __version__

from .doctor import diagnose
from .manifest import canonical
from .model import ASSET_DIRS, PackError
from .resolver import pack_roots
from .validation import validate_pack, validate_tiered_collection


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rig-wb pack")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("id")
    init.add_argument("--kind", choices=["core", "official", "domain", "project"], default="project")
    init.add_argument("--root", default=".rig/packs")
    validate = sub.add_parser("validate")
    validate.add_argument("path", nargs="?")
    validate.add_argument("--global", dest="global_", action="store_true")
    doctor = sub.add_parser("doctor")
    doctor.add_argument("path", nargs="?")
    doctor.add_argument("--json", action="store_true")
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
    test.add_argument("--json", action="store_true")
    sign = sub.add_parser("sign")
    sign.add_argument("pack")
    sign.add_argument("--private-key", required=True)
    sign.add_argument("--key-id", required=True)
    sign.add_argument("--signer", required=True)
    remove = sub.add_parser("remove")
    remove.add_argument("id")
    remove.add_argument("--scope", choices=["project", "user", "org"], default="project")
    remove.add_argument("--root")
    remove.add_argument("--yes", action="store_true")
    invoke = sub.add_parser("invoke")
    invoke.add_argument("entrypoint")
    invoke.add_argument("args", nargs=argparse.REMAINDER)
    return parser


def init_pack(pack_id: str, *, kind: str, root: pathlib.Path | str) -> pathlib.Path:
    from .manifest import PACK_ID, RESERVED_PACK_IDS
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
            "pack_schema_version": 1, "id": pack_id, "version": "0.1.0", "kind": kind,
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
            print(init_pack(args.id, kind=args.kind, root=args.root))
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
