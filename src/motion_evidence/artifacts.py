"""Private run artifacts, fingerprints and immutable prepared-run loading."""

import json
from hashlib import file_digest, sha256
from pathlib import Path

from .catalog import FrameCatalog
from .policy import POLICY_VERSION


def read_json(path):
    return json.loads(Path(path).read_text())


def read_json_lines(path):
    with Path(path).open() as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def append_json_line(path, value):
    with Path(path).open("a") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")


def json_fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_sha256(path):
    with Path(path).open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def load_review_run(output_dir):
    """Verify a prepared snapshot before using its frames, policy or tasks."""
    output_dir = Path(output_dir)
    manifest = read_json(output_dir / "manifest.json")
    if manifest.get("status") != "ready":
        raise ValueError("run is on hold; complete its detector data first")
    if (
        manifest["policy_version"] != POLICY_VERSION
        or json_fingerprint({key: value for key, value in manifest.items() if key != "fingerprint"})
        != manifest["fingerprint"]
    ):
        raise ValueError("run manifest changed or policy differs")
    for name, expected in manifest["artifact_hashes"].items():
        if file_sha256(output_dir / name) != expected:
            raise ValueError("prepared artifact changed: " + name)
    catalog = FrameCatalog(read_json_lines(output_dir / "frames.jsonl"), manifest["source"])
    return manifest, catalog, read_json_lines(output_dir / "tasks.jsonl")
