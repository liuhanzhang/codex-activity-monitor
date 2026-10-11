#!/usr/bin/env python3
"""Measure local token counts without exporting original experimental text."""

import argparse
from collections import Counter
from datetime import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import random
import resource
import statistics
import subprocess
import sys
import time

import tiktoken
import tiktoken.model


ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault('TIKTOKEN_CACHE_DIR', str(ROOT/'.local/product-preflight/tokenizer-cache'))


def encoding():
    return tiktoken.get_encoding('o200k_base')


def bench(size, streams):
    enc = encoding()
    base = 'def normalize(value: str) -> str:\n    """清理字符串并返回结果 🧪"""\n    return value.strip().casefold()\n'
    text = (base * (size // len(base.encode()) + 2)).encode()[:size].decode('utf-8', errors='ignore')
    # Independent buffers, serially recomputed by one snapshot worker.
    texts = [text[:-1] + str(index) for index in range(streams)]
    durations = []
    for _ in range(3):
        begin = time.perf_counter()
        for value in texts:
            enc.encode_ordinary(value)
        durations.append((time.perf_counter()-begin)*1000)
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform != 'darwin':
        peak *= 1024
    return {'bytes_per_stream': len(text.encode()), 'streams': streams,
            'batch_median_ms': statistics.median(durations), 'batch_max_ms': max(durations),
            'fits_250ms_refresh': max(durations) < 250, 'process_peak_rss_bytes': peak,
            'workload_scope': 'independent text buffers; serial snapshot counts, not a live arrival queue',
            'memory_scope': 'whole benchmark child including tokenizer vocabulary'}


def load_samples():
    report = json.loads((ROOT/'analysis/upstream-stream/verification.json').read_text())
    path = Path(report['trace_log'])
    deltas, finals = {}, {}
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt') as source:
        for line in source:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            message = row.get('fields', {}).get('message', '')
            if not message.startswith('Received message '):
                continue
            try:
                event = json.loads(message[17:])
            except ValueError:
                continue
            kind = event.get('type')
            if kind in ('response.custom_tool_call_input.delta', 'response.output_text.delta',
                        'response.reasoning_summary_text.delta'):
                key = (event['item_id'], kind, event.get('summary_index', event.get('content_index', 0)))
                deltas.setdefault(key, []).append(event['delta'])
            elif kind == 'response.output_item.done':
                finals[event['item']['id']] = event['item']
    samples = []
    for key, chunks in deltas.items():
        if key[0] in finals:
            samples.append((key[1], chunks))
    samples.extend((label, [text]) for label, text in [
        ('unicode', '中文测试 👨‍👩‍👧‍👦 🧪 e\u0301 北京欢迎你！'),
        ('boundary', 'antidisestablishmentarianism is a counterintuitive tokenization example.'),
        ('code', 'def greet(name: str) -> str:\n    return f"Hello, {name.strip()}!"\n'),
    ])
    return samples, report


def split_random(text, seed):
    rng, cursor, result = random.Random(seed), 0, []
    while cursor < len(text):
        length = rng.randint(1, 40)
        result.append(text[cursor:cursor+length])
        cursor += length
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    parser.add_argument('--bench-child', nargs=2, type=int)
    args = parser.parse_args()
    if args.bench_child:
        print(json.dumps(bench(*args.bench_child)))
        return
    enc = encoding()
    samples, old = load_samples()
    matching = {}
    for model in ('gpt-6-luna', 'gpt-6.1-sol', 'gpt-6-astra'):
        try:
            matching[model] = tiktoken.encoding_name_for_model(model)
        except KeyError:
            matching[model] = None
    results, corrections = [], []
    for label, original in samples:
        text = ''.join(original)
        expected = len(enc.encode_ordinary(text))
        variants = {'original': original, 'single_character': list(text)}
        variants.update({f'random_{seed}': split_random(text, seed) for seed in range(3)})
        naive = {name: sum(len(enc.encode_ordinary(chunk)) for chunk in chunks)
                 for name, chunks in variants.items()}
        counts = {}
        for name, chunks in variants.items():
            buffer = ''
            previous = 0
            stride = max(1, len(chunks)//30)
            for index, chunk in enumerate(chunks):
                buffer += chunk
                if (index+1) % stride == 0 or index+1 == len(chunks):
                    count = len(enc.encode_ordinary(buffer))
                    if count < previous:
                        corrections.append({'sample_kind': label, 'variant': name,
                                            'previous_count': previous, 'new_count': count})
                    previous = count
            counts[name] = previous
        assert all(count == expected for count in counts.values())
        results.append({'kind': label, 'chars': len(text), 'bytes': len(text.encode()),
                        'sha256': hashlib.sha256(text.encode()).hexdigest(),
                        'full_text_tokens': expected, 'recomputed_counts': counts,
                        'naive_delta_sum_counts': naive,
                        'naive_overcounts': {k: v-expected for k, v in naive.items()}})
    # Explicitly find negative prefix corrections in a tiny bounded string.
    sample = 'antidisestablishmentarianism counterintuitive tokenization'
    prefix_counts = [len(enc.encode_ordinary(sample[:i])) for i in range(1, len(sample)+1)]
    negative = [{'prefix_chars': i+2, 'before': a, 'after': b}
                for i, (a, b) in enumerate(zip(prefix_counts, prefix_counts[1:])) if b < a]
    benchmarks = []
    for size in (10_000, 100_000, 1_000_000):
        for streams in (1, 4, 8):
            proc = subprocess.run([sys.executable, __file__, '--bench-child', str(size), str(streams)],
                                  capture_output=True, text=True, check=True, timeout=60)
            benchmarks.append(json.loads(proc.stdout))
    model_source = Path(tiktoken.model.__file__).read_bytes()
    report = {'tiktoken_version': tiktoken.__version__, 'model_encoding_mapping': matching,
              'mapping_source_sha256': hashlib.sha256(model_source).hexdigest(),
              'selected_encoding': enc.name, 'encoding_status': 'explicit_estimate_for_unmapped_models',
              'text_count_definition': 'encode_ordinary received text; no hidden/format tokens',
              'source_trace': old['trace_log'], 'samples': results,
              'all_chunking_final_counts_equal': True,
              'negative_prefix_corrections': negative, 'sampled_count_corrections': corrections,
              'benchmarks': benchmarks}
    assert negative
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n')
    print(json.dumps({'samples': len(results), 'encoding': enc.name, 'model_mapping': matching,
                      'negative_prefix_corrections': len(negative),
                      'benchmarks': benchmarks, 'report': str(args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
