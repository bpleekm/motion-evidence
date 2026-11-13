import json
from fractions import Fraction
from hashlib import sha256

import av
import numpy as np
import pytest

from motion_evidence.common import digest, write
from motion_evidence.prepare import prepare


@pytest.fixture
def prepared(tmp_path):
    media = tmp_path / "source.mkv"
    pts = [0, 50, 150, 200, 400, 450, 700, 900]
    with av.open(str(media), "w") as container:
        stream = container.add_stream("ffv1", rate=20)
        stream.width, stream.height, stream.pix_fmt = 32, 24, "yuv420p"
        stream.time_base = Fraction(1, 1000)
        for i, time in enumerate(pts):
            frame = av.VideoFrame.from_ndarray(
                np.full((24, 32, 3), i * 20, np.uint8), format="rgb24"
            )
            frame.pts, frame.time_base = time, Fraction(1, 1000)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    sha = sha256(media.read_bytes()).hexdigest()
    frames = []
    with av.open(str(media)) as container:
        stream = container.streams.video[0]
        for frame in container.decode(stream):
            tb = Fraction(frame.time_base)
            frames.append(
                {
                    "frame_id": f"{sha[:20]}:s{stream.index}:pts{frame.pts}",
                    "file_id": sha[:20],
                    "stream_index": stream.index,
                    "pts": frame.pts,
                    "time_base": [tb.numerator, tb.denominator],
                    "time_ms": round(frame.pts * tb * 1000),
                    "width": 32,
                    "height": 24,
                    "key_frame": bool(frame.key_frame),
                }
            )
    source = {
        "schema_version": "motion-detect/v1",
        "case_id": "synthetic",
        "total_ms": 1000,
        "files": [
            {
                "file_id": sha[:20],
                "sha256": sha,
                "path": str(media),
                "stream_index": 0,
                "time_base": [1, 1000],
                "start_us": 0,
                "source_start_pts": 0,
                "width": 32,
                "height": 24,
            }
        ],
    }
    detector = tmp_path / "detect"
    detector.mkdir()
    write(detector / "source.json", source)
    observations = [
        {
            "frame_id": f["frame_id"],
            "time_ms": f["time_ms"],
            "seq": i + 1,
            "mog_pass": True,
            "motion_pass": False,
            "has_candidate": True,
            "warming": False,
            "personDetections": [{"score": 0.99}],
            "regions": {},
        }
        for i, f in enumerate(frames)
    ]
    for name, values in (("frames.jsonl", frames), ("observations.jsonl", observations)):
        (detector / name).write_text("".join(json.dumps(x) + "\n" for x in values))
    write(
        detector / "status.json",
        {
            "status": "complete",
            "artifacts": {
                name: digest(detector / name)
                for name in ("source.json", "frames.jsonl", "observations.jsonl")
            },
        },
    )
    out = tmp_path / "review"
    prepare(detector, out, context_ms=1000)
    return out, detector, frames
