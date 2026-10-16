"""User-scoped, retryable GUI integration. No resident uninstall process."""

from contextlib import contextmanager
import base64
import fcntl
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import sys
import time
import uuid

from .integration import KEYS, LABEL
from .runtime import ROOT, REAL_CLI, atomic_json

DATA_ROOT = Path.home() / 'Library/Application Support/CodexProgressMonitor'


class ControlError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def read_json(path, default=None):
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            raise ValueError('Expected an object')
        return value
    except FileNotFoundError:
        return {} if default is None else default
    except (ValueError, OSError) as exc:
        raise ControlError('invalid_record', f'无法读取管理记录：{path.name}') from exc


class LaunchEnvironment:
    def get(self, key):
        result = subprocess.run(['/bin/launchctl', 'getenv', key], capture_output=True)
        if result.returncode:
            raise ControlError('environment_read_failed', f'无法读取启动配置：{key}')
        # launchctl returns success for missing values too. It adds one newline
        # for a present value, including an empty value; preserve all other bytes.
        if result.stdout == b'':
            return None
        if not result.stdout.endswith(b'\n'):
            raise ControlError('environment_format', f'启动配置格式无法确认：{key}')
        return result.stdout[:-1].decode('utf-8')

    def set(self, key, value):
        args = ['setenv', key, value] if value is not None else ['unsetenv', key]
        result = subprocess.run(['/bin/launchctl', *args], capture_output=True)
        if result.returncode:
            raise ControlError('environment_write_failed', f'无法恢复或设置启动配置：{key}')
        if self.get(key) != value:
            raise ControlError('environment_verify_failed', f'启动配置写入后不一致：{key}')


class SystemServices:
    """Only remove our legacy agent; keep GUI login registration separate."""
    def backup_legacy(self, runtime):
        path = Path.home() / 'Library/LaunchAgents' / f'{LABEL}.plist'
        if not path.exists():
            return None
        raw = path.read_bytes()
        if str(runtime) not in json.dumps(plistlib.loads(raw)):
            raise ControlError('foreign_agent', '已有启动项不属于当前 Monitor，未修改。')
        loaded = subprocess.run(['/bin/launchctl','print',f'gui/{os.getuid()}/{LABEL}'], capture_output=True).returncode == 0
        return {'content': base64.b64encode(raw).decode('ascii'), 'loaded': loaded}

    def rollback_legacy(self, saved):
        if not saved:
            return
        path = Path.home() / 'Library/LaunchAgents' / f'{LABEL}.plist'
        raw = base64.b64decode(saved['content'], validate=True)
        if path.exists() and path.read_bytes() != raw:
            raise ControlError('agent_changed', '旧启动项已被修改，未覆盖。')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        path.chmod(0o600)
        if saved['loaded']:
            domain = f'gui/{os.getuid()}/{LABEL}'
            if subprocess.run(['/bin/launchctl', 'print', domain], capture_output=True).returncode:
                if subprocess.run(['/bin/launchctl', 'bootstrap', f'gui/{os.getuid()}', str(path)], capture_output=True).returncode:
                    raise ControlError('agent_rollback_failed', '旧启动项尚未恢复，备份仍保留。')

    def remove_gui_login(self, app):
        if app is None:
            return
        executable = Path(app) / 'Contents/MacOS/MonitorApp'
        if not executable.is_file():
            raise ControlError('login_cleanup_unavailable', '请使用重新下载的 App 完成登录项清理。')
        result = subprocess.run([str(executable), '--login-remove'], capture_output=True, text=True)
        try:
            success = json.loads(result.stdout).get('ok') is True
        except (ValueError, AttributeError):
            success = False
        if result.returncode or not success:
            raise ControlError('login_cleanup_failed', '登录启动尚未成功撤销，恢复记录仍保留。')

    def disable_legacy(self, runtime):
        path = Path.home() / 'Library/LaunchAgents' / f'{LABEL}.plist'
        if not path.exists():
            return
        data = plistlib.loads(path.read_bytes())
        if str(runtime) not in json.dumps(data):
            raise ControlError('foreign_agent', '已有启动项不属于当前 Monitor，未修改。')
        domain = f'gui/{os.getuid()}/{LABEL}'
        loaded = subprocess.run(['/bin/launchctl', 'print', domain], capture_output=True).returncode == 0
        if loaded:
            result = subprocess.run(['/bin/launchctl', 'bootout', domain], capture_output=True)
            if result.returncode:
                raise ControlError('agent_unload_failed', '无法停用旧 Monitor 自动重启服务。')
        path.unlink()

    def dependents(self, receipt):
        result = subprocess.run(['/bin/ps', '-axo', 'pid=,comm='], capture_output=True, text=True)
        if result.returncode:
            return [{'reason': 'process_visibility_unknown'}]
        actual = Path(receipt['real_cli'])
        apps = [p for p in actual.parents if p.suffix == '.app']
        prefix = str(apps[-1]) + '/' if apps else str(actual)
        found = []
        for line in result.stdout.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and (parts[1] == str(actual) or parts[1].startswith(prefix)):
                found.append({'pid': int(parts[0]), 'reason': 'codex_running'})
        # Registered bridges are checked too, including Python bridges whose
        # executable doesn't live in the Codex bundle.
        for path in Path(receipt['runtime']).glob('bridges/*.json'):
            info = read_json(path)
            pid = info.get('pid')
            if isinstance(pid, int) and process_alive(pid):
                from .app_session import process_identity
                identity = process_identity(pid)
                if identity is None or info.get('identity') is None or identity == info['identity']:
                    found.append({'pid': pid, 'reason': 'bridge_running'})
                else:
                    path.unlink(missing_ok=True)
            else:
                path.unlink(missing_ok=True)
        return found


