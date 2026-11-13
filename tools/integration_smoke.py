"""Exercise peer CLIs with synthetic media and an explicitly simulated AI transport."""

import argparse
import json
import os
import subprocess
import sys
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace


def generate(path):
    import av
    import numpy as np

    with av.open(str(path), "w") as container:
        stream = container.add_stream("libx264", rate=20)
        stream.width, stream.height, stream.pix_fmt = 96, 64, "yuv420p"
        stream.time_base = Fraction(1, 20)
        stream.options = {
            "preset": "ultrafast",
            "tune": "zerolatency",
            "x264-params": "keyint=20:min-keyint=20:scenecut=0",
        }
        for index in range(120):
            pixels = np.full((64, 96, 3), 40, np.uint8)
            if 30 <= index < 70:
                x = 4 + (index - 30) % 50
                pixels[20:40, x : x + 20] = 210
            if 70 <= index < 90:
                pixels[:] = 40 if index % 2 else 120
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            frame.pts, frame.time_base = index, Fraction(1, 20)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


def cli(module, arguments, python_path, cwd):
    env = dict(os.environ, PYTHONPATH=str(python_path), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, "-B", "-m", module, *map(str, arguments)],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"{module} failed; stdout={result.stdout!r}; stderr={result.stderr!r}")
    return json.loads(result.stdout)


class SyntheticResponses:
    """Only validates the software path. It does not judge visual activity."""

    def __init__(self, catalog):
        self.catalog = catalog

    def create(self, **kwargs):
        descriptor = json.loads(kwargs["input"][0]["content"][0]["text"])
        start, end = descriptor["editable"]
        a, b = self.catalog.index(start), self.catalog.index(end)
        returned = [
            item
            for item in kwargs["input"]
            if isinstance(item, dict) and item.get("type") == "function_call_output"
        ]
        if not returned:
            step = max(1, (b - a) // 3)
            call = SimpleNamespace(
                type="function_call",
                call_id="synthetic-sequence",
                name="get_sequence",
                arguments=json.dumps(
                    {
                        "start_frame_id": start,
                        "end_frame_id_exclusive": end,
                        "step_frames": step,
                        "roi": None,
                    }
                ),
            )
            return SimpleNamespace(
                id="synthetic-tool-response",
                model="synthetic-replay",
                status="completed",
                usage=None,
                output=[call],
            )
        output = returned[-1]["output"]
        if not isinstance(output, list) or not any(
            item["type"] == "input_image" for item in output
        ):
            raise AssertionError("synthetic tool did not receive actual images")
        frame_ids = [
            json.loads(item["text"])["frame_id"] for item in output if item["type"] == "input_text"
        ]
        if len(frame_ids) < 2:
            # Initial overviews also carry real original-frame IDs.
            frame_ids += [
                json.loads(item["text"])["frame_id"]
                for item in kwargs["input"][0]["content"][1:]
                if item["type"] == "input_text"
            ]
        result = {
            "segments": [
                {
                    "start_frame_id": start,
                    "end_frame_id_exclusive": end,
                    "activity": "static",
                    "evidence_frame_ids": list(dict.fromkeys(frame_ids)),
                    "reason": "Synthetic protocol replay; not an AI visual judgment.",
                }
            ]
        }
        return SimpleNamespace(
            id="synthetic-final-response",
            model="synthetic-replay",
            status="completed",
            usage=None,
            output=[],
            output_text=json.dumps(result),
        )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--detect-repo", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--backend", choices=("cpu-reference", "native-gpu"), default="cpu-reference")
    p.add_argument("--native-build", type=Path)
    args = p.parse_args()
    root = args.out.resolve()
    root.mkdir(parents=True, exist_ok=False)
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo / "src"))
    from motion_evidence.artifacts import load_review_run, read_json, read_json_lines, write_json
    from motion_evidence.openai_backend import review_tasks
    from motion_evidence.policy import POLICY_VERSION

    video = root / "synthetic.mp4"
    generate(video)
    env_file = root / "empty.env"
    env_file.write_text("OPENAI_API_KEY=\nOPENAI_MODEL=\nOPENAI_BASE_URL=\n")
    env_file.chmod(0o600)
    detect_args = ["run", "--input", video, "--out", root / "detect", "--backend", args.backend]
    if args.native_build:
        detect_args += ["--native-build", args.native_build.resolve()]
    detected = cli("motion_detect.cli", detect_args, args.detect_repo.resolve() / "src", root)
    assert (
        detected["status"] == "complete"
        and detected["decoded_frames"] == 120
        and detected["selected_frames"] == 30
    )
    indexed = read_json_lines(root / "detect/frames.jsonl")
    locks = root / "locks.json"
    write_json(
        locks,
        {
            "policy_version": POLICY_VERSION,
            "segments": [
                {
                    "start_frame_id": indexed[0]["frame_id"],
                    "end_frame_id_exclusive": indexed[10]["frame_id"],
                    "action": "accelerate",
                    "reason": "Synthetic confirmed range.",
                }
            ],
        },
    )
    prepared = cli(
        "motion_evidence.cli",
        [
            "prepare",
            "--detector",
            root / "detect",
            "--out",
            root / "review",
            "--locks",
            locks,
            "--review-range",
            "1200:1800",
        ],
        repo / "src",
        root,
    )
    assert prepared["tasks"] > 0
    held = cli(
        "motion_evidence.cli",
        ["review", "--run", root / "review", "--env-file", env_file, "--max-api-calls", "1"],
        repo / "src",
        root,
    )
    assert held["status"] == "hold"
    cli("motion_evidence.cli", ["merge", "--run", root / "review"], repo / "src", root)
    before = read_json(root / "review/timeline.json")
    assert before["status"] == "hold"
    _, catalog, tasks = load_review_run(root / "review")
    simulated = review_tasks(
        root / "review",
        env_file,
        max_api_calls=2 * len(tasks),
        client=SimpleNamespace(responses=SyntheticResponses(catalog)),
    )
    assert simulated["status"] == "complete"
    cli("motion_evidence.cli", ["merge", "--run", root / "review"], repo / "src", root)
    after = read_json(root / "review/timeline.json")
    assert after["status"] == "complete" and after["summary"]["total_ms"] == 6000
    assert any(
        x["source"] == "locked"
        and x["start_ms"] == 0
        and x["end_ms"] == 500
        and x["action"] == "accelerate"
        for x in after["segments"]
    )
    assert after["summary"]["retain_ms"] + after["summary"]["accelerate_ms"] == 6000
    missing = cli(
        "motion_detect.cli",
        ["run", "--input", root / "absent.mp4", "--out", root / "missing-detect"],
        args.detect_repo.resolve() / "src",
        root,
    )
    assert missing["status"] == "hold"
    assert (
        cli(
            "motion_evidence.cli",
            ["prepare", "--detector", root / "missing-detect", "--out", root / "missing-review"],
            repo / "src",
            root,
        )["status"]
        == "hold"
    )
    assert (
        cli("motion_evidence.cli", ["merge", "--run", root / "missing-review"], repo / "src", root)[
            "status"
        ]
        == "hold"
    )
    summary = {
        "status": "passed",
        "detector_backend": args.backend,
        "decoded_frames": 120,
        "selected_frames": 30,
        "tasks": len(tasks),
        "timeline_coverage_ms": 6000,
        "locked_range_unchanged": True,
        "configuration_hold_then_replay_complete": True,
        "missing_input_hold": True,
        "review_execution": "synthetic protocol replay; no real OpenAI request or visual-quality claim",
    }
    write_json(root / "integration.json", summary)
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
