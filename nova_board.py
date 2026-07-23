# nova_board.py
# Thin CLI on top of nova_clickup_client.py — no ClickUp API logic lives
# here, only argument parsing, output formatting, and confirmation prompts.
# Lets Marvin do routine Nova board maintenance (status moves, dependency
# checks, audits) from the terminal instead of spending a Claude Chat turn
# per action. See Nova Reference — nova_board CLI Design Spec v1.0.
#
# Explicitly out of scope for v1: no commands beyond the set below, no
# autonomous/scheduled execution, no natural-language interface.

import argparse
import sys
from datetime import UTC, datetime

from colorama import Fore, Style
from colorama import init as colorama_init

import nova_clickup_client as client

# Task names/descriptions are real ClickUp data (already contains em-dashes,
# emoji, etc.) that this tool doesn't control — reconfigure stdout so those
# never crash on a Windows console stuck on cp1252, the same bug class this
# project has hit and fixed in nova_benchmark.py and the Browser Hands harness.
if sys.platform == "win32":
    # mypy types sys.stdout/stderr as the narrower TextIO protocol, which
    # doesn't declare .reconfigure() -- it's real on the concrete TextIOWrapper
    # object at runtime (confirmed: this line executes today), a known,
    # common false-positive category, not a real type error.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

colorama_init(autoreset=True)

# Tasks larger than this get a confirmation prompt before a bulk move, unless -y is passed.
CONFIRM_THRESHOLD = 5


# ── Output helpers ───────────────────────────────────────────────


def _color_for_status(status: str) -> str:
    return {
        "blocked": Fore.RED,
        "in progress": Fore.YELLOW,
        "complete": Fore.CYAN,
    }.get(status, Fore.GREEN)  # "to do" with no blockers reads as ready -> green


def _colorize(text: str, color: str) -> str:
    return f"{color}{text}{Style.RESET_ALL}"


def _priority_of(task: dict) -> str:
    priority = task.get("priority") or {}
    return priority.get("priority", "none")


def _last_updated(task: dict) -> str:
    return datetime.fromtimestamp(int(task["date_updated"]) / 1000, tz=UTC).date().isoformat()


# ── Read commands ────────────────────────────────────────────────


def cmd_help(args, parser, sub_map) -> int:
    topic = args.topic
    if topic is None:
        parser.print_help()
        return 0
    sub = sub_map.get(topic)
    if sub is None:
        print(f"Unknown command: {topic}")
        return 1
    sub.print_help()
    return 0


def cmd_ready(args) -> int:
    tasks = client.list_board_tasks()
    ready_tasks = []
    for task in tasks:
        status = task["status"]["status"]
        if status in ("in progress", "complete"):
            continue
        if not client.get_unresolved_blockers(task["id"]):
            ready_tasks.append(task)

    if not ready_tasks:
        print("Nothing ready - every to-do/blocked task has an unresolved dependency.")
        return 0

    for task in sorted(ready_tasks, key=lambda t: t["name"]):
        print(f"{_colorize(task['id'], Fore.GREEN)}  {task['name']}  [priority: {_priority_of(task)}]")
    return 0


def cmd_why(args) -> int:
    task = client.get_task(args.task_id)
    status = task["status"]["status"]
    print(f"{task['id']}  {task['name']}  [{_colorize(status, _color_for_status(status))}]")

    chain = client.get_dependency_chain(args.task_id)
    if not chain:
        print("No blockers - this task is unblocked.")
        return 0

    for link in chain:
        color = _color_for_status(link["status"])
        print(f"  -> {_colorize(link['id'], color)}  {link['name']}  [{_colorize(link['status'], color)}]")
    return 0


def cmd_check(args) -> int:
    task = client.get_task(args.task_id)
    status = task["status"]["status"]
    unresolved = client.get_unresolved_blockers(args.task_id)

    print(f"{task['id']}  {task['name']}")
    print(f"  status: {_colorize(status, _color_for_status(status))}")
    print(f"  last activity: {_last_updated(task)}")

    if unresolved:
        print("  unresolved dependencies:")
        for blocker in unresolved:
            print(f"    - {blocker['id']}  {blocker['name']}  [{blocker['status']}]")
    else:
        print("  unresolved dependencies: none")

    if status == "in progress":
        ok, reason = client.qualifies_as_in_progress(args.task_id)
        verdict = "earned" if ok else f"NOT earned ({reason})"
        print(f"  in-progress status: {verdict}")

    return 0


