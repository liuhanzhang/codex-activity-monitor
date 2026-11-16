"""The public Gemma 4 tokenizer is a local estimate for covered Gemini models."""

from functools import lru_cache
import hashlib
import os
from pathlib import Path
import tempfile
from urllib.request import urlopen


ENCODING = 'gemma4'
FILE = 'gemma-4-E4B-it-tokenizer.json'
REVISION = '4ecc2ba63bcac23ba4a767fc87025b1450f84c9c'
URL = f'https://huggingface.co/google/gemma-4-E4B-it/resolve/{REVISION}/tokenizer.json'
SHA256 = '91d9cf86906f78e197021fda88214d448e5c120665d0243a8e776e046986c6bc'
LICENSE = 'https://www.apache.org/licenses/LICENSE-2.0.txt'
MODELS = ('gemini-', 'gemma-')


def cache_path():
    configured = os.environ.get('TIKTOKEN_CACHE_DIR')
    return Path(configured)/FILE if configured else Path(__file__).resolve().parents[2]/'.local/tokenizer-cache'/FILE


def encoding_for_model(model):
    normalized = model.lower().replace(' ', '-')
    if any(normalized.startswith(name) for name in MODELS) and cache_path().is_file():
        return ENCODING
    return 'o200k_base'


def _checksum(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


@lru_cache(maxsize=1)
def _load(path):
    if _checksum(path) != SHA256:
        raise ValueError('gemma_tokenizer_checksum')
    from tokenizers import Tokenizer
    return Tokenizer.from_file(str(path))


def count(text):
    return len(_load(cache_path()).encode(text, add_special_tokens=False).ids)


def prepare(cache, notices=None):
    """Fetch and verify the immutable asset during builds, never in the collector."""
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    path = cache/FILE
    if not path.is_file() or _checksum(path) != SHA256:
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as handle:
            temporary = Path(handle.name)
        try:
            with urlopen(URL, timeout=45) as response, temporary.open('wb') as handle:
                remaining = 40 * 1024 * 1024
                while chunk := response.read(64 * 1024):
                    remaining -= len(chunk)
                    if remaining < 0:
                        raise ValueError('gemma_tokenizer_too_large')
                    handle.write(chunk)
            if _checksum(temporary) != SHA256:
                raise ValueError('gemma_tokenizer_checksum')
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    if notices is not None:
        folder = Path(notices)/'Gemma4Tokenizer'
        folder.mkdir(parents=True, exist_ok=True)
        with urlopen(LICENSE, timeout=20) as response:
            (folder/'LICENSE.txt').write_bytes(response.read(32 * 1024))
        (folder/'NOTICE.txt').write_text(
            f'Gemma 4 E4B IT tokenizer from Google DeepMind. Licensed under Apache 2.0.\n{URL}\nSHA256 {SHA256}\n')
    return path
