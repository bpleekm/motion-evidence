"""Exact-PTS original RGB views, with per-request scope and persistent PNG reuse."""
from base64 import b64encode
from collections import OrderedDict
from fractions import Fraction
from hashlib import sha256
from io import BytesIO
from pathlib import Path

from .common import append, digest, fingerprint, load, write


class FrameCache:
    def __init__(self, max_bytes=256 * 1024 * 1024):
        self.max_bytes = max_bytes
        self.images = OrderedDict()
        self.bytes = 0
        self.verified = set()

    def get(self, frame_id):
        value = self.images.get(frame_id)
        if value is not None:
            self.images.move_to_end(frame_id)
        return value

    def put(self, frame_id, image):
        size = image.width * image.height * 3
        if size > self.max_bytes or frame_id in self.images:
            return
        while self.images and self.bytes + size > self.max_bytes:
            _, old = self.images.popitem(last=False)
            self.bytes -= old.width * old.height * 3
        self.images[frame_id] = image
        self.bytes += size


class EvidenceProvider:
    def __init__(self, out, manifest, catalog, task, max_images=24, memory_cache=None):
        self.out, self.catalog, self.task = Path(out), catalog, task
        self.files = {x["file_id"]: x for x in manifest["source"]["files"]}
        self.max_images = max_images
        self.seen = set()
        self.views = []
        self.memory = memory_cache if memory_cache is not None else FrameCache()
        self.decode_opens = 0
        self.cache_hits = 0
        self.cache = self.out / "evidence"
        self.cache.mkdir(exist_ok=True)

    def get_frames(self, frame_ids, roi=None):
        if not frame_ids or len(self.views) + len(frame_ids) > self.max_images:
            raise ValueError("image budget exceeded or empty request")
        positions = [self.catalog.index(x) for x in frame_ids]
        a, b = self.task["context"]
        if any(not a <= i < b for i in positions):
            raise ValueError("frame outside read-only task context")
        if len(set(frame_ids)) != len(frame_ids):
            raise ValueError("duplicate frames in request")
        if roi is not None and (len(roi) != 4 or any(type(x) is not int for x in roi)):
            raise ValueError("ROI must contain four original-image integer coordinates")
        self._ensure_originals(positions)
        content = []
        for index in positions:
            ref = self.catalog.frames[index]
            from PIL import Image
            image = Image.open(self._path(ref)).convert("RGB")
            original_size = list(image.size)
            if roi is not None:
                x, y, width, height = roi
                if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > image.width or y + height > image.height:
                    raise ValueError("ROI outside original image")
                image = image.crop((x, y, x + width, y + height))
            stream = BytesIO()
            image.save(stream, format="PNG")
            data = stream.getvalue()
            view = {"frame_id": ref["frame_id"], "time_ms": ref["time_ms"], "pts": ref["pts"], "time_base": ref["time_base"],
                    "original_size": original_size, "view_size": list(image.size), "roi": roi,
                    "image_sha256": sha256(data).hexdigest(), "task_id": self.task["task_id"]}
            append(self.out / "evidence.jsonl", view)
            self.views.append(view)
            self.seen.add(ref["frame_id"])
            import json
            content.extend([{"type": "input_text", "text": json.dumps(view, separators=(",", ":"))},
                            {"type": "input_image", "image_url": "data:image/png;base64," + b64encode(data).decode(), "detail": "original"}])
        return content

    def get_sequence(self, start_frame_id, end_frame_id_exclusive, step_frames=1, roi=None):
        start, end = self.catalog.index(start_frame_id), self.catalog.index(end_frame_id_exclusive)
        if type(step_frames) is not int or step_frames < 1 or end <= start:
            raise ValueError("invalid sequence")
        if end - start > step_frames * self.max_images:
            raise ValueError("sequence exceeds image budget; request a shorter range or larger step")
        return self.get_frames([self.catalog.boundary(i) for i in range(start, end, step_frames)], roi)

    def _path(self, ref):
        return self.cache / (fingerprint({"file": self.files[ref["file_id"]]["sha256"], "stream": ref["stream_index"], "pts": ref["pts"]}) + ".png")

    def _ensure_originals(self, positions):
        import av
        groups = {}
        for index in positions:
            ref = self.catalog.frames[index]
            item = self.files[ref["file_id"]]
            stat = Path(item["path"]).stat()
            identity = (item["sha256"], item["path"], stat.st_size, stat.st_mtime_ns)
            if identity not in self.memory.verified:
                if digest(item["path"]) != item["sha256"]:
                    raise ValueError("source media fingerprint changed")
                self.memory.verified.add(identity)
            path = self._path(ref)
            if path.is_file():
                receipt = load(path.with_suffix(".json"))
                if receipt["image_sha256"] != digest(path) or receipt["frame_id"] != ref["frame_id"]:
                    raise ValueError("cached source image changed")
                self.cache_hits += 1
            elif (image := self.memory.get(ref["frame_id"])) is not None:
                self._persist(ref, image)
                self.cache_hits += 1
            else:
                groups.setdefault(ref["file_id"], []).append(ref)
        for file_id, refs in groups.items():
            item = self.files[file_id]
            expected = {x["pts"]: x for x in refs}
            with av.open(item["path"]) as container:
                self.decode_opens += 1
                stream = container.streams[item["stream_index"]]
                stream.thread_count = 2
                container.seek(min(expected), stream=stream, backward=True, any_frame=False)
                for frame in container.decode(stream):
                    frame_id = f"{file_id}:s{stream.index}:pts{frame.pts}"
                    position = self.catalog.positions.get(frame_id)
                    if position is not None and self.task["context"][0] <= position < self.task["context"][1]:
                        self.memory.put(frame_id, frame.to_image())
                    if frame.pts in expected:
                        ref = expected.pop(frame.pts)
                        if Fraction(frame.time_base) != Fraction(*ref["time_base"]):
                            raise ValueError("decoded time base differs from catalog")
                        image = self.memory.get(frame_id)
                        if image is None:
                            image = frame.to_image()
                        if image.size != (ref["width"], ref["height"]):
                            raise ValueError("decoded dimensions differ from catalog")
                        self._persist(ref, image)
                    if not expected:
                        break
            if expected:
                raise ValueError("requested PTS was not decoded; no approximate replacement is allowed")

    def _persist(self, ref, image):
        path = self._path(ref)
        image.save(path, format="PNG")
        write(path.with_suffix(".json"), {"frame_id": ref["frame_id"], "image_sha256": digest(path)})
