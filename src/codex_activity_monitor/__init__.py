"""Codex Desktop local progress feedback."""

import importlib
import importlib.abc
import importlib.machinery
import sys

__version__ = "0.1.0"

LEGACY_MODULE_ALIASES = {
    'codex_activity_monitor.bridge': 'codex_activity_monitor.agents.codex.bridge',
    'codex_activity_monitor.events': 'codex_activity_monitor.agents.codex.events',
    'codex_activity_monitor.progress_state': 'codex_activity_monitor.agents.codex.progress_state',
    'codex_activity_monitor.integration': 'codex_activity_monitor.agents.codex.integration',
    'codex_activity_monitor.integration_status': 'codex_activity_monitor.agents.codex.integration_status',
    'codex_activity_monitor.history_index': 'codex_activity_monitor.agents.codex.history_index',
    'codex_activity_monitor.history_read': 'codex_activity_monitor.agents.codex.history_read',
    'codex_activity_monitor.history_source': 'codex_activity_monitor.agents.codex.history_source',
    'codex_activity_monitor.history_stream': 'codex_activity_monitor.agents.codex.history_stream',
    'codex_activity_monitor.history_content': 'codex_activity_monitor.agents.codex.history_content',
    'codex_activity_monitor.history_contract': 'codex_activity_monitor.agents.codex.history_contract',
    'codex_activity_monitor.reconciliation': 'codex_activity_monitor.agents.codex.reconciliation',
    'codex_activity_monitor.antigravity_transport': 'codex_activity_monitor.agents.antigravity.transport',
    'codex_activity_monitor.antigravity_source': 'codex_activity_monitor.agents.antigravity.source',
    'codex_activity_monitor.antigravity_records': 'codex_activity_monitor.agents.antigravity.records',
    'codex_activity_monitor.antigravity_projection': 'codex_activity_monitor.agents.antigravity.projection',
    'codex_activity_monitor.antigravity_history': 'codex_activity_monitor.agents.antigravity.history',
    'codex_activity_monitor.antigravity_tokenizer': 'codex_activity_monitor.agents.antigravity.tokenizer',
}


class _AliasLoader(importlib.abc.Loader):
    def __init__(self, fullname, target):
        self.fullname = fullname
        self.target = target
        self._canonical_spec = None

    def create_module(self, spec):
        module = importlib.import_module(self.target)
        self._canonical_spec = module.__spec__
        return module

    def exec_module(self, module):
        if self._canonical_spec is not None:
            module.__spec__ = self._canonical_spec
            module.__name__ = self._canonical_spec.name
            module.__package__ = self._canonical_spec.parent
        parent, _, attr = self.fullname.rpartition('.')
        if parent in sys.modules:
            setattr(sys.modules[parent], attr, module)


class _AliasFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        mapped = LEGACY_MODULE_ALIASES.get(fullname)
        if mapped is not None:
            return importlib.machinery.ModuleSpec(fullname, _AliasLoader(fullname, mapped))
        return None


if not any(isinstance(finder, _AliasFinder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _AliasFinder())


def __getattr__(name):
    fullname = f'{__name__}.{name}'
    if fullname in LEGACY_MODULE_ALIASES:
        module = importlib.import_module(fullname)
        globals()[name] = module
        return module
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
