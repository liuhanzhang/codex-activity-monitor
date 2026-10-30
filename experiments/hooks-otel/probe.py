#!/usr/bin/env python3
"""Isolated Codex Hooks/OTLP probe. Raw evidence stays in a private temp dir.

Default uses a loopback mock Responses server (no model charges). --real copies
the existing login into the private home and uses the normal OpenAI provider.
It never writes to the source CODEX_HOME or controls the running Desktop.
"""
import argparse
import collections
import datetime as dt
import gzip
import hashlib
import http.server
import json
import os
from pathlib import Path
import selectors
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time

CLI = "/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex"
EVENTS = ["SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse",
          "PostToolUse", "PermissionRequest", "Stop", "Interrupt"]


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def unvalue(v):
    for key in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if key in v:
            return v[key]
    return v


class Receiver:
    def __init__(self, port=0):
        self.rows = []
        self.spans = []
        self.metrics = []
        self.batches = []
        self.status = 200
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                data = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if self.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                received = time.time()
                obj = json.loads(data)
                rows = []
                for resource in obj.get("resourceLogs", []):
                    for scope in resource.get("scopeLogs", []):
                        for row in scope.get("logRecords", []):
                            attrs = {a["key"]: unvalue(a["value"]) for a in row.get("attributes", [])}
                            rows.append({"received_at": received, "time_unix_nano": row.get("timeUnixNano"),
                                         "trace_id": row.get("traceId"), "span_id": row.get("spanId"),
                                         "attrs": attrs})
                owner.batches.append({"received_at": received, "path": self.path,
                                      "status": owner.status, "records": len(rows), "bytes": len(data)})
                if owner.status == 200:
                    owner.rows.extend(rows)
                    for resource in obj.get("resourceSpans", []):
                        for scope in resource.get("scopeSpans", []):
                            owner.spans.extend({"received_at": received, **s} for s in scope.get("spans", []))
                    for resource in obj.get("resourceMetrics", []):
                        for scope in resource.get("scopeMetrics", []):
                            owner.metrics.extend({"received_at": received, **m} for m in scope.get("metrics", []))
                self.send_response(owner.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class ModelServer:
    """Deterministic SSE exercises actual Codex ingestion, not server decoding."""
    def __init__(self):
        self.requests = []
        self.mode = "text"
        self.turn_request = 0
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"data":[]}')

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                owner.turn_request += 1
                n = len(owner.requests) + 1
                rid, iid = f"resp_probe_{n}", f"msg_probe_{n}"
                events = [{"type": "response.created", "response": {"id": rid}}]
                if owner.mode == "tool" and owner.turn_request == 1:
                    arguments = json.dumps({"cmd": "sleep 0.3; printf 'HOOK_OTEL_PROBE\\n'", "max_output_tokens": 100})
                    item = {"id": f"fc_probe_{n}", "type": "function_call", "call_id": f"call_probe_{n}",
                            "name": "exec_command", "arguments": ""}
                    events.append({"type": "response.output_item.added", "item": item.copy()})
                    for chunk in (arguments[:20], arguments[20:]):
                        events.append({"type": "response.function_call_arguments.delta", "item_id": item["id"], "delta": chunk})
                    item["arguments"] = arguments
                    events.append({"type": "response.output_item.done", "item": item})
                    usage_output = 50
                else:
                    chunks = ["alpha beta gamma ", "中文流式测试。", "omega delta epsilon."]
                    item = {"id": iid, "type": "message", "role": "assistant", "content": []}
                    events.append({"type": "response.output_item.added", "item": item.copy()})
                    for chunk in chunks:
                        events.append({"type": "response.output_text.delta", "item_id": iid, "delta": chunk})
                    item["content"] = [{"type": "output_text", "text": "".join(chunks)}]
                    events.append({"type": "response.output_item.done", "item": item})
                    usage_output = 20
                events.append({"type": "response.completed", "response": {"id": rid, "output": [item],
                              "usage": {"input_tokens": 100, "output_tokens": usage_output,
                                        "output_tokens_details": {"reasoning_tokens": 0},
                                        "total_tokens": 100 + usage_output}}})
                owner.requests.append({"at": time.time(), "types": [e["type"] for e in events]})
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                try:
                    for event in events:
                        self.wfile.write(("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n").encode())
                        self.wfile.flush()
                        time.sleep(.12)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class App:
    def __init__(self, root, trust, delay):
        self.trust = trust
        env = {k: v for k, v in os.environ.items() if not k.startswith(("CODEX_", "OTEL_", "PREFLIGHT_"))}
        env.update(CODEX_HOME=str(root / "home"), CODEX_SQLITE_HOME=str(root / "sqlite"),
                   LOG_FORMAT="json", RUST_LOG="warn,codex_api::sse=trace,tungstenite::protocol=trace,tungstenite::protocol::frame=off")
        if delay is not None:
            env["OTEL_BLRP_SCHEDULE_DELAY"] = str(delay)
            env["OTEL_BSP_SCHEDULE_DELAY"] = str(delay)
            env["OTEL_METRIC_EXPORT_INTERVAL"] = str(delay)
        args = [CLI]
        args.append("app-server")
        self.proc = subprocess.Popen(args, cwd=root, env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.sel = selectors.DefaultSelector()
        self.buf = {"protocol": b"", "stderr": b""}
        self.rows, self.raw, self.replies, self.errors = [], [], {}, []
        self.next_id = 0
        for name, pipe in (("protocol", self.proc.stdout), ("stderr", self.proc.stderr)):
            os.set_blocking(pipe.fileno(), False)
            self.sel.register(pipe, selectors.EVENT_READ, name)
        self.request("initialize", {"clientInfo": {"name": "hooks_otel_probe", "version": "0.1"},
                                    "capabilities": {"experimentalApi": True}})
        self.send("initialized", {}, notification=True)

    def send(self, method, params, notification=False):
        row = {"method": method, "params": params}
        if not notification:
            self.next_id += 1
            row["id"] = self.next_id
        self.proc.stdin.write(json.dumps(row).encode() + b"\n")
        self.proc.stdin.flush()
        return row.get("id")

    def poll(self, wait=.1):
        for key, _ in self.sel.select(wait):
            name = key.data
            data = os.read(key.fileobj.fileno(), 262144)
            if not data:
                self.sel.unregister(key.fileobj)
                continue
            self.buf[name] += data
            lines = self.buf[name].split(b"\n")
            self.buf[name] = lines.pop()
            for line in lines:
                now = time.time()
                try:
                    row = json.loads(line)
                except ValueError:
                    if name == "stderr":
                        self.errors.append(line.decode(errors="replace")[:500])
                    continue
                if name == "protocol":
                    self.rows.append({"at": now, **row})
                    if "id" in row and "method" not in row:
                        self.replies[row["id"]] = row
                    elif "id" in row:
                        self.proc.stdin.write(json.dumps({"id": row["id"], "error": {
                            "code": -32601, "message": "Probe does not approve external actions"}}).encode() + b"\n")
                        self.proc.stdin.flush()
                else:
                    msg = row.get("fields", {}).get("message", "")
                    for prefix in ("Received message ", "SSE event: "):
                        pos = msg.find(prefix)
                        if pos >= 0:
                            try:
                                event = json.loads(msg[pos + len(prefix):])
                            except ValueError:
                                break
                            self.raw.append({"at": now, "source_at": row.get("timestamp"), "event": event})
                            break

    def pump(self, seconds):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            self.poll(min(.1, max(0, until - time.monotonic())))

    def request(self, method, params, timeout=40):
        target = self.send(method, params)
        until = time.monotonic() + timeout
        while target not in self.replies and time.monotonic() < until:
            self.poll()
            if self.proc.poll() is not None:
                raise RuntimeError(f"Backend exited: {self.errors[-3:]}")
        if target not in self.replies:
            raise TimeoutError(method)
        row = self.replies.pop(target)
        if "error" in row:
            raise RuntimeError(f"{method}: {row['error']}")
        return row["result"]

    def thread(self, root):
        return self.request("thread/start", {"model": "gpt-6.1-sol", "cwd": str(root),
                            "approvalPolicy": "never", "sandbox": "read-only",
                            "config": {"bypass_hook_trust": self.trust},
                            "developerInstructions": "Follow the test prompt literally. Do not read files or use network tools."})["thread"]["id"]

    def turn(self, thread, prompt, timeout=150):
        start = len(self.rows)
        raw_start = len(self.raw)
        t0 = time.time()
        turn = self.request("turn/start", {"threadId": thread, "input": [{"type": "text", "text": prompt}],
                                          "effort": "low", "summary": "auto"})["turn"]["id"]
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            self.poll()
            completed = [r for r in self.rows[start:] if r.get("method") == "turn/completed" and
                         r.get("params", {}).get("turn", {}).get("id") == turn]
            if completed:
                return {"thread_id": thread, "turn_id": turn, "start": t0, "end": time.time(),
                        "status": completed[-1]["params"]["turn"].get("status"),
                        "protocol": self.rows[start:], "raw": self.raw[raw_start:]}
        self.request("turn/interrupt", {"threadId": thread, "turnId": turn})
        raise TimeoutError("turn")

    def close(self):
        self.proc.stdin.close()
        self.pump(2)
        if self.proc.poll() is None:
            self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.sel.close()


def config(root, receiver, model=None, exporter=True, traces=False, metrics=False, sse=False):
    text = 'model = "gpt-6.1-sol"\nweb_search = "disabled"\ncli_auth_credentials_store = "file"\n'
    text += '[analytics]\nenabled = false\n[features]\nhooks = true\nmemories = false\n'
    text += '[otel]\nlog_user_prompt = false\n'
    for key, on, path in [('trace_exporter', traces, 'traces'), ('metrics_exporter', metrics, 'metrics')]:
        text += (f'{key} = {{ otlp-http = {{ endpoint = "http://127.0.0.1:{receiver.port}/v1/{path}", protocol = "json" }} }}\n' if on else f'{key} = "none"\n')
    text += (f'exporter = {{ otlp-http = {{ endpoint = "http://127.0.0.1:{receiver.port}/v1/logs", protocol = "json" }} }}\n'
             if exporter else 'exporter = "none"\n')
    if model:
        text = 'model_provider = "probe"\n' + text
        text += f'[model_providers.probe]\nname = "Local probe"\nbase_url = "http://127.0.0.1:{model.port}/v1"\nwire_api = "responses"\nrequires_openai_auth = false\nsupports_websockets = false\n'
    elif sse:
        text = 'model_provider = "sse-probe"\n' + text
        text += '[model_providers.sse-probe]\nname = "OpenAI SSE probe"\nwire_api = "responses"\nrequires_openai_auth = true\nsupports_websockets = false\n'
    (root / "home" / "config.toml").write_text(text)


def hooks(root, revision="v1"):
    handler = {"type": "command", "command": shlex.join([sys.executable, str(root / "hook.py"), revision]),
               "timeout": 2}
    save(root / "home" / "hooks.json", {"hooks": {event: [{"hooks": [handler]}] for event in EVENTS}})


def summarize(turn, receiver):
    def source_time(row):
        stamp = row["attrs"].get("event.timestamp")
        return dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() if stamp else float(row.get("time_unix_nano") or 0) / 1e9
    rows = [r for r in receiver.rows if r["attrs"].get("conversation.id") == turn["thread_id"] and
            turn["start"] - .01 <= source_time(r) <= turn["end"] + .01]
    raw = [r["event"] for r in turn["raw"]]
    completed = [e.get("response", {}) for e in raw if e.get("type") == "response.completed"]
    lag = []
    for row in rows:
        stamp = row["attrs"].get("event.timestamp")
        if stamp:
            lag.append((row["received_at"] - dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()) * 1000)
    lag.sort()
    pct = lambda p: round(lag[min(len(lag)-1, int((len(lag)-1)*p))], 2) if lag else None
    return {"status": turn["status"], "duration_s": round(turn["end"]-turn["start"], 3),
            "raw_kinds": dict(collections.Counter(e.get("type") for e in raw)),
            "usage": [e.get("usage") for e in completed],
            "otel_names": dict(collections.Counter(r["attrs"].get("event.name") for r in rows)),
            "otel_kinds": dict(collections.Counter(r["attrs"].get("event.kind") for r in rows if r["attrs"].get("event.kind"))),
            "otel_fields": sorted({k for r in rows for k in r["attrs"]}),
            "lag_ms": {"p50": pct(.5), "p95": pct(.95), "max": pct(1)},
            "protocol_methods": dict(collections.Counter(r.get("method") for r in turn["protocol"] if r.get("method")))}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--no-trust", action="store_true")
    parser.add_argument("--delay-ms", type=int)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--traces", action="store_true")
    parser.add_argument("--metrics", action="store_true")
    parser.add_argument("--sse", action="store_true")
    parser.add_argument("--single", action="store_true")
    parser.add_argument("--fault", action="store_true", help="return OTLP HTTP 503 across a completed turn")
    parser.add_argument("--interrupt", action="store_true", help="interrupt an additional synthetic turn")
    args = parser.parse_args()
    os.umask(0o077)
    root = Path(tempfile.mkdtemp(prefix="codex-hooks-otel-", dir="/private/tmp"))
    (root / "home").mkdir()
    original = Path.home() / ".codex" / "config.toml"
    before = hashlib.sha256(original.read_bytes()).hexdigest() if original.exists() else None
    if args.real:
        shutil.copyfile(Path.home() / ".codex" / "auth.json", root / "home" / "auth.json")
    (root / "hook.py").write_text('import json,os,sys,time\nx=json.load(sys.stdin)\nx.update(probe_received_at=time.time(),probe_revision=sys.argv[1])\nf=os.open(' + repr(str(root / "hooks.jsonl")) + ',os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)\nos.write(f,(json.dumps(x)+"\\n").encode());os.close(f)\nprint("{}")\n')
    hooks(root)
    receiver = Receiver()
    model = None if args.real else ModelServer()
    config(root, receiver, model, exporter=not args.reload, traces=args.traces, metrics=args.metrics, sse=args.sse)
    report = {"run_dir": str(root), "args": vars(args), "version": subprocess.check_output([CLI, "--version"], text=True).strip(),
              "scope": "isolated app-server; real provider" if args.real else "isolated app-server; synthetic local SSE"}
    app = None
    try:
        app = App(root, not args.no_trust, args.delay_ms)
        report["pid"] = app.proc.pid
        thread = app.thread(root)
        save(root / "hooks-list.json", app.request("hooks/list", {"threadId": thread}))
        turns = []
        prompts = (["Do not use tools. Write exactly 180-220 English words explaining why a streaming delta is not a contractual token ID. No preamble.",
                    "Do not use tools. 用中文写约300字，说明网络流式传输、接收批次和模型token的区别。只输出正文。",
                    "Use exec_command once to execute this exact harmless command: printf 'HOOK_OTEL_PROBE\\n'. Do not run any other tools. Then output only the word done."]
                   if args.real else ["synthetic text", "synthetic tool", "synthetic text after receiver restart"])
        if args.single:
            prompts = prompts[:1]
        for i, prompt in enumerate(prompts):
            if model:
                model.mode = "tool" if i == 1 else "text"
                model.turn_request = 0
            if i == 1 and args.reload:
                config(root, receiver, model)
                hooks(root, "v2")
                # Supported app-server config refresh, only in the isolated test.
                report["reload_response"] = app.request("config/batchWrite", {"edits": [], "reloadUserConfig": True})
            if i == 1 and args.fault:
                receiver.status = 503
            if i == 2:
                # Same endpoint goes away and returns; backend process is preserved.
                port = receiver.port
                receiver.close()
                old_rows, old_batches = receiver.rows, receiver.batches
                old_spans, old_metrics = receiver.spans, receiver.metrics
                receiver = Receiver(port)
                receiver.rows[:0] = old_rows
                receiver.batches[:0] = old_batches
                receiver.spans[:0] = old_spans
                receiver.metrics[:0] = old_metrics
                hooks(root, "v3")
            turn = app.turn(thread, prompt)
            app.pump(5 if args.fault and i == 1 else 2)
            if args.fault and i == 1:
                report["export_failure_task_status"] = turn["status"]
                report["export_failure_backend_alive"] = app.proc.poll() is None
                receiver.status = 200
            turns.append(turn)
            print(json.dumps({"stage": "turn_finished", "index": i, "duration_s": round(turn["end"]-turn["start"], 2), "run_dir": str(root)}), flush=True)
        report["turns"] = [summarize(t, receiver) for t in turns]
        report["same_process_alive"] = app.proc.poll() is None
        report["receiver_restart_port"] = receiver.port
        if args.interrupt:
            if model:
                model.mode, model.turn_request = "text", 0
            turn = app.request("turn/start", {"threadId": thread, "input": [{"type": "text", "text": "synthetic interrupt"}]})["turn"]["id"]
            app.pump(.32)
            app.request("turn/interrupt", {"threadId": thread, "turnId": turn})
            app.pump(1)
            report["interrupt_observed"] = any(r.get("method") == "turn/completed" and r.get("params", {}).get("turn", {}).get("id") == turn and r["params"]["turn"].get("status") == "interrupted" for r in app.rows)
        if args.reload:
            # A new thread in the same process vs a new backend process.
            new_thread = app.thread(root)
            if model:
                model.mode, model.turn_request = "text", 0
            t = app.turn(new_thread, "synthetic text in a new thread")
            app.pump(2)
            report["new_thread_same_process"] = summarize(t, receiver)
            app.close()
            app = App(root, not args.no_trust, args.delay_ms)
            new_thread = app.thread(root)
            if model:
                model.mode, model.turn_request = "text", 0
            t = app.turn(new_thread, "synthetic text after backend restart")
            app.pump(2)
            report["after_backend_restart"] = summarize(t, receiver)
        save(root / "turns-raw.json", turns)
    except Exception as error:
        report["error"] = repr(error)
    finally:
        if app:
            app.close()
            save(root / "protocol.json", app.rows)
            save(root / "stderr-errors.json", app.errors)
            report["backend_exit_code"] = app.proc.returncode
        if (root / "hooks.jsonl").exists():
            hook_rows = [json.loads(l) for l in (root / "hooks.jsonl").read_text().splitlines()]
            report["hook_events"] = dict(collections.Counter((r.get("hook_event_name", "") + ":" + r.get("probe_revision", "")) for r in hook_rows))
            report["hook_fields"] = {e: sorted({k for r in hook_rows if r.get("hook_event_name") == e for k in r}) for e in EVENTS}
        save(root / "otel.json", receiver.rows)
        save(root / "spans.json", receiver.spans)
        save(root / "metrics.json", receiver.metrics)
        save(root / "batches.json", receiver.batches)
        report["batch_count"] = len(receiver.batches)
        report["batch_statuses"] = dict(collections.Counter(str(b["status"]) for b in receiver.batches))
        report["span_count"] = len(receiver.spans)
        report["span_names"] = dict(collections.Counter(s.get("name") for s in receiver.spans))
        report["metric_names"] = sorted({m.get("name") for m in receiver.metrics})
        report["source_config_unchanged"] = before == (hashlib.sha256(original.read_bytes()).hexdigest() if original.exists() else None)
        receiver.close()
        if model:
            save(root / "mock-requests.json", model.requests)
            model.close()
        (root / "home" / "auth.json").unlink(missing_ok=True)
        save(root / "summary.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 1 if "error" in report else 0


if __name__ == "__main__":
    raise SystemExit(main())
