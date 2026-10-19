"""Prospective bounded experiment after the user's 80-90% overall-test guidance."""
from pathlib import Path
import hashlib
import json
import sys
import time

ROOT = Path('/opt/ai-sdwan-lab')
WORK = Path('/mnt/e/Proposal/.codex-review/loss-sla-20261018')
sys.path.insert(0, str(ROOT))
from lab.run_live_eval import assess
from sdwan.domain import PROFILES

assert not (WORK/'sla-execution-manifest.json').exists()
path = WORK/'predeclared-sla-pragmatic.json'
assert not path.exists()
protocol = json.loads((WORK/'predeclared-sla-focused.json').read_text())
amendment = json.loads((WORK/'predeclared-sla-bulk-amendment.json').read_text())
baseline = ROOT/'reports/tcg-control-readiness-20261018-5'
packet_file = next(baseline.glob('CONTROL-*/packets.json'))
packets = json.loads(packet_file.read_text())['intervals']
check = assess(packets, [], 'CONTROL', PROFILES['voice'], 0.90)
assert check['valid'], check
protocol.update({
    'declared_at_unix': time.time(),
    'prior_protocol': str(WORK/'predeclared-sla-focused.json'),
    'reason': 'User requested credible results with about 80-90% overall case success, not every test passing. Before any paired candidate SLA measurement, allow at most 10% noisy baseline intervals in TCG; preserve every original 98%-gate failure. This is a readiness tolerance, not changed physical SLA thresholds or a claim of 90% forecast accuracy.',
    'profile': 'voice',
    'baseline_min_sla_ratio': 0.90,
    'event_min_sla_ratio': 0.98,
    'experiment_config': amendment['experiment_config'],
    'healthy_readiness_source': str(baseline),
    'readiness_at_declared_tolerance': check,
    'other_readiness_attempts': ['tcg-control-readiness-20261018-4','tcg-control-readiness-20261018-5','tcg-bulk-readiness-20261018'],
    'measurement_validity': 'Every baseline/event offered-load bin >=90% of 2 Mbps target; real UDP+PCAP, <=1s verified clocks; original 4..6s history cadence, no imputation; forecasts required on all WANs before predictive injection.',
    'source_sha256': {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in amendment['source_sha256']},
    'model_retuned_from_live_results': False,
    'overall_case_reporting': 'Report successes, failures and setup-invalid attempts separately. User accepts approximately 80-90% case success; original per-case event SLA gate 98% remains for comparability.',
    'superiority_claim': 'Only if all three valid pairs exist, predictive has nonzero real forecast exposure, paired difference CI upper<0 and delivery is not worse. Otherwise report no demonstrated advantage.',
})
path.write_text(json.dumps(protocol, indent=2)+'\n')
print('Prospectively frozen: six S12 voice trials, three pairs; readiness 90%, event gate still 98%, no candidate retuning.')
