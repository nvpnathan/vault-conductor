from __future__ import annotations

import argparse
import json
import sys

from .commands import (
    cleanup_command,
    diff_command,
    doctor_command,
    init_command,
    log_command,
    mark_task,
    move_command,
    new_task_command,
    pr_command,
    scan_command,
    send_command,
    start_task,
    status_command,
    stop_task,
    sync_command,
    test_command,
)
from .config import load_config
from .constants import TASK_STATUSES
from .watch import watch_forever


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="conductor", description="Obsidian Agent Control Room CLI")
    parser.add_argument("--vault", help="Override vault path. Default: ~/Agent Control Room")
    parser.add_argument("--repos", help="Override repos root. Default: ~/repos")
    parser.add_argument("--config", help="Override config file path")
    parser.add_argument("--runtime-root", help="Override runtime root. Default: ~/.agent-control-room")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON where supported")
    parser.add_argument("--dry-run", action="store_true", help="Show actions without applying changes")
    parser.add_argument("--verbose", action="store_true", help="Print more operational detail")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    init.add_argument("--force", action="store_true")
    init.add_argument("--no-open", action="store_true")

    sub.add_parser("scan")

    new = sub.add_parser("new")
    new.add_argument("--repo", required=True)
    new.add_argument("--title", required=True)
    new.add_argument("--agent")
    new.add_argument("--priority", default="P2")
    new.add_argument("--risk", default="medium")
    new.add_argument("--status", default="backlog")
    new.add_argument("--goal", default="")
    new.add_argument("--acceptance", action="append")
    new.add_argument("--context", default="")
    new.add_argument("--test-command")

    board = sub.add_parser("board")
    board.add_argument("--print-path", action="store_true")
    board.add_argument("--uri", action="store_true")

    sub.add_parser("status")

    start = sub.add_parser("start")
    start.add_argument("task_id")

    stop = sub.add_parser("stop")
    stop.add_argument("task_id")
    stop.add_argument("--park", action="store_true")
    stop.add_argument("--kill", action="store_true")

    mark = sub.add_parser("mark")
    mark.add_argument("task_id")
    mark.add_argument("status", choices=TASK_STATUSES)
    mark.add_argument("--human", action="store_true")

    move = sub.add_parser("move")
    move.add_argument("task_id")
    move.add_argument("column_or_status")
    move.add_argument("--human", action="store_true")

    send = sub.add_parser("send")
    send.add_argument("task_id")
    send.add_argument("message")
    send.add_argument("--status", choices=TASK_STATUSES)

    log = sub.add_parser("log")
    log.add_argument("task_id")
    log.add_argument("--tail", type=int)

    diff = sub.add_parser("diff")
    diff.add_argument("task_id")
    diff.add_argument("--stat", action="store_true")
    diff.add_argument("--name-only", action="store_true")
    diff.add_argument("--full", action="store_true")
    diff.add_argument("--save", action="store_true")

    test = sub.add_parser("test")
    test.add_argument("task_id")

    pr = sub.add_parser("pr")
    pr.add_argument("task_id")
    pr.add_argument("--commit", action="store_true")
    pr.add_argument("--yes", action="store_true")
    pr.add_argument("--force", action="store_true")
    pr.add_argument("--dry-run", dest="command_dry_run", action="store_true")

    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("task_id")
    cleanup.add_argument("--yes", action="store_true")
    cleanup.add_argument("--force", action="store_true")
    cleanup.add_argument("--branch", action="store_true")
    cleanup.add_argument("--dry-run", dest="command_dry_run", action="store_true")

    sync = sub.add_parser("sync")
    sync.add_argument("--board-wins", action="store_true")

    watch = sub.add_parser("watch")
    watch.add_argument("--interval", type=float, default=1.0)

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--fix", action="store_true")
    doctor.add_argument("--json", dest="command_json", action="store_true")
    sub.add_parser("dashboard")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config(
        vault=args.vault,
        repos=args.repos,
        config=args.config,
        runtime_root=args.runtime_root,
        json_output=args.json or bool(getattr(args, "command_json", False)),
        dry_run=args.dry_run or bool(getattr(args, "command_dry_run", False)),
        verbose=args.verbose,
    )
    try:
        result, human = dispatch(config, args)
        if config.flags.get("json") and result is not None:
            print(json.dumps(result, indent=2))
        elif human:
            print(human)
        elif isinstance(result, str):
            print(result)
        return 0
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1


