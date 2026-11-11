"""Deterministic UI test model; never calls a real supplier."""
import importlib.util
import json
import re
from pathlib import Path

spec = importlib.util.spec_from_file_location('t21_base_provider', Path(__file__).resolve().parents[1] / 'backend/tests/provider_fixture.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
original_events = base.events_for


def events(kind, body):
    messages = body.get('input', {}).get('messages', body.get('messages', []))
    serialized = json.dumps(messages, ensure_ascii=False)
    if not any(marker in serialized for marker in ('REVISE_TEST_FILE', 'CONFIRM_TEST:')):
        yield from original_events(kind, body)
        return
    tools = body.get('parameters', {}).get('tools', body.get('tools', []))
    names = [t.get('name', t.get('function', {}).get('name')) for t in tools]
    relevant = [(i, json.dumps(m, ensure_ascii=False)) for i, m in enumerate(messages)
                if m.get('role') == 'user' and any(s in json.dumps(m) for s in ('REVISE_TEST_FILE', 'CONFIRM_TEST:'))]
    index, user = relevant[-1]
    confirm = 'CONFIRM_TEST:' in user
    result = any(m.get('role') == 'tool' for m in messages[index + 1:])
    name, args = None, None
    if 'declare_delivery' in names:
        name = 'declare_delivery'
        args = {'kind': 'answer' if confirm else 'files', 'required': [] if confirm else ['report.txt'],
                'deleted': [], 'changed': [] if confirm else ['report.txt'], 'content_checks': []}
    elif not result:
        if confirm:
            name = 'call_connector'
            args = {'connector_id': re.search(r'CONFIRM_TEST:([a-f0-9-]+)', user).group(1),
                    'tool': 'record_message', 'arguments': {'text': 'T21 exact message'}}
        else:
            name, args = 'write_file', {'path': 'report.txt', 'content': 'revised project file version two'}
    if name:
        text = json.dumps(args)
        parts = [{'tool_calls': [{'index': 0, 'id': 't21_call', 'function': {'name': name, 'arguments': text[:8]}}]},
                 {'tool_calls': [{'index': 0, 'function': {'arguments': text[8:]}}]}]
    else:
        parts = [{'content': 'T21 confirmation completed' if confirm else 'T21 revision completed'}]
    for i, part in enumerate(parts):
        choice = {'index': 0, 'finish_reason': ('tool_calls' if name else 'stop') if i == len(parts)-1 else None,
                  'message' if kind == 'qwen_dashscope' else 'delta': part}
        yield {'output': {'choices': [choice]}, 'usage': {'input_tokens': 5, 'output_tokens': 10}} if kind == 'qwen_dashscope' else {'choices': [choice]}


base.events_for = events
app = base.app
