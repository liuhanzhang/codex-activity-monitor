"""Explicit disposable profile for cross-process GUI acceptance, never production env."""

from pathlib import Path
import tempfile

from .app_control import AppController, ControlError, read_json
from .integration import KEYS
from .runtime import ROOT, atomic_json


class FileEnvironment:
    def __init__(self, path):
        self.path = path

    def get(self, key):
        value = read_json(self.path).get(key)
        if value is not None and not isinstance(value, str):
            raise ControlError('invalid_test_environment', '隔离环境值必须为字符串或未设置。')
        return value

    def set(self, key, value):
        if key not in KEYS:
            raise ControlError('invalid_test_environment', '隔离环境不接受其他变量。')
        values = read_json(self.path)
        if value is None:
            values.pop(key, None)
        else:
            values[key] = value
        atomic_json(self.path, values)


class ProfileServices:
    def __init__(self, root):
        self.root = root

    def backup_legacy(self, runtime):
        return None

    def disable_legacy(self, runtime):
        pass

    def rollback_legacy(self, saved):
        pass

    def remove_gui_login(self, app):
        pass

    def dependents(self, receipt):
        values = read_json(self.root / 'dependencies.json').get('pending', [])
        if not isinstance(values, list):
            raise ControlError('invalid_test_profile', '隔离依赖记录无效。')
        return values


def controller(root, app=None):
    root = Path(root).expanduser().resolve()
    allowed = (Path('/tmp').resolve(), Path(tempfile.gettempdir()).resolve(), ROOT / '.local')
    if not any(root.is_relative_to(base) and root != base for base in allowed):
        raise ControlError('invalid_test_profile', '隔离配置必须位于临时或本机测试目录。')
    if read_json(root / 'profile.json').get('kind') != 'codex-activity-monitor-disposable-test':
        raise ControlError('invalid_test_profile', '缺少明确的可丢弃测试标记。')
    data = root / 'data'
    if data.resolve() != data:
        raise ControlError('invalid_test_profile', '隔离数据目录不能重定向。')
    return AppController(data, environment=FileEnvironment(root / 'environment.json'),
                         services=ProfileServices(root), current_app=app), root / 'original-cli'
