"""Explicit local design activation via existing authenticated app controls.

No task definitions, process killing, cleanup commands, tokens or security settings
are changed. The existing Agent restarts its server after the shutdown request.
"""
import datetime
import base64
import argparse
import hashlib
import json
import re
from pathlib import Path
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--activate', action='store_true')
parser.add_argument('--resume', action='store_true')
parser.add_argument('--server-only', action='store_true')
parser.add_argument('--ui-only', action='store_true')
parser.add_argument('--mode', choices=('auto', 'light', 'dark'))
parser.add_argument('--palette', choices=('teal', 'blue', 'violet', 'graphite'))
args = parser.parse_args()
OUT = ROOT / 'tests' / 'design-runtime-check-20261025.json'
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
token = (ROOT / '.token').read_text(encoding='utf-8-sig').strip()
result = {'startedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'steps': []}
if (args.resume or args.server_only or args.ui_only) and OUT.exists():
    result['previousAttempt'] = json.loads(OUT.read_text(encoding='utf-8'))


def request(path, body=None, timeout=20):
    headers = {'X-Token': token}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request('http://127.0.0.1:47832' + path, data=data, headers=headers)
    with opener.open(req, timeout=timeout) as response:
        return json.load(response)


def step(name, value=True):
    result['steps'].append({'name': name, 'value': value})
    print(name, flush=True)


try:
    if not args.activate:
        raise RuntimeError('Explicit --activate required')
    mini = request('/api/mini')
    if (mini.get('manual') or {}).get('running') or (mini.get('trim') or {}).get('running'):
        raise RuntimeError('A maintenance operation is running; activation deferred')
    age = mini.get('agentAge')
    if not isinstance(age, (float, int)) or not 0 <= age < 45:
        raise RuntimeError('Agent recovery is not confirmed healthy')
    step('preflight_no_manual_or_trim_work_and_agent_healthy')
    started = time.monotonic()
    if args.resume or args.ui_only:
        appearance = request('/api/appearance')
        step('new_server_already_recovered_resume')
    else:
        request('/api/shutdown', {})
        step('server_restart_requested_through_existing_api')
        while time.monotonic() - started < 85:
            time.sleep(2)
            try:
                appearance = request('/api/appearance', timeout=3)
                if appearance.get('schemaVersion') == 1:
                    break
            except Exception:
                pass
        else:
            raise RuntimeError('Agent server recovery did not complete within 85 seconds')
        step('new_server_recovered_seconds', round(time.monotonic() - started, 2))
    widget = request('/api/widget') if args.resume or args.server_only else request('/api/widget', {'show': True}, timeout=35)
    # The mutex exists before the WPF Loaded event persists visible=true.
    for _ in range(8):
        if widget.get('running') is True:
            break
        time.sleep(1)
        widget = request('/api/widget')
    if not widget.get('running'):
        raise RuntimeError('Updated widget was not confirmed running; new server remains available')
    step('updated_widget_running')
    if appearance.get('recoveryRequired'):
        raise RuntimeError('Appearance store needs repair; preferences were not overwritten')
    appearance = request('/api/appearance', {'revision': appearance['revision'], 'mode': args.mode or appearance['mode'], 'palette': args.palette or appearance['palette']})
    step('appearance_confirmed', {k: appearance[k] for k in ('revision', 'mode', 'palette')})
    mini = request('/api/mini')
    if mini.get('appearance', {}).get('revision') != appearance['revision']:
        raise RuntimeError('Mini appearance does not match confirmed store')
    step('mini_and_appearance_api_revision_match')
    req = urllib.request.Request('http://127.0.0.1:47832/', headers={'X-Token': token})
    with opener.open(req, timeout=15) as response:
        html = response.read()
        policy = response.headers.get('Content-Security-Policy', '')
    if hashlib.sha256(html).digest() != hashlib.sha256((ROOT / 'index.html').read_bytes()).digest():
        raise RuntimeError('Served HTML does not match approved source')
    step('served_html_matches_source')
    for script in re.findall(rb'<script>(.*?)</script>', html, re.S):
        normalized = script.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
        expected = "'sha256-" + base64.b64encode(hashlib.sha256(normalized).digest()).decode() + "'"
        if expected not in policy:
            raise RuntimeError('Served CSP does not authorize the actual inline script')
    step('served_csp_matches_normalized_scripts')
    step('widget_after_server_restart', request('/api/widget'))
    result['status'] = 'pass'
except Exception as exc:
    result['status'] = 'failed'
    result['error'] = type(exc).__name__ + ': ' + str(exc)
    print(result['error'], flush=True)
finally:
    result['completedAt'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
sys.exit(0 if result['status'] == 'pass' else 1)
