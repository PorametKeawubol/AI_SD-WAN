"""Summarize every attempt and preserve evidence; never tune or relabel failures."""
from pathlib import Path
import hashlib
import json
import shutil
import sys
import time

import numpy as np

ROOT=Path('/opt/ai-sdwan-lab')
WORK=Path('/mnt/e/Proposal/.codex-review/loss-sla-20261018')
WINDOWS=Path('/mnt/e/Proposal/AI_SD-WAN')
sys.path.insert(0,str(ROOT))
from sdwan.domain import PROFILES
from sdwan.model import fingerprint

latest=ROOT/'reports/loss-sla-tcg-voice-20261018-retry1'
assert not any(json.loads(p.read_text())['status']=='running' for p in latest.glob('*/trial.json'))
packet_results=json.loads((latest/'results.json').read_text())
destination=WINDOWS/'reports/flexiedge-candidate-validation-20261018'
assert not destination.exists()
destination.mkdir()

manual={}
for name in ['observe-v2','observe-v2-cache','observe-v3-normal','observe-v3-loss']:
    source=WORK/name
    shutil.copytree(source,destination/name)
    document=json.loads((source/'summary.json').read_text())
    states=json.loads((source/'states.json').read_text())
    manual[name]={
        'complete':document['complete'],'policy_unchanged':document['policy_unchanged'],
        'model_unchanged':document['model_unchanged'],'model_sha256':document['model_sha256'],
        'frames_with_all_wan_forecasts':sum(set((r['state'].get('last_decision') or {}).get('predicted',{}))=={'wan1','wan2','wan3'} for r in states),
        'wans':{wan:{'windows':row['window_counters'],'metrics':row['forecast_metrics']} for wan,row in document['wans'].items()}}

attempt_names=['tcg-control-readiness-20261018','tcg-control-readiness-20261018-2',
    'tcg-control-readiness-20261018-3','tcg-control-readiness-20261018-4',
    'tcg-control-readiness-20261018-5','tcg-bulk-readiness-20261018',
    'loss-sla-tcg-voice-20261018','loss-sla-tcg-voice-20261018-retry1']
attempts=[]
for name in attempt_names:
    source=ROOT/'reports'/name
    if not source.exists():
        attempts.append({'name':name,'result':'preflight failed before output directory creation'})
        continue
    shutil.copytree(source,destination/name)
    result=json.loads((source/'results.json').read_text()) if (source/'results.json').exists() else None
    attempts.append({'name':name,'results':result})
    for suffix in ['-clock.json','-routing.json']:
        file=ROOT/'reports/flexiwan'/(name+suffix)
        if file.exists():
            shutil.copy2(file,destination/file.name)

observed=[]
for row in packet_results['trials']:
    name=f"{row['scenario']}-{row['profile']}-{row['strategy']}-{row['repeat']:03d}"
    path=latest/name
    detail=json.loads((path/'trial.json').read_text())
    states=json.loads((path/'states.json').read_text())
    entry={**row,'trial_status':detail['status'],'phase':detail['phase'],
        'state_count':len(states),
        'all_wan_forecast_frames_during_event':sum(70<=frame['second']<150 and set((frame['state'].get('last_decision') or {}).get('predicted',{}))=={'wan1','wan2','wan3'} for frame in states),
        'state_frames_during_event':sum(70<=frame['second']<150 for frame in states),
        'all_wan_forecast_frames_before_event':sum(frame['second']<70 and set((frame['state'].get('last_decision') or {}).get('predicted',{}))=={'wan1','wan2','wan3'} for frame in states)}
    if (path/'packets.json').exists():
        packet_document=json.loads((path/'packets.json').read_text())
        intervals=packet_document['intervals']
        event=[frame for frame in intervals if 80<=frame['second']<150]
        baseline=[frame for frame in intervals if 10<=frame['second']<70]
        p=PROFILES[row['profile']]
        def good(frame):
            return (frame['received_packets']>0 and frame['loss_pct']<=p.loss_pct and frame['latency_ms']<=p.latency_ms
                    and frame['jitter_ms']<=p.jitter_ms and frame['received_mbps']>=frame['offered_mbps']*.95)
        entry['baseline']={'intervals':len(baseline),'sla_good_intervals':sum(good(r) for r in baseline),
            'min_offered_mbps':min((r['offered_mbps'] for r in baseline),default=None)}
        entry['event_measurement']={'intervals':len(event),'sla_good_intervals':sum(good(r) for r in event),
            'loss_pct_mean':float(np.mean([r['loss_pct'] for r in event])) if event else None,
            'received_mbps_mean':float(np.mean([r['received_mbps'] for r in event])) if event else None,
            'offered_mbps_min':min((r['offered_mbps'] for r in event),default=None)}
        if (path/'audit.json').exists():
            audit=json.loads((path/'audit.json').read_text())
            entry['verified_applies']=[{'second':item['timestamp']-packet_document['started_at'],
                'reason':item['payload'].get('reason'),'target':item['payload'].get('target')}
                for item in audit.get('events',[]) if item['kind']=='applied']
            entry['verified_switches_before_fault']=sum(0<=item['second']<70 for item in entry['verified_applies'])
            entry['verified_switches_during_event']=sum(70<=item['second']<150 for item in entry['verified_applies'])
    observed.append(entry)

