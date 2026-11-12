import json

import pytest

from motion_evidence.common import POLICY, digest, load, open_run, rows, write
from motion_evidence.merge import merge
from motion_evidence.models import validate_result
from motion_evidence.prepare import prepare


def test_lock_splits_editable_and_is_immutable(prepared, tmp_path):
    _, detector, frames = prepared
    path = tmp_path / "locks.json"
    write(path, {"policy_version": POLICY, "segments": [{"start_frame_id": frames[2]["frame_id"], "end_frame_id_exclusive": frames[4]["frame_id"], "action": "accelerate", "reason": "synthetic lock"}]})
    out = tmp_path / "locked"
    prepare(detector, out, locks_file=path)
    _, _, tasks = open_run(out)
    assert len(tasks) == 2
    assert all(not (a < 4 and b > 2) for a, b in (x["editable"] for x in tasks))
    timeline = merge(out)
    assert timeline["status"] == "hold"
    assert any(x["source"] == "locked" and x["start_ms"] == 150 and x["end_ms"] == 400 and x["action"] == "accelerate" for x in timeline["segments"])


def test_person_presence_is_not_a_review_trigger(prepared, tmp_path):
    _, detector, _ = prepared
    observations = rows(detector / "observations.jsonl")
    for row in observations:
        row.update(mog_pass=False, motion_pass=False, has_candidate=False)
    (detector / "observations.jsonl").write_text("".join(json.dumps(x) + "\n" for x in observations))
    status = load(detector / "status.json")
    status["artifacts"]["observations.jsonl"] = digest(detector / "observations.jsonl")
    write(detector / "status.json", status)
    out = tmp_path / "static-person"
    prepare(detector, out)
    assert not open_run(out)[2]


def test_rejects_unseen_evidence_and_gaps(prepared):
    out, _, frames = prepared
    _, catalog, tasks = open_run(out)
    task = tasks[0]
    segment = {"start_frame_id": catalog.boundary(task["editable"][0]), "end_frame_id_exclusive": catalog.boundary(task["editable"][1]),
               "activity": "static", "evidence_frame_ids": [frames[0]["frame_id"], frames[1]["frame_id"]], "reason": "synthetic"}
    with pytest.raises(ValueError):
        validate_result({"segments": [segment]}, catalog, task, {frames[0]["frame_id"]})
    segment["start_frame_id"] = frames[1]["frame_id"]
    with pytest.raises(ValueError, match="gap"):
        validate_result({"segments": [segment]}, catalog, task, {x["frame_id"] for x in frames})


def test_missing_detector_holds_and_tampering_rejects(prepared, tmp_path):
    assert prepare(tmp_path / "absent", tmp_path / "missing")["status"] == "hold"
    assert merge(tmp_path / "missing")["status"] == "hold"
    out, _, _ = prepared
    (out / "tasks.jsonl").write_text("")
    with pytest.raises(ValueError, match="changed"):
        open_run(out)
