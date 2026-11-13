"""Pure half-open interval operations on frame ordinals."""


def merge_intervals(spans):
    merged = []
    for start, end in sorted(spans):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return merged


def subtract_intervals(spans, excluded):
    remaining = []
    for start, end in merge_intervals(spans):
        cursor = start
        for excluded_start, excluded_end in merge_intervals(excluded):
            if excluded_end <= cursor:
                continue
            if excluded_start >= end:
                break
            if excluded_start > cursor:
                remaining.append([cursor, excluded_start])
            cursor = max(cursor, excluded_end)
        if cursor < end:
            remaining.append([cursor, end])
    return remaining
