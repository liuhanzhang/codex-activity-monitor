"""Official output and reasoning token accounting, without model text.

The response ledger and cumulative server snapshots are alternative views of
the same usage. Snapshot differences require a verified turn baseline; `last`
is never added. All clocks below are original observation clocks.
"""

from collections import OrderedDict
from copy import deepcopy
import math


MAX_THREADS = 64
MAX_TURNS = 4
MAX_RESPONSES = 256
MAX_ITEMS = 512
MAX_PENDING = 128
RETENTION_SECONDS = 600


def token_number(value):
    return value if type(value) is int and 0 <= value <= 2**63-1 else None


def identifier(value):
    return isinstance(value,str) and 0<len(value)<=256


def usage_fields(value, camel=False):
    if not isinstance(value, dict):
        return {}
    raw_output=value.get('outputTokens' if camel else 'output_tokens')
    output = token_number(raw_output)
    details = value.get('output_tokens_details')
    raw_reasoning=(value.get('reasoningOutputTokens') if camel else
                   details.get('reasoning_tokens') if isinstance(details, dict) else None)
    reasoning = token_number(raw_reasoning)
    if (raw_output is not None and output is None) or (raw_reasoning is not None and reasoning is None):
        return {}
    if output is not None and reasoning is not None and reasoning > output:
        return {}
    return {k: v for k, v in (('output_tokens', output), ('reasoning_tokens', reasoning)) if v is not None}


def pair(value):
    return [value.get('output_tokens'), value.get('reasoning_tokens')]


def complete_pair(values):
    return len(values) == 2 and all(token_number(v) is not None for v in values) and values[1] <= values[0]


def response_metadata(response):
    """Allowlist numeric usage and generated identities, never attribution bodies.

    The installed transport sometimes omits output but supplies item attribution.
    Only a complete, nonnegative attribution whose output sum matches usage is
    usable. Historical/input-only IDs are excluded, including zero-output IDs.
    """
    result = {k: response[k] for k in ('id', 'model', 'status') if identifier(response.get(k))}
    result['usage'] = usage_fields(response.get('usage'))
    output = response.get('output')
    items = output if isinstance(output, list) else []
    valid = len(items) <= MAX_ITEMS and all(isinstance(i, dict) and identifier(i.get('id'))
                                          and identifier(i.get('type')) for i in items)
    result['item_ids'] = [i['id'] for i in items[:MAX_ITEMS] if isinstance(i, dict) and identifier(i.get('id'))]
    result['items_complete'] = bool(items) and valid and len(set(result['item_ids'])) == len(items)
    result['items_source'] = 'output'
    if not items:
        usage = response.get('usage')
        attribution = usage.get('attribution') if isinstance(usage, dict) else None
        attributed = attribution.get('items') if isinstance(attribution, dict) else None
        if isinstance(attributed, dict) and len(attributed) <= MAX_ITEMS and attributed:
            counts = {k: token_number(v.get('output_tokens')) if isinstance(v, dict) else None
                      for k, v in attributed.items()}
            if (all(identifier(k) and v is not None for k, v in counts.items())
                    and sum(counts.values()) == result['usage'].get('output_tokens')):
                result['item_ids'] = [k for k, v in counts.items() if v > 0]
                result['items_complete'] = bool(result['item_ids'])
                result['items_source'] = 'validated_attribution'
    return result


def new_turn(observed=False):
    return {'observed_start': observed, 'coverage': observed, 'responses': OrderedDict(),
            'baseline': None, 'snapshot': None, 'terminal': False, 'diagnostics': [], 'saturated': False,
            'started_at': None}


