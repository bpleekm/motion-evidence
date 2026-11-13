"""Expose stable prepare, review, merge and report commands."""

import argparse
import json
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="motion-evidence")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--detector", type=Path, required=True)
    prepare_parser.add_argument("--out", type=Path, required=True)
    prepare_parser.add_argument("--context-ms", type=int, default=1000)
    prepare_parser.add_argument("--max-task-ms", type=int, default=30000)
    prepare_parser.add_argument("--locks", type=Path)
    prepare_parser.add_argument(
        "--review-range", action="append", default=[], help="additional START_MS:END_MS"
    )
    review_parser = commands.add_parser("review")
    review_parser.add_argument("--run", type=Path, required=True)
    review_parser.add_argument("--env-file", type=Path, default=Path(".env"))
    review_parser.add_argument("--max-api-calls", type=int, required=True)
    review_parser.add_argument("--max-rounds", type=int, default=6)
    review_parser.add_argument("--max-images", type=int, default=24)
    review_parser.add_argument("--max-cache-mb", type=int, default=256)
    for name in ("merge", "report"):
        command_parser = commands.add_parser(name)
        command_parser.add_argument("--run", type=Path, required=True)
    return parser


def main() -> None:
    options = build_parser().parse_args()
    if options.command == "prepare":
        from .planning import prepare_review_run

        ranges = [tuple(map(int, value.split(":"))) for value in options.review_range]
        result = prepare_review_run(
            options.detector,
            options.out,
            context_ms=options.context_ms,
            max_task_ms=options.max_task_ms,
            locks_file=options.locks,
            extra_ranges=ranges,
        )
    elif options.command == "review":
        from .openai_backend import review_tasks

        result = review_tasks(
            options.run,
            options.env_file,
            options.max_api_calls,
            max_rounds=options.max_rounds,
            max_images=options.max_images,
            max_cache_mb=options.max_cache_mb,
        )
    elif options.command == "merge":
        from .timeline import merge_timeline

        value = merge_timeline(options.run)
        result = {
            "status": value["status"],
            **value["summary"],
            "held_tasks": len(value["held_tasks"]),
        }
    else:
        from .artifacts import read_json, read_json_lines

        value = read_json(options.run / "timeline.json")
        result = {
            "status": value["status"],
            **value["summary"],
            "held_tasks": len(value["held_tasks"]),
            "hold_events": len(read_json_lines(options.run / "holds.jsonl"))
            if (options.run / "holds.jsonl").is_file()
            else 0,
        }
        result["upstream_holds"] = len(
            read_json(options.run / "manifest.json").get("upstream_holds", [])
        )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
