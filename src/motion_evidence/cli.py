import argparse
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser(prog="motion-evidence")
    sub = p.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--detector", type=Path, required=True)
    prepare.add_argument("--out", type=Path, required=True)
    prepare.add_argument("--context-ms", type=int, default=1000)
    prepare.add_argument("--max-task-ms", type=int, default=30000)
    prepare.add_argument("--locks", type=Path)
    prepare.add_argument("--review-range", action="append", default=[], help="additional START_MS:END_MS")
    for name in ("merge", "report"):
        command = sub.add_parser(name)
        command.add_argument("--run", type=Path, required=True)
    args = p.parse_args()
    if args.command == "prepare":
        from .prepare import prepare as execute
        ranges = [tuple(map(int, x.split(":"))) for x in args.review_range]
        result = execute(args.detector, args.out, context_ms=args.context_ms, max_task_ms=args.max_task_ms, locks_file=args.locks, extra_ranges=ranges)
    elif args.command == "merge":
        from .merge import merge
        value = merge(args.run)
        result = {"status": value["status"], **value["summary"], "held_tasks": len(value["held_tasks"])}
    else:
        from .common import load, rows
        value = load(args.run / "timeline.json")
        result = {"status": value["status"], **value["summary"], "held_tasks": len(value["held_tasks"]),
                  "hold_events": len(rows(args.run / "holds.jsonl")) if (args.run / "holds.jsonl").is_file() else 0}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
