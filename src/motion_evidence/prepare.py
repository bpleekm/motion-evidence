"""Turn detector measurements into bounded, reproducible review tasks."""
from bisect import bisect_left, bisect_right
import json
from pathlib import Path

from .common import POLICY, Catalog, append, digest, fingerprint, load, rows, subtract, write


def prepare(detector, out, *, context_ms=1000, max_task_ms=30000, locks_file=None, extra_ranges=()):
    detector, out = Path(detector), Path(out)
    out.mkdir(parents=True, exist_ok=False)
    required = ("status.json", "source.json", "frames.jsonl", "observations.jsonl")
    if any(not (detector / name).is_file() for name in required):
        return _hold(out, "detector_artifacts", "complete detector output")
    status = load(detector / "status.json")
    if status.get("status") != "complete":
        return _hold(out, "detector_hold", "complete detector data before preparing review")
    for name, expected in status.get("artifacts", {}).items():
        if Path(name).name != name or digest(detector / name) != expected:
            raise ValueError("detector artifact fingerprint mismatch")
    source = load(detector / "source.json")
    if source.get("schema_version") != "motion-detect/v1":
        raise ValueError("unsupported detector contract")
    frames = rows(detector / "frames.jsonl")
    catalog = Catalog(frames, source["total_ms"])
    observations = rows(detector / "observations.jsonl")
    if not observations:
        return _hold(out, "observations", "selected frame observations")
    if context_ms < 0 or max_task_ms <= 0:
        raise ValueError("invalid task limits")
    baseline, candidates = [], []
    previous = 0
    for row in observations:
        current = catalog.index(row["frame_id"])
        if current < previous or row["time_ms"] != catalog.time(current):
            raise ValueError("observation identity/order mismatch")
        activity = "uncertain" if row["warming"] else "active" if row["mog_pass"] or row["motion_pass"] else "static"
        if current > previous:
            baseline.append({"start": previous, "end": current, "activity": activity, "source": "detector_provisional"})
            if not row["warming"] and (row["mog_pass"] != row["motion_pass"] or (row["has_candidate"] and not (row["mog_pass"] or row["motion_pass"]))):
                candidates.append([previous, current])
        previous = current
    if previous < len(frames):
        baseline.append({"start": previous, "end": len(frames), "activity": "uncertain", "source": "detector_tail"})
    locks = []
    if locks_file:
        document = load(locks_file)
        if document["policy_version"] != POLICY:
            raise ValueError("locks belong to a different policy")
        for row in document["segments"]:
            a, b = catalog.index(row["start_frame_id"]), catalog.index(row["end_frame_id_exclusive"])
            if not 0 <= a < b <= len(frames) or row["action"] not in ("retain", "accelerate"):
                raise ValueError("invalid lock")
            locks.append({"start": a, "end": b, "action": row["action"], "reason": row["reason"]})
        locks.sort(key=lambda x: x["start"])
        if any(a["end"] > b["start"] for a, b in zip(locks, locks[1:])):
            raise ValueError("overlapping locks")
    times = [x["time_ms"] for x in frames]
    for a, b in extra_ranges:
        if not 0 <= a < b <= source["total_ms"]:
            raise ValueError("extra review range outside source")
        candidates.append([max(0, bisect_right(times, a) - 1), min(len(frames), bisect_left(times, b))])
    editable = subtract(candidates, [[x["start"], x["end"]] for x in locks])
    tasks = []
    # Keep hard bounds on task size; contextual image windows may overlap safely.
    for a, b in editable:
        while a < b:
            end = min(b, max(a + 1, bisect_left(times, catalog.time(a) + max_task_ms)))
            start_context = max(0, bisect_right(times, catalog.time(a) - context_ms) - 1)
            end_context = min(len(frames), max(end, bisect_left(times, catalog.time(end) + context_ms)))
            task = {"editable": [a, end], "context": [start_context, end_context], "policy_version": POLICY}
            task["task_id"] = "task-" + fingerprint({"source": source, **task})[:20]
            tasks.append(task)
            a = end
    for name, value in (("baseline.json", baseline), ("locks.json", locks)):
        write(out / name, value)
    for name, values in (("frames.jsonl", frames), ("tasks.jsonl", tasks)):
        with (out / name).open("w") as stream:
            for value in values:
                stream.write(json.dumps(value, separators=(",", ":")) + "\n")
    hashes = {name: digest(out / name) for name in ("baseline.json", "locks.json", "frames.jsonl", "tasks.jsonl")}
    manifest = {"schema_version": "motion-evidence/v1", "policy_version": POLICY, "status": "ready", "source": source,
                "artifact_hashes": hashes, "settings": {"context_ms": context_ms, "max_task_ms": max_task_ms},
                "summary": {"tasks": len(tasks), "core_ms": sum(catalog.time(b) - catalog.time(a) for a, b in editable), "total_ms": source["total_ms"]}}
    manifest["fingerprint"] = fingerprint(manifest)
    write(out / "manifest.json", manifest)
    return manifest["summary"] | {"status": "ready"}


def _hold(out, code, requirement):
    value = {"schema_version": "motion-evidence/v1", "policy_version": POLICY, "status": "hold", "code": code}
    write(out / "manifest.json", value)
    append(out / "holds.jsonl", {"task_id": None, "code": code, "requirement": requirement})
    return value
