"""Filter protocol to progress-relevant data before it crosses the local socket."""

from dataclasses import dataclass
from datetime import datetime
import json

from .tool_labels import command_name, command_summary
from .usage import response_metadata, usage_fields


DELTA_KINDS = {
    'response.output_text.delta': 'text',
    'response.custom_tool_call_input.delta': 'tool_input',
    'response.function_call_arguments.delta': 'tool_input',
    'response.reasoning_summary_text.delta': 'summary',
}
TOOL_TYPES = {'commandExecution', 'mcpToolCall', 'dynamicToolCall', 'collabAgentToolCall',
              'webSearch', 'fileChange', 'imageGeneration', 'imageView'}

# Explicit protocol intake. Unknown and out-of-scope methods must not silently
# become progress simply because they share a thread/item prefix.
NOTIFICATION_KINDS = {}
for _kind, _methods in {
    'error': 'error',
    'metadata': 'thread/started thread/name/updated thread/settings/updated model/rerouted',
    'thread_status': 'thread/status/changed',
    'thread_boundary': 'thread/archived thread/deleted thread/unarchived thread/closed thread/reverted',
    'turn': 'turn/started turn/completed',
    'usage': 'thread/tokenUsage/updated',
    'item': 'item/started item/completed',
    'review': 'item/autoApprovalReview/started item/autoApprovalReview/completed autoApprovalReview/strictReviewRequired',
    'plan': 'turn/plan/updated',
    'visible_text': 'item/agentMessage/delta item/plan/delta item/reasoning/summaryTextDelta item/reasoning/summaryPartAdded',
    'tool_progress': 'item/commandExecution/outputDelta item/commandExecution/terminalInteraction item/fileChange/outputDelta item/fileChange/patchUpdated item/mcpToolCall/progress',
    'resolved': 'serverRequest/resolved',
    'auth': 'modelProvider/authRecoveryStarted modelProvider/authRecoveryCompleted',
    'hook': 'hook/started hook/completed',
    'goal': 'thread/goal/updated thread/goal/cleared',
    'project': 'project/changed thread/project/updated',
    'environment': 'thread/environment/connected thread/environment/disconnected',
    'login': 'mcpServer/oauthLogin/completed account/login/completed',
    'startup': 'mcpServer/startupStatus/updated',
    'compaction': 'thread/compacted',
    'verification': 'model/verification',
    'buffering': 'model/safetyBuffering/updated',
    'warning': 'warning guardianWarning',
    'raw_identity': 'rawResponseItem/completed',
}.items():
    NOTIFICATION_KINDS.update(dict.fromkeys(_methods.split(), _kind))

BLOCKING_REQUESTS = {
    'item/commandExecution/requestApproval', 'item/fileChange/requestApproval',
    'item/permissions/requestApproval', 'item/tool/requestUserInput',
    'mcpServer/elicitation/request', 'applyPatchApproval', 'execCommandApproval',
}
SERVER_REQUESTS = BLOCKING_REQUESTS | {'item/tool/call', 'account/chatgptAuthTokens/refresh'}
ITEM_TYPES = {'userMessage', 'hookPrompt', 'agentMessage', 'functionCallOutput', 'plan',
              'reasoning', 'subAgentActivity', 'sleep', 'enteredReviewMode',
              'exitedReviewMode', 'contextCompaction'} | TOOL_TYPES


def brief(value, limit=160):
    return str(value).splitlines()[0][:limit] if value is not None and str(value) else ''


def file_metadata(changes):
    if not isinstance(changes, list):
        return []
    return [{'path': brief(c.get('path'))} for c in changes[:64] if isinstance(c, dict) and c.get('path')]


def thread_metadata(value):
    return {k: value[k] for k in ('id','name','cwd','model','modelProvider','status','ephemeral','threadSource') if k in value}


