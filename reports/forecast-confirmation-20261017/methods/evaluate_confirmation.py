"""Execute the frozen additional link-level confirmation protocol exactly once."""
from pathlib import Path
import hashlib
import json
import re
import shutil
import statistics
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path('/opt/ai-sdwan-lab')
sys.path.insert(0, str(ROOT))
from sdwan.domain import METRICS
from sdwan.forecast_search import search_plan, train_search
from sdwan.model import Forecaster, fingerprint
from sdwan.optimization import chronological_split, clip_predictions, prepare_partition, validate_dataset
from scripts.explain_forecaster import explain

WORK = Path('/mnt/e/Proposal/.codex-review/readability-ml-20261017')
PROTOCOL = json.loads((WORK / 'ml-protocol.json').read_text())
FRESH = ROOT / 'artifacts/link-campaign-confirmation-20261017'
OUTPUT = ROOT / 'artifacts/model-search-link-v2-20261017'
REPORTS = ROOT / 'artifacts/link-confirmation-report-20261017'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def audit_fresh(document):
    status = json.loads((FRESH / 'status.json').read_text())
    assert status['completed'] and status['cleanup_complete'] and not status['errors']
    assert status['episodes_completed'] == 9
    observed = json.loads((FRESH / 'protocol.json').read_text())
    assert observed == PROTOCOL['fresh_collection']
    audit = {'probe_batches': 0, 'icmp_packets_sent': 0, 'icmp_packets_received': 0,
             'counter_rows': 0, 'maximum_metric_discrepancy': 0.0,
             'counter_formula_verified': True, 'raw_probe_recalculation_verified': True,
             'pcap_scope': 'first 1000 packets per WAN only; not the complete experiment'}
    for episode_index, episode in enumerate(document['episodes']):
        counters = [json.loads(line) for line in (FRESH / f'counters-{episode_index:02}.jsonl').read_text().splitlines()]
        assert len(counters) == 75
        lookup = {(row['tick'], row['link_id']): row for row in counters}
        for link_index, (link, series) in enumerate(episode['series'].items()):
            raw = [json.loads(line) for line in (FRESH / f'probes-{episode_index:02}-{link_index}.jsonl').read_text().splitlines()]
            assert len(raw) == len(series) == 25
            for tick, (row, probe) in enumerate(zip(series, raw)):
                summary = re.search(r'(\d+) packets transmitted, (\d+) received', probe['stdout'])
                assert summary and int(summary[1]) == 100
                sent, received = map(int, summary.groups())
                timings = [float(value) for value in re.findall(r'time[=<]([\d.]+) ms', probe['stdout'])]
                assert len(timings) == received and received > 0
                expected = {'latency_ms': statistics.mean(timings),
                            'jitter_ms': statistics.pstdev(timings),
                            'loss_pct': 100.0 * (sent - received) / sent}
                for metric, value in expected.items():
                    delta = abs(row[metric] - value)
                    audit['maximum_metric_discrepancy'] = max(audit['maximum_metric_discrepancy'], delta)
                    assert delta < 1e-8, (episode_index, tick, link, metric, delta)
                counter = lookup[(tick, link)]
                calculated = 100 * (counter['tx_bytes_after'] - counter['tx_bytes_before']) * 8 / (
                    counter['elapsed_seconds'] * counter['configured_mbps'] * 1e6)
                assert counter['tx_bytes_after'] >= counter['tx_bytes_before']
                assert np.isclose(calculated, counter['raw_utilization_pct'], atol=1e-12)
                assert np.isclose(min(100, max(0, calculated)), row['utilization_pct'], atol=1e-12)
                assert counter['observed_at'] == row['timestamp']
                audit['probe_batches'] += 1
                audit['icmp_packets_sent'] += sent
                audit['icmp_packets_received'] += received
                audit['counter_rows'] += 1
    return audit


