import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT/'packaging'
sys.path.insert(0, str(PACKAGING))
try:
    spec = importlib.util.spec_from_file_location('cam_build_app', PACKAGING/'build_app.py')
    build_app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build_app)
finally:
    sys.path.remove(str(PACKAGING))


class FakeDistribution:
    def __init__(self, root, name, files):
        self.root = root
        self.metadata = {'Name': name}
        self.files = [Path(name) for name in files] if files is not None else None

    def locate_file(self, file):
        return self.root/file


class DependencyLicenseTests(unittest.TestCase):
    def test_copies_notices_and_preserves_licenses_without_python_caches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'source'
            output = root/'licenses'
            files = {
                'module/licenses/LICENSE.txt': 'actual license',
                'module/licenses/NOTICE.md': 'actual notice',
                'module/licenses/extra.py': 'previously collected source',
                'module/licenses/__pycache__/extra.cpython-314.pyc': 'cached bytecode',
                'module/licenses/__pycache__/other.dat': 'other cache content',
                'module/licenses/legacy.PYO': 'old bytecode',
                'module/readme.txt': 'not a license',
                'module-1.dist-info/licenses/COPYING': 'distribution license',
            }
            for name, content in files.items():
                path = source/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            dist = FakeDistribution(source, 'example', [*files, 'module/licenses/missing.txt'])
            build_app.copy_dependency_licenses(output, [dist, FakeDistribution(source, 'empty', None)])

            found = {str(path.relative_to(output)): path.read_text()
                     for path in output.rglob('*') if path.is_file()}
            self.assertEqual(found, {
                'example/licenses/LICENSE.txt': 'actual license',
                'example/licenses/NOTICE.md': 'actual notice',
                'example/licenses/extra.py': 'previously collected source',
                'example/licenses/COPYING': 'distribution license',
            })

    def test_excludes_project_and_pip_distributions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'source'
            path = source/'package/licenses/LICENSE'
            path.parent.mkdir(parents=True)
            path.write_text('license')
            entries = [FakeDistribution(source, name, ['package/licenses/LICENSE'])
                       for name in ('pip', 'codex-activity-monitor')]
            output = root/'licenses'
            build_app.copy_dependency_licenses(output, entries)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
