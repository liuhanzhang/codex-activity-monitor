#!/usr/bin/env python3
"""Invented English demo data. Uses only stdlib; never opens Codex history."""

import argparse
import json
import math
from pathlib import Path
import time


SCOPE = "readme-demo"
PROMPT = (
    "Fix the retry policy in our Python API client. Retry HTTP 429 and temporary "
    "5xx responses with exponential backoff, honor Retry-After, and never retry "
    "authentication errors. Ask one subagent to review tests and another to "
    "check the documentation. Run the focused test suite and summarize the changes."
)
COMMAND = "python -m pytest tests/test_retry.py -q"
RESULT = """........................                                                 [100%]
24 passed in 13.42s

Covered: HTTP 429, Retry-After, exponential backoff,
         temporary 5xx responses, and authentication errors.
"""
DIFF = """diff --git a/orbit/client.py b/orbit/client.py
--- a/orbit/client.py
+++ b/orbit/client.py
@@ -81,5 +81,10 @@ def request(self, method, url):
     response = self.transport.send(method, url)
-    if response.status_code >= 400:
+    if response.status_code in (401, 403):
+        response.raise_for_status()
+    if response.status_code == 429 or response.status_code >= 500:
+        delay = retry_after(response) or self.backoff(attempt)
+        self.sleep(delay)
+        continue
+    if response.status_code >= 400:
         response.raise_for_status()
     return response
"""


def message(key, **params):
    return {"key": key, "params": params}


