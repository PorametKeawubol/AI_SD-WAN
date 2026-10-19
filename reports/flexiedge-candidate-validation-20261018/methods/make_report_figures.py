from pathlib import Path
import json

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT=Path('/opt/ai-sdwan-lab')
WORK=Path('/mnt/e/Proposal/.codex-review/loss-sla-20261018')
old=json.loads(Path('/mnt/e/Proposal/AI_SD-WAN/reports/forecast-confirmation-20261017/confirmation.json').read_text())
loss=json.loads((ROOT/'reports/loss-confirmation-20261018/confirmation.json').read_text())
metrics=['latency_ms','jitter_ms','loss_pct','utilization_pct']
titles=['RTT (ms)','Jitter (ms)','Loss (percentage points)','Utilization (percentage points)']
labels=['Latest value','Demo model','Updated model']
colors=['#ad793b','#aa8192','#16776b']
fig,axes=plt.subplots(2,2,figsize=(10,5.4),constrained_layout=True)
for ax,metric,title in zip(axes.flat,metrics,titles):
    if metric=='loss_pct':
        entry=loss['metrics'][metric]
        values=[entry['mae'][name] for name in ['persistence','original_demo','loss_v3_gated']]
        note=f'New loss test: {entry["samples"]} windows / {entry["nonzero_targets"]} nonzero'
    else:
        entry=old['metrics'][metric]
        values=[entry['mae'][name] for name in ['persistence','original_frozen','new_gated_candidate']]
        note=f'Prior link test: {entry["eligible_windows"]} windows / head unchanged'
    bars=ax.bar(labels,values,color=colors)
    for bar,value in zip(bars,values):
        ax.text(bar.get_x()+bar.get_width()/2,value,f'{value:.3f}',ha='center',va='bottom',fontsize=12)
    ax.set_title(title,fontsize=14)
    ax.text(.5,.97,note,transform=ax.transAxes,ha='center',va='top',fontsize=10.5,color='#50575e')
    ax.set_ylim(0,max(values)*1.33+.001)
    ax.tick_params(labelsize=11.5)
    ax.grid(axis='y',alpha=.2)
    ax.set_axisbelow(True)
fig.suptitle('Measured link-level forecast MAE at 30 seconds; lower is better\nLoss uses the additional nonzero-loss confirmation set',fontsize=14)
fig.savefig(WORK/'forecast-mae-updated.png',dpi=220)
plt.close(fig)

names=['observe-v2','observe-v2-cache','observe-v3-normal','observe-v3-loss']
documents=[json.loads((WORK/name/'summary.json').read_text()) for name in names]
fig,axes=plt.subplots(1,2,figsize=(9,3.1),constrained_layout=True)
ax=axes[0]
rows=[document['wans']['wan1']['window_counters'] for document in documents]
candidate=[row['candidate_windows'] for row in rows]
eligible=[row.get('forecast_available_windows',0) for row in rows]
x=np.arange(4)
ax.bar(x,candidate,color='#dce1e5',label='Candidate history')
ax.bar(x,eligible,color='#16776b',label='Valid for forecast')
for position,valid,total in zip(x,eligible,candidate):
    ax.text(position,total+.5,f'{valid}/{total}',ha='center',fontsize=10)
ax.set_xticks(x,['Prev. RPC','Prev. cache','New normal','New loss'])
ax.set_ylim(0,40)
ax.set_title('Four 3-minute readiness runs',fontsize=11.5)
ax.set_ylabel('Windows per WAN (all 3 identical)',fontsize=10)
ax.legend(fontsize=9,loc='upper left')
ax=axes[1]
latest=documents[2]['wans']
x=np.arange(3)
for offset,key,label,color in [(-.18,'persistence_mae','Latest value','#ad793b'),(.18,'forecast_mae','Updated model','#16776b')]:
    values=[latest[wan]['forecast_metrics']['latency_ms'][key] for wan in ['wan1','wan2','wan3']]
    bars=ax.bar(x+offset,values,width=.35,label=label,color=color)
    for bar,value in zip(bars,values):
        ax.text(bar.get_x()+bar.get_width()/2,value+.6,f'{value:.1f}',ha='center',fontsize=10.5)
ax.set_xticks(x,['WAN1','WAN2','WAN3'])
ax.set_ylim(0,84)
ax.set_title('Latest normal run\n8 RTT targets per WAN',fontsize=11.5)
ax.set_ylabel('RTT MAE (ms); lower is better',fontsize=10)
ax.legend(fontsize=9,loc='upper left')
for ax in axes:
    ax.tick_params(labelsize=10.5)
    ax.grid(axis='y',alpha=.15)
    ax.set_axisbelow(True)
fig.savefig(WORK/'sampling-readiness.png',dpi=240)
plt.close(fig)

entry=loss['metrics']['loss_pct']
fig,axes=plt.subplots(1,2,figsize=(10,3.4),constrained_layout=True)
for ax,key,title in zip(axes,['mae','mae_nonzero'],['All final windows','Actual loss > 0%']):
    values=[entry[key][name] for name in ['persistence','original_demo','frozen_v2','loss_v3_gated']]
    bars=ax.bar(['Latest\nvalue','Demo\nmodel','Previous\nmodel','Updated\nloss'],values,
                color=['#ad793b','#aa8192','#909aa6','#16776b'])
    for bar,value in zip(bars,values):
        ax.text(bar.get_x()+bar.get_width()/2,value,f'{value:.3f}',ha='center',va='bottom',fontsize=12)
    ax.set_ylim(0,max(values)*1.25+.01)
    ax.set_title(title,fontsize=14)
    ax.set_ylabel('Loss MAE (percentage points)',fontsize=12)
    ax.tick_params(labelsize=11)
    ax.grid(axis='y',alpha=.2)
    ax.set_axisbelow(True)
fig.suptitle('Real ICMP loss / 30-second forecast / 63 final windows (41 nonzero)',fontsize=14)
fig.savefig(WORK/'results/loss-confirmation-mae.png',dpi=220)
plt.close(fig)
print('Prepared updated MAE and actual edge sampling figures.')
