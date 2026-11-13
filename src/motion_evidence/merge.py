"""Deterministic policy and coverage checks; pending AI ranges remain explicit holds."""

from pathlib import Path

from .common import POLICY, load, open_run, rows, write
from .models import validate_result


def action(activity):
    return "accelerate" if activity == "static" else "retain"


def merge(out):
    out = Path(out)
    if load(out / "manifest.json").get("status") == "hold":
        result = {
            "schema_version": "motion-evidence/v1",
            "policy_version": POLICY,
            "status": "hold",
            "held_tasks": [],
            "segments": [],
            "summary": {},
            "missing": ["detector_artifacts"],
        }
        write(out / "timeline.json", result)
        return result
    manifest, catalog, tasks = open_run(out)
    baseline, locks = load(out / "baseline.json"), load(out / "locks.json")
    records = rows(out / "decisions.jsonl") if (out / "decisions.jsonl").is_file() else []
    decisions = {}
    active = load(out / "review-state.json") if (out / "review-state.json").is_file() else None
    for row in records:
        if (
            active
            and row["manifest"] == manifest["fingerprint"]
            and row["version"] == active["version"]
        ):
            decisions[row["task_id"]] = row
    overlays = []
    held_tasks = []
    for task in tasks:
        row = decisions.get(task["task_id"])
        if row and row["status"] == "decided":
            result = validate_result(row["result"], catalog, task, set(row["seen_frame_ids"]))
            for s in result["segments"]:
                overlays.append(
                    {
                        "start": catalog.index(s["start_frame_id"]),
                        "end": catalog.index(s["end_frame_id_exclusive"]),
                        "activity": s["activity"],
                        "source": "ai",
                        "task_id": task["task_id"],
                    }
                )
                if s["activity"] == "uncertain" and task["task_id"] not in held_tasks:
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
    for a, b in zip(boundaries, boundaries[1:]):
        candidates = [(0, x) for x in baseline if x["start"] <= a < x["end"]]
        candidates += [(1, x) for x in overlays if x["start"] <= a < x["end"]]
        candidates += [(2, x) for x in locks if x["start"] <= a < x["end"]]
        if not candidates:
            raise ValueError("timeline coverage gap")
        priority, value = max(candidates, key=lambda x: x[0])
        row = {
            "start_ms": catalog.time(a),
            "end_ms": catalog.time(b),
            "source": "locked" if priority == 2 else value["source"],
            "activity": value.get("activity", "confirmed"),
            "action": value.get("action", action(value.get("activity"))),
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
        "policy_version": POLICY,
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
    write(out / "timeline.json", result)
    return result
