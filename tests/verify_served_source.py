"""Read-only runtime/source/CSP integrity; never a browser layout test."""
from pathlib import Path
import argparse, base64, datetime, hashlib, json, re, urllib.request

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--expected-sha', required=True)
parser.add_argument('--report', required=True)
args = parser.parse_args()
assert re.fullmatch(r'[a-f0-9]{64}', args.expected_sha)
assert re.fullmatch(r'[a-z0-9-]+\.json', args.report)
source = (ROOT/'index.html').read_bytes()
assert b'\r' not in source, 'Unexpected CR line ending'
sha = hashlib.sha256(source).hexdigest()
assert sha == args.expected_sha, 'Source changed during freeze'
token = (ROOT/'.token').read_text(encoding='utf-8-sig').strip()
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
def request(path):
    return urllib.request.Request('http://127.0.0.1:47832'+path, headers={'X-Token':token})
with opener.open(request('/'),timeout=12) as response:
    served = response.read()
    policy = response.headers.get('Content-Security-Policy','')
assert served == source, 'Served source mismatch'
scripts = re.findall(rb'<script>(.*?)</script>', served, re.S)
assert scripts, 'No inline scripts found'
for script in scripts:
    digest = base64.b64encode(hashlib.sha256(script).digest()).decode()
    assert "'sha256-"+digest+"'" in policy, 'CSP hash mismatch'
with opener.open(request('/api/healthz'),timeout=12) as response:
    health = json.load(response)
with opener.open(request('/api/widget'),timeout=12) as response:
    widget = json.load(response)
assert widget['running'], 'Widget not confirmed running'
record = dict(date=datetime.datetime.now(datetime.timezone.utc).isoformat(),status='pass',
              sourceSha256=sha,servedSourceMatches=True,lineEndingCRCount=0,
              inlineScripts=len(scripts),cspHashesMatch=True,serverPid=health['pid'],
              widgetRunning=True,browserRendered=False,
              note='Read-only source/CSP/runtime integrity; not browser layout or UI operation.')
(ROOT/'tests'/args.report).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
print('PASS served source/CSP match, LF preserved; server and widget running. No layout claim.')