class UsageBook:
    def __init__(self):
        self.threads = OrderedDict()
        self.items = OrderedDict()
        self.pending = OrderedDict()
        self.diagnostics = OrderedDict()

    def identity(self, backend, thread_id, engine):
        return (engine.backends.get(backend, {}).get('home') or backend, thread_id)

    def state(self, identity, at=0):
        if identity not in self.threads:
            self.threads[identity] = {'turns': OrderedDict(), 'current': None, 'total': None,
                                      'total_turn': None, 'new_thread': False,
                                      'total_last': None,
                                      'updated_at': at}
        state = self.threads[identity]
        state['updated_at'] = max(state['updated_at'], at)
        self.threads.move_to_end(identity)
        while len(self.threads) > MAX_THREADS:
            self.threads.popitem(last=False)
        return state

    def turn(self, state, turn_id):
        if turn_id not in state['turns']:
            state['turns'][turn_id] = new_turn()
        while len(state['turns']) > MAX_TURNS:
            state['turns'].popitem(last=False)
        return state['turns'][turn_id]

    def note(self, turn, reason):
        if reason not in turn['diagnostics']:
            turn['diagnostics'] = (turn['diagnostics'] + [reason])[-16:]

    def start(self, identity, turn_id, replayed, at):
        state = self.state(identity, at)
        record = self.turn(state, turn_id)
        if state['current'] == turn_id or record['observed_start'] or record['terminal']:
            return
        previous = state['turns'].get(state['current'])
        state['current'] = turn_id
        record['observed_start'] = not replayed
        record['coverage'] = not replayed
        record['started_at'] = at
        # A closed predecessor must have every observed model item covered by a
        # response, and its last cumulative snapshot must be reconciled exactly.
        if state['new_thread'] and not replayed:
            record['baseline'] = [0, 0]
        elif previous and previous['terminal'] and previous['coverage'] and state['total_turn'] in state['turns']:
            old_id = state['total_turn']
            old = state['turns'][old_id]
            counts = self.ledger(old)
            old_items = [i for i in self.items.values() if i['identity'] == identity and i['turn_id'] == old_id]
            covered = {i for r in old['responses'].values() for i in r['item_ids']}
            if (old is previous and old['baseline'] is not None and complete_pair(counts)
                    and [old['baseline'][n]+counts[n] for n in range(2)] == state['total']
                    and all(i['id'] in covered for i in old_items)):
                record['baseline'] = list(state['total'])
        state['new_thread'] = False
        if replayed:
            self.note(record, 'replayed_turn_start')

    def ledger(self, record):
        return [sum(r['usage'][n] for r in record['responses'].values())
                if all(r['usage'][n] is not None for r in record['responses'].values()) else None
                for n in range(2)]

    def counts(self, record):
        ledger = self.ledger(record) if record['coverage'] else [None, None]
        if record['snapshot'] is not None:
            # A verified cumulative difference covers responses missing during
            # a gap. Later response events are not added to that difference.
            if record['coverage'] and complete_pair(ledger) and all(ledger[n] >= record['snapshot'][n] for n in range(2)):
                return ledger, 'responses'
            return list(record['snapshot']), 'cumulative_difference'
        return ledger, 'responses' if record['coverage'] else 'unavailable'

    def gap(self, backend, engine, reason, origin_at=None):
        for thread in engine.threads.values():
            if thread.backend != backend:
                continue
            identity = self.identity(backend, thread.id, engine)
            state = self.threads.get(identity)
            if state and thread.turn_id and thread.status in ('inProgress', 'unknown'):
                record = self.turn(state, thread.turn_id)
                if origin_at is not None and record['started_at'] is not None and origin_at < record['started_at']:
                    continue
                record['coverage'] = False
                record['snapshot'] = None
                self.note(record, reason)

    def ingest(self, event, engine):
        kind = event.data.get('type')
        method = event.data.get('method')
        params = event.data.get('params', {})
        backend = event.backend
        if kind and (kind.endswith('.delta') or kind == 'response.reasoning_summary_part.added'):
            return
        if method and ('Delta' in method or method.endswith('/delta')):
            return
        if event.direction == 'lifecycle':
            if kind in ('bridge/gap', 'bridge/disconnected', 'bridge/stopped', 'bridge/reconnected'):
                self.gap(backend, engine, kind)
            elif kind == 'bridge/heartbeat' and engine.backends[backend].get('gap_at') == event.at:
                self.gap(backend, engine, 'bridge/gap')
            return
        if event.direction == 'protocol_out' and params.get('threadId'):
            thread_id = params['threadId']
            turn_id = params.get('turnId') or params.get('turn', {}).get('id')
            identity = self.identity(backend, thread_id, engine)
            if turn_id and (backend, turn_id) in engine.conflicted_turns:
                state = self.state(identity, event.at)
                record = self.turn(state, turn_id)
                record.update(coverage=False, baseline=None, snapshot=None)
                self.note(record, 'identity_conflict')
                self.gap(backend, engine, 'identity_conflict')
                return
            if method == 'turn/started' and getattr(event,'turn_started_accepted',False):
                self.start(identity, turn_id, event.replayed, event.at)
            elif method == 'turn/completed':
                state = self.state(identity, event.at)
                self.turn(state, turn_id)['terminal'] = True
                if state['current'] is None:
                    state['current'] = turn_id
            elif method == 'thread/tokenUsage/updated':
                self.cumulative(identity, turn_id, params.get('tokenUsage', {}), event)
            elif method in ('thread/reverted', 'thread/compacted'):
                state = self.state(identity, event.at)
                state.update(total=None,total_turn=None)
                for record in state['turns'].values():
                    record.update(baseline=None,snapshot=None,coverage=False)
                    self.note(record, method)
        relevant = method in ('item/started', 'item/completed', 'turn/started') or kind in (
            'response.output_item.added','response.output_item.done',
            'response.completed','response.failed','response.incomplete')
        if not relevant:
            return
        if method in ('item/started', 'item/completed') or kind in ('response.output_item.added', 'response.output_item.done'):
            self.item(event, engine)
        if kind in ('response.completed', 'response.failed', 'response.incomplete'):
            response = event.data.get('response', {})
            if not self.response(event, engine):
                key = (backend, response.get('id'))
                self.pending.setdefault(key, event)
                while len(self.pending) > MAX_PENDING:
                    _, abandoned = self.pending.popitem(last=False)
                    self.diagnostics['pending_limit'] = self.diagnostics.get('pending_limit', 0)+1
                    self.gap(abandoned.backend,engine,'pending_limit',origin_at=abandoned.at)
        for key, pending in list(self.pending.items()):
            if event.at-pending.at > RETENTION_SECONDS:
                self.pending.pop(key)
                self.diagnostics['unassociated_expired'] = self.diagnostics.get('unassociated_expired', 0)+1
                # Expiry is discovered later; it is not a new transport gap.
                # Never invalidate a turn begun after that usage.
                self.gap(pending.backend,engine,'unassociated_usage_expired',origin_at=pending.at)
            elif self.response(pending, engine):
                self.pending.pop(key)

    def item(self, event, engine):
        params = event.data.get('params', {})
        item = params.get('item', event.data.get('item', {}))
        item_id = item.get('id')
        if (not item_id or item.get('delivery') == 'async' or item.get('type') not in
                ('reasoning','agentMessage','message','custom_tool_call','function_call','web_search_call','image_generation_call')):
            return
        known = engine.items.get((event.backend, item_id), {})
        turn_id = params.get('turnId') or item.get('metadata', {}).get('turn_id') or known.get('turn_id')
        thread_id = params.get('threadId') or engine.turns.get((event.backend, turn_id))
        if not thread_id or not turn_id or (event.backend, turn_id) in engine.conflicted_turns:
            return
        identity = self.identity(event.backend, thread_id, engine)
        state = self.state(identity, event.at)
        self.turn(state, turn_id)
        key = (event.backend, item_id)
        current = self.items.setdefault(key, {'id': item_id, 'identity': identity, 'turn_id': turn_id,
                                             'type': item.get('type')})
        if current['identity'] != identity or current['turn_id'] != turn_id:
            current['conflict'] = True
            for owner,turn in ((identity,turn_id),(current['identity'],current['turn_id'])):
                affected=self.state(owner,event.at)
                record=self.turn(affected,turn)
                record.update(coverage=False,baseline=None,snapshot=None)
                self.note(record,'item_identity_conflict')
            return
        self.items.move_to_end(key)
        while len(self.items) > MAX_ITEMS:
            self.items.popitem(last=False)

    def response(self, event, engine):
        response = event.data.get('response', {})
        response_id = response.get('id')
        if not response_id:
            if response.get('usage'):
                self.gap(event.backend,engine,'response_id_missing')
            return True
        ids = response.get('item_ids', [])
        identity = event.data.get('identity', {})
        turn_id = identity.get('turn_id')
        thread_id = identity.get('thread_id')
        owners = set()
        unknown = False
        for item_id in ids:
            item = self.items.get((event.backend, item_id))
            if item:
                owners.add((item['identity'], item['turn_id']))
            else:
                known = engine.items.get((event.backend, item_id), {})
                owner = engine.turns.get((event.backend, known.get('turn_id')))
                if owner:
                    owners.add((self.identity(event.backend, owner, engine), known['turn_id']))
                else:
                    unknown = True
        if turn_id and (event.backend, turn_id) in engine.conflicted_turns:
            return True
        if turn_id:
            mapped = engine.turns.get((event.backend, turn_id))
            if mapped and thread_id and mapped != thread_id:
                owners.add((self.identity(event.backend, mapped, engine), turn_id))
            thread_id = thread_id or mapped
        if thread_id and turn_id:
            owners.add((self.identity(event.backend, thread_id, engine), turn_id))
        elif thread_id and any(owner[1]!=thread_id for owner, _ in owners):
            return False
        if not owners and response.get('usage',{}).get('output_tokens') == 0:
            return True  # Unassociated transport warmup has no generated usage.
        if not response.get('items_complete') and not (thread_id and turn_id):
            for owner,turn in owners:
                affected=self.state(owner,event.at)
                record=self.turn(affected,turn)
                record.update(coverage=False,baseline=None,snapshot=None)
                self.note(record,'response_membership_incomplete')
            return False
        if unknown and not (thread_id and turn_id):
            return False
        if len(owners) != 1:
            if owners:
                for owner, turn in owners:
                    state = self.state(owner, event.at)
                    record = self.turn(state, turn)
                    record.update(coverage=False, baseline=None, snapshot=None)
                    self.note(record, 'response_identity_conflict')
            return False
        owner, turn_id = next(iter(owners))
        state = self.state(owner, event.at)
        record = self.turn(state, turn_id)
        values = pair(response.get('usage', {}))
        entry = {'id': response_id, 'backend': event.backend, 'turn_id': turn_id,
                 'item_ids': list(ids), 'usage': values}
        previous = record['responses'].get(response_id)
        if previous:
            conflict=any(old is not None and new is not None and old!=new for old,new in zip(previous['usage'],values))
            if conflict or set(previous['item_ids']) != set(ids):
                record.update(coverage=False, snapshot=None, baseline=None)
                self.note(record, 'response_conflict')
            else:
                previous['usage']=[old if old is not None else new for old,new in zip(previous['usage'],values)]
            return True
        if record['saturated'] or len(record['responses']) >= MAX_RESPONSES:
            record.update(saturated=True, coverage=False)
            self.note(record, 'response_limit')
            return True
        record['responses'][response_id] = entry
        if any(v is None for v in values):
            self.note(record, 'usage_field_missing')
        # Late old usage invalidates a newer cumulative baseline, never its
        # independently associated response ledger.
        if state['current'] and state['current'] != turn_id:
            current = self.turn(state, state['current'])
            current.update(baseline=None, snapshot=None)
            self.note(current, 'late_previous_turn_usage')
        return True

    def cumulative(self, identity, turn_id, usage, event):
        if not turn_id:
            return
        state = self.state(identity, event.at)
        record = self.turn(state, turn_id)
        total = pair(usage.get('total', {}))
        if not complete_pair(total):
            self.note(record, 'cumulative_field_missing')
            return
        if state['current'] and state['current'] != turn_id:
            # An older turn notification may be a delayed cumulative prefix,
            # not a regression of current accounting. It cannot invalidate the
            # independently identified current response ledger or move total
            # backwards; neither can it prove a current-turn difference.
            current = self.turn(state, state['current'])
            current.update(baseline=None, snapshot=None)
            self.note(current, 'late_previous_turn_snapshot')
            self.note(record, 'stale_cumulative_prefix')
            return
        if state['total'] is not None and any(total[n] < state['total'][n] for n in range(2)):
            for old in state['turns'].values():
                old.update(baseline=None, snapshot=None, coverage=False)
                self.note(old, 'cumulative_regression')
            state['total'] = None
            return
        baseline = record['baseline']
        if baseline is not None:
            diff = [total[n]-baseline[n] for n in range(2)]
            if complete_pair(diff):
                ledger=self.ledger(record)
                if (record['coverage'] and complete_pair(ledger)
                        and not all(ledger[n]>=diff[n] for n in range(2))
                        and not all(ledger[n]<=diff[n] for n in range(2))):
                    record.update(snapshot=None,baseline=None,coverage=False)
                    self.note(record,'cumulative_response_conflict')
                else:
                    record['snapshot'] = diff
            else:
                record.update(snapshot=None, baseline=None, coverage=False)
                self.note(record, 'baseline_conflict')
        state['total'], state['total_turn'],state['total_last'] = total, turn_id, pair(usage.get('last',{}))

    def view(self, identity, turn_id):
        state = self.threads.get(identity)
        record = state['turns'].get(turn_id) if state else None
        counts, source = self.counts(record) if record else ([None, None], 'unavailable')
        return {'turn_id': turn_id, 'output_tokens': counts[0], 'reasoning_tokens': counts[1],
                'source': source, 'diagnostics': list(record['diagnostics']) if record else ['turn_start_unobserved']}

    def dump(self):
        return {'version': 2, 'threads': [{'home': home, 'id': thread, 'state': deepcopy(state)}
                                         for (home, thread), state in self.threads.items()]}

    def restore(self, data):
        if not isinstance(data, dict) or data.get('version') != 2:
            return
        for entry in data.get('threads', [])[:MAX_THREADS]:
            try:
                previous=entry['state']
                if not identifier(entry['id']) or not isinstance(entry['home'],str) or len(entry['home'])>4096:
                    continue
                state={'current':previous['current'],'total':previous['total'],'total_turn':previous['total_turn'],
                       'total_last':previous.get('total_last'),'new_thread':False,'updated_at':previous['updated_at']}
                if not isinstance(state['updated_at'],(int,float)) or not math.isfinite(state['updated_at']):
                    continue
                turns = previous['turns']
                if len(turns) > MAX_TURNS or any(len(t['responses']) > MAX_RESPONSES for t in turns.values()):
                    continue
                restored=OrderedDict()
                for turn_id,old in turns.items():
                    if not identifier(turn_id):
                        raise ValueError('Invalid recovery turn')
                    record=new_turn(bool(old['observed_start']))
                    started_at=old.get('started_at')
                    if started_at is not None:
                        if not isinstance(started_at,(int,float)) or not math.isfinite(started_at):
                            raise ValueError('Invalid recovery start')
                        record['started_at']=started_at
                    for key in ('coverage','terminal','saturated'):
                        record[key]=bool(old[key])
                    for key in ('baseline','snapshot'):
                        values=old[key]
                        record[key]=list(values) if values is not None and complete_pair(values) else None
                    record['diagnostics']=[str(v)[:64] for v in old['diagnostics'][:16]]
                    for response_id,response in old['responses'].items():
                        if (not identifier(response_id) or len(response['item_ids'])>MAX_ITEMS
                                or not all(identifier(i) for i in response['item_ids'])
                                or len(response['usage'])!=2 or any(v is not None and token_number(v) is None for v in response['usage'])):
                            raise ValueError('Invalid recovery response')
                        record['responses'][response_id]={'id':response_id,'backend':str(response['backend'])[:256],
                            'turn_id':turn_id,'item_ids':list(response['item_ids']),'usage':list(response['usage'])}
                    restored[turn_id]=record
                state['turns']=restored
                for key in ('total','total_last'):
                    if state[key] is not None and not complete_pair(state[key]):
                        state[key]=None
                self.threads[(entry['home'], entry['id'])] = state
            except (KeyError, TypeError, AttributeError, ValueError):
                continue

    def tick(self, now, engine):
        active={self.identity(t.backend,t.id,engine) for t in engine.threads.values()
                if t.status=='inProgress' or any(a.active for a in t.activities.values())}
        for identity, state in list(self.threads.items()):
            if identity not in active and now-state['updated_at'] > RETENTION_SECONDS:
                self.threads.pop(identity)
        for key, item in list(self.items.items()):
            state = self.threads.get(item['identity'])
            if state is None or item['turn_id'] not in state['turns']:
                self.items.pop(key)
