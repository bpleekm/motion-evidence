from hashlib import file_digest, sha256
from fractions import Fraction
import json
from pathlib import Path

POLICY = "static-all-accelerate/v1"


def load(path):
    return json.loads(Path(path).read_text())


def rows(path):
    with Path(path).open() as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def append(path, value):
    with Path(path).open("a") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def digest(path):
    with Path(path).open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def union(spans):
    result = []
    for a, b in sorted(spans):
        if b <= a:
            continue
        if result and a <= result[-1][1]:
            result[-1][1] = max(b, result[-1][1])
        else:
            result.append([a, b])
    return result


def subtract(spans, excluded):
    result = []
    for a, b in union(spans):
        cursor = a
        for x, y in union(excluded):
            if y <= cursor:
                continue
            if x >= b:
                break
            if x > cursor:
                result.append([cursor, x])
            cursor = max(cursor, y)
        if cursor < b:
            result.append([cursor, b])
    return result


class Catalog:
    def __init__(self, frames, source):
        self.frames = frames
        self.total_ms = source["total_ms"]
        total_ms = self.total_ms
        self.positions = {x["frame_id"]: i for i, x in enumerate(frames)}
        if len(self.positions) != len(frames) or not frames or total_ms <= frames[-1]["time_ms"]:
            raise ValueError("invalid catalog identity or duration")
        if frames[0]["time_ms"] != 0 or any(a["time_ms"] >= b["time_ms"] for a, b in zip(frames, frames[1:])):
            raise ValueError("frame times must start at zero and increase")
        files = {x["file_id"]: x for x in source["files"]}
        for frame in frames:
            item = files[frame["file_id"]]
            expected_id = f"{frame['file_id']}:s{frame['stream_index']}:pts{frame['pts']}"
            expected_time = round(Fraction(item["start_us"], 1000) + (frame["pts"] - item["source_start_pts"]) * Fraction(*frame["time_base"]) * 1000)
            if frame["frame_id"] != expected_id or frame["stream_index"] != item["stream_index"] or frame["time_base"] != item["time_base"] or frame["time_ms"] != expected_time:
                raise ValueError("catalog identity/PTS mapping differs from source")
            if (frame["width"], frame["height"]) != (item["width"], item["height"]):
                raise ValueError("catalog dimensions differ from source")

    def index(self, frame_id):
        if frame_id == "@end":
            return len(self.frames)
        return self.positions[frame_id]

    def boundary(self, index):
        return "@end" if index == len(self.frames) else self.frames[index]["frame_id"]

    def time(self, index):
        return self.total_ms if index == len(self.frames) else self.frames[index]["time_ms"]


def open_run(out):
    out = Path(out)
    manifest = load(out / "manifest.json")
    if manifest.get("status") != "ready":
        raise ValueError("run is on hold; complete its detector data first")
    if manifest["policy_version"] != POLICY or fingerprint({k: v for k, v in manifest.items() if k != "fingerprint"}) != manifest["fingerprint"]:
        raise ValueError("run manifest changed or policy differs")
    for name, expected in manifest["artifact_hashes"].items():
        if digest(out / name) != expected:
            raise ValueError("prepared artifact changed: " + name)
    catalog = Catalog(rows(out / "frames.jsonl"), manifest["source"])
    return manifest, catalog, rows(out / "tasks.jsonl")
