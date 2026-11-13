"""Source-validated frame identities and exact timeline boundaries."""

from fractions import Fraction
from typing import Any


class FrameCatalog:
    def __init__(self, frames: list[dict[str, Any]], source_manifest: dict[str, Any]):
        self.frames = frames
        self.total_ms = source_manifest["total_ms"]
        self.positions = {frame["frame_id"]: index for index, frame in enumerate(frames)}
        if (
            len(self.positions) != len(frames)
            or not frames
            or self.total_ms <= frames[-1]["time_ms"]
        ):
            raise ValueError("invalid catalog identity or duration")
        if frames[0]["time_ms"] != 0 or any(
            previous["time_ms"] >= current["time_ms"]
            for previous, current in zip(frames, frames[1:])
        ):
            raise ValueError("frame times must start at zero and increase")

        source_files = {
            source_file["file_id"]: source_file for source_file in source_manifest["files"]
        }
        for frame in frames:
            source_file = source_files[frame["file_id"]]
            expected_id = f"{frame['file_id']}:s{frame['stream_index']}:pts{frame['pts']}"
            expected_time = round(
                Fraction(source_file["start_us"], 1000)
                + (frame["pts"] - source_file["source_start_pts"])
                * Fraction(*frame["time_base"])
                * 1000
            )
            if (
                frame["frame_id"] != expected_id
                or frame["stream_index"] != source_file["stream_index"]
                or frame["time_base"] != source_file["time_base"]
                or frame["time_ms"] != expected_time
            ):
                raise ValueError("catalog identity/PTS mapping differs from source")
            if (frame["width"], frame["height"]) != (source_file["width"], source_file["height"]):
                raise ValueError("catalog dimensions differ from source")

    def index(self, frame_id: str) -> int:
        return len(self.frames) if frame_id == "@end" else self.positions[frame_id]

    def boundary(self, index: int) -> str:
        return "@end" if index == len(self.frames) else self.frames[index]["frame_id"]

    def time(self, index: int) -> int:
        return self.total_ms if index == len(self.frames) else self.frames[index]["time_ms"]
