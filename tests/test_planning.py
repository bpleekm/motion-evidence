import json

import pytest

from motion_evidence.artifacts import (
    file_sha256,
    load_review_run,
    read_json,
    read_json_lines,
    write_json,
)
from motion_evidence.catalog import FrameCatalog
from motion_evidence.planning import prepare_review_run
from motion_evidence.policy import POLICY_VERSION
from motion_evidence.schemas import validate_review_result
from motion_evidence.timeline import merge_timeline


def test_lock_splits_editable_and_is_immutable(prepared, tmp_path):
    _, detector, frames = prepared
    path = tmp_path / "locks.json"
    write_json(
        path,
        {
            "policy_version": POLICY_VERSION,
            "segments": [
                {
                    "start_frame_id": frames[2]["frame_id"],
                    "end_frame_id_exclusive": frames[4]["frame_id"],
                    "action": "accelerate",
                    "reason": "synthetic lock",
                }
            ],
        },
    )
    out = tmp_path / "locked"
    prepare_review_run(detector, out, locks_file=path)
    _, _, tasks = load_review_run(out)
    assert len(tasks) == 2
    assert all(not (a < 4 and b > 2) for a, b in (x["editable"] for x in tasks))
    timeline = merge_timeline(out)
    assert timeline["status"] == "hold"
    assert any(
        x["source"] == "locked"
        and x["start_ms"] == 150
        and x["end_ms"] == 400
        and x["action"] == "accelerate"
        for x in timeline["segments"]
    )


def test_person_presence_is_not_a_review_trigger(prepared, tmp_path):
    _, detector, _ = prepared
    observations = read_json_lines(detector / "observations.jsonl")
    for row in observations:
        row.update(mog_pass=False, motion_pass=False, has_candidate=False)
    (detector / "observations.jsonl").write_text(
        "".join(json.dumps(x) + "\n" for x in observations)
    )
    status = read_json(detector / "status.json")
    status["artifacts"]["observations.jsonl"] = file_sha256(detector / "observations.jsonl")
    write_json(detector / "status.json", status)
    out = tmp_path / "static-person"
    prepare_review_run(detector, out)
    assert not load_review_run(out)[2]


def test_rejects_unseen_evidence_and_gaps(prepared):
    out, _, frames = prepared
    _, catalog, tasks = load_review_run(out)
    task = tasks[0]
    segment = {
        "start_frame_id": catalog.boundary(task["editable"][0]),
        "end_frame_id_exclusive": catalog.boundary(task["editable"][1]),
        "activity": "static",
        "evidence_frame_ids": [frames[0]["frame_id"], frames[1]["frame_id"]],
        "reason": "synthetic",
    }
    with pytest.raises(ValueError):
        validate_review_result({"segments": [segment]}, catalog, task, {frames[0]["frame_id"]})
    segment["start_frame_id"] = frames[1]["frame_id"]
    with pytest.raises(ValueError, match="gap"):
        validate_review_result(
            {"segments": [segment]}, catalog, task, {x["frame_id"] for x in frames}
        )


def test_missing_detector_holds_and_tampering_rejects(prepared, tmp_path):
    assert prepare_review_run(tmp_path / "absent", tmp_path / "missing")["status"] == "hold"
    assert merge_timeline(tmp_path / "missing")["status"] == "hold"
    out, _, _ = prepared
    (out / "tasks.jsonl").write_text("")
    with pytest.raises(ValueError, match="changed"):
        load_review_run(out)


def update_detector(detector, name, values):
    (detector / name).write_text("".join(json.dumps(value) + "\n" for value in values))
    status = read_json(detector / "status.json")
    status["artifacts"][name] = file_sha256(detector / name)
    write_json(detector / "status.json", status)


@pytest.mark.parametrize(
    "field,value",
    [
        ("frame_id", "different-frame"),
        ("stream_index", 1),
        ("time_base", [1, 2000]),
        ("time_ms", 151),
        ("width", 33),
    ],
)
def test_catalog_rejects_source_mismatch_despite_updated_artifact_hash(
    prepared, tmp_path, field, value
):
    _, detector, frames = prepared
    changed = [dict(frame) for frame in frames]
    changed[2][field] = value
    update_detector(detector, "frames.jsonl", changed)
    with pytest.raises(ValueError, match="catalog"):
        prepare_review_run(detector, tmp_path / "wrong-catalog")


@pytest.mark.parametrize("fault", ["sequence", "duplicate-frame"])
def test_observation_order_is_checked(prepared, tmp_path, fault):
    _, detector, _ = prepared
    observed = read_json_lines(detector / "observations.jsonl")
    if fault == "sequence":
        observed[1]["seq"] = observed[0]["seq"]
    else:
        observed[1].update(frame_id=observed[0]["frame_id"], time_ms=observed[0]["time_ms"])
    update_detector(detector, "observations.jsonl", observed)
    with pytest.raises(ValueError, match="observation"):
        prepare_review_run(detector, tmp_path / "wrong-order")


def test_catalog_uses_file_offset_and_native_time_base():
    files = [
        {
            "file_id": "first",
            "stream_index": 1,
            "time_base": [1, 20],
            "start_us": 0,
            "source_start_pts": 10,
            "width": 16,
            "height": 12,
        },
        {
            "file_id": "second",
            "stream_index": 0,
            "time_base": [1, 1000],
            "start_us": 100000,
            "source_start_pts": 500,
            "width": 16,
            "height": 12,
        },
    ]
    frames = []
    for item, points in zip(files, ((10, 11), (500, 575))):
        for pts, ms in zip(points, (0, 50) if item["file_id"] == "first" else (100, 175)):
            frames.append(
                {
                    "frame_id": f"{item['file_id']}:s{item['stream_index']}:pts{pts}",
                    "file_id": item["file_id"],
                    "stream_index": item["stream_index"],
                    "pts": pts,
                    "time_base": item["time_base"],
                    "time_ms": ms,
                    "width": 16,
                    "height": 12,
                }
            )
    catalog = FrameCatalog(frames, {"total_ms": 250, "files": files})
    assert catalog.time(catalog.index("second:s0:pts575")) == 175
    assert catalog.time(catalog.index("@end")) == 250


def test_upstream_person_hold_survives_prepare_and_report(prepared, tmp_path, monkeypatch, capsys):
    from motion_evidence.cli import main

    _, detector, _ = prepared
    status = read_json(detector / "status.json")
    status["person"] = {
        "backend": "opencl-fp32",
        "requested": 2,
        "completed": 0,
        "held": 2,
        "hold_code": "person_model",
    }
    write_json(detector / "status.json", status)
    out = tmp_path / "upstream-hold"
    prepare_review_run(detector, out)
    manifest, _, _ = load_review_run(out)
    assert manifest["upstream_holds"] == [status["person"]]
    merge_timeline(out)
    monkeypatch.setattr("sys.argv", ["motion-evidence", "report", "--run", str(out)])
    main()
    assert json.loads(capsys.readouterr().out)["upstream_holds"] == 1