def main():
    if OUTPUT.exists() or REPORTS.exists():
        raise FileExistsError('Preserve the one-shot experiment; use existing results for reproduction')
    assert json.loads(json.dumps(search_plan())) == PROTOCOL['search']
    old_path = Path(PROTOCOL['development_data'])
    assert sha(old_path) == PROTOCOL['development_dataset_sha256']
    old = json.loads(old_path.read_text())
    fresh = json.loads((FRESH / 'dataset.json').read_text())
    assert len(old['episodes']) == 12 and len(fresh['episodes']) == 9
    assert [e['id'] for e in fresh['episodes']] == [f'link-{s}' for s in PROTOCOL['fresh_collection']['seeds']]
    raw_audit = audit_fresh(fresh)
    combined = {**old, 'episodes': old['episodes'] + fresh['episodes']}
    dataset = validate_dataset(combined)
    # Freeze and retain the exact combined input before invoking search.
    REPORTS.mkdir()
    combined_path = REPORTS / 'dataset.json'
    combined_path.write_text(json.dumps(combined, allow_nan=False) + '\n')
    shutil.copy2(WORK / 'ml-protocol.json', REPORTS / 'predeclared-protocol.json')
    baseline = ROOT / 'artifacts/model'
    previous = ROOT / 'artifacts/model-search-link-20261017'
    assert fingerprint(baseline) == PROTOCOL['old_frozen_fingerprint']
    assert fingerprint(previous) == PROTOCOL['previous_optimized_fingerprint']
    report = train_search(dataset, OUTPUT, tuple(PROTOCOL['combined_counts']), baseline,
                          provenance='isolated_linux_veth_netem_fresh_confirmation')
    partitions = chronological_split(dataset, *PROTOCOL['combined_counts'])
    hold = prepare_partition(partitions['holdout'], dataset.sampling)
    current = hold.x[:, [0, 6, 12, 18]]
    trend = current + 30 * hold.x[:, [5, 11, 17, 23]]
    trend = np.column_stack([clip_predictions(trend[:, i], i) for i in range(4)])
    predictions = {'persistence': current, 'linear_trend': trend,
                   'original_frozen': Forecaster(baseline).predict_matrix(hold.x),
                   'previous_optimized': Forecaster(previous).predict_matrix(hold.x),
                   'new_gated_candidate': Forecaster(OUTPUT).predict_matrix(hold.x)}
    summary = {'scope': PROTOCOL['scope'], 'data_sha256': dataset.sha256,
               'model_sha256': fingerprint(OUTPUT), 'runtime_promoted': False,
               'partitions': report['partition_episodes'], 'raw_measurement_audit': raw_audit,
               'final_window_counters': hold.counters, 'metrics': {}}
    for i, metric in enumerate(METRICS):
        mask = hold.available[:, i]
        target = hold.y[mask, i]
        errors = {name: np.abs(values[mask, i] - target) for name, values in predictions.items()}
        maes = {name: float(values.mean()) for name, values in errors.items()}
        groups = hold.groups[mask]
        group_ids = sorted(set(groups))
        differences = errors['new_gated_candidate'] - errors['persistence']
        per_episode = {str(g): float(differences[groups == g].mean()) for g in group_ids}
        # Descriptive CI, reported only; never used to change the saved candidate.
        rng = np.random.default_rng(20261817 + i)
        blocks = [differences[groups == g] for g in group_ids]
        boot = [float(np.concatenate([blocks[j] for j in rng.integers(0, len(blocks), len(blocks))]).mean())
                for _ in range(10000)]
        ci = np.quantile(boot, [.025, .975]).tolist()
        summary['metrics'][metric] = {'eligible_windows': int(mask.sum()), 'mae': maes,
            'gain_pct': 100 * (maes['persistence'] - maes['new_gated_candidate']) / maes['persistence'] if maes['persistence'] else 0,
            'paired_mae_difference_by_episode': per_episode, 'descriptive_episode_bootstrap_95ci': ci,
            'acceptance': report['metrics'][metric]['acceptance_gate'],
            'selected_parameters': report['selected_parameters'][metric]}
    # Every comparator and every final metric are retained, without choosing a best plot subset.
    fig, axes = plt.subplots(2, 2, figsize=(10, 5.4), constrained_layout=True)
    names = ['RTT (ms)', 'Jitter (ms)', 'Loss (percentage points)', 'Utilization (percentage points)']
    short = ['Latest value', 'Linear trend', 'Original model', 'Previous model', 'Optimized v2']
    colors = ['#b37322', '#8894a0', '#9b627d', '#82aaa5', '#16776b']
    for ax, metric, label in zip(axes.flat, METRICS, names):
        vals = list(summary['metrics'][metric]['mae'].values())
        bars = ax.bar(range(5), vals, color=colors)
        for b, value in zip(bars, vals):
            ax.text(b.get_x() + b.get_width()/2, b.get_height(), f'{value:.3f}', ha='center', va='bottom', fontsize=9)
        ax.set_xticks(range(5), short, fontsize=8, rotation=12)
        ax.set_title(label, fontsize=12)
        ax.set_ylim(0, max(vals)*1.23 + .001)
        ax.grid(axis='y', alpha=.2)
        ax.set_axisbelow(True)
    fig.suptitle('Fresh confirmation / all 3 final episodes / real Linux veth packets\nMAE at 30 seconds; lower is better', fontsize=12)
    fig.savefig(REPORTS / 'confirmation-mae.png', dpi=220)
    plt.close(fig)
    explanation = explain(combined_path, OUTPUT, REPORTS / 'explanation')
    # Readable short labels for the small image-focused Word appendix.
    active = [(m,e) for m,e in explanation['metrics'].items() if 'residual_contributions' in e]
    if active:
        metric_short = {'latency_ms':'RTT','jitter_ms':'Jitter','loss_pct':'Loss','utilization_pct':'Util'}
        stat_short = {'current':'latest','lag1':'5 s ago','lag3':'15 s ago','mean':'mean','std':'variation','trend':'trend'}
        fig, axes = plt.subplots(1,len(active),figsize=(7,2.55),constrained_layout=True)
        for ax,(metric,entry) in zip(np.atleast_1d(axes),active):
            ordered = sorted(entry['residual_contributions'].items(),key=lambda item:item[1])[-4:]
            labels = []
            for name,_ in ordered:
                if name in ('up','history_fraction'):
                    labels.append('Link up' if name == 'up' else 'History available')
                else:
                    prefix,stat = name.rsplit('_',1)
                    labels.append(f"{metric_short.get(prefix,prefix)} {stat_short.get(stat,stat)}")
            ax.barh(labels,[value for _,value in ordered],color='#16776b')
            ax.set_title(metric_short[metric],fontsize=11)
            ax.tick_params(labelsize=9)
            ax.set_xlabel('Mean |contribution|',fontsize=8)
            ax.grid(axis='x',alpha=.2)
        fig.savefig(REPORTS / 'explanation/feature-contributions-compact.png',dpi=240)
        plt.close(fig)
    (REPORTS / 'confirmation.json').write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n')
    (REPORTS / 'raw-audit.json').write_text(json.dumps(raw_audit, indent=2) + '\n')
    # Verify the original runtime model was preserved throughout.
    assert fingerprint(baseline) == PROTOCOL['old_frozen_fingerprint']
    target = WORK / 'ml-results'
    if target.exists():
        raise FileExistsError('Do not overwrite retained results')
    shutil.copytree(REPORTS, target)
    shutil.copytree(OUTPUT, target / 'model')
    print(json.dumps({'output': str(target), 'metrics': summary['metrics'], 'raw_audit': raw_audit}, indent=2))


if __name__ == '__main__':
    main()
