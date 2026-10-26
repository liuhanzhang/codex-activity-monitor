#!/usr/bin/env python3
"""One real model turn in a dedicated test chat, observed by another client.

Both clients use the existing isolated Desktop backend. Record receipt times,
original JSON payload bytes, visible text increments, item states and reported
usage. Text rates measure local receipt, not upstream model generation. A token
rate is deliberately not inferred without a verified tokenizer.
"""

import argparse
from collections import Counter, deque
import datetime
import json
from pathlib import Path
import queue
import socket
import threading
import time
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from launch_isolated_desktop import ROOT, WebSocketProbe


def initialize(client, name):
    result = client.request(1, 'initialize', {
        'clientInfo': {'name': name, 'version': '0.1.0'},
        'capabilities': {'experimentalApi': True},
    })
    client.send_json({'method': 'initialized'})
    return result


def receive(client, name, inbox, stop):
    client.sock.settimeout(None)
    while not stop.is_set():
        try:
            message = client.receive_json()
            inbox.put((name, time.monotonic(), client.last_message_bytes, message))
        except socket.timeout:
            continue
        except (OSError, RuntimeError, ValueError) as exc:
            if not stop.is_set():
                inbox.put((name, time.monotonic(), 0, {'receiver_error': str(exc)}))
            return


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--mode', choices=('text', 'command', 'reasoning', 'coding',
                                         'long_coding', 'long_inline'), default='text')
    parser.add_argument('--effort', choices=('low', 'medium', 'high', 'xhigh', 'max'))
    parser.add_argument('--summary', choices=('auto', 'concise', 'detailed', 'none'), default='auto')
    parser.add_argument('--prompt-file', type=Path,
                        help='Use a saved experimental prompt instead of the mode preset')
    parser.add_argument('--patch-streaming', action='store_true',
                        help='Enable experimental patch preview only for this test chat')
    args = parser.parse_args()
    run = Path((ROOT / 'latest-run.txt').read_text().strip())
    report = json.loads((run / 'report.json').read_text())
    endpoint = urlsplit(report['websocket_url'])
    if endpoint.hostname != '127.0.0.1':
        raise RuntimeError('The probe only uses the isolated localhost backend')
    stamp = datetime.datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d-%H%M%S')
    evidence = run / ('stream-' + stamp + '-' + args.mode + '-' + args.summary)
    evidence.mkdir(mode=0o700)
    clients = []
    stop = threading.Event()
    inbox = queue.Queue()
    readers = []
    thread_id = None
    turn_id = None
    summary = {'backend_url': report['websocket_url'], 'model': None, 'mode': args.mode,
               'ephemeral': False, 'status': 'initializing',
               'rate_definition': 'Unicode characters per second received by the observer',
               'byte_definition': 'Received app-server WebSocket JSON payload bytes; excludes framing',
               'token_rate': None, 'token_rate_reason': 'No verified model tokenizer available'}
    try:
        driver = WebSocketProbe((endpoint.hostname, endpoint.port))
        clients.append(driver)
        driver_info = initialize(driver, 'codex_activity_monitor_test_driver')
        observer = WebSocketProbe((endpoint.hostname, endpoint.port))
        clients.append(observer)
        observer_info = initialize(observer, 'codex_activity_monitor_test_observer')
        for info in (driver_info, observer_info):
            if info.get('codexHome') != report['codex_home']:
                raise RuntimeError('Unexpected backend Codex home')
        account = driver.request(2, 'account/read', {'refreshToken': False})
        summary['signed_in'] = bool(account.get('account'))
        if not summary['signed_in']:
            raise RuntimeError('The isolated backend is not signed in')
        models = driver.request(3, 'model/list', {'limit': 20}).get('data', [])
        config = driver.request(8, 'config/read', {'includeLayers': False}).get('config', {})
        model = config.get('model') or next(item['model'] for item in models if item.get('isDefault'))
        effort = args.effort or ('xhigh' if args.mode == 'reasoning' else
                                 'low' if args.mode == 'text' else 'medium')
        summary['requested_effort'] = effort
        summary['configured_effort'] = config.get('model_reasoning_effort')
        summary['requested_summary'] = args.summary
        summary['model'] = model
        instructions = 'This is a telemetry test. '
        if args.mode in ('text', 'reasoning'):
            instructions += 'Do not use tools.'
        elif args.mode in ('coding', 'long_coding'):
            instructions += ('Use apply_patch and shell tools only inside the working directory. '
                             'Do not access other project files or the network.')
        elif args.mode == 'long_inline':
            instructions += ('Generate and execute the harmless inline Python program requested '
                             'by the user. Only print to stdout; do not access files or the network.')
        else:
            instructions += 'Only run the exact harmless command specified in the user prompt.'
        thread_params = {
            'model': model, 'ephemeral': False, 'cwd': str(evidence),
            'approvalPolicy': 'never',
            'sandbox': 'workspace-write' if args.mode in ('coding', 'long_coding') else 'read-only',
            'developerInstructions': instructions,
        }
        feature_overrides = {}
        if args.patch_streaming:
            feature_overrides['features.apply_patch_streaming_events'] = True
        if feature_overrides:
            thread_params['config'] = feature_overrides
        summary['thread_config_overrides'] = feature_overrides
        started = driver.request(4, 'thread/start', thread_params)
        summary['start_response_model'] = started.get('model')
        summary['start_response_effort'] = started.get('reasoningEffort')
        thread_id = started['thread']['id']
        summary['thread_id'] = thread_id
        driver.request(7, 'thread/name/set', {
            'threadId': thread_id, 'name': '流式监控实验 ' + stamp})
        resumed = observer.request(2, 'thread/resume', {'threadId': thread_id})
        summary['observer_resume_effort'] = resumed.get('reasoningEffort')
        summary['independent_observer_subscribed'] = True
        for name, client in [('driver', driver), ('observer', observer)]:
            reader = threading.Thread(target=receive, args=(client, name, inbox, stop), daemon=True)
            reader.start()
            readers.append(reader)
        begin = time.monotonic()
        prompt = ('请用中文写二十四条关于等待软件响应的建议，每条约四十到五十个汉字。'
                  '编号1到24，每条一行。直接输出这些建议，不要前言结语，不要调用工具。')
        if args.mode == 'command':
            prompt = ('这是命令状态监控实验。请调用 shell 执行以下只向 stdout 打印的命令，'
                      '不要访问其他文件或网络：\n'
                      'python3 -c \'import time; print("PROBE_BEGIN", flush=True); '
                      'time.sleep(3); print("PROBE_END", flush=True)\'\n'
                      '命令完成后只用一句中文说明看到了什么。')
        elif args.mode == 'reasoning':
            prompt = ('这是流式进度监控实验，请解一道组合计数题，不要调用工具或搜索。'
                      '长度为30的二进制字符串中，恰好包含10个1，并且不含连续子串111，'
                      '共有多少个？请只输出最终整数，不输出推导过程。')
        elif args.mode == 'coding':
            prompt = ('这是文件编辑事件监控实验，只在当前目录操作。请按顺序完成三个步骤，'
                      '不要合并两次文件编辑。\n'
                      '1. 使用 apply_patch 创建 demo.py，约30至50行。包含带类型标注和文档字符串的 '
                      'greet(name: str) -> str、greet_many(names: list[str]) -> list[str]、main()。'
                      '初始 greet 直接返回 f"Hello, {name}!"。main 用 argparse 接收名字列表并逐行打印问候。\n'
                      '2. 创建后运行 python3 -c \'import time; print("FILE_CREATED", flush=True); '
                      'time.sleep(2)\'，然后再次使用 apply_patch 修改 greet：先去除名字首尾空白，'
                      '空名字改用 World；其他函数继续复用 greet。\n'
                      '3. 运行 python3 -B -c \'import demo; assert demo.greet("  Ada  ") == "Hello, Ada!"; '
                      'assert demo.greet("   ") == "Hello, World!"; '
                      'assert demo.greet_many(["Ada", ""]) == ["Hello, Ada!", "Hello, World!"]; '
                      'print("PATCH_PROBE_OK", flush=True)\' 验证。\n'
                      '不要安装依赖，不要联网。最后只用一句中文说明完成情况。')
        elif args.mode == 'long_coding':
            prompt = ('这是长工具参数流式监控实验，只在当前目录操作。请使用一次 apply_patch '
                      '创建一个完整的 long_demo.py 文件，180至240行，至少6000个字符。'
                      '必须把完整代码直接放进这次 patch 参数中，不能用 shell、循环或压缩字符串生成文件。'
                      '实现一个仅用标准库的内存记录处理示例：数据类 Record，以及去空白、大小写归一化、'
                      '去重、按类别分组、计数、求和、均值、最小最大值、筛选、排序等至少12个函数。'
                      '每个函数写类型标注、文档字符串、输入边界处理，并提供明确的示例记录和自检断言。'
                      '所有输入写在代码中，不读取外部文件，不联网。main 运行自检并打印 LONG_FILE_OK。'
                      '正常排版，不能为了凑行数堆空行或重复注释。创建后执行 python3 -B long_demo.py，'
                      '验证通过。最后只用一句中文说明结果。')
        elif args.mode == 'long_inline':
            prompt = ('这是长工具参数流式监控实验。请生成并执行一次 shell 命令：'
                      "python3 -B - <<'PY'\n完整的Python程序\nPY\n"
                      '其中程序必须直接 inline 在这个命令的 heredoc 中，不能先写文件，不能用 exec、'
                      '压缩字符串或循环生成源代码。程序至少150行、5000个字符，正常排版，不堆空行或重复注释。'
                      '实现至少12个不同的纯函数：字符串去空白、大小写、词频、去重、分组、过滤、'
                      '排序、数字求和均值、极值、中位数和分位数等，包含类型标注、文档字符串、'
                      '边界处理、显式示例输入、自检断言和 main。'
                      '只用标准库，只向 stdout 打印，不读写文件、不联网。'
                      '执行一开始打印 INLINE_PROBE_BEGIN 并 flush，运行自检后 sleep(3)，'
                      '然后打印 INLINE_PROBE_OK 并 flush。'
                      '使用一个 shell 工具调用执行这条长命令；执行完只用一句中文说明结果。')
        if args.prompt_file:
            prompt = args.prompt_file.read_text()
        (evidence / 'prompt.txt').write_text(prompt)
        turn_params = {
            'threadId': thread_id,
            'input': [{'type': 'text', 'text': prompt}],
            'effort': effort, 'summary': args.summary,
        }
        if args.mode in ('coding', 'long_coding'):
            turn_params['sandboxPolicy'] = {
                'type': 'workspaceWrite', 'writableRoots': [str(evidence)],
                'networkAccess': False, 'excludeSlashTmp': True, 'excludeTmpdirEnvVar': True,
            }
        driver.send_json({'id': 5, 'method': 'turn/start', 'params': turn_params})
        completed = set()
        counts = {'driver': Counter(), 'observer': Counter()}
        text_by_client = {'driver': {}, 'observer': {}}
        final_text_by_client = {'driver': {}, 'observer': {}}
        json_bytes = {'driver': 0, 'observer': 0}
        deltas = []
        recent = deque()
        usage = []
        item_states = []
        reasoning_deltas = []
        reasoning_characters = 0
        reasoning_utf8_bytes = 0
        observed_settings = []
        patch_updates = []
        diff_updates = []
        tool_item_events = []
        patch_sizes = {}
        last_event = None
        last_progress = begin
        errors = []
        statuses = {}
        state = 'waiting_for_model'
        with (evidence / 'events.jsonl').open('w') as raw, (evidence / 'samples.jsonl').open('w') as samples:
            while time.monotonic() - begin < args.timeout:
                try:
                    name, received, size, message = inbox.get(timeout=0.5)
                except queue.Empty:
                    name = None
                if name is not None:
                    elapsed = received - begin
                    raw.write(json.dumps({'client': name, 'seconds': elapsed,
                                          'json_payload_bytes': size, 'message': message}, ensure_ascii=False) + '\n')
                    raw.flush()
                    if 'receiver_error' in message:
                        raise RuntimeError(message['receiver_error'])
                    method = message.get('method', 'rpc_response')
                    params = message.get('params', {})
                    if 'id' in message and 'method' in message:
                        # Built-in tools run in the backend; this probe has no dynamic tools.
                        client = driver if name == 'driver' else observer
                        client.send_json({'id': message['id'], 'error': {
                            'code': -32601, 'message': 'Client requests are not supported by the telemetry probe'}})
                    if method == 'rpc_response' and 'error' in message:
                        errors.append(message['error'])
                    if name == 'driver' and method == 'rpc_response' and message.get('id') == 5:
                        turn_id = message.get('result', {}).get('turn', {}).get('id')
                    target = params.get('threadId') == thread_id
                    if target:
                        counts[name][method] += 1
                        json_bytes[name] += size
                        if name == 'observer':
                            last_event = received
                            if method.startswith('item/reasoning/') and 'delta' in params:
                                delta = params['delta']
                                reasoning_characters += len(delta)
                                reasoning_utf8_bytes += len(delta.encode('utf-8'))
                                reasoning_deltas.append({'seconds': elapsed, 'method': method,
                                                         'characters': len(delta),
                                                         'utf8_bytes': len(delta.encode('utf-8'))})
                                state = 'reasoning'
                            if method == 'thread/settings/updated':
                                observed_settings.append({'seconds': elapsed, 'settings': params})
                            if method == 'item/fileChange/patchUpdated':
                                patch_size = sum(len(change.get('diff', ''))
                                                 for change in params.get('changes', []))
                                patch_sizes[params['itemId']] = patch_size
                                patch_updates.append({'seconds': elapsed,
                                                      'itemId': params['itemId'],
                                                      'patch_characters': patch_size,
                                                      'changes': params.get('changes', [])})
                                state = 'generating_patch'
                            if method == 'turn/diff/updated':
                                diff_updates.append({'seconds': elapsed, 'diff': params['diff'],
                                                     'diff_characters': len(params['diff'])})
                        if method == 'item/agentMessage/delta':
                            delta = params['delta']
                            item_id = params['itemId']
                            text_by_client[name][item_id] = text_by_client[name].get(item_id, '') + delta
                            if name == 'observer':
                                state = 'receiving_text'
                                deltas.append((elapsed, len(delta), len(delta.encode('utf-8'))))
                                recent.append((received, len(delta)))
                        if name == 'observer' and method in ('item/started', 'item/completed'):
                            item = params['item']
                            item_states.append({'seconds': elapsed, 'event': method,
                                                'type': item.get('type'), 'id': item.get('id')})
                            if item.get('type') not in ('userMessage', 'agentMessage', 'reasoning'):
                                tool_item_events.append({'seconds': elapsed, 'event': method, 'item': item})
                            if method == 'item/started':
                                if item.get('type') == 'reasoning':
                                    state = 'reasoning'
                                elif item.get('type') == 'commandExecution':
                                    state = 'executing_command'
                                elif item.get('type') == 'fileChange':
                                    state = 'editing_file'
                                elif item.get('type') not in ('userMessage', 'agentMessage'):
                                    state = 'calling_tool'
                            elif item.get('type') in ('commandExecution', 'fileChange',
                                                      'reasoning', 'agentMessage'):
                                state = 'waiting_for_model'
                        if method == 'item/completed' and params['item'].get('type') == 'agentMessage':
                            item = params['item']
                            final_text_by_client[name][item['id']] = item['text']
                        if name == 'observer' and method == 'thread/tokenUsage/updated':
                            usage.append({'seconds': elapsed, 'usage': params['tokenUsage']})
                        if method == 'error':
                            errors.append(params.get('error', params))
                        if method == 'turn/completed':
                            completed.add(name)
                            statuses[name] = params['turn']['status']
                            if params['turn'].get('error'):
                                errors.append(params['turn']['error'])
                now = time.monotonic()
                if now - last_progress >= 1 or len(completed) == 2:
                    while recent and now - recent[0][0] > 2:
                        recent.popleft()
                    sample = {'seconds': now - begin,
                              'text_characters': sum(map(len, text_by_client['observer'].values())),
                              'text_characters_per_second_2s': sum(n for _, n in recent) / 2,
                              'reasoning_delta_count': len(reasoning_deltas),
                              'reasoning_characters': reasoning_characters,
                              'patch_update_count': len(patch_updates),
                              'patch_characters': sum(patch_sizes.values()),
                              'diff_update_count': len(diff_updates),
                              'diff_characters': diff_updates[-1]['diff_characters'] if diff_updates else 0,
                              'json_payload_bytes': json_bytes['observer'],
                              'seconds_since_last_event': None if last_event is None else now - last_event,
                              'state': 'completed' if 'observer' in completed else state}
                    samples.write(json.dumps(sample) + '\n'); samples.flush()
                    print(json.dumps(sample), flush=True)
                    last_progress = now
                if len(completed) == 2:
                    break
                if errors:
                    break
        observer_text = ''.join(text_by_client['observer'].values())
        driver_text = ''.join(text_by_client['driver'].values())
        duration = time.monotonic() - begin
        first = deltas[0][0] if deltas else None
        last = deltas[-1][0] if deltas else None
        interval = last - first if first is not None else 0
        summary.update({'status': 'completed' if len(completed) == 2 and not errors and
                        all(s == 'completed' for s in statuses.values()) else 'incomplete',
                        'duration_seconds': duration, 'turn_statuses': statuses,
                        'notification_counts': {name: dict(value) for name, value in counts.items()},
                        'json_payload_bytes': json_bytes, 'text_characters': len(observer_text),
                        'text_utf8_bytes': len(observer_text.encode('utf-8')),
                        'driver_observer_text_equal': driver_text == observer_text,
                        'observer_deltas_match_completed_items':
                            text_by_client['observer'] == final_text_by_client['observer'],
                        'turn_id': turn_id,
                        'text_delta_count': len(deltas), 'first_text_seconds': first,
                        'last_text_seconds': last,
                        'text_stream_characters_per_second':
                            sum(n for _, n, _ in deltas[1:]) / interval if interval > 0 else None,
                        'deltas': [{'seconds': sec, 'characters': n, 'utf8_bytes': size}
                                   for sec, n, size in deltas],
                        'usage_updates': usage, 'item_states': item_states, 'errors': errors})
        summary.update({'reasoning_delta_count': len(reasoning_deltas),
                        'reasoning_characters': reasoning_characters,
                        'reasoning_utf8_bytes': reasoning_utf8_bytes,
                        'reasoning_deltas': reasoning_deltas,
                        'patch_updates': patch_updates,
                        'diff_updates': diff_updates,
                        'tool_item_events': tool_item_events,
                        'observed_settings': observed_settings})
        (evidence / 'output.txt').write_text(observer_text)
    except Exception as exc:
        summary['status'] = 'failed'
        summary['error'] = str(exc)
        raise
    finally:
        if summary['status'] != 'completed' and thread_id and turn_id and clients:
            try:
                clients[0].send_json({'id': 6, 'method': 'turn/interrupt', 'params': {
                    'threadId': thread_id, 'turnId': turn_id}})
            except OSError:
                pass
        stop.set()
        for client in clients:
            try:
                client.sock.shutdown(socket.SHUT_RD)
            except OSError:
                pass
        for reader in readers:
            reader.join(timeout=2)
        for client in clients:
            client.close()
        (evidence / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
        (run / 'latest-stream.txt').write_text(str(evidence) + '\n')
        print(json.dumps({'evidence_directory': str(evidence), 'status': summary['status']}), flush=True)


if __name__ == '__main__':
    main()
