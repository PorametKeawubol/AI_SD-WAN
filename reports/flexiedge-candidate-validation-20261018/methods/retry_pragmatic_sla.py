"""One same-protocol retry after read-back resolved an already completed write."""
from pathlib import Path
import json
import os
import subprocess
import sys
import time

ROOT=Path('/opt/ai-sdwan-lab')
WORK=Path('/mnt/e/Proposal/.codex-review/loss-sla-20261018')
sys.path.insert(0,str(ROOT))
from sdwan.model import fingerprint

prior=json.loads((WORK/'sla-execution-manifest.json').read_text())
failure=json.loads((ROOT/'reports/loss-sla-tcg-voice-20261018/results.json').read_text())
assert failure['packet_observed_trials']==0 and not failure['complete']
resolved=json.loads((WORK/'reconciled-readback.json').read_text())
assert resolved['all_jobs_complete'] and resolved['both_agents_match_controller']
command=list(prior['command'])
output_index=command.index('--output')+1
command[output_index]='loss-sla-tcg-voice-20261018-retry1'
assert fingerprint(ROOT/'artifacts/model-search-loss-v3-20261018')==prior['model_sha256']
assert not (ROOT/'reports'/command[output_index]).exists()
manifest=WORK/'sla-retry1-execution-manifest.json'
assert not manifest.exists()
manifest.write_text(json.dumps({**prior,'frozen_at_unix':time.time(),'command':command,
    'first_attempt_preserved':str(ROOT/'reports/loss-sla-tcg-voice-20261018'),
    'reason':'No first-attempt packet measurement started. Read-back reconciliation verified both agents and completed jobs without replay. All trial parameters, candidate and assessment gates unchanged.'},indent=2)+'\n')
environment={**os.environ,'PATH':str(ROOT/'lab/runtime/ssh-mux-20261018')+os.pathsep+os.environ['PATH']}
print('Retrying the same frozen six-trial protocol after read-back reconciliation.',flush=True)
result=subprocess.run(command,cwd=ROOT,env=environment)
raise SystemExit(result.returncode)
