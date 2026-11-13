"""Responses image/tool loop. Only metadata is persisted; errors are sanitized."""

import json
from pathlib import Path

from dotenv import dotenv_values

from .common import POLICY, append, fingerprint, load, open_run, rows, write
from .evidence import EvidenceProvider, FrameCache
from .models import ReviewResult, validate_result

INSTRUCTIONS = """Review only the editable video range under the supplied policy.
Static sitting, sleeping and static standing permit acceleration. Person presence alone is not activity.
Classify visible actual activity as active, stable scenes or illumination/background-only variation as static.
If the nature or boundary of an activity cannot be established, request source frames/crops/sequences or mark uncertain.
Use only supplied frame IDs. Context is read-only. Cite at least two delivered, different source times per segment.
Boundaries must cover exactly the editable range, with no overlap or gaps. Newly refined boundaries must have been viewed.
Output concise observable evidence, not speculative intent or identity. Do not describe the provisional detector votes as verified truth.
"""


def tools():
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


def settings(env_file):
    values = dotenv_values(env_file, interpolate=False) if Path(env_file).is_file() else {}

    def get(name):
        return values.get(name) or ""

    return {name: get(name) for name in ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL")}


def review(
    out, env_file, max_api_calls, *, max_rounds=6, max_images=24, max_cache_mb=256, client=None
):
    out = Path(out)
    if load(out / "manifest.json").get("status") == "hold":
        return {"status": "hold", "missing": ["detector_artifacts"]}
    manifest, catalog, tasks = open_run(out)
    if not tasks:
        return {"status": "complete", "decided": 0, "held_attempts": 0, "api_attempts": 0}
    config = settings(env_file)
    missing = [name for name in ("OPENAI_API_KEY", "OPENAI_MODEL") if not config[name]]
    if client is None and missing:
        append(
            out / "holds.jsonl",
            {"task_id": None, "code": "configuration", "requirement": ", ".join(missing)},
        )
        return {"status": "hold", "missing": missing, "pending_tasks": len(tasks)}
    if max_api_calls < 1 or max_rounds < 1 or max_images < 3 or max_cache_mb < 0:
        raise ValueError("positive API/round budget and at least three images required")
    if client is None:
        from openai import OpenAI

        client = OpenAI(
            api_key=config["OPENAI_API_KEY"],
            base_url=config["OPENAI_BASE_URL"] or None,
            max_retries=0,
        )
    model = config["OPENAI_MODEL"] or "test-model"
    version = fingerprint(
        {
            "run": manifest["fingerprint"],
            "policy": POLICY,
            "model": model,
            "instructions": INSTRUCTIONS,
            "endpoint": fingerprint(config["OPENAI_BASE_URL"] or "default"),
            "tools": tools(),
            "result_schema": ReviewResult.model_json_schema(),
        }
    )
    write(
        out / "review-state.json",
        {"manifest": manifest["fingerprint"], "version": version, "model": model},
    )
    existing = rows(out / "decisions.jsonl") if (out / "decisions.jsonl").is_file() else []
    done = {
        x["task_id"]
        for x in existing
        if x["version"] == version
        and x["status"] == "decided"
        and all(s["activity"] != "uncertain" for s in x["result"]["segments"])
    }
    calls = rows(out / "api-calls.jsonl") if (out / "api-calls.jsonl").is_file() else []
    used = sum(x["event"] == "attempt" for x in calls)
    completed = held = 0
    cache = FrameCache(max_bytes=max_cache_mb * 1024 * 1024)
    for task in tasks:
        if task["task_id"] in done:
            continue
        provider = EvidenceProvider(out, manifest, catalog, task, max_images, cache)
        a, b = task["editable"]
        initial = sorted({a, (a + b - 1) // 2, b - 1})
        # Very short tasks use context to supply two distinct observations.
        if len(initial) < 2:
            initial = sorted(
                set(initial + [max(task["context"][0], a - 1), min(task["context"][1] - 1, b)])
            )
        descriptor = {
            "task_id": task["task_id"],
            "policy_version": POLICY,
            "editable": [catalog.boundary(a), catalog.boundary(b)],
            "context": [catalog.boundary(x) for x in task["context"]],
        }
        history = []
        status, reason, decision = "hold", "round_limit", None
        try:
            history.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": json.dumps(descriptor)},
                        *provider.get_frames([catalog.boundary(i) for i in initial]),
                    ],
                }
            )
            for _ in range(max_rounds):
                if used >= max_api_calls:
                    reason = "api_budget"
                    break
                used += 1
                append(
                    out / "api-calls.jsonl",
                    {
                        "event": "attempt",
                        "number": used,
                        "task_id": task["task_id"],
                        "model": model,
                    },
                )
                response = client.responses.create(
                    model=model,
                    instructions=INSTRUCTIONS,
                    input=history,
                    tools=tools(),
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
                append(
                    out / "api-calls.jsonl",
                    {
                        "event": "response",
                        "number": used,
                        "task_id": task["task_id"],
                        "response_id": response.id,
                        "model": getattr(response, "model", model),
                        "usage": usage.model_dump() if usage else None,
                    },
                )
                if getattr(response, "status", "completed") != "completed":
                    reason = "incomplete_response"
                    break
                history.extend(response.output)
                requested = [x for x in response.output if x.type == "function_call"]
                if requested:
                    for call in requested:
                        if call.name not in ("get_frames", "get_sequence"):
                            raise ValueError("unknown evidence tool")
                        arguments = json.loads(call.arguments)
                        append(
                            out / "tools.jsonl",
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
                        history.append(
                            {
                                "type": "function_call_output",
                                "call_id": call.call_id,
                                "output": result,
                            }
                        )
                    continue
                decision = validate_result(
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
        record = {
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
        append(out / "decisions.jsonl", record)
        if status == "decided" and reason is None:
            completed += 1
        else:
            held += 1
            append(
                out / "holds.jsonl",
                {
                    "task_id": task["task_id"],
                    "code": reason,
                    "requirement": "resolve before re-running this task",
                },
            )
        if reason == "api_budget":
            break
    return {
        "status": "complete" if completed + len(done) == len(tasks) else "hold",
        "decided": completed + len(done),
        "held_attempts": held,
        "api_attempts": used,
    }
