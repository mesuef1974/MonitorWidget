"""Activate widget polish, preserving shared appearance and other user settings."""
from pathlib import Path
import datetime
import hashlib
import json
import re
import base64
import shutil
import time
import urllib.request
import atomic_json
import argparse

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--preserve-opacity',action='store_true')
parser.add_argument('--report-name',default='polish-runtime-20261025.json')
args=parser.parse_args()
if not re.fullmatch(r'[a-z0-9-]+\.json',args.report_name):raise ValueError('Invalid local report name')
out=ROOT/'tests'/args.report_name
report={'startedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),'steps':[]}
token=(ROOT/'.token').read_text(encoding='utf-8-sig').strip()
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))

def call(path,body=None,timeout=12):
    headers={'X-Token':token};data=None
    if body is not None:
        headers['Content-Type']='application/json';data=json.dumps(body).encode()
    with opener.open(urllib.request.Request('http://127.0.0.1:47832'+path,data=data,headers=headers),timeout=timeout) as r:
        return json.load(r)

def record(name,value=True):
    report['steps'].append({'name':name,'value':value});print(name,flush=True)

try:
    mini=call('/api/mini')
    if (mini.get('manual') or {}).get('running') or (mini.get('trim') or {}).get('running'):
        raise RuntimeError('Maintenance running; widget restart deferred')
    before=call('/api/appearance');server_pid=call('/api/healthz')['pid']
    record('preflight_no_maintenance')
    settings=ROOT/'widget.settings.json'
    backup=ROOT/'backups/design_20261025/widget_polish'
    backup.mkdir(exist_ok=True)
    if settings.exists() and not (backup/settings.name).exists():shutil.copy2(settings,backup/settings.name)
    stopped=call('/api/widget',{'show':False},timeout=30)
    if stopped.get('running'):raise RuntimeError('Previous widget did not stop')
    with atomic_json.transaction(settings) as (_,value):
        previous=value.get('opacity');target_opacity=previous if args.preserve_opacity and previous is not None else 0.96
        value['opacity']=target_opacity
    record('background_opacity_preserved' if args.preserve_opacity else 'background_opacity_restored',{'previous':previous,'current':target_opacity})
    widget=call('/api/widget',{'show':True},timeout=35)
    for _ in range(8):
        if widget.get('running'):break
        time.sleep(1);widget=call('/api/widget')
    if not widget.get('running'):raise RuntimeError('Updated widget not confirmed running')
    record('updated_widget_running')
    _,saved=atomic_json.read(settings)
    if saved.get('opacity')!=target_opacity:raise RuntimeError('Saved opacity changed during activation')
    record('saved_opacity_confirmed',saved['opacity'])
    after=call('/api/appearance')
    record('shared_appearance_not_written',{'before':before,'after':after})
    if call('/api/healthz')['pid']!=server_pid:raise RuntimeError('Server changed unexpectedly')
    record('server_not_restarted')
    with opener.open(urllib.request.Request('http://127.0.0.1:47832/',headers={'X-Token':token}),timeout=12) as r:
        page=r.read();policy=r.headers.get('Content-Security-Policy','')
    if page!=(ROOT/'index.html').read_bytes():raise RuntimeError('HTML source mismatch')
    for script in re.findall(rb'<script>(.*?)</script>',page,re.S):
        script=script.replace(b'\r\n',b'\n').replace(b'\r',b'\n')
        if "'sha256-"+base64.b64encode(hashlib.sha256(script).digest()).decode()+"'" not in policy:
            raise RuntimeError('CSP mismatch')
    record('served_html_and_csp_match')
    report['status']='pass'
except Exception as exc:
    report['status']='failed';report['error']=type(exc).__name__+': '+str(exc);print(report['error'],flush=True)
finally:
    report['completedAt']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
raise SystemExit(0 if report['status']=='pass' else 1)
