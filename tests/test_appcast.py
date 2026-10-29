import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

spec = importlib.util.spec_from_file_location('cam_appcast', Path(__file__).resolve().parents[1]/'packaging/appcast.py')
appcast = importlib.util.module_from_spec(spec)
spec.loader.exec_module(appcast)


class AppcastTests(unittest.TestCase):
    def test_published_release_accepts_dmg_and_legacy_zip_assets(self):
        record = {'version': '0.1.0-alpha.4'}
        for extension in ('dmg', 'zip'):
            release = {'draft': False, 'assets': [{
                'name': f'Codex-Activity-Monitor-0.1.0-alpha.4-macos-arm64.{extension}', 'state': 'uploaded'}]}
            with self.subTest(extension=extension), \
                 patch.object(appcast.subprocess, 'check_output', return_value=json.dumps(release).encode()):
                self.assertEqual(appcast.published_release(record), release)

    def test_release_requires_matching_uploaded_installer(self):
        for draft, name, state in ((True, '0.1.0-alpha.4-macos-arm64.dmg', 'uploaded'),
                                   (False, '0.1.0-alpha.3-macos-arm64.dmg', 'uploaded'),
                                   (False, '0.1.0-alpha.4-macos-arm64.dmg', 'new'),
                                   (False, '0.1.0-alpha.4-macos-arm64.exe', 'uploaded')):
            release = {'draft': draft, 'assets': [{'name': 'Codex-Activity-Monitor-' + name, 'state': state}]}
            with self.subTest(release=release), \
                 patch.object(appcast.subprocess, 'check_output', return_value=json.dumps(release).encode()), \
                 self.assertRaises(ValueError):
                appcast.published_release({'version': '0.1.0-alpha.4'})

    def test_channels_and_build_numbers_generate_only_web_downloads(self):
        records = [dict(version='0.2.0-alpha.1', build=3, prerelease=True, published_at='2026-10-28T12:00:00Z'),
                   dict(version='0.1.0', build=2, prerelease=False, published_at='2026-10-28T11:00:00Z')]
        for prereleases, expected in [(False, ['2']), (True, ['3', '2'])]:
            feed = ET.fromstring(appcast.render(records, prereleases=prereleases))
            items = feed.findall('channel/item')
            self.assertEqual([item.findtext(f'{{{appcast.SPARKLE}}}version') for item in items], expected)
            for item in items:
                self.assertIsNone(item.find('enclosure'))
                self.assertTrue(item.findtext('link').startswith('https://github.com/LiuHanzhang/codex-activity-monitor/releases/tag/v'))
                self.assertEqual(item.findtext(f'{{{appcast.SPARKLE}}}minimumSystemVersion'), '14.0.0')
        # Empty stable channel is deliberate while the project has only alphas.
        self.assertEqual(ET.fromstring(appcast.render(records[:1], prereleases=False)).findall('channel/item'), [])

    def test_bad_release_records_cannot_be_published(self):
        valid = dict(version='0.1.0-alpha.2', build=2, prerelease=True, published_at='2026-10-28T10:21:53Z')
        for overrides in [dict(build=True), dict(build=0), dict(version='../unsafe'), dict(prerelease='false'),
                          dict(published_at='2026-10-28T10:21:53')]:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                appcast.render([valid | overrides], prereleases=True)
        with self.assertRaises(ValueError):
            appcast.render([valid, valid], prereleases=True)

    def test_github_classification_can_disagree_with_name(self):
        records = [dict(version='1.2.3-alpha.1', build=7, prerelease=False, published_at='2026-10-28T10:21:53Z'),
                   dict(version='1.2.4', build=9, prerelease=True, published_at='2026-10-28T10:22:53Z')]
        stable = ET.fromstring(appcast.render(records, prereleases=False))
        self.assertEqual([item.findtext('title') for item in stable.findall('channel/item')], ['1.2.3-alpha.1'])
        all_releases = ET.fromstring(appcast.render(records, prereleases=True))
        self.assertEqual([item.findtext('title') for item in all_releases.findall('channel/item')], ['1.2.4', '1.2.3-alpha.1'])

    def test_sync_uses_github_flag_instead_of_suffix(self):
        record = dict(version='1.2.3-alpha.1', build=7, prerelease=True, published_at='2026-10-28T10:21:53Z')
        with patch.object(appcast, 'published_release', return_value={'prerelease': False, 'published_at': record['published_at']}):
            with self.assertRaises(ValueError):
                appcast.verify_releases([record])
            synchronized = appcast.sync_releases([record])
            self.assertFalse(synchronized[0]['prerelease'])
            appcast.verify_releases(synchronized)
