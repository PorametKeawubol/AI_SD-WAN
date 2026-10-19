"""Presentation-only plot using the frozen one-shot results; no model selection."""
from pathlib import Path
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

work=Path('/mnt/e/Proposal/.codex-review/readability-ml-20261017/ml-results')
report=json.loads((work/'confirmation.json').read_text())
fig,axes=plt.subplots(2,2,figsize=(10,5.4),constrained_layout=True)
names=['RTT (ms)','Jitter (ms)','Loss (percentage points)','Utilization (percentage points)']
labels=['Latest value','Linear trend','Original model','Previous model','Optimized v2']
colors=['#b37322','#8894a0','#9b627d','#82aaa5','#16776b']
for ax,(metric,entry),name in zip(axes.flat,report['metrics'].items(),names):
    values=list(entry['mae'].values())
    bars=ax.bar(range(5),values,color=colors)
    for bar,value in zip(bars,values):
        digits=6 if metric=='loss_pct' and value<.001 else 3
        ax.text(bar.get_x()+bar.get_width()/2,bar.get_height(),f'{value:.{digits}f}',ha='center',va='bottom',fontsize=9)
    ax.set_xticks(range(5),labels,fontsize=8,rotation=12)
    ax.set_title(name,fontsize=12)
    ax.set_ylim(0,max(values)*1.25+.001)
    ax.grid(axis='y',alpha=.2)
    ax.set_axisbelow(True)
    if metric=='loss_pct':
        ax.text(.5,.97,'All 69 target losses are 0%: limited test',transform=ax.transAxes,
                ha='center',va='top',fontsize=9,color='#793d28')
fig.suptitle('Fresh confirmation / all 3 final episodes / 69 windows per metric\n30-second forecast MAE; lower is better',fontsize=12)
fig.savefig(work/'confirmation-mae.png',dpi=220)
plt.close(fig)
