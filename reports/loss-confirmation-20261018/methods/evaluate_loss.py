"""One-shot loss-only selection with untouched acceptance and final episodes."""
from pathlib import Path
import copy
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
WORK = Path('/mnt/e/Proposal/.codex-review/loss-sla-20261018')
sys.path.insert(0, str(ROOT))
from sdwan.domain import METRICS
from sdwan.forecast_search import search_plan, select_candidate
from sdwan.linear import LinearHead
from sdwan.model import Forecaster, fingerprint
from sdwan.optimization import acceptance_gate, chronological_split, clip_predictions, prepare_partition, validate_dataset


def raw_audit(directory, document):
    audit = {'probe_batches': 0, 'icmp_sent': 0, 'icmp_received': 0, 'nonzero_loss_batches': 0,
             'maximum_metric_discrepancy': 0.0, 'counter_rows': 0, 'counters_verified': True,
             'pcap_scope': 'first 1000 captured frames per WAN, not the entire campaign'}
    for episode_index, episode in enumerate(document['episodes']):
        counters = [json.loads(line) for line in (directory / f'counters-{episode_index:02}.jsonl').read_text().splitlines()]
        assert len(counters) == 75
        lookup = {(row['tick'], row['link_id']): row for row in counters}
        for link_index, (link, series) in enumerate(episode['series'].items()):
            probes = [json.loads(line) for line in (directory / f'probes-{episode_index:02}-{link_index}.jsonl').read_text().splitlines()]
            assert len(probes) == len(series) == 25
            for tick, (row, probe) in enumerate(zip(series, probes)):
                match = re.search(r'(\d+) packets transmitted, (\d+) received', probe['stdout'])
                assert match and int(match[1]) == 100
                sent, received = map(int, match.groups())
                timings = [float(value) for value in re.findall(r'time[=<]([\d.]+) ms', probe['stdout'])]
                assert len(timings) == received and received > 0
                expected = {'latency_ms': statistics.mean(timings), 'jitter_ms': statistics.pstdev(timings),
                            'loss_pct': 100.0 * (sent - received) / sent}
                for key, value in expected.items():
                    delta = abs(row[key] - value)
                    audit['maximum_metric_discrepancy'] = max(audit['maximum_metric_discrepancy'], delta)
                    assert delta < 1e-8
                counter = lookup[(tick, link)]
                util = 100 * (counter['tx_bytes_after'] - counter['tx_bytes_before']) * 8 / (counter['elapsed_seconds'] * counter['configured_mbps'] * 1e6)
                assert np.isclose(util, counter['raw_utilization_pct'], atol=1e-10)
                assert np.isclose(np.clip(util, 0, 100), row['utilization_pct'], atol=1e-10)
                audit['probe_batches'] += 1
                audit['icmp_sent'] += sent
                audit['icmp_received'] += received
                audit['nonzero_loss_batches'] += int(received < sent)
                audit['counter_rows'] += 1
    return audit


def bootstrap_difference(differences, groups, seed):
    rng = np.random.default_rng(seed)
    blocks = [differences[groups == group] for group in sorted(set(groups))]
    means = [np.concatenate([blocks[i] for i in rng.integers(0, len(blocks), len(blocks))]).mean() for _ in range(10000)]
    return np.quantile(means, [.025, .975]).tolist()


