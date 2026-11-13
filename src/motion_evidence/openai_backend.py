"""Responses image/tool loop. Only metadata is persisted; errors are sanitized."""

import json
from pathlib import Path

from dotenv import dotenv_values

from .artifacts import (
    append_json_line,
    json_fingerprint,
    load_review_run,
    read_json,
    read_json_lines,
    write_json,
)
from .evidence import EvidenceProvider, FrameCache
from .policy import POLICY_VERSION
from .schemas import ReviewResult, validate_review_result

INSTRUCTIONS = """Review only the editable video range under the supplied policy.
Static sitting, sleeping and static standing permit acceleration. Person presence alone is not activity.
Classify visible actual activity as active, stable scenes or illumination/background-only variation as static.
If the nature or boundary of an activity cannot be established, request source frames/crops/sequences or mark uncertain.
Use only supplied frame IDs. Context is read-only. Cite at least two delivered, different source times per segment.
Boundaries must cover exactly the editable range, with no overlap or gaps. Newly refined boundaries must have been viewed.
Output concise observable evidence, not speculative intent or identity. Do not describe the provisional detector votes as verified truth.
"""


def build_evidence_tools():
    roi = {"type": ["array", "null"], "items": {"type": "integer"}}
    definitions = [
        (
            "get_frames",
            "View original full frames or crops in original pixel coordinates.",
            {"frame_ids": {"type": "array", "items": {"type": "string"}}, "roi": roi},
        ),
        (
            "get_sequence",
            "View ordered frames, including native frames for boundary refinement.",
            {
                "start_frame_id": {"type": "string"},
                "end_frame_id_exclusive": {"type": "string"},
                "step_frames": {"type": "integer"},
                "roi": roi,
            },
        ),
    ]
    return [
        {
            "type": "function",
            "name": name,
            "description": description,
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            },
        }
        for name, description, properties in definitions
    ]


