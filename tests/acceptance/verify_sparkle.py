"""Actual Sparkle in a disposable moved app, with loopback or allowlisted public feeds."""

import argparse
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from urllib.parse import urlparse
import sys

ROOT = Path(__file__).resolve().parents[2]
RELEASE = 'https://github.com/LiuHanzhang/codex-activity-monitor/releases/tag/v0.1.0-alpha.2'


def feed(case, current_build='2'):
    if case == 'malformed':
        return b'not an XML feed'
    build = str(current_build) if case == 'latest' else '9999'
    minimum = '99.0.0' if case == 'incompatible' else '14.0.0'
    return f'''<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
<channel><title>Synthetic acceptance only</title><item><title>Synthetic update</title>
<sparkle:version>{build}</sparkle:version><sparkle:shortVersionString>Acceptance {build}</sparkle:shortVersionString>
<sparkle:minimumSystemVersion>{minimum}</sparkle:minimumSystemVersion>
<link>{RELEASE}</link></item></channel></rss>'''.encode()


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, content, requests, **kwargs):
        self.content, self.requests = content, requests
        super().__init__(*args, **kwargs)

    def do_GET(self):
        self.requests.append(self.path)
        self.send_response(200)
        self.send_header('Content-Type', 'application/rss+xml')
        self.send_header('Content-Length', str(len(self.content)))
        self.end_headers()
        self.wfile.write(self.content)

    def log_message(self, *_):
        pass


