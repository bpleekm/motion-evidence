from base64 import b64decode
from io import BytesIO

import pytest
from PIL import Image

from motion_evidence.common import open_run
from motion_evidence.evidence import EvidenceProvider, FrameCache


def test_vfr_exact_pts_roi_and_intermediate_frame_reuse(prepared):
    out, _, frames = prepared
    manifest, catalog, tasks = open_run(out)
    cache = FrameCache(max_bytes=32 * 24 * 3 * 8)
    provider = EvidenceProvider(out, manifest, catalog, tasks[0], memory_cache=cache)
    content = provider.get_frames([frames[2]["frame_id"], frames[6]["frame_id"]])
    pixels = [
        Image.open(BytesIO(b64decode(x["image_url"].split(",", 1)[1])))
        for x in content
        if x["type"] == "input_image"
    ]
    assert [x.size for x in pixels] == [(32, 24), (32, 24)]
    assert abs(pixels[0].getpixel((0, 0))[0] - 40) <= 2
    assert abs(pixels[1].getpixel((0, 0))[0] - 120) <= 2
    assert provider.decode_opens == 1
    # This middle frame was decoded while satisfying the first window, not requested.
    crop = provider.get_frames([frames[4]["frame_id"]], [1, 2, 10, 8])
    assert provider.decode_opens == 1 and provider.cache_hits == 1
    image = Image.open(BytesIO(b64decode(crop[1]["image_url"].split(",", 1)[1])))
    assert image.size == (10, 8)
    assert cache.bytes <= cache.max_bytes


def test_scope_and_cache_integrity(prepared):
    out, _, frames = prepared
    manifest, catalog, tasks = open_run(out)
    restricted = dict(tasks[0], context=[2, 4])
    provider = EvidenceProvider(out, manifest, catalog, restricted)
    with pytest.raises(ValueError, match="outside"):
        provider.get_frames([frames[0]["frame_id"]])
    provider.get_frames([frames[2]["frame_id"]])
    provider._path(frames[2]).write_bytes(b"corrupted-cache")
    with pytest.raises(ValueError, match="changed"):
        provider.get_frames([frames[2]["frame_id"]])


def test_sequence_checks_the_whole_range_before_sparse_sampling(prepared):
    out, _, frames = prepared
    manifest, catalog, tasks = open_run(out)
    provider = EvidenceProvider(out, manifest, catalog, dict(tasks[0], context=[2, 5]))
    # A very large step must not disguise an out-of-context end boundary.
    with pytest.raises(ValueError, match="sequence outside"):
        provider.get_sequence(frames[2]["frame_id"], frames[7]["frame_id"], step_frames=20)
    assert provider.decode_opens == 0 and not provider.seen
