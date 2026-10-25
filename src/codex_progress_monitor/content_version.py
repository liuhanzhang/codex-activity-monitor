"""Stable section-prefix versions; tokens carry no authority to open a source."""

import base64
from dataclasses import dataclass
import hashlib
import json
import re

MAX_TOKEN_BYTES = 16_384
MAX_CURSOR = 2**63-1
DETAIL_VERSION = 2


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def _reject_constant(_):
    raise ValueError('invalid_page_token')


def _semantic_message(value):
    if value is None or isinstance(value, str):
        return True
    return (isinstance(value, dict) and set(value) == {'key', 'params'} and
            isinstance(value['key'], str) and isinstance(value['params'], dict) and
            all(isinstance(k, str) and type(v) in (str, int, float, bool, type(None))
                for k, v in value['params'].items()))


def _metadata(identity, source, state, section, size):
    return {'identity': list(identity), 'source': source, 'state': state, 'format': section['format'],
            'notice': section.get('notice_message') or section.get('notice'),
            'text_message': section.get('text_message'), 'snapshot_chars': size}


def _digest(metadata, text):
    digest = hashlib.sha256(metadata+b'\0')
    digest.update(text.encode('utf-8'))
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class SectionVersion:
    identity: tuple[str, ...]
    snapshot_chars: int
    metadata: bytes
    digest: str
    token: str

    @classmethod
    def create(cls, identity, source, state, section):
        text = section['text']
        metadata = _metadata(identity, source, state, section, len(text))
        encoded = canonical(metadata)
        digest = _digest(encoded, text)
        token = base64.urlsafe_b64encode(canonical({'version': 1, 'metadata': metadata, 'sha256': digest})).rstrip(b'=').decode('ascii')
        if len(token) > MAX_TOKEN_BYTES:
            raise ValueError('page_token_too_large')
        return cls(tuple(identity), len(text), encoded, digest, token)

    @classmethod
    def parse(cls, value):
        if not isinstance(value, str) or not 1 <= len(value) <= MAX_TOKEN_BYTES or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
            raise ValueError('invalid_page_token')
        try:
            raw = base64.b64decode(value+'='*(-len(value)%4), altchars=b'-_', validate=True)
            payload = json.loads(raw, parse_constant=_reject_constant)
            if (not isinstance(payload, dict) or set(payload) != {'version', 'metadata', 'sha256'} or
                    type(payload['version']) is not int or payload['version'] != 1 or canonical(payload) != raw):
                raise ValueError('invalid_page_token')
            meta, digest = payload['metadata'], payload['sha256']
            if (not isinstance(meta, dict) or set(meta) != {'identity', 'source', 'state', 'format', 'notice', 'text_message', 'snapshot_chars'} or
                    not isinstance(meta['identity'], list) or len(meta['identity']) != 5 or
                    not all(isinstance(v, str) and v and '\0' not in v for v in meta['identity']) or
                    type(meta['snapshot_chars']) is not int or not 0 <= meta['snapshot_chars'] <= MAX_CURSOR or
                    not all(isinstance(meta[k], str) and 0 < len(meta[k]) <= 64 for k in ('source', 'state', 'format')) or
                    not _semantic_message(meta['notice']) or not _semantic_message(meta['text_message']) or
                    not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)):
                raise ValueError('invalid_page_token')
            return cls(tuple(meta['identity']), meta['snapshot_chars'], canonical(meta), digest, value)
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            raise ValueError('invalid_page_token') from None

    def matches(self, source, state, section):
        size = self.snapshot_chars
        if size > len(section['text']):
            return False
        metadata = canonical(_metadata(self.identity, source, state, section, size))
        return self.metadata == metadata and self.digest == _digest(metadata, section['text'][:size])


def page_request(scope, thread, turn, phase, section, offset, token):
    """Validate before any history lookup, also for direct in-process callers."""
    if type(offset) is not int or not 0 <= offset <= MAX_CURSOR:
        raise ValueError('invalid_offset')
    if token is None:
        if offset:
            raise ValueError('page_token_required')
        return None
    version = SectionVersion.parse(token)
    if version.identity != (scope, thread, turn, phase, section):
        raise ValueError('page_identity_mismatch')
    if not (0 <= offset < version.snapshot_chars or offset == version.snapshot_chars == 0):
        raise ValueError('invalid_offset')
    return version


def paginate(result, phase, section_id, offset, limit, version):
    """Hash and slice the same immutable rendered string, never a second read."""
    if type(limit) is not int or not 1 <= limit <= 262144:
        raise ValueError('invalid_page_limit')
    result = {**result, 'version': DETAIL_VERSION, 'phase': phase}
    sections = result['sections']
    if version is not None and sections:
        section = next((s for s in sections if s['id'] == section_id), None)
        if section is None or not version.matches(result.get('source'), result['state'], section):
            return {**result, 'state': 'content_changed', 'sections': [], 'attachments': []}
        sections = [section]
    elif section_id is not None:
        sections = [s for s in sections if s['id'] == section_id]
    pages = []
    for section in sections:
        identity = (phase['scope_id'], phase['thread_id'], phase['turn_id'], phase['id'], section['id'])
        current = version or SectionVersion.create(identity, result['source'], result['state'], section)
        start = offset if version is not None else 0
        stop = min(current.snapshot_chars, start+limit)
        pages.append({**section, 'text': section['text'][start:stop], 'offset': start,
                      'total_chars': current.snapshot_chars, 'next_offset': stop if stop < current.snapshot_chars else None,
                      'page_token': current.token})
    return {**result, 'sections': pages}
