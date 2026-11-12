from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Segment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    start_frame_id: str
    end_frame_id_exclusive: str
    activity: Literal["static", "active", "uncertain"]
    evidence_frame_ids: list[str] = Field(min_length=2)
    reason: str


class ReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    segments: list[Segment] = Field(min_length=1)


def validate_result(value, catalog, task, seen):
    result = ReviewResult.model_validate(value)
    cursor = task["editable"][0]
    endpoints = set(task["editable"])
    for segment in result.segments:
        a, b = catalog.index(segment.start_frame_id), catalog.index(segment.end_frame_id_exclusive)
        if a != cursor or not a < b <= task["editable"][1]:
            raise ValueError("review contains gap, overlap or editable-range violation")
        if a not in endpoints and segment.start_frame_id not in seen:
            raise ValueError("unseen start boundary")
        if b not in endpoints and segment.end_frame_id_exclusive not in seen:
            raise ValueError("unseen end boundary")
        ids = set(segment.evidence_frame_ids)
        if not ids <= seen or len({catalog.frames[catalog.index(x)]["time_ms"] for x in ids}) < 2:
            raise ValueError("review must cite at least two actually delivered source times")
        cursor = b
    if cursor != task["editable"][1]:
        raise ValueError("review does not cover the whole editable range")
    return result.model_dump()
