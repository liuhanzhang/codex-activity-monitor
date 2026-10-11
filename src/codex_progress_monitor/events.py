"""Filter protocol to progress-relevant data before it crosses the local socket."""

from dataclasses import dataclass
from datetime import datetime
import json


DELTA_KINDS = {
    'response.output_text.delta': 'text',
    'response.custom_tool_call_input.delta': 'tool_input',
    'response.function_call_arguments.delta': 'tool_input',
    'response.reasoning_summary_text.delta': 'summary',
}
TOOL_TYPES = {'commandExecution', 'mcpToolCall', 'dynamicToolCall', 'collabToolCall',
              'webSearch', 'fileChange', 'imageGeneration', 'imageView'}


def thread_metadata(value):
    return {k: value[k] for k in ('id','name','cwd','model','modelProvider','status','ephemeral','threadSource') if k in value}


def item_metadata(value):
    allowed = ('type','id','status','name','call_id','metadata','server','tool','command',
               'cwd','exitCode','durationMs','processId','source','receiverThreadId')
    result = {k: value[k] for k in allowed if k in value}
    if 'command' in result:
        result['command'] = str(result['command']).split('\n', 1)[0][:160]
    if 'metadata' in result:
        result['metadata'] = {k:v for k,v in result['metadata'].items() if k in ('turn_id','thread_id')}
    # Completed text crosses IPC once for reconciliation, then is released by the engine.
    for key in ('text','input','arguments','summary','content'):
        if key in value:
            if value.get('type')=='userMessage':
                continue
            if value.get('type')=='reasoning' and key in ('text','content'):
                continue
            result[key] = value[key]
    return result


def filter_event(direction, event):
    if direction == 'upstream':
        kind = event.get('type','')
        allowed = set(DELTA_KINDS) | {'response.output_item.added','response.output_item.done',
                                    'response.reasoning_summary_part.added',
                                    'response.completed','response.failed','response.incomplete'}
        if kind not in allowed:
            return None
        if 'item' in event:
            result = {k:v for k,v in event.items() if k in ('type','sequence_number','output_index')}
            result['item'] = item_metadata(event['item'])
            if kind.endswith('.added'):
                for k in ('text','input','arguments','summary','content'):
                    result['item'].pop(k,None)
            return result
        if 'response' in event:
            response = event['response']
            return {'type':kind, 'response':{k:response[k] for k in ('id','model','status','usage') if k in response}}
        return {k:v for k,v in event.items() if k in ('type','item_id','delta','content_index','summary_index','sequence_number')}
    if direction == 'protocol_in':
        if 'method' not in event:
            reply = event.get('result',{})
            safe = {'decision':reply['decision']} if isinstance(reply,dict) and 'decision' in reply else {}
            return {'id':event['id'], 'result':safe} if 'id' in event else None
        method = event['method']
        params = event.get('params',{})
        if method not in ('initialize','initialized','thread/start','thread/resume','thread/read',
                          'thread/name/set','turn/start','turn/interrupt','config/read','model/list'):
            return None
        return {'id':event.get('id'), 'method':method,
                'params':{k:v for k,v in params.items() if k in ('threadId','turnId','model','cwd','name','ephemeral','threadSource')}}
    if direction != 'protocol_out':
        return event
    method, params = event.get('method'), event.get('params',{})
    if not method:
        result = event.get('result',{})
        if not isinstance(result,dict):
            return None
        safe = {k:result[k] for k in ('codexHome','model','reasoningEffort') if k in result}
        if isinstance(result.get('thread'),dict):
            safe['thread'] = thread_metadata(result['thread'])
            safe['thread']['turns'] = [{'id':t.get('id'),'status':t.get('status')} for t in result['thread'].get('turns',[])[-1:]]
        if isinstance(result.get('config'),dict):
            safe['config'] = {'model':result['config'].get('model')}
        return {'id':event.get('id'), 'result':safe} if safe else None
    if not method.startswith(('thread/','turn/','item/','serverRequest/','model/')):
        return None
    # Usage/diff/context bodies do not help live activity and can be large.
    if method in ('thread/tokenUsage/updated','turn/diff/updated','turn/plan/updated'):
        return None
    result = {'method':method}
    if 'id' in event:
        result['id'] = event['id']
    safe = {k:v for k,v in params.items() if k in ('threadId','turnId','itemId','requestId','startedAtMs',
                                                 'completedAtMs','name','threadName','status','model','toModel','summaryIndex')}
    if 'thread' in params:
        safe['thread'] = thread_metadata(params['thread'])
    if 'turn' in params:
        safe['turn'] = {k:v for k,v in params['turn'].items() if k in ('id','status')}
    if 'item' in params:
        safe['item'] = item_metadata(params['item'])
    if method in ('item/agentMessage/delta','item/reasoning/summaryTextDelta'):
        safe['delta'] = params.get('delta','')
    elif method=='item/commandExecution/outputDelta':
        safe['output_chars'] = len(params.get('delta',''))
    result['params'] = safe
    return result


def decode_log(line):
    try:
        row = json.loads(line)
        message = row.get('fields',{}).get('message','')
        for prefix in ('Received message ', 'SSE event: '):
            if message.startswith(prefix):
                value = json.loads(message[len(prefix):])
                return value, row.get('timestamp')
    except (ValueError,UnicodeError,AttributeError):
        pass
    return None, None


@dataclass
class Event:
    backend: str
    direction: str
    data: dict
    at: float
    wall_time: float

    @classmethod
    def from_row(cls, row, backend='replay'):
        at = row.get('observed_ns',row.get('observed_monotonic_ns',0))/1e9
        wall = row.get('observed_timestamp',0)
        if not at:
            wall = wall or (datetime.fromisoformat(row['source_timestamp'].replace('Z','+00:00')).timestamp() if row.get('source_timestamp') else 0)
            at = wall
        return cls(row.get('backend',backend), row['direction'],row.get('event',{}),at,wall)