def fixtures(base, elapsed=54, rate=47):
    now = base + elapsed * 1000
    details = {}

    def phase(key, kind, start, end, *, thread="retry", label=None, content=None,
              action=None, targets=None):
        state = "active" if end is None or end > elapsed else "completed"
        value = {
            "id": key, "scope_id": SCOPE, "thread_id": thread,
            "turn_id": thread + "-turn", "item_id": key,
            "item_type": {"input": "userMessage", "tool_execution": "commandExecution"}.get(kind, kind),
            "namespace": "canonical" if kind in ("input", "tool_execution") else "model",
            "track": "marker" if kind == "input" else "tool" if kind == "tool_execution" else "model",
            "kind": kind, "label": label or kind, "status": state,
            "start_ms": base + start * 1000,
            "end_ms": base + end * 1000 if end is not None and end <= elapsed else None,
            "observed_until_ms": now, "start_source": "producer", "end_source": "producer",
            "quality": [],
        }
        if label:
            value["label_message"] = message("activity.named", name=label)
        if action:
            value.update(item_type="collabAgentToolCall", coordination_action=action,
                         related_agent_ids=targets or [], namespace="canonical")
        if kind == "input":
            value["is_initial"] = True
        sections = content if content is not None else [("body", "Text", "text", "Synthetic demo activity.")]
        details[key] = {
            "version": 1, "state": "available", "phase": value, "source": "synthetic",
            "attachments": [], "sections": [
                {"id": sid, "title": title, "format": fmt, "text": text,
                 "offset": 0, "total_chars": len(text), "next_offset": None}
                for sid, title, fmt, text in sections
            ],
        }
        return value

    def text(body, title="Text", fmt="text"):
        return [("body", title, fmt, body)]

    phases = [
        phase("retry-input", "input", 0, 0, content=text(PROMPT, "User input")),
        phase("retry-plan", "reasoning", 0, 6, content=text(
            "I will inspect the client and its retry tests, then separate test and documentation "
            "review into two subtasks. Authentication failures must stay outside the retry loop.",
            "Visible reasoning summary")),
        phase("retry-intro", "text", 6, 9, content=text(
            "I found the retry policy in orbit/client.py. I am checking the failure cases "
            "while two reviewers inspect the tests and documentation.")),
        phase("retry-delegate", "tool_input", 9, 11, label="spawn_agent", content=text(
            '{"task": "Review retry edge cases and focused tests"}', "Arguments", "code")),
        phase("retry-spawn", "tool_execution", 11, 12, action="spawn",
              targets=["test-reviewer", "docs-reviewer"], content=text(
                  '{"agents": ["Test reviewer", "Docs reviewer"]}', "Result", "code")),
        phase("retry-inspect", "tool_execution", 12, 19, label="rg retry orbit/ tests/",
              content=[("input", "Command", "code", "rg -n 'retry|Retry-After' orbit/ tests/"),
                       ("output", "Output", "code", "orbit/client.py:81: def request(self, method, url):\n"
                        "tests/test_retry.py:14: def test_retries_rate_limit():\n"
                        "tests/test_retry.py:62: def test_does_not_retry_auth_errors():")]),
        phase("retry-design", "reasoning", 19, 25, content=text(
            "The retry condition currently includes every HTTP error. Restrict it to 429 and "
            "server errors, preserve Retry-After, and inject sleep so the tests stay fast.",
            "Visible reasoning summary")),
        phase("retry-patch", "file_edit_input", 25, 33, label="orbit/client.py",
              content=text(DIFF, "Patch", "diff")),
        phase("retry-apply", "tool_execution", 33, 35, label="apply_patch",
              content=[("input", "Patch", "diff", DIFF),
                       ("output", "Result", "code", "Updated orbit/client.py and tests/test_retry.py.")]),
        phase("retry-tests", "tool_execution", 35, 49, label=COMMAND,
              content=[("input", "Command", "code", COMMAND), ("output", "Output", "code", RESULT)]),
        phase("retry-check", "reasoning", 49, 52, content=text(
            "All focused tests pass. Both reviewers confirmed that authentication errors "
            "are never retried and the documented behavior matches the implementation.",
            "Visible reasoning summary")),
        phase("retry-summary", "text", 52, None, content=text(
            "Implemented the retry policy in orbit/client.py.\n\n"
            "• Retry HTTP 429 and temporary server errors with exponential backoff.\n"
            "• Honor Retry-After when the server provides it.\n"
            "• Fail immediately for HTTP 401 and 403.\n\n"
            "Validation: 24 focused tests passed. Both reviewers finished their checks.")),
        phase("tests-reasoning", "reasoning", 12, 22, thread="test-reviewer", content=text(
            "Check boundary cases: capped backoff, Retry-After precedence, and HTTP 401/403.",
            "Visible reasoning summary")),
        phase("tests-input", "tool_input", 22, 25, thread="test-reviewer", label=COMMAND,
              content=text(COMMAND, "Command", "code")),
        phase("tests-run", "tool_execution", 25, 42, thread="test-reviewer", label=COMMAND,
              content=[("input", "Command", "code", COMMAND), ("output", "Output", "code", RESULT)]),
        phase("tests-report", "text", 42, 48, thread="test-reviewer", content=text(
            "Reviewed 24 focused tests. Added coverage for Retry-After and immediate "
            "authentication failures. No remaining issues found.")),
        phase("docs-reasoning", "reasoning", 13, 25, thread="docs-reviewer", content=text(
            "Compare the documented retry contract with the updated implementation.",
            "Visible reasoning summary")),
        phase("docs-input", "tool_input", 25, 29, thread="docs-reviewer", label="rg retry docs/",
              content=text("rg -n 'retry|backoff' docs/", "Command", "code")),
        phase("docs-run", "tool_execution", 29, 40, thread="docs-reviewer", label="rg retry docs/",
              content=[("input", "Command", "code", "rg -n 'retry|backoff' docs/"),
                       ("output", "Output", "code", "docs/client.md:48: Retry policy\n"
                        "docs/client.md:52: Rate limits honor Retry-After.\n"
                        "docs/client.md:55: Authentication errors are not retried.")]),
        phase("docs-report", "text", 40, 50, thread="docs-reviewer", content=text(
            "Updated docs/client.md with the retryable status codes, backoff cap, "
            "and Retry-After behavior. Examples match the tested policy.")),
    ]

    def bundle(thread, items, agents):
        return {
            "version": 1, "scope_id": SCOPE, "thread_id": thread,
            "selected_turn_id": thread + "-turn", "current_turn_id": thread + "-turn",
            "turns": [{"thread_id": thread, "turn_id": thread + "-turn", "status": "active", "start_ms": base}],
            "agents": agents, "phases": [p for p in items if p["track"] != "marker"],
            "markers": [p for p in items if p["track"] == "marker"],
            "origin_ms": base, "end_ms": now, "now_ms": now,
            "revision": "readme-demo-1", "quality": [], "earlier_active_turns": [],
        }

    def agent(thread, name, parent=None, status="active"):
        return {"id": thread, "thread_id": thread, "name": name, "display_name": name,
                "parent_thread_id": parent, "status": status, "status_scope": "selected_parent_turn", "quality": []}

    root = bundle("retry", phases, [agent("retry", "Main"),
        agent("test-reviewer", "Test reviewer", "retry", "completed"),
        agent("docs-reviewer", "Docs reviewer", "retry", "completed")])
    threads = {"retry": {"bundles": {"current": root}}}
    chats = []
    for thread, name, project, key, speed, output, reasoning in [
        ("retry", "Fix API retry handling", "orbit-api", "activity.text", rate, 8426, 2368),
        ("pagination", "Document pagination", "orbit-api", "activity.text", 31, 3210, 986),
        ("settings", "Polish settings layout", "orbit-web", "activity.running", 0, 1948, 612),
    ]:
        chats.append({"id": thread, "backend": SCOPE, "thread_source": "user", "name": name,
            "cwd": "/demo/" + project, "turn_id": thread + "-turn", "model": "Codex",
            "status": "inProgress", "connected": True, "has_active_work": True,
            "display_message": message(key, target="npm test" if thread == "settings" else ""),
            "display_seconds": 2 if thread == "retry" else 8, "generation_rate": speed,
            "generation_rate_status": "ready", "generation_rate_coverage": "complete",
            "usage": {"turn_id": thread + "-turn", "output_tokens": output, "reasoning_tokens": reasoning}})
        if thread != "retry":
            items = [phase(thread + "-input", "input", 0, 0, thread=thread, content=text(
                "Document cursor pagination with a short Python example." if thread == "pagination" else
                "Align the settings form labels and run the UI tests.", "User input")),
                phase(thread + "-plan", "reasoning", 0, 14, thread=thread),
                phase(thread + "-work", "tool_execution", 14, 42, thread=thread,
                      label="rg pagination docs/" if thread == "pagination" else "npm test"),
                phase(thread + "-live", "text" if thread == "pagination" else "tool_execution",
                      42, None, thread=thread, label=None if thread == "pagination" else "npm test")]
            threads[thread] = {"bundles": {"current": bundle(thread, items, [agent(thread, "Main")])}}
    chats.append({"id": "completed", "backend": SCOPE, "thread_source": "user",
        "name": "Add health-check endpoint", "cwd": "/demo/orbit-api", "status": "completed",
        "connected": True, "has_active_work": False, "last_progress_seconds": 36,
        "display_message": message("activity.completed"), "generation_rate": 0})
    snapshot = {
        "schema_version": 1, "updated_at": time.time(), "running": True,
        "menu_presentation": {"version": 1, "mode": "speed", "active_conversation_count": 3, "reason": "multi_conversation"},
        "overview": {"version": 1, "coverage": "complete", "generation_rate": rate + 31,
                     "active_thread_count": 3, "rate_status": "ready", "quality": []},
        "timeline_summary": {"version": 1, "revision": 1, "available": True, "quality": []},
        "backends": {SCOPE: {"connected": True, "implementation": {"modules": {
            "app_session": "synthetic", "observer_permit": "synthetic"}}}},
        "threads": chats,
    }
    return snapshot, {"threads": threads, "details": details}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--keep-fresh", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    base = time.time() * 1000 - 54000
    while True:
        control_path = args.output / "control.json"
        control = json.loads(control_path.read_text()) if control_path.exists() else {}
        elapsed = float(control.get("seconds", 54))
        if "advance_from" in control:
            elapsed += max(0, time.time() - control["advance_from"])
        rate = max(0, round(47 + 5 * math.sin(time.time()))) if control.get("animate_rate") else 47
        snapshot, timeline = fixtures(base, elapsed, rate)
        for name, value in (("snapshot", snapshot), ("timeline", timeline)):
            path = args.output / (name + ".json")
            temporary = path.with_suffix(".new")
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            temporary.replace(path)
        if not args.keep_fresh:
            break
        time.sleep(0.25)


if __name__ == "__main__":
    main()