def main():
    protocol = json.loads((WORK / 'predeclared-protocol-v2.json').read_text())
    fresh = Path(protocol['data_directory'])
    output = Path(protocol['new_loss_bundle'])
    reports = Path(protocol['reports'])
    assert not output.exists() and not reports.exists(), 'Never rerun selection over opened confirmation sets'
    assert json.loads(json.dumps(search_plan())) == protocol['candidate_selection']
    for name, expected in protocol['code_sha256'].items():
        actual_source = (ROOT / name).read_bytes()
        if name == 'lab/loss_forecast_confirmation.py':
            retained = (WORK / 'loss-confirmation-generator-original.py').read_bytes()
            assert hashlib.sha256(retained).hexdigest() == expected
            # Ruff split a single import after collection started. Keep and
            # verify exact collection source; no function or parameter changed.
            formatted = retained.replace(
                b'from lab.loss_forecast_campaign import ORIGINAL_PLAN, conditions as loss_conditions',
                b'from lab.loss_forecast_campaign import ORIGINAL_PLAN\nfrom lab.loss_forecast_campaign import conditions as loss_conditions')
            assert actual_source == formatted
        else:
            assert hashlib.sha256(actual_source).hexdigest() == expected
    assert json.loads((fresh / 'protocol.json').read_text()) == protocol['collection']
    status = json.loads((fresh / 'status.json').read_text())
    assert status['completed'] and status['cleanup_complete'] and not status['errors']
    assert status['episodes_completed'] == 12
    fresh_document = json.loads((fresh / 'dataset.json').read_text())
    old_directory = Path(protocol['old_training_directory'])
    old_status = json.loads((old_directory / 'status.json').read_text())
    assert old_status['cleanup_complete']
    old_document = json.loads((old_directory / 'dataset.json').read_text())
    old_training = {**old_document, 'episodes': old_document['episodes'][:6]}
    assert [episode['id'] for episode in old_training['episodes']] == protocol['old_training_episode_ids']
    audit_old = raw_audit(old_directory, old_training)
    audit_new = raw_audit(fresh, fresh_document)
    audit = {key: audit_old[key] + audit_new[key] for key in
             ['probe_batches', 'icmp_sent', 'icmp_received', 'nonzero_loss_batches', 'counter_rows']}
    audit.update(maximum_metric_discrepancy=max(audit_old['maximum_metric_discrepancy'], audit_new['maximum_metric_discrepancy']),
                 counters_verified=True, pcap_scope='First 1000 frames per WAN in each of two campaigns; not the complete packet stream.',
                 unused_original_complete_episodes=[e['id'] for e in old_document['episodes'][6:]])
    document = {**fresh_document, 'episodes': old_training['episodes'] + fresh_document['episodes']}
    dataset = validate_dataset(document)
    partitions = chronological_split(dataset, *protocol['combined_counts'])
    prepared = {name: prepare_partition(partitions[name], dataset.sampling) for name in ['train', 'tune', 'accept']}
    original, previous = Path(protocol['baseline_model']), Path(protocol['frozen_v2'])
    assert fingerprint(original) == protocol['baseline_sha256']
    assert fingerprint(previous) == protocol['frozen_v2_sha256']
    index, metric = 2, 'loss_pct'
    parameters, model, weight, tune_mae, tuning_audit = select_candidate(prepared['train'], prepared['tune'], index)
    accept = prepared['accept']
    mask = accept.available[:, index]
    current = accept.x[mask, index * 6]
    raw = clip_predictions(current + weight * model.predict(accept.x[mask]), index)
    gate = acceptance_gate(accept.y[mask, index], raw, current, accept.groups[mask], weight=weight,
                           bootstrap_seed=202610182)
    deployed_weight = weight if gate['accepted'] else 0.0
    # Save the frozen selection and gate before constructing or examining Holdout.
    reports.mkdir(parents=True)
    shutil.copy2(WORK / 'predeclared-protocol-v2.json', reports / 'predeclared-protocol.json')
    shutil.copy2(WORK / 'predeclared-protocol.json', reports / 'original-protocol-before-coverage-amendment.json')
    (reports / 'selection-frozen.json').write_text(json.dumps({'metric': metric, 'parameters': parameters,
        'tuning_weight': weight, 'deployed_weight': deployed_weight, 'tuning_mae': tune_mae,
        'acceptance': gate, 'tuning_candidates': tuning_audit}, indent=2) + '\n')
    shutil.copytree(previous, output)
    metadata = json.loads((output / 'metadata.json').read_text())
    prior_metadata = copy.deepcopy(metadata)
    if isinstance(model, LinearHead):
        metadata['linear_heads'][metric] = model.specification
    else:
        metadata['linear_heads'].pop(metric, None)
        model.save_model(output / (metric + '.json'))
    metadata['blend_weights'][metric] = deployed_weight
    metadata['enabled_metrics'][metric] = deployed_weight > 0
    metadata['selected_parameters'][metric] = {**parameters, 'tuning_weight': weight,
                                              'candidate_weight': deployed_weight, 'tuning_mae': tune_mae}
    metadata['loss_adaptation'] = {'dataset_sha256': dataset.sha256, 'partitions': {name: [e.id for e in episodes] for name, episodes in partitions.items()},
        'acceptance': gate, 'source_bundle_sha256': fingerprint(previous),
        'untouched_heads': ['latency_ms', 'jitter_ms', 'utilization_pct'],
        'scope': 'real ICMP/UDP in isolated Linux veth/NetEm, no flexiEdge/VPP/overlay'}
    metadata['offline_candidate'], metadata['runtime_promoted'] = True, False
    (output / 'metadata.json').write_text(json.dumps(metadata, indent=2, allow_nan=False) + '\n')
    (output / 'README.md').write_text(
        '# Loss-focused offline candidate\n\n'
        'RTT, jitter, and utilization heads are unchanged from model-search-link-v2-20261017.\n'
        'Only packet loss was selected with new measured normal/loss controls, chronological Train/Tune/Acceptance/Holdout.\n'
        f'Loss family: {parameters["family"]}; acceptance passed: {gate["accepted"]}; active residual weight: {deployed_weight}.\n'
        'This directory is an experimental candidate, not a replacement for the frozen default demo bundle.\n')
    candidate = Forecaster(output)
    previous_model = Forecaster(previous)
    hold = prepare_partition(partitions['holdout'], dataset.sampling)
    predictions = {'persistence': hold.x[:, [0, 6, 12, 18]],
                   'original_demo': Forecaster(original).predict_matrix(hold.x),
                   'frozen_v2': previous_model.predict_matrix(hold.x),
                   'loss_v3_gated': candidate.predict_matrix(hold.x)}
    raw_candidate = clip_predictions(hold.x[:, 12] + weight * model.predict(hold.x), 2)
    for i in [0, 1, 3]:
        assert np.array_equal(predictions['frozen_v2'][:, i], predictions['loss_v3_gated'][:, i])
        key = METRICS[i]
        assert metadata['blend_weights'][key] == prior_metadata['blend_weights'][key]
        assert metadata['selected_parameters'][key] == prior_metadata['selected_parameters'][key]
    results = {'dataset_sha256': dataset.sha256, 'bundle_sha256': fingerprint(output), 'runtime_promoted': False,
        'scope': metadata['loss_adaptation']['scope'], 'partitions': metadata['loss_adaptation']['partitions'],
        'acceptance': gate, 'selected_parameters': metadata['selected_parameters'][metric],
        'raw_audit': audit, 'window_counters': hold.counters, 'metrics': {}}
    for i, key in enumerate(METRICS):
        mask = hold.available[:, i]
        target = hold.y[mask, i]
        groups = hold.groups[mask]
        errors = {name: np.abs(values[mask, i] - target) for name, values in predictions.items()}
        nonzero = target > 0
        entry = {'samples': len(target), 'nonzero_targets': int(nonzero.sum()), 'zero_targets': int((~nonzero).sum()),
                 'mae': {name: float(error.mean()) for name, error in errors.items()}}
        entry['gain_vs_persistence_pct'] = 100 * (1 - entry['mae']['loss_v3_gated'] / entry['mae']['persistence']) if entry['mae']['persistence'] else None
        entry['descriptive_episode_bootstrap_95ci'] = bootstrap_difference(errors['loss_v3_gated'] - errors['persistence'], groups, 202610180 + i)
        if key == metric:
            assert nonzero.sum() >= 30, 'No claim without measured nonzero final targets'
            entry['raw_selected_candidate_mae'] = float(np.abs(raw_candidate[mask] - target).mean())
            entry['mae_nonzero'] = {name: float(error[nonzero].mean()) for name, error in errors.items()}
            entry['mae_zero'] = {name: float(error[~nonzero].mean()) if (~nonzero).any() else None for name, error in errors.items()}
            entry['by_episode'] = {str(group): {'samples': int((groups == group).sum()),
                'nonzero_targets': int(nonzero[groups == group].sum()),
                'mae': {name: float(error[groups == group].mean()) for name, error in errors.items()}}
                for group in sorted(set(groups))}
            entry['threshold_1pct'] = {}
            actual = target > 1
            for name, values in predictions.items():
                forecast = values[mask, i] > 1
                tp, fp, fn = int((actual & forecast).sum()), int((~actual & forecast).sum()), int((actual & ~forecast).sum())
                entry['threshold_1pct'][name] = {'true_positive': tp, 'false_positive': fp, 'false_negative': fn,
                    'precision': tp / (tp + fp) if tp + fp else None,
                    'recall': tp / (tp + fn) if tp + fn else None}
            traces = []
            for row_index in np.flatnonzero(mask):
                traces.append({'episode': str(hold.groups[row_index]), 'actual_loss_at_plus_30s': float(hold.y[row_index, i]),
                    **{name: float(values[row_index, i]) for name, values in predictions.items()}})
            (reports / 'holdout-loss-predictions.json').write_text(json.dumps(traces, indent=2) + '\n')
        results['metrics'][key] = entry
    assert fingerprint(original) == protocol['baseline_sha256']
    assert fingerprint(previous) == protocol['frozen_v2_sha256']
    results['baseline_bundles_preserved'] = True
    (reports / 'confirmation.json').write_text(json.dumps(results, indent=2, allow_nan=False) + '\n')
    (reports / 'raw-audit.json').write_text(json.dumps(audit, indent=2) + '\n')
    (reports / 'dataset.json').write_text(json.dumps(document, allow_nan=False) + '\n')
    loss = results['metrics'][metric]
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.4), constrained_layout=True)
    labels = ['Latest value', 'Demo model', 'Frozen v2', 'Loss v3']
    colors = ['#ad793b', '#aa8192', '#909aa6', '#16776b']
    for ax, field, title in zip(axes, ['mae', 'mae_nonzero'], ['All final windows', 'Actual loss > 0%']):
        vals = list(loss[field].values())
        bars = ax.bar(labels, vals, color=colors)
        for bar, value in zip(bars, vals):
            ax.text(bar.get_x() + bar.get_width()/2, value, f'{value:.3f}', ha='center', va='bottom')
        ax.set_ylim(0, max(vals)*1.23 + .01)
        ax.set_title(title)
        ax.set_ylabel('Loss MAE (percentage points)')
        ax.grid(axis='y', alpha=.2)
        ax.set_axisbelow(True)
    fig.suptitle(f'Real ICMP loss / 30-second forecast / {loss["samples"]} final windows ({loss["nonzero_targets"]} nonzero)')
    fig.savefig(reports / 'loss-confirmation-mae.png', dpi=220)
    plt.close(fig)
    shutil.copytree(reports, WORK / 'results')
    shutil.copytree(output, WORK / 'results/model')
    print(json.dumps({'reports': str(reports), 'loss': loss, 'acceptance': gate, 'raw_audit': audit}, indent=2))


if __name__ == '__main__':
    main()