def dispatch(config, args):
    match args.command:
        case "init":
            result = init_command(config, force=args.force, open_obsidian=not args.no_open)
            return result, f"Initialized Agent Control Room at {result['vaultPath']}"
        case "scan":
            result = scan_command(config)
            return result, f"Discovered {len(result['repos'])} repositories."
        case "new":
            result = new_task_command(
                config,
                repo=args.repo,
                title=args.title,
                agent=args.agent,
                priority=args.priority,
                risk=args.risk,
                status=args.status,
                goal=args.goal,
                acceptance=args.acceptance,
                context=args.context,
                test_command=args.test_command,
            )
            return result, f"Created {result['id']}: {result['path']}"
        case "board":
            if args.uri:
                uri = f"obsidian://open?path={config.board_path}"
                return uri, uri
            return str(config.board_path), str(config.board_path)
        case "status":
            result = status_command(config)
            if config.flags.get("json"):
                return result, ""
            lines = [f"{task['id']} {task['status']} {task['repo']} {task['title']}" for task in result["tasks"]]
            for task_id, session in result["sessions"].items():
                status = session.get("status") or "running"
                label = "RUNNING" if status == "running" else "SESSION"
                lines.append(f"{label} {task_id} status={status} workspace={session.get('workspace_ref')}")
            return "\n".join(lines), "\n".join(lines)
        case "start":
            result = start_task(config, args.task_id)
            return result, f"Started {args.task_id}; run {result['run_id']}; workspace {result['workspace_ref']}"
        case "stop":
            status = stop_task(config, args.task_id, park=args.park, kill=args.kill)
            return {"status": status}, f"Stopped {args.task_id}; status {status}"
        case "mark":
            mark_task(config, args.task_id, args.status, human=args.human)
            return {"id": args.task_id, "status": args.status}, f"Marked {args.task_id} {args.status}"
        case "move":
            move_command(config, args.task_id, args.column_or_status, human=args.human)
            return {"id": args.task_id, "target": args.column_or_status}, f"Moved {args.task_id} to {args.column_or_status}"
        case "send":
            result = send_command(config, args.task_id, args.message, status=args.status)
            return result, "Follow-up saved."
        case "log":
            output = log_command(config, args.task_id, tail=args.tail)
            return output, output
        case "diff":
            output = diff_command(config, args.task_id, stat=args.stat, name_only=args.name_only, full=args.full, save=args.save)
            return output, output
        case "test":
            result = test_command(config, args.task_id)
            return result, f"Test command exited {result['exitCode']}"
        case "pr":
            url = pr_command(config, args.task_id, commit=args.commit, yes=args.yes, force=args.force, dry_run=config.flags.get("dry_run", False))
            return {"pr_url": url}, f"PR opened: {url}"
        case "cleanup":
            result = cleanup_command(config, args.task_id, yes=args.yes, force=args.force, branch=args.branch, dry_run=config.flags.get("dry_run", False))
            return result, f"{'DRY RUN: would remove' if result.get('dryRun') else 'Removed'} {result['removed']}"
        case "sync":
            result = sync_command(config, board_wins=args.board_wins)
            return result, f"Synced {result['synced']} tasks."
        case "watch":
            watch_forever(config, poll_interval=args.interval)
            return None, ""
        case "doctor":
            result = doctor_command(config, fix=args.fix)
            if config.flags.get("json"):
                return result, ""
            lines = ["Agent Control Room Doctor", ""]
            lines.extend(f"{check['status'].ljust(4)} {check['message']}" for check in result["checks"])
            return "\n".join(lines), "\n".join(lines)
        case "dashboard":
            from .dashboard import main as dashboard_main

            dashboard_main()
            return None, ""
    raise ValueError(f"Unknown command: {args.command}")


def watch_main() -> int:
    config = load_config()
    watch_forever(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
