import importlib.util
from pathlib import Path
import plistlib
import unittest


spec = importlib.util.spec_from_file_location('cam_app_metadata', Path(__file__).resolve().parents[1]/'packaging/app_metadata.py')
app_metadata = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app_metadata)


class AppMetadataTests(unittest.TestCase):
    def test_source_and_standalone_preserve_the_same_prerelease_identity(self):
        metadata = {'project': {'version': '1.2.3'},
                    'tool': {'codex-activity-monitor': {'version-suffix': 'alpha.10', 'release-prerelease': True, 'build-number': 42,
                                                      'sparkle-public-key': 'A'*43 + '='}}}
        for distribution in ('source', 'standalone', 'preview'):
            with self.subTest(distribution=distribution):
                info = plistlib.loads(plistlib.dumps(app_metadata.bundle_info(metadata, distribution=distribution)))
                self.assertEqual(info['CAMReleaseVersion'], '1.2.3-alpha.10')
                self.assertEqual(info['CFBundleShortVersionString'], '1.2.3')
                self.assertEqual(info['CFBundleVersion'], '42')
                self.assertEqual(info['CFBundleIdentifier'].endswith('.preview'), distribution == 'preview')
                self.assertTrue(info['SUFeedURL'].endswith('/appcast-prerelease.xml'))
                self.assertTrue(info['CAMReleasePrerelease'])
                self.assertEqual(info['SUDefaultsDomain'], info['CFBundleIdentifier'] + '.updates')
                self.assertEqual(info['SUEnableAutomaticChecks'], distribution != 'preview')
                self.assertFalse(info['SUAutomaticallyUpdate'])
                self.assertFalse(info['SUEnableSystemProfiling'])
        del metadata['tool']['codex-activity-monitor']['version-suffix']
        self.assertEqual(app_metadata.release_version(metadata), '1.2.3')
        self.assertTrue(app_metadata.bundle_info(metadata, distribution='standalone')['SUFeedURL'].endswith('/appcast-prerelease.xml'))

    def test_channel_is_independent_from_version_name(self):
        for suffix in (None, 'alpha.2', 'preview.1'):
            for prerelease in (False, True):
                with self.subTest(suffix=suffix, prerelease=prerelease):
                    settings = {'build-number': 7, 'release-prerelease': prerelease}
                    if suffix:
                        settings['version-suffix'] = suffix
                    metadata = {'project': {'version': '1.2.3'}, 'tool': {'codex-activity-monitor': settings}}
                    info = app_metadata.bundle_info(metadata, distribution='standalone')
                    self.assertEqual(info['CAMReleaseVersion'], '1.2.3' + (f'-{suffix}' if suffix else ''))
                    self.assertEqual(info['CAMReleasePrerelease'], prerelease)
                    self.assertEqual(info['CAMUpdateChannel'], 'prerelease' if prerelease else 'stable')
                    self.assertTrue(info['SUFeedURL'].endswith('/appcast-prerelease.xml' if prerelease else '/appcast.xml'))

    def test_missing_or_nonboolean_classification_is_not_guessed_from_suffix(self):
        for value in (None, 'false', 0, 1):
            metadata = {'project': {'version': '1.2.3'}, 'tool': {'codex-activity-monitor': {
                'version-suffix': 'alpha.1', 'release-prerelease': value, 'build-number': 7}}}
            with self.subTest(value=value), self.assertRaises(ValueError):
                app_metadata.bundle_info(metadata, distribution='standalone')
