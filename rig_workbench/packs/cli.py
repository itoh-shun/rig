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
    test.add_argument("--provider", choices=["mock", "claude", "codex", "command"])
    test.add_argument("--model")
    test.add_argument("--judge-provider", choices=["mock", "claude", "codex", "command"])
    test.add_argument("--judge-model")
    test.add_argument("--command", dest="provider_command")
    test.add_argument("--judge-command")
    test.add_argument("--timeout", type=float, default=30)
    test.add_argument("--json", action="store_true")
    remove = sub.add_parser("remove")
    remove.add_argument("id")
    remove.add_argument("--scope", choices=["project", "user", "org"], default="project")
    remove.add_argument("--root")
    remove.add_argument("--yes", action="store_true")
    return parser


def init_pack(pack_id: str, *, kind: str, root: pathlib.Path | str) -> pathlib.Path:
    from .manifest import PACK_ID
    if not PACK_ID.fullmatch(pack_id):
        raise PackError("pack id is invalid")
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
            for item in sorted(root.iterdir()) if item.is_dir()]


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
            if result.verification_status == "unverified":
                print("[WARN] installed unverified project pack", file=sys.stderr)
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
            )
            if args.json:
                print(canonical(report), end="")
            else:
                print(f"pack test: {report['status']} ({report['pack']})")
                for failure in report["failures"]:
                    print(f"- {failure}")
            return code
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