by_key={(row['strategy'],row['repeat']):row for row in observed}
pairs=[]
for repeat in range(3):
    reactive=by_key.get(('reactive',repeat))
    predictive=by_key.get(('predictive_synthetic_lab',repeat))
    if reactive and predictive and reactive.get('valid') and predictive.get('valid'):
        pairs.append({'repeat':repeat,
            'sla_violation_fraction_difference':reactive['sla_interval_ratio']-predictive['sla_interval_ratio'],
            'predictive_forecast_frames_during_event':predictive['all_wan_forecast_frames_during_event'],
            'endpoint_loss_difference':predictive['event_measurement']['loss_pct_mean']-reactive['event_measurement']['loss_pct_mean'],
            'endpoint_goodput_difference':predictive['event_measurement']['received_mbps_mean']-reactive['event_measurement']['received_mbps_mean']})
ci=None
if len(pairs)>=3:
    differences=np.array([r['sla_violation_fraction_difference'] for r in pairs])
    rng=np.random.default_rng(202610180)
    ci=np.quantile([rng.choice(differences,len(differences),replace=True).mean() for _ in range(10000)],[.025,.975]).tolist()
sla_by_strategy={name:float(np.mean([row['sla_interval_ratio'] for row in observed
    if row['strategy']==name and row.get('valid') and 'sla_interval_ratio' in row]))
    for name in ['reactive','predictive_synthetic_lab']
    if any(row['strategy']==name and row.get('valid') and 'sla_interval_ratio' in row for row in observed)}
advantage=(len(pairs)==3 and ci[1]<0 and all(r['predictive_forecast_frames_during_event']>0 for r in pairs)
           and all(r['endpoint_loss_difference']<=0 and r['endpoint_goodput_difference']>=0 for r in pairs))

loss=json.loads((WORK/'results/confirmation.json').read_text())
trace=json.loads((WORK/'results/holdout-loss-predictions.json').read_text())
check={name:float(np.mean([abs(row[name]-row['actual_loss_at_plus_30s']) for row in trace]))
       for name in ['persistence','original_demo','frozen_v2','loss_v3_gated']}
assert len(trace)==63 and sum(r['actual_loss_at_plus_30s']>0 for r in trace)==41
assert all(abs(check[name]-loss['metrics']['loss_pct']['mae'][name])<1e-6 for name in check)

report={'scope':'Measured packet loss confirmation and actual flexiEdge/TCG validation; all attempts retained',
    'created_at':time.time(),'frozen_model_sha256':fingerprint(ROOT/'artifacts/model-search-loss-v3-20261018'),
    'loss_mae_recomputed_from_saved_predictions':check,'manual_runs':manual,
    'packet_attempts':attempts,'latest_trials':observed,'latest_complete':packet_results['complete'],
    'valid_pairs':pairs,'paired_violation_difference_95ci':ci,'mean_sla_interval_ratio':sla_by_strategy,
    'demonstrated_sla_advantage':bool(advantage),
    'promoted_to_default_runtime':False,
    'software_checks':{'pytest':{'command':'.venv/bin/python -m pytest -q','passed':376,'skipped':7,'warnings':1,'seconds':24.60},
        'ruff':'Passed changed source and test files'},
    'limitations':['One physical host and QEMU TCG, not recovered KVM or ISP circuits',
        'New loss Holdout has three independent episodes; overlapping windows are not independent replicates',
        'RTT/jitter/utilization gains belong to the prior 69-window link experiment, not this loss set',
        'Normal flexiEdge readiness shows worse forecast MAE than Persistence for several metrics',
        'Readiness baseline tolerance prospectively changed from 98% to 90%; event SLA and measurement gates unchanged',
        'Default live service keeps forecast disabled; experimental candidate is explicitly selected only']}
