"""Turn detector measurements into bounded, reproducible review tasks."""

import json
from bisect import bisect_left, bisect_right
from pathlib import Path

from .artifacts import (
    append_json_line,
    file_sha256,
    json_fingerprint,
    read_json,
    read_json_lines,
    write_json,
)
from .catalog import FrameCatalog
from .intervals import subtract_intervals
from .policy import POLICY_VERSION


def prepare_review_run(
    detector_dir,
    output_dir,
    *,
    context_ms=1000,
    max_task_ms=30000,
    locks_file=None,
    extra_ranges=(),
):
    detector_dir, output_dir = Path(detector_dir), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    required = ("status.json", "source.json", "frames.jsonl", "observations.jsonl")
    if any(not (detector_dir / name).is_file() for name in required):
        return _write_prepare_hold(output_dir, "detector_artifacts", "complete detector output")
    detector_status = read_json(detector_dir / "status.json")
    if detector_status.get("status") != "complete":
        return _write_prepare_hold(
            output_dir, "detector_hold", "complete detector data before preparing review"
        )
    for name, expected in detector_status.get("artifacts", {}).items():
        if Path(name).name != name or file_sha256(detector_dir / name) != expected:
            raise ValueError("detector artifact fingerprint mismatch")
    source = read_json(detector_dir / "source.json")
    if source.get("schema_version") != "motion-detect/v1":
        raise ValueError("unsupported detector contract")
    frames = read_json_lines(detector_dir / "frames.jsonl")
    catalog = FrameCatalog(frames, source)
    observations = read_json_lines(detector_dir / "observations.jsonl")
    if not observations:
        return _write_prepare_hold(output_dir, "observations", "selected frame observations")
    if context_ms < 0 or max_task_ms <= 0:
        raise ValueError("invalid task limits")
    baseline, candidates = [], []
    previous = 0
    for sequence, row in enumerate(observations, 1):
        current = catalog.index(row["frame_id"])
        if (
            row["seq"] != sequence
            or (sequence > 1 and current <= previous)
            or current < previous
            or row["time_ms"] != catalog.time(current)
        ):
            raise ValueError("observation identity/order mismatch")
        activity = (
            "uncertain"
            if row["warming"]
            else "active"
            if row["mog_pass"] or row["motion_pass"]
            else "static"
        )
        if current > previous:
            baseline.append(
                {
                    "start": previous,
                    "end": current,
                    "activity": activity,
                    "source": "detector_provisional",
                }
            )
            if not row["warming"] and (
                row["mog_pass"] != row["motion_pass"]
                or (row["has_candidate"] and not (row["mog_pass"] or row["motion_pass"]))
            ):
                candidates.append([previous, current])
        previous = current
    if previous < len(frames):
        baseline.append(
            {
                "start": previous,
                "end": len(frames),
                "activity": "uncertain",
                "source": "detector_tail",
            }
        )
    locks = []
    if locks_file:
        document = read_json(locks_file)
        if document["policy_version"] != POLICY_VERSION:
            raise ValueError("locks belong to a different policy")
        for segment in document["segments"]:
            start_index, end_index = (
                catalog.index(segment["start_frame_id"]),
                catalog.index(segment["end_frame_id_exclusive"]),
            )
            if not 0 <= start_index < end_index <= len(frames) or segment["action"] not in (
                "retain",
                "accelerate",
            ):
                raise ValueError("invalid lock")
            locks.append(
                {
                    "start": start_index,
                    "end": end_index,
                    "action": segment["action"],
                    "reason": segment["reason"],
                }
            )
        locks.sort(key=lambda x: x["start"])
        if any(a["end"] > b["start"] for a, b in zip(locks, locks[1:])):
            raise ValueError("overlapping locks")
    times = [x["time_ms"] for x in frames]
    for start_ms, end_ms in extra_ranges:
        if not 0 <= start_ms < end_ms <= source["total_ms"]:
            raise ValueError("extra review range outside source")
        candidates.append(
            [
                max(0, bisect_right(times, start_ms) - 1),
                min(len(frames), bisect_left(times, end_ms)),
            ]
        )
    editable = subtract_intervals(candidates, [[x["start"], x["end"]] for x in locks])
    tasks = []
    # Keep hard bounds on task size; contextual image windows may overlap safely.
    for start_index, end_index in editable:
        while start_index < end_index:
            task_end = min(
                end_index,
                max(start_index + 1, bisect_left(times, catalog.time(start_index) + max_task_ms)),
            )
            start_context = max(0, bisect_right(times, catalog.time(start_index) - context_ms) - 1)
            end_context = min(
                len(frames), max(task_end, bisect_left(times, catalog.time(task_end) + context_ms))
            )
            task = {
                "editable": [start_index, task_end],
                "context": [start_context, end_context],
                "policy_version": POLICY_VERSION,
            }
            task["task_id"] = "task-" + json_fingerprint({"source": source, **task})[:20]
            tasks.append(task)
            start_index = task_end
    for name, value in (("baseline.json", baseline), ("locks.json", locks)):
        write_json(output_dir / name, value)
    for name, values in (("frames.jsonl", frames), ("tasks.jsonl", tasks)):
        with (output_dir / name).open("w") as stream:
            for value in values:
                stream.write(json.dumps(value, separators=(",", ":")) + "\n")
    hashes = {
        name: file_sha256(output_dir / name)
        for name in ("baseline.json", "locks.json", "frames.jsonl", "tasks.jsonl")
    }
    manifest = {
        "schema_version": "motion-evidence/v1",
        "policy_version": POLICY_VERSION,
        "status": "ready",
        "source": source,
        "artifact_hashes": hashes,
        "settings": {"context_ms": context_ms, "max_task_ms": max_task_ms},
        "upstream_holds": [detector_status["person"]]
        if detector_status.get("person", {}).get("held")
        else [],
        "summary": {
            "tasks": len(tasks),
            "core_ms": sum(catalog.time(end) - catalog.time(start) for start, end in editable),
            "total_ms": source["total_ms"],
        },
    }
    manifest["fingerprint"] = json_fingerprint(manifest)
    write_json(output_dir / "manifest.json", manifest)
    return manifest["summary"] | {"status": "ready"}


def _write_prepare_hold(output_dir, code, requirement):
    value = {
        "schema_version": "motion-evidence/v1",
        "policy_version": POLICY_VERSION,
        "status": "hold",
        "code": code,
    }
    write_json(output_dir / "manifest.json", value)
    append_json_line(
        output_dir / "holds.jsonl", {"task_id": None, "code": code, "requirement": requirement}
    )
    return value
