"""Detached queue-draining worker for Mission Control autonomous runs."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import pathlib
import subprocess
import sys
import time

from .mission_jobs import queue_items, update_worker_state, wait_for_worker_registration


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _queued(root: pathlib.Path) -> tuple[int, str | None]:
    items, error = queue_items(root)
    return sum(1 for item in items if item.get("status") == "queued"), error


def run_worker(root: pathlib.Path, *, provider: str, verifier_provider: str,
               max_parallel: int, generation: str) -> int:
    wait_for_worker_registration(root, generation)
    update_worker_state(root, generation, status="running", pid=os.getpid(), running_at=_now())
    any_failure = False
    cycles = 0
    try:
        while True:
            count, error = _queued(root)
            if error:
                print(f"[mission-worker] {error}", flush=True)
                update_worker_state(root, generation, status="failed", error=error,
                                    finished_at=_now(), last_exit_code=1)
                return 1
            if count == 0:
                break
            cycles += 1
            print(
                f"[mission-worker] cycle={cycles} queued={count} provider={provider} "
                f"verifier={verifier_provider} parallel={max_parallel}",
                flush=True,
            )
            command = [
                sys.executable, "-m", "rig_workbench.cli", "queue", "go",
                "--backend", "local",
                "--provider", provider,
                "--verifier-provider", verifier_provider,
                "--max-parallel", str(max_parallel),
            ]
            env = dict(os.environ)
            env["RIG_INVOKER"] = "mission-control-worker/v1"
            proc = subprocess.run(command, cwd=root, env=env, check=False)
            if proc.returncode != 0:
                any_failure = True
            # queue go snapshots its current batch. A task added while that batch
            # was executing remains queued, so loop and pick it up next.
            time.sleep(0.15)
        status = "completed_with_failures" if any_failure else "completed"
        exit_code = 1 if any_failure else 0
        update_worker_state(
            root,
            generation,
            status=status,
            finished_at=_now(),
            last_exit_code=exit_code,
            cycles=cycles,
            pid=os.getpid(),
        )
        print(f"[mission-worker] {status}; cycles={cycles}", flush=True)
        return exit_code
    except BaseException as exc:
        update_worker_state(
            root,
            generation,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            finished_at=_now(),
            last_exit_code=1,
            cycles=cycles,
            pid=os.getpid(),
        )
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m rig_workbench.mission_worker")
    parser.add_argument("--repo", type=pathlib.Path, required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--verifier-provider", required=True)
    parser.add_argument("--max-parallel", type=int, required=True)
    parser.add_argument("--generation", required=True)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    root = args.repo.resolve()
    raise SystemExit(
        run_worker(
            root,
            provider=args.provider,
            verifier_provider=args.verifier_provider,
            max_parallel=args.max_parallel,
            generation=args.generation,
        )
    )


if __name__ == "__main__":
    main()
