from pathlib import Path
import json
import sys
import numpy as np

root=Path('/opt/ai-sdwan-lab')
sys.path.insert(0,str(root))
from sdwan.optimization import validate_dataset, chronological_split, prepare_partition
from sdwan.domain import METRICS

work=Path('/mnt/e/Proposal/.codex-review/readability-ml-20261017')
dataset=validate_dataset(json.loads((work/'ml-results/dataset.json').read_text()))
splits=chronological_split(dataset,12,3,3,3)
hold=prepare_partition(splits['holdout'],dataset.sampling)
result={'scope':'Descriptive diagnostics only; saved selection and acceptance remain unchanged',
        'window_counters':hold.counters,'metrics':{}}
for i,metric in enumerate(METRICS):
    mask=hold.available[:,i]
    y=hold.y[mask,i]
    current=hold.x[mask,i*6]
    result['metrics'][metric]={'targets':len(y),'target_nonzero':int(np.count_nonzero(y)),
        'current_nonzero':int(np.count_nonzero(current)),'target_min':float(y.min()),'target_max':float(y.max()),
        'target_mean':float(y.mean()),'windows_per_episode':{str(g):int(np.count_nonzero(hold.groups[mask]==g)) for g in sorted(set(hold.groups[mask]))}}
(work/'ml-results/descriptive-diagnostics.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