assert report['frozen_model_sha256']==loss['bundle_sha256']
(destination/'validation.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
(WORK/'runtime-validation.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
for filename in ['predeclared-sla-protocol.json','predeclared-sla-sampler-amendment.json',
    'predeclared-sla-sampler-amendment-2.json','predeclared-sla-bulk-amendment.json',
    'predeclared-sla-focused.json','predeclared-sla-pragmatic.json','sla-execution-manifest.json',
    'sla-retry1-execution-manifest.json','reconciled-readback.json','readiness-results.json',
    'cache-deployment.json','evidence-copy-audit.json']:
    shutil.copy2(WORK/filename,destination/filename)
shutil.copy2(WORK/'sampling-readiness.png',destination/'sampling-readiness.png')
shutil.copy2(WORK/'forecast-mae-updated.png',destination/'forecast-mae-updated.png')

completed=sum(r.get('packet_measurement_completed',False) for r in observed)
valid=sum(r.get('valid',False) for r in observed)
if advantage:
    sla_text=f'การเปรียบเทียบ S12 โปรไฟล์ voice มีคู่ทดลองที่ใช้ได้ {len(pairs)} คู่ และผลต่างสัดส่วนช่วงผิด SLA มีช่วงความเชื่อมั่นต่ำกว่าศูนย์ แต่ยังจำกัดเฉพาะกรณีนี้'
else:
    sla_text=f'การเปรียบเทียบ S12 โปรไฟล์ voice วัดแพ็กเก็ตจบ {completed} รอบ ใช้ประเมินได้ {valid} รอบ และได้คู่เปรียบเทียบสมบูรณ์ {len(pairs)} คู่ จึงยังไม่ยืนยันว่าโมเดลใหม่ลดช่วงที่ผิด SLA ได้'
if len(pairs)==3:
    ci_text=f'{100*ci[0]:.1f} ถึง {100*ci[1]:.1f}'
    conclusion='ต่ำกว่าศูนย์ แต่ยังจำกัดเฉพาะกรณีนี้' if advantage else 'ไม่ต่ำกว่าศูนย์ทั้งช่วง จึงยังไม่ยืนยันประโยชน์ต่อ SLA'
    sla_text=(f'การเปรียบเทียบ S12 โปรไฟล์ voice ครบสามคู่ มีสัดส่วนวินาทีที่ผ่าน SLA เฉลี่ย '
        f'Reactive {100*sla_by_strategy["reactive"]:.1f}% และ Predictive {100*sla_by_strategy["predictive_synthetic_lab"]:.1f}% '
        f'ผลต่างช่วงผิด SLA มีช่วงความเชื่อมั่น 95% {ci_text} จุดเปอร์เซ็นต์ ซึ่ง{conclusion}')
failed=sum(not row.get('passed',False) for row in observed if row.get('valid'))
if failed==6 and valid==6:
    sla_text+=' ทั้งหกรอบยังไม่ผ่านเกณฑ์รายกรณี'
body=('การรันรุ่นล่าสุดผ่าน flexiEdge ด้วย QEMU TCG สร้างค่าคาดการณ์จริงได้ 11/24 หน้าต่างต่อ WAN ในรอบภาวะปกติ '
      'แต่ RTT มี MAE 25.1–68.9 ms เทียบ Persistence 1.6–3.2 ms และรอบที่เกิด loss ไม่มีประวัติครบเกณฑ์ '
      +sla_text+' จึงยังไม่เปลี่ยนโมเดลควบคุมปกติ')
summary=('ระบบรวบรวมข้อมูลและสร้างค่าคาดการณ์ผ่าน flexiEdge ได้จริง แต่ข้อมูลภายใต้ QEMU TCG ยังขาดช่วง '
         'และค่าคาดการณ์บางตัวแปรแย่กว่า Persistence ผลเปรียบเทียบจึงยังไม่เพียงพอที่จะยืนยันประโยชน์ต่อ SLA '
         'จึงคงการเลือกเส้นทางจากค่าปัจจุบันเป็นหลัก และใช้โมเดลปรับปรุงในบริการทดลองเพื่อพัฒนาต่อ')
if advantage:
    summary=('ผลทดลอง S12 ผ่าน flexiEdge พบประโยชน์ต่อ SLA ในคู่ทดลองที่ใช้ได้ แต่ยังมีข้อมูลขาดช่วงภายใต้ QEMU TCG '
             'และค่าคาดการณ์บางตัวแปรแย่กว่า Persistence จึงยังไม่ใช้แทนระบบควบคุมปกติ '
             'โดยต้องยืนยันเพิ่มในภาวะปกติ เหตุการณ์อื่น และสภาพแวดล้อม KVM ก่อนใช้งานต่อเนื่อง')
summary_doc={'body_narrative':body,'summary_narrative':summary,
    'appendix_figure_caption':'ความพร้อมของข้อมูลและข้อจำกัดความแม่นยำเมื่อรันผ่าน flexiEdge ด้วย QEMU TCG',
    'appendix_figure_file':'sampling-readiness.png','latest_complete':packet_results['complete'],
    'completed_packet_trials':completed,'valid_trials':valid,'valid_pairs':len(pairs),'sla_advantage':bool(advantage)}
(WORK/'sla-final-summary.json').write_text(json.dumps(summary_doc,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k not in ['manual_runs','packet_attempts','latest_trials']},indent=2),flush=True)
