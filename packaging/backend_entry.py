"""PyInstaller entrypoint. Spawn diversion must precede CLI imports/parsing."""

import multiprocessing

if __name__ == '__main__':
    multiprocessing.freeze_support()

    from codex_progress_monitor.execution import initialize_bundle
    initialize_bundle()

    import sys
    if sys.argv[1:] == ['--self-check']:
        import json
        from codex_progress_monitor.execution import bundle_resources
        from codex_progress_monitor.metrics import count_text
        from codex_progress_monitor.runtime import source_identity
        from codex_progress_monitor.tokenizer_process import TokenizerProcess
        manifest = json.loads((bundle_resources()/'build-manifest.json').read_text())
        worker = TokenizerProcess(timeout=10)
        counts = {}
        try:
            for encoding in manifest['encodings']:
                expected, _ = count_text('Hello, 世界！打包验证。', encoding)
                actual, _ = worker.count('Hello, 世界！打包验证。', encoding)
                if actual != expected or actual <= 0:
                    raise RuntimeError('tokenizer_self_check_failed')
                counts[encoding] = actual
            print(json.dumps({'ok': True, 'build_id': manifest['build_id'], 'python': sys.version.split()[0],
                              'tokenizer_counts': counts, 'worker': worker.diagnostics(),
                              'implementation': source_identity('collector', 'bridge', 'execution', 'runtime')}))
        finally:
            worker.close()
    else:
        from codex_progress_monitor.cli import main
        raise SystemExit(main())