def cmd_audit(args) -> int:
    mismatches = []
    for task in client.list_board_tasks():
        status = task["status"]["status"]
        if status == "in progress":
            ok, reason = client.qualifies_as_in_progress(task["id"])
            if not ok:
                mismatches.append((task, f"marked in progress but {reason}"))
        elif status == "blocked":
            if not client.get_unresolved_blockers(task["id"]):
                mismatches.append((task, "marked blocked but has zero unresolved dependencies"))

    if not mismatches:
        print(f"{Fore.GREEN}No mismatches found.{Style.RESET_ALL}")
        return 0

    for task, reason in mismatches:
        print(f"{_colorize(task['id'], Fore.RED)}  {task['name']}  - {reason}")
    return 1


def cmd_find(args) -> int:
    keyword = args.keyword.lower()
    matches = [t for t in client.list_board_tasks(include_closed=True) if keyword in t["name"].lower()]

    if not matches:
        print("No matches.")
        return 0

    for task in matches:
        status = task["status"]["status"]
        print(
            f"{_colorize(task['id'], _color_for_status(status))}  {task['name']}  "
            f"[{status}] priority={_priority_of(task)}"
        )
    return 0


# ── Write commands ───────────────────────────────────────────────


def cmd_move(args) -> int:
    ids = list(args.ids)
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            ids.extend(line.strip() for line in f if line.strip())

    if not ids:
        print("No task IDs given.")
        return 1

    if args.status == "in progress" and not args.force:
        refusals = [(tid, client.get_unresolved_blockers(tid)) for tid in ids]
        refusals = [(tid, blockers) for tid, blockers in refusals if blockers]
        if refusals:
            for tid, blockers in refusals:
                blocker = blockers[0]
                print(
                    f"{Fore.RED}Refused{Style.RESET_ALL}: {tid} has an unresolved dependency on "
                    f"{blocker['id']} ({blocker['name']}). Use --force to override."
                )
            return 1

    if args.dry_run:
        print(f"[dry-run] Would set {len(ids)} task(s) to '{args.status}': {', '.join(ids)}")
        return 0

    if len(ids) > CONFIRM_THRESHOLD and not args.yes:
        confirm = input(f"Move {len(ids)} tasks to '{args.status}'? [y/N] ")
        if confirm.strip().lower() != "y":
            print("Aborted.")
            return 1

    for tid in ids:
        client.update_status(tid, args.status)
        print(f"{tid} -> {args.status}")
    return 0


def cmd_block(args) -> int:
    if args.new:
        target = client.get_task(args.task_id)
        if args.dry_run:
            print(
                f"[dry-run] Would create task '{args.new}', link it as a dependency of "
                f"{args.task_id}, and set {args.task_id} to blocked."
            )
            return 0
        description = f"Blocks: {target['id']} ({target['name']})"
        new_task = client.create_task(args.new, description)
        client.add_dependency(args.task_id, new_task["id"])
        client.update_status(args.task_id, "blocked")
        print(f"Created {new_task['id']} ({args.new}); {args.task_id} now blocked on it.")
        return 0

    if args.dry_run:
        print(f"[dry-run] Would link {args.task_id} depends_on {args.on}, and set {args.task_id} to blocked.")
        return 0
    client.add_dependency(args.task_id, args.on)
    client.update_status(args.task_id, "blocked")
    print(f"{args.task_id} now blocked on {args.on}.")
    return 0


def cmd_link(args) -> int:
    if args.dry_run:
        print(f"[dry-run] Would link {args.task_id} depends_on {args.to_id}.")
        return 0
    client.add_dependency(args.task_id, args.to_id)
    print(f"Linked: {args.task_id} depends on {args.to_id}.")
    return 0


def cmd_unlink(args) -> int:
    if args.dry_run:
        print(f"[dry-run] Would remove dependency: {args.task_id} depends_on {args.from_id}.")
        return 0
    client.remove_dependency(args.task_id, args.from_id)
    print(f"Unlinked: {args.task_id} no longer depends on {args.from_id}.")
    return 0


