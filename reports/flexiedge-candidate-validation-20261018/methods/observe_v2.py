"""Manual-only readiness observation with frozen v2 on actual flexiEdge probes."""
from pathlib import Path
import argparse
import json
import os
import signal
import socket
import ssl
import subprocess
import sys
import time

import httpx

ROOT = Path('/opt/ai-sdwan-lab')
parser = argparse.ArgumentParser()
parser.add_argument('--cached', action='store_true')
parser.add_argument('--latest', action='store_true')
parser.add_argument('--loss-event', action='store_true')
args = parser.parse_args()
WORK = Path('/mnt/e/Proposal/.codex-review/loss-sla-20261018') / (('observe-v3-loss' if args.loss_event else 'observe-v3-normal') if args.latest else ('observe-v2-cache' if args.cached else 'observe-v2'))
CONFIG = ROOT / ('config/flexiwan.tcg-cache-20261018.local.json' if args.cached else 'config/flexiwan.local.json')
sys.path.insert(0, str(ROOT))
from lab.evaluate_forecast import evaluation_windows, measured_series, metric_errors
from sdwan.flexiwan import FlexiManageClient, FlexiWANConfig
from sdwan.model import Forecaster, fingerprint

assert not WORK.exists()
WORK.mkdir()
for port in [8000,8001]:
    with socket.socket() as sock:
        assert sock.connect_ex(('127.0.0.1',port)) != 0
private = json.loads((ROOT / 'lab/runtime/observability/private.json').read_text())
credentials = json.loads((ROOT / 'lab/runtime/credentials.json').read_text())
environment = {**os.environ, 'FLEXIWAN_ACCESS_KEY': credentials['access_key'],
               'SDWAN_DATABASE_URL': private['database_url'], 'SDWAN_API_TOKEN': private['api_token'],
               'SDWAN_TLS_CERT': str(ROOT / 'lab/runtime/observability/certs/api/server.crt'),
               'SDWAN_TLS_KEY': str(ROOT / 'lab/runtime/observability/certs/api/server.key'),
               'SDWAN_CA_FILE': str(ROOT / 'lab/runtime/certs/certificate.pem')}
os.environ['FLEXIWAN_ACCESS_KEY'] = credentials['access_key']
model_path = ROOT / ('artifacts/model-search-loss-v3-20261018' if args.latest else 'artifacts/model-search-link-v2-20261017')
before = fingerprint(model_path)
controller = FlexiManageClient(FlexiWANConfig.load(CONFIG))
policy_before = controller.policy()
stream = 'candidate-readiness-tcg-' + str(int(time.time()))
command = [sys.executable, '-m', 'sdwan', 'serve', '--port', '8001', '--flexiwan-config',
           str(CONFIG), '--model', str(model_path),
           '--experimental-synthetic-forecast', '--stream', stream]
states, report = [], {'scope': 'Manual-only actual flexiEdge/TCG readiness; not a randomized SLA comparison',
                      'model_sha256': before, 'stream': stream, 'policy_changes_authorized_here': False}
process = None
loss_active = False
try:
    with (WORK / 'service.log').open('w') as log:
        process = subprocess.Popen(command,cwd=ROOT,env=environment,stdout=log,stderr=log,start_new_session=True)
        context = ssl.create_default_context(cafile=environment['SDWAN_CA_FILE'])
        with httpx.Client(base_url='https://localhost:8001',headers={'Authorization':'Bearer '+private['api_token']},
                          verify=context,trust_env=False,timeout=3) as http:
            for _ in range(40):
                if process.poll() is not None:
                    raise RuntimeError('Manual observation service failed to start')
                try:
                    health=http.get('/health')
                    if health.status_code==200:
                        assert health.json()['model_loaded']
                        report['health']=health.json()
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(1)
            else:
                raise RuntimeError('Manual observation readiness timeout')
            started=time.monotonic()
            for tick in range(37):
                time.sleep(max(0, started+tick*5-time.monotonic()))
                if args.loss_event and tick in [14,30]:
                    from lab.impairments import apply, plan
                    apply(plan('S10' if tick==14 else 'CONTROL'))
                    loss_active = tick==14
                    print('Controlled actual WAN1 packet-loss event: '+str(loss_active),flush=True)
                state=http.get('/api/state').json()
                assert state['mode']=='manual'
                states.append({'second':time.monotonic()-started,'state':state})
                temporary=WORK / 'states.tmp'
                temporary.write_text(json.dumps(states,allow_nan=False))
                temporary.replace(WORK / 'states.json')
                if tick % 6 == 0:
                    print(f'Manual observation: {tick*5}s / 180s; {len(state.get("telemetry",{}))} WANs',flush=True)
        report['complete']=True
except Exception as error:
    report['complete']=False
    report['error_type']=type(error).__name__
finally:
    if loss_active:
        from lab.impairments import apply, plan
        apply(plan('CONTROL'))
    if process and process.poll() is None:
        os.killpg(process.pid,signal.SIGTERM)
        try:
            process.wait(timeout=35)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGKILL)
            process.wait(timeout=5)
    report['policy_unchanged']=controller.policy()==policy_before
    controller.close()
    report['model_unchanged']=fingerprint(model_path)==before
    if states:
        series=measured_series(states)
        report['wans']={}
        model=Forecaster(model_path)
        for link,rows in series.items():
            windows,counts=evaluation_windows(rows,'causal_linear_5s')
            report['wans'][link]={'unique_fresh_measurements':len(rows),'window_counters':counts,
                                  'forecast_metrics':metric_errors(windows,model),
                                  'interval_seconds':[b.timestamp-a.timestamp for a,b in zip(rows,rows[1:])]}
    (WORK / 'summary.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='wans'},indent=2))
    for link, entry in report.get('wans',{}).items():
        intervals=entry['interval_seconds']
        print(json.dumps({'wan':link,'measurements':entry['unique_fresh_measurements'],
                          'window_counters':entry['window_counters'],
                          'interval_min_max':[min(intervals),max(intervals)] if intervals else None},indent=2))
