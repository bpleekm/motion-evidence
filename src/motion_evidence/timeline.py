"""Deterministic policy and coverage checks; pending AI ranges remain explicit holds."""

from pathlib import Path

from .artifacts import load_review_run, read_json, read_json_lines, write_json
from .policy import POLICY_VERSION, action_for_activity
from .schemas import validate_review_result


def merge_timeline(output_dir):
    output_dir = Path(output_dir)
    if read_json(output_dir / "manifest.json").get("status") == "hold":
        result = {
            "schema_version": "motion-evidence/v1",
            "policy_version": POLICY_VERSION,
            "status": "hold",
            "held_tasks": [],
            "segments": [],
            "summary": {},
            "missing": ["detector_artifacts"],
        }
        write_json(output_dir / "timeline.json", result)
        return result
    manifest, catalog, tasks = load_review_run(output_dir)
    baseline = read_json(output_dir / "baseline.json")
    locks = read_json(output_dir / "locks.json")
    records = (
        read_json_lines(output_dir / "decisions.jsonl")
        if (output_dir / "decisions.jsonl").is_file()
        else []
    )
    decisions = {}
    review_state = (
        read_json(output_dir / "review-state.json")
        if (output_dir / "review-state.json").is_file()
        else None
    )
    for record in records:
        if (
            review_state
            and record["manifest"] == manifest["fingerprint"]
            and record["version"] == review_state["version"]
        ):
            decisions[record["task_id"]] = record
    overlays = []
    held_tasks = []
    for task in tasks:
        decision = decisions.get(task["task_id"])
        if decision and decision["status"] == "decided":
            result = validate_review_result(
                decision["result"], catalog, task, set(decision["seen_frame_ids"])
            )
            for segment in result["segments"]:
                overlays.append(
                    {
                        "start": catalog.index(segment["start_frame_id"]),
                        "end": catalog.index(segment["end_frame_id_exclusive"]),
                        "activity": segment["activity"],
                        "source": "ai",
                        "task_id": task["task_id"],
                    }
                )
                if segment["activity"] == "uncertain" and task["task_id"] not in held_tasks:
                    held_tasks.append(task["task_id"])
        else:
            held_tasks.append(task["task_id"])
            overlays.append(
                {
                    "start": task["editable"][0],
                    "end": task["editable"][1],
                    "activity": "uncertain",
                    "source": "hold",
                    "task_id": task["task_id"],
                }
            )
    boundaries = sorted(
        {
            0,
            len(catalog.frames),
            *[v[k] for v in baseline + overlays + locks for k in ("start", "end")],
        }
    )
    output = []
    for start_index, end_index in zip(boundaries, boundaries[1:]):
        candidates = [(0, x) for x in baseline if x["start"] <= start_index < x["end"]]
        candidates += [(1, x) for x in overlays if x["start"] <= start_index < x["end"]]
        candidates += [(2, x) for x in locks if x["start"] <= start_index < x["end"]]
        if not candidates:
            raise ValueError("timeline coverage gap")
        priority, value = max(candidates, key=lambda x: x[0])
        row = {
            "start_ms": catalog.time(start_index),
            "end_ms": catalog.time(end_index),
            "source": "locked" if priority == 2 else value["source"],
            "activity": value.get("activity", "confirmed"),
            "action": value.get("action", action_for_activity(value.get("activity"))),
            "task_id": value.get("task_id"),
        }
        if output and all(
            output[-1][k] == row[k] for k in ("source", "activity", "action", "task_id")
        ):
            output[-1]["end_ms"] = row["end_ms"]
        else:
            output.append(row)
    if (
        output[0]["start_ms"] != 0
        or output[-1]["end_ms"] != manifest["source"]["total_ms"]
        or any(a["end_ms"] != b["start_ms"] for a, b in zip(output, output[1:]))
    ):
        raise ValueError("invalid merged coverage")
    result = {
        "schema_version": "motion-evidence/v1",
        "policy_version": POLICY_VERSION,
        "manifest": manifest["fingerprint"],
        "status": "hold" if held_tasks else "complete",
        "held_tasks": held_tasks,
        "segments": output,
        "summary": {
            "total_ms": manifest["source"]["total_ms"],
            "retain_ms": sum(
                x["end_ms"] - x["start_ms"] for x in output if x["action"] == "retain"
            ),
            "accelerate_ms": sum(
                x["end_ms"] - x["start_ms"] for x in output if x["action"] == "accelerate"
            ),
            "unresolved_ms": sum(
                x["end_ms"] - x["start_ms"] for x in output if x["activity"] == "uncertain"
            ),
        },
    }
    write_json(output_dir / "timeline.json", result)
    return result