def cmd_split(args) -> int:
    if args.dry_run:
        print(
            f"[dry-run] Would mark {args.task_id} 'SUPERSEDED' + complete, create "
            f"'{args.keep}' and '{args.new}' as child tasks, then prompt for each child's dependencies."
        )
        return 0

    original = client.get_task(args.task_id)
    child_a = client.create_task(args.keep)
    child_b = client.create_task(args.new)

    superseded_name = f"{original['name']} — SUPERSEDED by {child_a['id']}, {child_b['id']}"
    client.update_task(args.task_id, name=superseded_name, status="complete")
    print(f"{args.task_id} marked superseded + complete.")
    print(f"Created {child_a['id']} ({args.keep}) and {child_b['id']} ({args.new}).")

    for child in (child_a, child_b):
        answer = input(
            f"Does '{child['name']}' ({child['id']}) depend on any existing tasks? "
            f"Comma-separated task IDs, or blank for none: "
        ).strip()
        for dep_id in (x.strip() for x in answer.split(",")):
            if dep_id:
                client.add_dependency(child["id"], dep_id)
                print(f"  {child['id']} now depends on {dep_id}")

    return 0


COMMAND_HANDLERS = {
    "ready": cmd_ready,
    "ls": cmd_ready,
    "why": cmd_why,
    "check": cmd_check,
    "audit": cmd_audit,
    "find": cmd_find,
    "move": cmd_move,
    "mv": cmd_move,
    "block": cmd_block,
    "link": cmd_link,
    "unlink": cmd_unlink,
    "split": cmd_split,
}


# ── Parser construction ──────────────────────────────────────────


def build_parser():
    parser = argparse.ArgumentParser(
        prog="nova_board",
        description="Nova ClickUp board management CLI - enforces the board's dependency/status house rules.",
    )
    subparsers = parser.add_subparsers(dest="command")
    sub_map = {}

    def add_command(name, aliases=None, **kwargs):
        sub = subparsers.add_parser(name, aliases=aliases or [], **kwargs)
        sub_map[name] = sub
        for alias in aliases or []:
            sub_map[alias] = sub
        return sub

    help_parser = add_command("help", help="List all commands, or show detailed usage for one")
    help_parser.add_argument("topic", nargs="?", default=None)

    add_command("ready", aliases=["ls"], help="Tasks with zero unresolved dependencies, not already in-progress")

    why_parser = add_command("why", help="Walk the full blocker chain for a task")
    why_parser.add_argument("task_id")

    check_parser = add_command("check", help="Single-task snapshot: status, dependencies, staleness")
    check_parser.add_argument("task_id")

    add_command("audit", help="Full board scan for status/dependency mismatches")

    find_parser = add_command("find", help="Search task names by keyword")
    find_parser.add_argument("keyword")

    move_parser = add_command("move", aliases=["mv"], help="Bulk status change")
    move_parser.add_argument("status", choices=client.VALID_STATUSES)
    move_parser.add_argument("ids", nargs="*", help="Task IDs")
    move_parser.add_argument("--file", help="File of task IDs, one per line")
    move_parser.add_argument("--force", action="store_true", help="Allow 'in progress' despite unresolved deps")
    move_parser.add_argument("--dry-run", action="store_true")
    move_parser.add_argument("-y", "--yes", action="store_true", help="Skip the bulk-move confirmation prompt")

    block_parser = add_command("block", help="Mark a task blocked, linking the blocker")
    block_parser.add_argument("task_id")
    block_group = block_parser.add_mutually_exclusive_group(required=True)
    block_group.add_argument("--on", help="Existing task ID that's the blocker")
    block_group.add_argument("--new", help="Title for a new placeholder blocker task")
    block_parser.add_argument("--dry-run", action="store_true")

    link_parser = add_command("link", help="Add a dependency link without changing status")
    link_parser.add_argument("task_id")
    link_parser.add_argument("--to", required=True, dest="to_id")
    link_parser.add_argument("--dry-run", action="store_true")

    unlink_parser = add_command("unlink", help="Remove a dependency link")
    unlink_parser.add_argument("task_id")
    unlink_parser.add_argument("--from", required=True, dest="from_id")
    unlink_parser.add_argument("--dry-run", action="store_true")

    split_parser = add_command("split", help="Split a task into two, superseding the original")
    split_parser.add_argument("task_id")
    split_parser.add_argument("--keep", required=True, help="Name for the first child task")
    split_parser.add_argument("--new", required=True, help="Name for the second child task")
    split_parser.add_argument("--dry-run", action="store_true")

    return parser, sub_map


def main() -> int:
    parser, sub_map = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    if args.command == "help":
        return cmd_help(args, parser, sub_map)

    handler = COMMAND_HANDLERS[args.command]
    try:
        return handler(args)
    except RuntimeError as e:
        print(f"{Fore.RED}Error:{Style.RESET_ALL} {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
