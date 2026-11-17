"""Codex Desktop local progress feedback."""

import importlib
import importlib.abc
import importlib.machinery
import sys
import types

__version__ = "0.1.0"

LEGACY_MODULE_ALIASES = {
    # agents.codex
    'codex_activity_monitor.bridge': 'codex_activity_monitor.agents.codex.bridge',
    'codex_activity_monitor.events': 'codex_activity_monitor.agents.codex.events',
    'codex_activity_monitor.codex_projection': 'codex_activity_monitor.agents.codex.projection',
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
    # agents.antigravity
    'codex_activity_monitor.antigravity_transport': 'codex_activity_monitor.agents.antigravity.transport',
    'codex_activity_monitor.antigravity_source': 'codex_activity_monitor.agents.antigravity.source',
    'codex_activity_monitor.antigravity_records': 'codex_activity_monitor.agents.antigravity.records',
    'codex_activity_monitor.antigravity_projection': 'codex_activity_monitor.agents.antigravity.projection',
    'codex_activity_monitor.antigravity_history': 'codex_activity_monitor.agents.antigravity.history',
    'codex_activity_monitor.antigravity_tokenizer': 'codex_activity_monitor.agents.antigravity.tokenizer',
    # core
    'codex_activity_monitor.agent_presentation': 'codex_activity_monitor.core.agent_presentation',
    'codex_activity_monitor.agent_relations': 'codex_activity_monitor.core.agent_relations',
    'codex_activity_monitor.engine': 'codex_activity_monitor.core.engine',
    'codex_activity_monitor.identity_budget': 'codex_activity_monitor.core.identity_budget',
    'codex_activity_monitor.menu_presentation': 'codex_activity_monitor.core.menu_presentation',
    'codex_activity_monitor.metrics': 'codex_activity_monitor.core.metrics',
    'codex_activity_monitor.overview': 'codex_activity_monitor.core.overview',
    'codex_activity_monitor.request_rate': 'codex_activity_monitor.core.request_rate',
    'codex_activity_monitor.state': 'codex_activity_monitor.core.state',
    'codex_activity_monitor.tokenizer_process': 'codex_activity_monitor.core.tokenizer_process',
    'codex_activity_monitor.tool_labels': 'codex_activity_monitor.core.tool_labels',
    'codex_activity_monitor.usage': 'codex_activity_monitor.core.usage',
    # timeline (timeline itself is a package with facade to timeline.timeline)
    'codex_activity_monitor.content_version': 'codex_activity_monitor.timeline.content_version',
    'codex_activity_monitor.query_request': 'codex_activity_monitor.timeline.query_request',
    'codex_activity_monitor.query_response': 'codex_activity_monitor.timeline.query_response',
    'codex_activity_monitor.timeline_query': 'codex_activity_monitor.timeline.timeline_query',
    'codex_activity_monitor.timeline_storage': 'codex_activity_monitor.timeline.timeline_storage',
    # history (history itself is a package with facade to history.history)
    'codex_activity_monitor.agent_identity': 'codex_activity_monitor.history.agent_identity',
    'codex_activity_monitor.history_refresh': 'codex_activity_monitor.history.history_refresh',
    'codex_activity_monitor.resource_budget': 'codex_activity_monitor.history.resource_budget',
    # runtime (runtime itself is a package with facade to runtime.runtime)
    'codex_activity_monitor.app_session': 'codex_activity_monitor.runtime.app_session',
    'codex_activity_monitor.collector': 'codex_activity_monitor.runtime.collector',
    'codex_activity_monitor.diagnostic_health': 'codex_activity_monitor.runtime.diagnostic_health',
    'codex_activity_monitor.diagnostic_log': 'codex_activity_monitor.runtime.diagnostic_log',
    'codex_activity_monitor.execution': 'codex_activity_monitor.runtime.execution',
    'codex_activity_monitor.json_encoding': 'codex_activity_monitor.runtime.json_encoding',
    'codex_activity_monitor.observation_lease': 'codex_activity_monitor.runtime.observation_lease',
    'codex_activity_monitor.observer_permit': 'codex_activity_monitor.runtime.observer_permit',
    'codex_activity_monitor.owned_files': 'codex_activity_monitor.runtime.owned_files',
    'codex_activity_monitor.publication': 'codex_activity_monitor.runtime.publication',
    'codex_activity_monitor.state_client': 'codex_activity_monitor.runtime.state_client',
    'codex_activity_monitor.state_transport': 'codex_activity_monitor.runtime.state_transport',
    # control
    'codex_activity_monitor.app_control': 'codex_activity_monitor.control.app_control',
    'codex_activity_monitor.display': 'codex_activity_monitor.control.display',
    'codex_activity_monitor.test_profile': 'codex_activity_monitor.control.test_profile',
    # i18n (i18n itself is a package with facade to i18n.i18n)
    'codex_activity_monitor.messages_control': 'codex_activity_monitor.i18n.messages_control',
    'codex_activity_monitor.messages_detail': 'codex_activity_monitor.i18n.messages_detail',
    'codex_activity_monitor.messages_diagnostics': 'codex_activity_monitor.i18n.messages_diagnostics',
    'codex_activity_monitor.messages_en': 'codex_activity_monitor.i18n.messages_en',
    'codex_activity_monitor.messages_zh_cn': 'codex_activity_monitor.i18n.messages_zh_cn',
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


class _PackageFacade(types.ModuleType):
    """Lazy forwarding package so `pkg` acts as both a subpackage and its primary module."""

    def __getattr__(self, name):
        if name == '_facade_target' or (name.startswith('__') and name.endswith('__')):
            raise AttributeError(name)
        target = importlib.import_module(self.__dict__['_facade_target'])
        if name in self.__dict__:
            return self.__dict__[name]
        return getattr(target, name)

    def __setattr__(self, name, value):
        if name == '_facade_target' or (name.startswith('__') and name.endswith('__')) or isinstance(value, types.ModuleType):
            super().__setattr__(name, value)
            return
        target = importlib.import_module(self.__dict__['_facade_target'])
        setattr(target, name, value)

    def __delattr__(self, name):
        if name in self.__dict__:
            super().__delattr__(name)
            return
        target = importlib.import_module(self.__dict__['_facade_target'])
        delattr(target, name)


def bind_package_facade(package_name, target_module):
    module = sys.modules[package_name]
    module.__dict__['_facade_target'] = target_module
    module.__class__ = _PackageFacade


if not any(isinstance(finder, _AliasFinder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _AliasFinder())


def __getattr__(name):
    fullname = f'{__name__}.{name}'
    if fullname in LEGACY_MODULE_ALIASES:
        module = importlib.import_module(fullname)
        globals()[name] = module
        return module
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
