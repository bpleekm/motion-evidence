import json
from types import SimpleNamespace

from motion_evidence.backend import review, settings
from motion_evidence.common import open_run, rows
from motion_evidence.merge import merge
from motion_evidence.prepare import prepare


def test_missing_env_holds_without_inheriting_credentials(prepared, tmp_path, monkeypatch):
    out, _, _ = prepared
    monkeypatch.setenv("OPENAI_API_KEY", "inherited-value-must-not-be-used")
    assert not settings(tmp_path / "missing.env")["OPENAI_API_KEY"]
    assert review(out, tmp_path / "missing.env", 2)["status"] == "hold"
    result = merge(out)
    assert result["status"] == "hold" and result["summary"]["accelerate_ms"] == 0


def test_actual_image_tool_outputs_call_ids_and_resume(prepared, tmp_path):
    out, _, frames = prepared
    _, catalog, tasks = open_run(out)
    task = tasks[0]

    class Responses:
        count = 0

        def create(self, **kwargs):
            self.count += 1
            if self.count == 1:
                call = SimpleNamespace(type="function_call", call_id="call-1", name="get_frames",
                                       arguments=json.dumps({"frame_ids": [frames[1]["frame_id"], frames[6]["frame_id"]], "roi": [0, 0, 16, 12]}))
                return SimpleNamespace(id="response-1", model="fake", status="completed", usage=None, output=[call])
            returned = next(x for x in kwargs["input"] if isinstance(x, dict) and x.get("type") == "function_call_output")
            assert returned["call_id"] == "call-1"
            assert sum(x["type"] == "input_image" for x in returned["output"]) == 2
            assert any(getattr(x, "call_id", None) == "call-1" for x in kwargs["input"])
            payload = {"segments": [{"start_frame_id": catalog.boundary(task["editable"][0]), "end_frame_id_exclusive": catalog.boundary(task["editable"][1]),
                                     "activity": "static", "evidence_frame_ids": [frames[1]["frame_id"], frames[6]["frame_id"]], "reason": "synthetic transcript"}]}
            return SimpleNamespace(id="response-2", model="fake", status="completed", usage=None, output=[], output_text=json.dumps(payload))

    responses = Responses()
    client = SimpleNamespace(responses=responses)
    assert review(out, tmp_path / "missing.env", 2, client=client)["status"] == "complete"
    assert merge(out)["summary"]["accelerate_ms"] == catalog.time(task["editable"][1])
    assert review(out, tmp_path / "missing.env", 2, client=client)["api_attempts"] == 2
    assert responses.count == 2


def test_error_sanitization_and_total_attempt_budget(prepared, tmp_path):
    out, _, _ = prepared

    class Broken:
        count = 0

        def create(self, **kwargs):
            self.count += 1
            raise RuntimeError("credential-example-should-never-enter-logs")

    responses = Broken()
    client = SimpleNamespace(responses=responses)
    assert review(out, tmp_path / "missing.env", 1, client=client)["status"] == "hold"
    assert "credential-example" not in (out / "decisions.jsonl").read_text()
    review(out, tmp_path / "missing.env", 1, client=client)
    assert responses.count == 1
    assert len([x for x in rows(out / "api-calls.jsonl") if x["event"] == "attempt"]) == 1


def test_uncertain_result_remains_hold(prepared, tmp_path):
    out, _, frames = prepared
    _, catalog, tasks = open_run(out)
    task = tasks[0]

    class Uncertain:
        def create(self, **kwargs):
            payload = {"segments": [{"start_frame_id": catalog.boundary(task["editable"][0]), "end_frame_id_exclusive": catalog.boundary(task["editable"][1]),
                                     "activity": "uncertain", "evidence_frame_ids": [frames[0]["frame_id"], frames[6]["frame_id"]], "reason": "insufficient synthetic evidence"}]}
            return SimpleNamespace(id="response-1", model="fake", status="completed", usage=None, output=[], output_text=json.dumps(payload))

    assert review(out, tmp_path / "missing.env", 1, client=SimpleNamespace(responses=Uncertain()))["status"] == "hold"
    assert merge(out)["status"] == "hold"


def test_upstream_missing_remains_hold(tmp_path):
    out = tmp_path / "missing"
    prepare(tmp_path / "absent", out)
    assert review(out, tmp_path / "missing.env", 1)["status"] == "hold"
