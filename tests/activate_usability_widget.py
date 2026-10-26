"""Targeted activation of the checked widget; no cleanup or preference writes."""
from pathlib import Path
import argparse, datetime, hashlib, json, re, urllib.request

root=Path(__file__).resolve().parent.parent
parser=argparse.ArgumentParser()
parser.add_argument('--expected-sha',required=True)
parser.add_argument('--report',default='usability-recovery-widget-20261026.json')
args=parser.parse_args()
assert re.fullmatch(r'[a-z0-9-]+\.json',args.report)
assert hashlib.sha256((root/'widget.ps1').read_bytes()).hexdigest()==args.expected_sha
token=(root/'.token').read_text(encoding='utf-8-sig').strip()
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def call(path,body=None):
    headers={'X-Token':token}
    data=None
    if body is not None:
        headers['Content-Type']='application/json'
        data=json.dumps(body).encode()
    with opener.open(urllib.request.Request('http://127.0.0.1:47832'+path,data=data,headers=headers),timeout=55) as response:
        return json.load(response)
record=dict(date=datetime.datetime.now(datetime.timezone.utc).isoformat(),widgetSha256=args.expected_sha,browserRendered=False)
try:
    mini=call('/api/mini')
    assert not (mini.get('manual') or {}).get('running') and not (mini.get('trim') or {}).get('running'), 'Maintenance active; activation deferred'
    before=call('/api/appearance')
    pid=call('/api/healthz')['pid']
    assert call('/api/widget')['running'], 'Widget hidden or unavailable; no automatic activation'
    # show=True already targets and restarts only this checkout's widget process.
    assert call('/api/widget',{'show':True})['running'], 'Updated widget not confirmed visible'
    assert call('/api/appearance')==before,'Shared appearance changed'
    assert call('/api/healthz')['pid']==pid,'Server changed'
    record.update(status='pass',widgetRunning=True,sharedAppearancePreserved=True,serverUnchanged=True)
    print('PASS checked widget activated; shared appearance preserved and server unchanged.')
except Exception as error:
    record.update(status='failed',error=type(error).__name__+': '+str(error))
    raise
finally:
    (root/'tests'/args.report).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
