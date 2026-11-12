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
    review = sub.add_parser("review")
    review.add_argument("--run", type=Path, required=True)
    review.add_argument("--env-file", type=Path, default=Path(".env"))
    review.add_argument("--max-api-calls", type=int, required=True)
    review.add_argument("--max-rounds", type=int, default=6)
    review.add_argument("--max-images", type=int, default=24)
    review.add_argument("--max-cache-mb", type=int, default=256)
    for name in ("merge", "report"):
        command = sub.add_parser(name)
        command.add_argument("--run", type=Path, required=True)
    args = p.parse_args()
    if args.command == "prepare":
        from .prepare import prepare as execute
        ranges = [tuple(map(int, x.split(":"))) for x in args.review_range]
        result = execute(args.detector, args.out, context_ms=args.context_ms, max_task_ms=args.max_task_ms, locks_file=args.locks, extra_ranges=ranges)
    elif args.command == "review":
        from .backend import review as execute
        result = execute(args.run, args.env_file, args.max_api_calls, max_rounds=args.max_rounds, max_images=args.max_images, max_cache_mb=args.max_cache_mb)
    elif args.command == "merge":
        from .merge import merge
        value = merge(args.run)
        result = {"status": value["status"], **value["summary"], "held_tasks": len(value["held_tasks"])}
    else:
        from .common import load, rows
        value = load(args.run / "timeline.json")
        result = {"status": value["status"], **value["summary"], "held_tasks": len(value["held_tasks"]),
                  "hold_events": len(rows(args.run / "holds.jsonl")) if (args.run / "holds.jsonl").is_file() else 0}
        result["upstream_holds"] = len(load(args.run / "manifest.json").get("upstream_holds", []))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
