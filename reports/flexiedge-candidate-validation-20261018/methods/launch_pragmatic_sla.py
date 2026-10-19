"""Run exactly the fixed six-trial candidate comparison; preserve any failed attempt."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import time

ROOT = Path('/opt/ai-sdwan-lab')
WORK = Path('/mnt/e/Proposal/.codex-review/loss-sla-20261018')
sys.path.insert(0, str(ROOT))
from sdwan.model import fingerprint

protocol = json.loads((WORK/'predeclared-sla-pragmatic.json').read_text())
loss = json.loads((ROOT/'reports/loss-confirmation-20261018/confirmation.json').read_text())
model = Path(protocol['model_directory'])
assert fingerprint(model) == loss['bundle_sha256']
for name, digest in protocol['source_sha256'].items():
    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest() == digest, name
destination = ROOT/'reports/loss-sla-tcg-voice-20261018'
manifest = WORK/'sla-execution-manifest.json'
assert not destination.exists() and not manifest.exists()
command = [sys.executable, 'scripts/run_live_lab.py', '--output', destination.name,
           '--model', str(model), '--config', protocol['experiment_config'],
           '--verify-clocks-only', '--no-edge-observer', '--baseline-min-sla-ratio', '0.90',
           '--suite', 'balanced', '--scenarios', *protocol['scenarios'], '--profiles', 'voice',
           '--strategies', *protocol['strategies'], '--repetitions', '3', '--order-seed', str(protocol['order_seed'])]
manifest.write_text(json.dumps({'frozen_at_unix': time.time(), 'model_sha256': fingerprint(model),
                               'protocol': str(WORK/'predeclared-sla-pragmatic.json'),
                               'command': command, 'accelerator': 'TCG software emulation'}, indent=2)+'\n')
environment = {**os.environ, 'PATH': str(ROOT/'lab/runtime/ssh-mux-20261018')+os.pathsep+os.environ['PATH']}
print('Starting six prospectively fixed real flexiEdge packet trials.', flush=True)
result = subprocess.run(command, cwd=ROOT, env=environment)
print(json.dumps({'returncode': result.returncode, 'preserved_output': str(destination)}), flush=True)
raise SystemExit(result.returncode)
