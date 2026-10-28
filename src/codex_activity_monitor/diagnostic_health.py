"""Bounded current health. Historical counters alone never sustain an alarm."""

import time


class HealthMonitor:
    def __init__(self, log):
        self.log = log
        self.previous = {}
        self.counters = {}
        self.changed_at = {}

    def recent_increment(self, name, value, now):
        old = self.counters.get(name, 0)
        self.counters[name] = value
        if value > old:
            self.changed_at[name] = now
        return now - self.changed_at.get(name, -1000) < 5

    def snapshot(self, engine, history, metadata, timeline_error, persistence, now=None):
        now = time.monotonic() if now is None else now
        components = []

        def add(component, state, code, count=0):
            item = {'component': component, 'agent': 'codex', 'instance_id': self.log.instance,
                    'state': state, 'code': code, 'count': count}
            components.append(item)
            previous = self.previous.get(component)
            identity = (state, code)
            if identity != previous:
                recovered = previous is not None and previous[0] in ('degraded', 'unavailable') and state == 'healthy'
                self.log.emit(code, component=component,
                              level='error' if state == 'unavailable' else 'warning' if state == 'degraded' else 'info',
                              transition='recovered' if recovered else 'started', params={'count': count})
                self.previous[component] = identity

        add('collector', 'healthy', 'collector_responsive')
        backends = list(engine.backends.values())
        connected = [b for b in backends if b.get('connected')]
        gaps = sum(b.get('parse_errors', 0) + b.get('dropped_packets', 0) - b.get('unrelated_dropped_packets', 0)
                   for b in backends)
        # Heartbeats provide positive progress even when there are no token events.
        recent_gap = self.recent_increment('gaps', gaps, now)
        if connected:
            add('bridge', 'degraded' if recent_gap else 'healthy',
                'observation_gap' if recent_gap else 'bridge_connected', gaps)
        else:
            add('bridge', 'unknown', 'waiting_for_connection')
        errors = sum(engine.counts.get(k, 0) for k in ('invalid_ipc', 'oversized_ipc', 'timeline_projection_error'))
        invalid = self.recent_increment('ipc', errors, now)
        add('parsing', 'degraded' if invalid else 'healthy', 'parse_error' if invalid else 'parsing_ready', errors)
        streams = [s for s in engine.streams.values() if s.ended_at is None and not s.ended_by_history]
        failures = sum(s.count_state == 'failed' for s in streams)
        budgets = sum(s.count_state == 'budget' for s in streams)
        add('tokenizer', 'degraded' if failures or budgets else 'healthy',
            'tokenizer_error' if failures else 'count_budget' if budgets else 'tokenizer_ready', failures + budgets)
        history_stats = history.diagnostics()
        reads = history_stats.get('read_errors', 0)
        history_stalled = history_stats.get('reading_age_ms', 0) > 5000 or history_stats.get('worker_alive') is False
        add('history', 'degraded' if reads or history_stalled else 'healthy',
            'history_slow' if history_stalled else 'history_read_error' if reads else 'history_ready', reads)
        storage_error = metadata.error if metadata else 'metadata_unavailable'
        storage_stats = metadata.diagnostics() if metadata and hasattr(metadata, 'diagnostics') else {}
        if storage_stats.get('worker_alive') is False:
            storage_error = 'metadata_unavailable'
        elif storage_stats.get('writing_age_ms', 0) > 5000:
            storage_error = 'metadata_slow'
        add('metadata', 'degraded' if storage_error else 'healthy', storage_error or 'metadata_ready')
        add('timeline_query', 'unavailable' if timeline_error == 'query_unavailable' else 'healthy',
            'query_unavailable' if timeline_error == 'query_unavailable' else 'query_ready')
        failed = persistence.get('current_error') is not None or persistence.get('worker_alive') is False
        slow = persistence.get('writing_age_ms', 0) > 5000
        add('checkpoint', 'degraded' if failed or slow else 'healthy',
            'snapshot_write_failed' if failed else 'checkpoint_slow' if slow else 'checkpoint_ready', persistence.get('snapshot_write_failures', 0))
        logging = self.log.diagnostics()
        dropped = self.recent_increment('log_drops', logging['dropped'], now)
        add('logging', 'degraded' if logging['last_error'] or dropped else 'healthy',
            'logging_unavailable' if logging['last_error'] else 'log_dropped' if dropped else 'logging_ready', logging['dropped'])
        return {'version': 1, 'instance_id': self.log.instance, 'components': components}