def load_openai_settings(env_file):
    values = dotenv_values(env_file, interpolate=False) if Path(env_file).is_file() else {}

    def get(name):
        return values.get(name) or ""

    return {name: get(name) for name in ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL")}


def review_tasks(
    output_dir,
    env_file,
    max_api_calls,
    *,
    max_rounds=6,
    max_images=24,
    max_cache_mb=256,
    client=None,
):
    output_dir = Path(output_dir)
    if read_json(output_dir / "manifest.json").get("status") == "hold":
        return {"status": "hold", "missing": ["detector_artifacts"]}
    manifest, catalog, tasks = load_review_run(output_dir)
    if not tasks:
        return {"status": "complete", "decided": 0, "held_attempts": 0, "api_attempts": 0}
    settings = load_openai_settings(env_file)
    missing = [name for name in ("OPENAI_API_KEY", "OPENAI_MODEL") if not settings[name]]
    if client is None and missing:
        append_json_line(
            output_dir / "holds.jsonl",
            {"task_id": None, "code": "configuration", "requirement": ", ".join(missing)},
        )
        return {"status": "hold", "missing": missing, "pending_tasks": len(tasks)}
    if max_api_calls < 1 or max_rounds < 1 or max_images < 3 or max_cache_mb < 0:
        raise ValueError("positive API/round budget and at least three images required")
    if client is None:
        from openai import OpenAI

        client = OpenAI(
            api_key=settings["OPENAI_API_KEY"],
            base_url=settings["OPENAI_BASE_URL"] or None,
            max_retries=0,
        )
    model = settings["OPENAI_MODEL"] or "test-model"
    version = json_fingerprint(
        {
            "run": manifest["fingerprint"],
            "policy": POLICY_VERSION,
            "model": model,
            "instructions": INSTRUCTIONS,
            "endpoint": json_fingerprint(settings["OPENAI_BASE_URL"] or "default"),
            "tools": build_evidence_tools(),
            "result_schema": ReviewResult.model_json_schema(),
        }
    )
    write_json(
        output_dir / "review-state.json",
        {"manifest": manifest["fingerprint"], "version": version, "model": model},
    )
    existing_decisions = (
        read_json_lines(output_dir / "decisions.jsonl")
        if (output_dir / "decisions.jsonl").is_file()
        else []
    )
    completed_task_ids = {
        record["task_id"]
        for record in existing_decisions
        if record["version"] == version
        and record["status"] == "decided"
        and all(segment["activity"] != "uncertain" for segment in record["result"]["segments"])
    }
    api_call_events = (
        read_json_lines(output_dir / "api-calls.jsonl")
        if (output_dir / "api-calls.jsonl").is_file()
        else []
    )
    api_attempt_count = sum(event["event"] == "attempt" for event in api_call_events)
    completed_task_count = held_attempt_count = 0
    frame_cache = FrameCache(max_bytes=max_cache_mb * 1024 * 1024)
    for task in tasks:
        if task["task_id"] in completed_task_ids:
            continue
        provider = EvidenceProvider(output_dir, manifest, catalog, task, max_images, frame_cache)
        start_index, end_index = task["editable"]
        initial_indices = sorted({start_index, (start_index + end_index - 1) // 2, end_index - 1})
        # Very short tasks use context to supply two distinct observations.
        if len(initial_indices) < 2:
            initial_indices = sorted(
                set(
                    initial_indices
                    + [
                        max(task["context"][0], start_index - 1),
                        min(task["context"][1] - 1, end_index),
                    ]
                )
            )
        descriptor = {
            "task_id": task["task_id"],
            "policy_version": POLICY_VERSION,
            "editable": [catalog.boundary(start_index), catalog.boundary(end_index)],
            "context": [catalog.boundary(x) for x in task["context"]],
        }
        conversation = []
        status, reason, decision = "hold", "round_limit", None
        try:
            conversation.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": json.dumps(descriptor)},
                        *provider.get_frames([catalog.boundary(i) for i in initial_indices]),
                    ],
                }
            )
            for _ in range(max_rounds):
                if api_attempt_count >= max_api_calls:
                    reason = "api_budget"
                    break
                api_attempt_count += 1
                append_json_line(
                    output_dir / "api-calls.jsonl",
                    {
                        "event": "attempt",
                        "number": api_attempt_count,
                        "task_id": task["task_id"],
                        "model": model,
                    },
                )
                response = client.responses.create(
                    model=model,
                    instructions=INSTRUCTIONS,
                    input=conversation,
                    tools=build_evidence_tools(),
                    parallel_tool_calls=False,
                    text={
                        "format": {
                            "type": "json_schema",
                            "name": "review_result",
                            "strict": True,
                            "schema": ReviewResult.model_json_schema(),
                        }
                    },
                )
                usage = getattr(response, "usage", None)
                append_json_line(
                    output_dir / "api-calls.jsonl",
                    {
                        "event": "response",
                        "number": api_attempt_count,
                        "task_id": task["task_id"],
                        "response_id": response.id,
                        "model": getattr(response, "model", model),
                        "usage": usage.model_dump() if usage else None,
                    },
                )
                if getattr(response, "status", "completed") != "completed":
                    reason = "incomplete_response"
                    break
                conversation.extend(response.output)
                tool_calls = [item for item in response.output if item.type == "function_call"]
                if tool_calls:
                    for call in tool_calls:
                        if call.name not in ("get_frames", "get_sequence"):
                            raise ValueError("unknown evidence tool")
                        arguments = json.loads(call.arguments)
                        append_json_line(
                            output_dir / "tools.jsonl",
                            {"task_id": task["task_id"], "tool": call.name, "arguments": arguments},
                        )
                        try:
                            result = getattr(provider, call.name)(**arguments)
                        except (ValueError, KeyError, TypeError):
                            result = json.dumps(
                                {
                                    "error": "invalid scope, references, crop or image budget; request a valid smaller view"
                                }
                            )
                        conversation.append(
                            {
                                "type": "function_call_output",
                                "call_id": call.call_id,
                                "output": result,
                            }
                        )
                    continue
                decision = validate_review_result(
                    json.loads(response.output_text), catalog, task, provider.seen
                )
                status = "decided"
                reason = (
                    "uncertain_result"
                    if any(s["activity"] == "uncertain" for s in decision["segments"])
                    else None
                )
                break
        except Exception as error:
            # SDK error text can contain credentials or a private request; only classify it.
            reason = "review_error:" + type(error).__name__
        decision_record = {
            "task_id": task["task_id"],
            "manifest": manifest["fingerprint"],
            "version": version,
            "model": model,
            "status": status,
            "hold_code": reason,
            "result": decision if status == "decided" else None,
            "seen_frame_ids": sorted(provider.seen),
            "decode_opens": provider.decode_opens,
            "cache_hits": provider.cache_hits,
        }
        append_json_line(output_dir / "decisions.jsonl", decision_record)
        if status == "decided" and reason is None:
            completed_task_count += 1
        else:
            held_attempt_count += 1
            append_json_line(
                output_dir / "holds.jsonl",
                {
                    "task_id": task["task_id"],
                    "code": reason,
                    "requirement": "resolve before re-running this task",
                },
            )
        if reason == "api_budget":
            break
    return {
        "status": "complete"
        if completed_task_count + len(completed_task_ids) == len(tasks)
        else "hold",
        "decided": completed_task_count + len(completed_task_ids),
        "held_attempts": held_attempt_count,
        "api_attempts": api_attempt_count,
    }
