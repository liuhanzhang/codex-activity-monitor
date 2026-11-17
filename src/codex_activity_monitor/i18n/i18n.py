"""Language-independent messages and complete, explicitly registered catalogs."""

from string import Formatter
import math

from .messages_zh_cn import MESSAGES
from .messages_en import MESSAGES as ENGLISH_MESSAGES

DEFAULT_LANGUAGE = 'zh-CN'
CATALOGS = {DEFAULT_LANGUAGE: MESSAGES}
PHASE_KEYS = {
    'waiting': 'waiting', 'reasoning': 'reasoning', 'text': 'text', 'plan': 'plan',
    'tool_input': 'preparing', 'tool_ready': 'processing', 'tool': 'running',
    'approval': 'pending', 'input': 'pending', 'unknown': 'unknown',
    'review': 'review', 'compaction': 'compaction', 'sleep': 'sleep',
    'auth': 'auth', 'retry': 'retry', 'buffering': 'buffering', 'hook': 'processing',
    'completed': 'completed', 'interrupted': 'stopped', 'failed': 'failed', 'idle': 'idle',
}


def message(key, **params):
    return {'key': key, 'params': params}


def phase_message(phase):
    return message('activity.' + PHASE_KEYS.get(phase, 'processing'))


def available_languages():
    return tuple(CATALOGS)


def validate_language(language):
    if not isinstance(language, str) or language not in CATALOGS:
        raise ValueError(tr('cli.unsupported_language', language=language,
                            available=', '.join(available_languages())))
    return language


def register_catalog(language, catalog):
    """Only complete translations with matching template parameters are selectable."""
    def fields(template):
        if isinstance(template, dict) and set(template) == {'one', 'other'}:
            one, other = fields(template['one']), fields(template['other'])
            if one != other:
                raise ValueError('Plural forms must use matching parameters')
            return one
        if not isinstance(template, str):
            raise ValueError('Invalid translation template')
        return {field for _, field, _, _ in Formatter().parse(template) if field}
    if set(catalog) != set(MESSAGES) or any(
            fields(catalog[key]) != fields(value)
            for key, value in MESSAGES.items()):
        raise ValueError('Language catalog must cover all message keys and parameters')
    CATALOGS[language] = dict(catalog)


def tr(key, locale=DEFAULT_LANGUAGE, _depth=0, **params):
    catalog = CATALOGS.get(locale, MESSAGES)
    if _depth >= 8 or not isinstance(key, str):
        return catalog['activity.processing'].format(target='')
    template = catalog.get(key, catalog['activity.processing'])
    if isinstance(template, dict):
        template = template['one' if params.get('count') == 1 else 'other']
    values = {'target': '', 'language_hint': '', **params}
    values = {k: render_message(v, locale, _depth=_depth + 1) if isinstance(v, dict) and 'key' in v else v
              for k, v in values.items()}
    try:
        for _, field, _, _ in Formatter().parse(template):
            if field:
                value = values[field]
                if (not isinstance(value, (str, int, float)) or isinstance(value, bool) or
                        isinstance(value, float) and not math.isfinite(value)):
                    raise ValueError('Invalid message parameter')
        # Targets remain raw in the snapshot; punctuation belongs to presentation.
        if values.get('target'):
            values['target'] = ' · ' + str(values['target'])
        return template.format(**values)
    except (KeyError, ValueError, TypeError):
        return catalog['activity.processing'].format(target='')


def render_message(value, locale=DEFAULT_LANGUAGE, _depth=0):
    if not isinstance(value, dict) or not isinstance(value.get('key'), str):
        return tr('activity.processing', locale)
    params = value.get('params', {})
    if not isinstance(params, dict):
        return tr('activity.processing', locale)
    safe = {k: v for k, v in params.items() if isinstance(k, str) and k not in {'key', 'locale', '_depth'}}
    return tr(value['key'], locale, _depth=_depth, **safe)


register_catalog('en', ENGLISH_MESSAGES)
