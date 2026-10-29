import importlib.util
from pathlib import Path
import plistlib
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('cam_embed_sparkle', Path(__file__).resolve().parents[1]/'packaging/embed_sparkle.py')
embed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(embed)


class SparkleDownloadLabelTests(unittest.TestCase):
    def tables(self, root):
        paths = []
        for locale in ['Base', 'zh_CN', 'zh_TW', 'zh_HK']:
            path = root/'Resources'/f'{locale}.lproj/Sparkle.strings'
            path.parent.mkdir(parents=True)
            path.write_bytes(plistlib.dumps({'Learn More…': 'old', 'Install Update': 'keep'}, fmt=plistlib.FMT_BINARY))
            paths.append(path)
        return paths

    def test_only_web_download_label_changes_in_supported_languages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self.tables(root)
            embed.customize_download_button(root)
            self.assertEqual([plistlib.loads(p.read_bytes())['Learn More…'] for p in paths], ['Download', '下载', '下載', '下載'])
            for path in paths:
                self.assertEqual(plistlib.loads(path.read_bytes())['Install Update'], 'keep')

    def test_sdk_label_change_fails_before_any_resources_are_modified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self.tables(root)
            paths[-1].write_bytes(plistlib.dumps({'changed SDK key': 'value'}))
            before = [p.read_bytes() for p in paths]
            with self.assertRaises(RuntimeError):
                embed.customize_download_button(root)
            self.assertEqual([p.read_bytes() for p in paths], before)
