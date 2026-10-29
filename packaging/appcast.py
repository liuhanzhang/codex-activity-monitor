"""Generate informational-only Sparkle feeds from explicitly published releases."""

import argparse
from datetime import datetime
from email.utils import format_datetime
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PROJECT = 'LiuHanzhang/codex-activity-monitor'
SPARKLE = 'http://www.andymatuschak.org/xml-namespaces/sparkle'
ET.register_namespace('sparkle', SPARKLE)


def validate(records):
    builds, versions = set(), set()
    for record in records:
        version = record['version']
        if not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?', version):
            raise ValueError(f'Invalid release version: {version}')
        build = record['build']
        if type(build) is not int or build <= 0 or build in builds or version in versions:
            raise ValueError('Release versions and positive build numbers must be unique.')
        if type(record['prerelease']) is not bool:
            raise ValueError('The GitHub prerelease classification must be a boolean.')
        if datetime.fromisoformat(record['published_at'].replace('Z', '+00:00')).tzinfo is None:
            raise ValueError('Release time must contain a timezone.')
        builds.add(build); versions.add(version)
    return sorted(records, key=lambda record: record['build'], reverse=True)


def render(records, *, prereleases):
    rss = ET.Element('rss', version='2.0')
    channel = ET.SubElement(rss, 'channel')
    ET.SubElement(channel, 'title').text = 'Codex Activity Monitor Updates'
    ET.SubElement(channel, 'link').text = f'https://github.com/{PROJECT}/releases'
    for record in validate(records):
        if record['prerelease'] and not prereleases:
            continue
        item = ET.SubElement(channel, 'item')
        ET.SubElement(item, 'title').text = record['version']
        ET.SubElement(item, f'{{{SPARKLE}}}version').text = str(record['build'])
        ET.SubElement(item, f'{{{SPARKLE}}}shortVersionString').text = record['version']
        ET.SubElement(item, f'{{{SPARKLE}}}minimumSystemVersion').text = '14.0.0'
        ET.SubElement(item, f'{{{SPARKLE}}}hardwareRequirements').text = 'arm64'
        ET.SubElement(item, 'pubDate').text = format_datetime(datetime.fromisoformat(record['published_at'].replace('Z', '+00:00')))
        ET.SubElement(item, 'link').text = f'https://github.com/{PROJECT}/releases/tag/v{record["version"]}'
        # No enclosure: Sparkle's web-download mode cannot install executable code.
    ET.indent(rss, space='  ')
    return ET.tostring(rss, encoding='utf-8', xml_declaration=True) + b'\n'


def published_release(record):
    tag = 'v' + record['version']
    release = json.loads(subprocess.check_output(['gh', 'api', f'repos/{PROJECT}/releases/tags/{tag}']))
    prefix = f'Codex-Activity-Monitor-{record["version"]}-macos-arm64'
    expected = {prefix + '.dmg', prefix + '.zip'}
    if release['draft'] or not any(asset['name'] in expected and asset['state'] == 'uploaded' for asset in release['assets']):
        raise ValueError(f'{tag} is not a published release with its expected macOS arm64 archive.')
    return release


def verify_releases(records):
    for record in validate(records):
        release = published_release(record)
        if release['prerelease'] != record['prerelease'] or release['published_at'] != record['published_at']:
            raise ValueError(f'GitHub metadata changed for {record["version"]}; run --sync-releases.')


def sync_releases(records):
    synchronized = []
    for record in validate(records):
        release = published_release(record)
        synchronized.append(record | {'prerelease': release['prerelease'], 'published_at': release['published_at']})
    return synchronized


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--verify-releases', action='store_true')
    parser.add_argument('--sync-releases', action='store_true', help='Copy release classification/time from GitHub, without inspecting the version suffix.')
    args = parser.parse_args()
    directory = ROOT/'website/updates'
    records = json.loads((directory/'releases.json').read_text())
    if args.sync_releases:
        if args.check:
            parser.error('--sync-releases writes metadata and cannot be combined with --check')
        records = sync_releases(records)
        (directory/'releases.json').write_text(json.dumps(records, indent=2)+'\n')
    if args.verify_releases:
        verify_releases(records)
    for filename, prereleases in [('appcast.xml', False), ('appcast-prerelease.xml', True), ('appcast-alpha.xml', True)]:
        content = render(records, prereleases=prereleases)
        path = directory/filename
        if args.check:
            if not path.is_file() or path.read_bytes() != content:
                raise SystemExit(f'{filename} is stale. Run bin/generate-appcast.')
        else:
            path.write_bytes(content)


if __name__ == '__main__':
    main()