def run_case(source, output, case, interactive=False, *, feed_url=None, client_build=None, client_version=None,
             expected_version='Acceptance 9999', manual=False):
    with tempfile.TemporaryDirectory(prefix='cam-sparkle-', dir='/private/tmp') as directory:
        root = Path(directory)
        app = root/'moved 中文 App'/'Monitor Update Test.app'
        shutil.copytree(source, app, symlinks=True)
        identifier = 'com.liuhanzhang.codexactivitymonitor.updatetest.' + uuid.uuid4().hex
        requests = []
        source_info = plistlib.loads((app/'Contents/Info.plist').read_bytes())
        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler,
            content=feed(case, client_build or source_info['CFBundleVersion']), requests=requests))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        plist = app/'Contents/Info.plist'
        info = plistlib.loads(plist.read_bytes())
        info.update(CFBundleIdentifier=identifier, CFBundleName='Monitor Update Test', CFBundleDisplayName='Monitor Update Test',
                    CAMDistribution='update-test', SUDefaultsDomain=identifier+'.updates',
                    SUFeedURL=feed_url or f'http://127.0.0.1:{server.server_port}/updates/appcast-alpha.xml',
                    SUEnableAutomaticChecks=case != 'disabled' and not manual, NSAppTransportSecurity={'NSAllowsLocalNetworking': True})
        if client_build is not None:
            info['CFBundleVersion'] = str(client_build)
        if client_version is not None:
            info['CAMReleaseVersion'] = client_version
        plist.write_bytes(plistlib.dumps(info))
        config_path = app/'Contents/Resources/backend.json'
        config = json.loads(config_path.read_text())
        config = dict(python=sys.executable, source_root=str(root), runtime=str(root/'run'), data_root=str(root/'data'), build_id=config['build_id'])
        config_path.write_text(json.dumps(config))
        subprocess.run(['codesign', '--force', '--deep', '--sign', '-', str(app)], check=True, capture_output=True)
        executable = app/'Contents/MacOS/MonitorApp'
        diagnostics = output/(case+'.jsonl')
        logs = output/(case+'.log')
        # A moved app must work without loading Sparkle out of the SwiftPM build.
        policy = '(version 1)(allow default)(deny file-read* (subpath "'+str(ROOT/'macos/.build')+'"))'
        with logs.open('w') as log:
            process = subprocess.Popen(['/usr/bin/sandbox-exec', '-p', policy, str(executable), '--read-only',
                '--update-acceptance', '--data-root', str(root/'data'), '--runtime', str(root/'run'),
                '--settings', '--show', '--ui-diagnostics', str(diagnostics), '--ui-diagnostics-seconds', '120',
                *(['--update-manual-check'] if manual else [])],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log)
            try:
                deadline = time.monotonic()+40
                observed = None
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise AssertionError(f'{case}: app exited with {process.returncode}; inspect {logs}')
                    if diagnostics.exists():
                        lines = diagnostics.read_text().splitlines()
                        if lines:
                            observed = json.loads(lines[-1])
                            if case == 'available' and observed.get('update_available') == expected_version:
                                break
                            if case in ('latest', 'incompatible') and observed.get('update_status'):
                                break
                            if case == 'malformed' and requests and observed.get('update_can_check') and observed.get('update_last_check'):
                                break
                            if case == 'disabled' and observed.get('update_enabled') and not observed.get('update_automatic'):
                                time.sleep(2)
                                break
                    time.sleep(.1)
                assert observed and observed.get('update_enabled'), (case, observed)
                assert not any('api.github.com' in request for request in requests)
                if case == 'available':
                    assert observed.get('update_available') == expected_version, observed
                elif case == 'latest':
                    assert observed.get('update_status') == '当前已是最新版本。', observed
                elif case == 'incompatible':
                    assert observed.get('update_status') == '当前没有适用于这台 Mac 的更新。', observed
                elif case == 'malformed':
                    assert requests and not observed.get('update_available') and observed.get('update_status') is None, observed
                elif case == 'disabled':
                    assert requests == [], requests
                result = {'case': case, 'feed_url': info['SUFeedURL'], 'client_build': info['CFBundleVersion'], 'manual': manual,
                          'requests': requests, 'state': {k:v for k,v in observed.items() if k.startswith('update_')},
                          'bundle_id': identifier, 'app': str(app)}
                print(json.dumps(result), flush=True)
                if interactive:
                    print('Interactive acceptance ready; Ctrl-C cleans up this disposable app.', flush=True)
                    try:
                        while process.poll() is None:
                            time.sleep(.5)
                    except KeyboardInterrupt:
                        pass
                return result
            finally:
                # Process cleanup is distinct from a normal GUI-quit acceptance.
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait(timeout=5)
                        raise AssertionError('Disposable app did not exit after termination signal.')
                # This generated domain belongs only to this disposable bundle;
                # cleanup must not exercise the user's real login service.
                subprocess.run(['/usr/bin/defaults', 'delete', identifier+'.updates'],
                               check=True, capture_output=True, timeout=10)
                server.shutdown(); server.server_close(); thread.join(timeout=3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--interactive', action='store_true')
    parser.add_argument('--feed-url', help='Test only the allowlisted production HTTPS update feeds.')
    parser.add_argument('--client-build', type=int)
    parser.add_argument('--client-version')
    parser.add_argument('--expected-version', default='Acceptance 9999')
    parser.add_argument('--case', choices=['available','latest','incompatible','malformed','disabled'])
    parser.add_argument('--manual', action='store_true')
    args = parser.parse_args()
    source = args.app.resolve()
    info = plistlib.loads((source/'Contents/Info.plist').read_bytes())
    if args.feed_url:
        url = urlparse(args.feed_url)
        assert url.scheme == 'https' and url.netloc == 'liuhanzhang.github.io' and url.path in [
            '/codex-activity-monitor/updates/appcast.xml', '/codex-activity-monitor/updates/appcast-prerelease.xml',
            '/codex-activity-monitor/updates/appcast-alpha.xml'] and not url.query and not url.fragment
        assert args.case in ['available','latest','incompatible','disabled'], 'Choose a production expectation explicitly.'
        assert info['CFBundleIdentifier'] in ['com.liuhanzhang.codexactivitymonitor','com.liuhanzhang.codexactivitymonitor.preview']
    else:
        assert info['CFBundleIdentifier'].endswith('.preview'), 'Use the isolated Preview as the source bundle.'
    args.output.mkdir(parents=True, exist_ok=False)
    cases = [args.case] if args.case else ['available'] if args.interactive else ['available', 'latest', 'incompatible', 'malformed', 'disabled']
    results = [run_case(source, args.output, case, args.interactive, feed_url=args.feed_url, client_build=args.client_build,
                        client_version=args.client_version, expected_version=args.expected_version, manual=args.manual) for case in cases]
    (args.output/'result.json').write_text(json.dumps(results, indent=2)+'\n')


if __name__ == '__main__':
    main()
