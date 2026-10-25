"""One complete query reply, within the existing GUI command output budget."""

import json
from typing import Literal

from .json_encoding import encode_limited

MAX_RESPONSE_BYTES = 8*1024*1024  # Includes the terminating newline.
QueryError = Literal['query_unavailable', 'query_too_large', 'query_response_invalid']


def failure(code: QueryError):
    return {'version': 1, 'error': code}


def encode_response(value, *, max_bytes=MAX_RESPONSE_BYTES):
    if max_bytes < 64:
        raise ValueError('invalid_query_response_budget')
    try:
        encoded = encode_limited(value, max_bytes-1)
    except (ValueError, TypeError, RecursionError):
        encoded = encode_limited(failure('query_unavailable'), max_bytes-1)
    if encoded is None:
        encoded = encode_limited(failure('query_too_large'), max_bytes-1)
    assert encoded is not None  # The fixed error envelope fits the minimum accepted budget.
    return encoded+b'\n'


def read_response(reader):
    line = reader.readline(MAX_RESPONSE_BYTES+1)
    if not line:
        return failure('query_unavailable')
    if len(line) > MAX_RESPONSE_BYTES:
        return failure('query_too_large')
    if not line.endswith(b'\n'):
        return failure('query_response_invalid')
    try:
        value = json.loads(line.decode('utf-8'))
    except (ValueError, UnicodeError, RecursionError):
        return failure('query_response_invalid')
    return value if isinstance(value, dict) else failure('query_response_invalid')