def item_metadata(value):
    allowed = ('type','id','status','name','namespace','call_id','metadata','server','tool','command',
               'cwd','exitCode','durationMs','processId','source','receiverThreadIds',
               'senderThreadId','agentThreadId','kind','durationMs','success','delivery')
    result = {k: value[k] for k in allowed if k in value}
    if 'command' in result:
        result['command_name'] = command_name(result['command'])
        result['command'] = command_summary(result['command'])
    if 'metadata' in result:
        result['metadata'] = {k:v for k,v in result['metadata'].items() if k in ('turn_id','thread_id')}
    if 'receiverThreadIds' in result:
        result['receiverThreadIds'] = result['receiverThreadIds'][:64]
    if isinstance(value.get('agentsStates'),dict):
        result['agentsStates'] = {str(k)[:256]: {'status': v.get('status')}
                                 for k,v in list(value['agentsStates'].items())[:64] if isinstance(v,dict)}
    if 'changes' in value:
        result['changes'] = file_metadata(value['changes'])
        result['file_count'] = len(value['changes']) if isinstance(value['changes'],list) else 0
    if isinstance(value.get('appContext'),dict):
        result['appContext'] = {k:brief(v) for k,v in value['appContext'].items() if k in ('appName','actionName')}
    # Only model text / arguments cross IPC for reconciliation. Arbitrary tool
    # result objects, hook fragments and user input never need to cross it.
    for key in ('text','input','arguments','summary','content'):
        if key in value:
            if value.get('type') not in ('agentMessage','plan','reasoning','message','custom_tool_call','function_call'):
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
            result['identity'] = {k:v for k,v in event.get('identity',{}).items() if k in ('thread_id','turn_id')}
            if kind.endswith('.added'):
                for k in ('text','input','arguments','summary','content'):
                    result['item'].pop(k,None)
            return result
        if 'response' in event:
            response = event['response']
            result = {'type':kind, 'response':response_metadata(response)}
            result['identity'] = {k:v for k,v in event.get('identity',{}).items() if k in ('thread_id','turn_id')}
            result['identity'].update({k:event[k] for k in ('thread_id','turn_id') if k in event})
            return result
        return {k:v for k,v in event.items() if k in ('type','item_id','delta','content_index','summary_index','sequence_number')}
    if direction == 'protocol_in':
        if 'method' not in event:
            reply = event.get('result',{})
            safe = {k:reply[k] for k in ('decision','success') if isinstance(reply,dict) and k in reply}
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
    if method not in NOTIFICATION_KINDS and method not in SERVER_REQUESTS:
        return None
    result = {'method':method}
    if 'id' in event:
        result['id'] = event['id']
    safe = {k:v for k,v in params.items() if k in ('threadId','turnId','itemId','requestId','startedAtMs',
             'completedAtMs','name','threadName','status','model','toModel','summaryIndex',
             'reviewId','targetItemId','willRetry','showBufferingUi','processId','loginId',
             'success','environmentId','projectId','callId','tool','namespace')}
    if method in ('applyPatchApproval','execCommandApproval'):
        safe['threadId'] = params.get('conversationId')
        safe['itemId'] = params.get('callId')
    if isinstance(params.get('threadSettings'),dict):
        safe['threadSettings'] = {k:params['threadSettings'][k] for k in ('model',) if k in params['threadSettings']}
    if method=='thread/tokenUsage/updated':
        usage=params.get('tokenUsage',{})
        safe['tokenUsage']={k:usage_fields(usage.get(k),camel=True) for k in ('total','last')} if isinstance(usage,dict) else {}
    if isinstance(params.get('review'),dict):
        safe['review'] = {'status':params['review'].get('status')}
    if isinstance(params.get('goal'),dict):
        safe['goal'] = {'status':params['goal'].get('status')}
    if isinstance(params.get('run'),dict):
        safe['run'] = {k:v for k,v in params['run'].items() if k in ('id','executionMode','scope','status','eventName')}
    if isinstance(params.get('plan'),list):
        safe['plan'] = [{'step':brief(v.get('step')), 'status':v.get('status')} for v in params['plan'][:64] if isinstance(v,dict)]
    if 'changes' in params:
        safe['changes'] = file_metadata(params['changes'])
        safe['file_count'] = len(params['changes']) if isinstance(params['changes'],list) else 0
    if 'thread' in params:
        safe['thread'] = thread_metadata(params['thread'])
    if 'turn' in params:
        safe['turn'] = {k:v for k,v in params['turn'].items() if k in ('id','status')}
    if 'item' in params:
        safe['item'] = item_metadata(params['item'])
        if method=='rawResponseItem/completed':
            safe['item'] = {k:v for k,v in safe['item'].items() if k in ('id','type','call_id')}
    if method in ('item/agentMessage/delta','item/plan/delta','item/reasoning/summaryTextDelta'):
        safe['delta'] = params.get('delta','')
    elif method in ('item/commandExecution/outputDelta','item/fileChange/outputDelta'):
        safe['output_chars'] = len(params.get('delta',''))
    elif method=='item/mcpToolCall/progress':
        safe['has_progress'] = bool(params.get('message'))
    if method=='model/verification':
        safe['verification_present'] = bool(params.get('verifications'))
    result['params'] = safe
    return result


def decode_log(line):
    try:
        row = json.loads(line)
        message = row.get('fields',{}).get('message','')
        for prefix in ('Received message ', 'SSE event: '):
            if message.startswith(prefix):
                value = json.loads(message[len(prefix):])
                identity={}
                spans = row.get('spans')
                for span in (spans if isinstance(spans,list) else []) + [row.get('span',{}),row.get('fields',{})]:
                    if not isinstance(span,dict):
                        continue
                    thread=span.get('thread_id',span.get('conversation_id',span.get('conversation.id')))
                    if thread:
                        identity['thread_id']=thread
                    if span.get('turn_id'):
                        identity['turn_id']=span['turn_id']
                if identity:
                    value['identity']=identity
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
    replayed: bool = False

    @classmethod
    def from_row(cls, row, backend='replay'):
        at = row.get('observed_ns',row.get('observed_monotonic_ns',0))/1e9
        wall = row.get('observed_timestamp',0)
        if not at:
            wall = wall or (datetime.fromisoformat(row['source_timestamp'].replace('Z','+00:00')).timestamp() if row.get('source_timestamp') else 0)
            at = wall
        return cls(row.get('backend',backend), row['direction'],row.get('event',{}),at,wall,bool(row.get('replayed',False)))
