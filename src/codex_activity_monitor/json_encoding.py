"""Bounded UTF-8 JSON buffers for internal metadata and query replies."""

import json


def encode_limited(value, max_bytes: int) -> bytes | None:
    if max_bytes < 0:
        raise ValueError('invalid_json_budget')
    encoded = bytearray()
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    for chunk in encoder.iterencode(value):
        remaining = max_bytes-len(encoded)
        if len(chunk) > remaining:  # UTF-8 cannot be shorter than its code point count.
            return None
        chunk = chunk.encode('utf-8')
        if len(chunk) > remaining:
            return None
        encoded.extend(chunk)
    return bytes(encoded)