def process_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class AppController:
    def __init__(self, root=DATA_ROOT, runtime=None, environment=None, services=None, current_app=None):
        self.root = Path(root).expanduser().resolve()
        self.receipt_path = self.root / 'integration.json'
        self.preferences = self.root / 'app-state.json'
        self.enabled = self.root / 'integration.enabled'
        self.session = self.root / 'observation.json'
        self.requested_runtime = Path(runtime).resolve() if runtime else None
        self.environment = environment or LaunchEnvironment()
        self.services = services or SystemServices()
        self.current_app = Path(current_app).resolve() if current_app else None

    @contextmanager
    def locked(self):
        if self.root in (Path('/'), Path.home(), ROOT):
            raise ControlError('unsafe_data_root', '不能把系统、用户或源码根目录作为 App 数据目录。')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        with (self.root / 'management.lock').open('a+') as lock:
            deadline=time.monotonic()+5
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic()>=deadline:
                        raise ControlError('management_busy', '另一个管理操作尚未完成，请稍后重试。')
                    time.sleep(.05)
            marker = self.root / '.monitor-owned'
            if not marker.exists():
                if any(p.name != 'management.lock' for p in self.root.iterdir()):
                    raise ControlError('foreign_data_root', '数据目录包含未登记内容，未接管。')
                marker.write_text('codex-progress-monitor:1\n')
            elif marker.read_text() != 'codex-progress-monitor:1\n':
                raise ControlError('foreign_data_root', '数据目录归属无法确认。')
            yield

    def _receipt(self):
        value = read_json(self.receipt_path)
        if value and value.get('version') != 1:
            raise ControlError('record_version', '管理记录版本不受支持，未覆盖。')
        return value

    def status(self):
        receipt = self._receipt()
        prefs = read_json(self.preferences)
        state = receipt.get('state', 'restored' if prefs.get('restored') else 'new')
        return {'version': 1, 'ok': True, 'state': state,
                'configured': state == 'configured', 'runtime': receipt.get('runtime'),
                'purge': receipt.get('purge', False), 'pending': receipt.get('pending', []),
                'data_root': str(self.root), 'error': receipt.get('error')}

    def _write(self, receipt):
        receipt['updated_at'] = time.time()
        atomic_json(self.receipt_path, receipt)

    def set_preferences(self, launch_at_login):
        with self.locked():
            prefs = read_json(self.preferences)
            prefs.update(version=1, launch_at_login=bool(launch_at_login))
            atomic_json(self.preferences, prefs)
        return self.status()

    def _save_restored(self, value):
        prefs = read_json(self.preferences)
        prefs.update(version=1, restored=value, language=prefs.get('language', 'zh-CN'),
                     launch_at_login=prefs.get('launch_at_login', True))
        atomic_json(self.preferences, prefs)

    def configure(self, app, real_cli=REAL_CLI, python=sys.executable, source_root=ROOT,
                  legacy_runtime=None, legacy_unset=False):
        app, actual, python, source = (Path(p).expanduser().resolve() for p in (app, real_cli, python, source_root))
        if not app.is_dir() or not (app / 'Contents/MacOS/MonitorApp').is_file():
            raise ControlError('app_missing', '找不到 Monitor App，请先构建或重新下载。')
        if not os.access(actual, os.X_OK) or not os.access(python, os.X_OK):
            raise ControlError('entry_missing', '找不到可执行的原版 CLI 或本机 Python。')
        if not (source / 'src/codex_progress_monitor/cli.py').is_file():
            raise ControlError('source_missing', '找不到本机核心入口。')
        with self.locked():
            old = self._receipt()
            if old.get('state') in ('restoring', 'pending_cleanup', 'configuring', 'error'):
                raise ControlError('cleanup_pending', '请先完成上次恢复与清理，再重新配置。')
            current = {key: self.environment.get(key) for key in KEYS}
            legacy = Path(legacy_runtime).resolve() if legacy_runtime else None
            previous = old.get('previous') if old.get('state') == 'configured' else None
            runtime = Path(old['runtime']) if old.get('runtime') else (self.requested_runtime or self.root / 'run')
            imported = False
            legacy_backup = old.get('legacy_backup')
            if previous is None and legacy and (legacy / 'desktop-environment.json').exists():
                saved = read_json(legacy / 'desktop-environment.json')
                previous = saved.get('previous')
                if not isinstance(previous, dict) or set(previous) != set(KEYS):
                    raise ControlError('legacy_invalid', '旧接入备份不完整，未修改配置。')
                if any(value is not None and not isinstance(value, str) for value in previous.values()):
                    raise ControlError('legacy_invalid', '旧接入原值格式无效，未修改配置。')
                ambiguous = [key for key, value in previous.items() if value == '']
                if ambiguous and not legacy_unset:
                    raise ControlError('legacy_ambiguous', '旧备份无法区分空值与未设置，需确认原先状态后迁移。')
                previous = {key: (None if value == '' and legacy_unset else value) for key, value in previous.items()}
                legacy_backup = {'record': saved, 'path': str(legacy / 'desktop-environment.json'),
                                 'empty_resolution': 'user_confirmed_monitor_introduced_unset' if legacy_unset else 'preserved'}
                for key in KEYS:
                    if current[key] is not None and current[key] != saved.get('installed', {}).get(key):
                        previous[key] = current[key]
                runtime, imported = legacy, True
            if previous is None:
                if current['CODEX_CLI_PATH'] and 'codex-observed' in current['CODEX_CLI_PATH']:
                    raise ControlError('backup_missing', '检测到旧包装器但找不到原备份，未覆盖当前配置。')
                previous = current.copy()
            runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
            if len(str(runtime / 'events.sock').encode()) > 103:
                raise ControlError('runtime_long', '运行目录过长，无法创建本地通信入口。')
            launcher = self.root / 'codex-observed'
            if actual == launcher:
                raise ControlError('recursive_cli', '原 CLI 不能指向 Monitor 包装器。')
            installed = dict(zip(KEYS, (str(launcher), '0', '0', '')))
            receipt = {'version': 1, 'id': old.get('id') if old.get('state') == 'configured' else uuid.uuid4().hex,
                       'state': 'configuring', 'previous': previous, 'installed': installed,
                       'runtime': str(runtime), 'app': str(app), 'real_cli': str(actual),
                       'python': str(python), 'source_root': str(source), 'imported_legacy': imported or old.get('imported_legacy', False),
                       'restored_keys': [], 'purge': False}
            if legacy_backup:
                receipt['legacy_backup'] = legacy_backup
            receipt['legacy_agent_backup'] = self.services.backup_legacy(runtime)
            managed_path = runtime / 'app-managed.json'
            managed_before = managed_path.read_bytes() if managed_path.exists() else None
            launcher_before = launcher.read_bytes() if launcher.exists() else None
            legacy_launcher = runtime / 'codex-observed' if receipt['imported_legacy'] else None
            legacy_launcher_before = legacy_launcher.read_bytes() if legacy_launcher and legacy_launcher.exists() else None
            legacy_login = runtime / 'login-start' if receipt['imported_legacy'] else None
            legacy_login_before = legacy_login.read_bytes() if legacy_login and legacy_login.exists() else None
            enabled_before = self.enabled.exists()
            self._write(receipt)
            try:
                launcher.write_text(self._launcher(app, actual, python, source, runtime))
                launcher.chmod(0o700)
                # A running Desktop may still hold this old absolute path.
                # Upgrade its startup fallback too, without ending its bridge.
                if legacy_launcher:
                    legacy_launcher.write_text(self._launcher(app, actual, python, source, runtime))
                    legacy_launcher.chmod(0o700)
                atomic_json(runtime / 'app-managed.json', {'version': 1, 'session_file': str(self.session), 'enabled_file': str(self.enabled)})
                self.services.disable_legacy(runtime)
                if legacy_login_before is not None:
                    legacy_login.write_text('#!/bin/sh\n# Migrated to GUI login management; never reapply Codex environment.\nexit 0\n')
                    legacy_login.chmod(0o700)
                for key, value in installed.items():
                    self.environment.set(key, value)
                self.enabled.touch(mode=0o600)
                receipt['state'] = 'configured'
                self._write(receipt)
                self._save_restored(False)
            except Exception as exc:
                failures = []
                for key, value in installed.items():
                    try:
                        if self.environment.get(key) == value:
                            self.environment.set(key, current[key])
                    except Exception:
                        failures.append(key)
                try:
                    if managed_before is None:
                        managed_path.unlink(missing_ok=True)
                    else:
                        managed_path.write_bytes(managed_before)
                    if launcher_before is not None:
                        launcher.write_bytes(launcher_before)
                        launcher.chmod(0o700)
                    else:
                        launcher.unlink(missing_ok=True)
                    if legacy_launcher:
                        if legacy_launcher_before is None:
                            legacy_launcher.unlink(missing_ok=True)
                        else:
                            legacy_launcher.write_bytes(legacy_launcher_before)
                            legacy_launcher.chmod(0o700)
                    if legacy_login_before is not None:
                        legacy_login.write_bytes(legacy_login_before)
                        legacy_login.chmod(0o700)
                    if enabled_before:
                        self.enabled.touch(mode=0o600)
                    else:
                        self.enabled.unlink(missing_ok=True)
                    self.services.rollback_legacy(receipt.get('legacy_agent_backup'))
                except Exception:
                    failures.append('resources_or_legacy_agent')
                if not failures and old.get('state') == 'configured':
                    old['error'] = str(exc)
                    self._write(old)
                else:
                    receipt.update(state='error', error=str(exc), rollback_pending=failures)
                    self._write(receipt)
                raise
        return self.status()

    def _launcher(self, app, actual, python, source, runtime):
        quote = shlex.quote
        command = ['exec', '/usr/bin/env', 'PYTHONPATH=' + str(source / 'src'), str(python), '-m',
                   'codex_progress_monitor.cli', '--runtime', str(runtime), 'bridge', '--real-cli', str(actual), '--']
        return ('#!/bin/sh\n'
                f'if [ ! -x {quote(str(actual))} ]; then echo "Codex 原版 CLI 不可用" >&2; exit 127; fi\n'
                f'if [ -f {quote(str(self.enabled))} ] && [ -x {quote(str(app / "Contents/MacOS/MonitorApp"))} ] && '
                f'[ -x {quote(str(python))} ] && [ -f {quote(str(source / "src/codex_progress_monitor/cli.py"))} ]; then\n'
                + '  ' + ' '.join(quote(x) if i else x for i, x in enumerate(command)) + ' "$@"\nfi\n'
                + f'exec {quote(str(actual))} "$@"\n')

    def resume(self, owner_pid):
        with self.locked():
            receipt = self._receipt()
            if receipt.get('state') != 'configured':
                raise ControlError('not_configured', '请先配置接入。')
            if owner_pid <= 1 or not process_alive(owner_pid):
                raise ControlError('owner_missing', '找不到 Monitor 界面进程。')
            current = read_json(self.session)
            if (current.get('owner_pid') != owner_pid and current.get('active') is True and
                    isinstance(current.get('owner_pid'), int) and process_alive(current['owner_pid']) and
                    -5 <= time.time() - current.get('heartbeat_at', 0) < 5):
                raise ControlError('another_gui_running', '另一个 Monitor 界面正在采集，请先退出该实例。')
            session = {'version': 1, 'id': uuid.uuid4().hex, 'owner_pid': owner_pid,
                       'heartbeat_at': time.time(), 'active': True}
            atomic_json(self.session, session)
            from .cli import start
            start(Path(receipt['runtime']))
        return {**self.status(), 'session': session}

    def _stop(self, receipt):
        self.session.unlink(missing_ok=True)
        if receipt.get('runtime'):
            from .cli import stop, running
            runtime = Path(receipt['runtime'])
            stop(runtime)
            if running(runtime):
                raise ControlError('stop_timeout', '采集器尚未停止，请重试；没有结束 Codex。')
            # A stale snapshot is not proof that the writer has finished. Its
            # lifetime lock remains held until after the final snapshot write.
            lock = runtime / 'collector.lock'
            if lock.exists():
                with lock.open('r') as handle:
                    try:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError as exc:
                        raise ControlError('stop_timeout', '采集器仍在退出，恢复记录已保留，请稍后重试。') from exc

    def stop(self, owner_pid=None):
        if not self.root.exists():
            return self.status()
        with self.locked():
            session = read_json(self.session)
            if owner_pid is not None and session and session.get('owner_pid') != owner_pid:
                raise ControlError('another_gui_running', '当前采集由另一个 Monitor 界面管理，未停止。')
            self._stop(self._receipt())
        return self.status()

    def restore(self, purge=False):
        if purge and not self.root.exists():
            return {'version': 1, 'ok': True, 'state': 'purged', 'configured': False, 'pending': []}
        with self.locked():
            receipt = self._receipt()
            if not receipt:
                if purge:
                    return self._purge_without_integration()
                self._save_restored(True)
                return self.status()
            if receipt.get('state') == 'restored':
                if not purge:
                    return self.status()
                receipt.update(state='pending_cleanup', purge=True)
            else:
                if not isinstance(receipt.get('previous'), dict) or set(receipt['previous']) != set(KEYS):
                    raise ControlError('backup_missing', '原配置备份缺失，停止恢复和删除。')
                receipt.update(state='restoring', purge=bool(purge or receipt.get('purge')))
            self._write(receipt)
            self.enabled.unlink(missing_ok=True)
            self.services.disable_legacy(Path(receipt['runtime']))
            self._stop(receipt)
            if receipt['state'] == 'restoring':
                for key, installed in receipt['installed'].items():
                    if key in receipt['restored_keys']:
                        continue
                    current = self.environment.get(key)
                    if current == installed:
                        self.environment.set(key, receipt['previous'][key])
                        outcome = 'restored'
                    else:
                        outcome = 'user_change_preserved'
                    receipt.setdefault('key_results', {})[key] = outcome
                    receipt['restored_keys'].append(key)
                    self._write(receipt)
                receipt['state'] = 'pending_cleanup'
                self._write(receipt)
            return self._cleanup(receipt)

    def cleanup(self):
        if not self.root.exists():
            return self.status()
        # A crash between individual environment writes is a resumable restore,
        # not a cleanup-only operation. Reacquire the lock inside restore.
        if self._receipt().get('state') == 'restoring':
            return self.restore()
        with self.locked():
            receipt = self._receipt()
            if receipt.get('state') != 'pending_cleanup':
                return self.status()
            return self._cleanup(receipt)

    def _cleanup(self, receipt):
        dependencies = self.services.dependents(receipt)
        if dependencies:
            receipt['pending'] = dependencies
            self._write(receipt)
            return self.status()
        receipt['pending'] = []
        self._write(receipt)
        runtime = Path(receipt['runtime'])
        # Delete explicit owned resources only; a migrated runtime can contain
        # unrelated experiments. Never remove the enclosing project/venv.
        managed = runtime / 'app-managed.json'
        record = read_json(managed)
        if record.get('session_file') == str(self.session) or receipt.get('imported_legacy'):
            for name in ('app-managed.json', 'snapshot.json', 'collector.json', 'events.sock',
                         'collector.log', 'collector.previous.log'):
                (runtime / name).unlink(missing_ok=True)
        if receipt.get('imported_legacy'):
            for name in ('codex-observed', 'login-start', 'installation.json', 'desktop-environment.json'):
                (runtime / name).unlink(missing_ok=True)
        (runtime / 'collector.lock').unlink(missing_ok=True)
        bridges = runtime / 'bridges'
        if bridges.is_dir() and not bridges.is_symlink() and not any(bridges.iterdir()):
            bridges.rmdir()
        if receipt.get('imported_legacy') and runtime.exists() and not any(runtime.iterdir()):
            runtime.rmdir()
        (self.root / 'codex-observed').unlink(missing_ok=True)
        self.enabled.unlink(missing_ok=True)
        self.session.unlink(missing_ok=True)
        if receipt.get('purge'):
            return self._purge_without_integration()
        receipt.update(state='restored', pending=[])
        receipt.pop('previous', None)
        receipt.pop('error', None)
        self._write(receipt)
        self._save_restored(True)
        return self.status()

    def _purge_without_integration(self):
        # Root was created for this controller; foreign directories are never
        # recursively deleted based on an installation record.
        allowed = {'management.lock', '.monitor-owned', 'integration.json', 'app-state.json', 'codex-observed',
                   'integration.enabled', 'observation.json', 'run'}
        unknown = [p.name for p in self.root.iterdir() if p.name not in allowed]
        if unknown:
            raise ControlError('unowned_data', '数据目录包含未登记文件，未执行全部清理。')
        run = self.root / 'run'
        if run.exists() and (run.is_symlink() or any(p.name not in {'collector.lock', 'bridges'} for p in run.iterdir())):
            raise ControlError('unowned_data', '运行目录包含未清理或未登记文件，未删除。')
        if (run / 'bridges').exists() and any((run / 'bridges').iterdir()):
            raise ControlError('unowned_data', '运行目录仍有桥接记录，未删除。')
        receipt=self._receipt()
        self.services.remove_gui_login(self.current_app or receipt.get('app'))
        for path in self.root.iterdir():
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        self.root.rmdir()
        return {'version': 1, 'ok': True, 'state': 'purged', 'configured': False, 'pending': []}
