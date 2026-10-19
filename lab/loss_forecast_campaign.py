"""Collect a loss-focused real-packet campaign in owned, isolated veth links.

Reuses the audited collector, retains the five-second cadence and 30-second
target, and includes sustained nonzero loss rather than changing test labels.
Network conditions/seed/profile names are never supplied to the forecaster.
"""

import argparse
import hashlib
import inspect
import json
import random
from pathlib import Path

from lab import link_forecast_campaign as collector

DEFAULT_COUNTS = (6, 3, 3, 3)
DEFAULT_SEED = 2026101800
ORIGINAL_PLAN = collector.plan


def conditions(seed, link_index, elapsed):
    rng = random.Random(seed + 1009 * link_index)
    level = rng.uniform(3.0, 10.0)
    if link_index == 0:
        # Smooth degradation and recovery with episode-specific timing/amplitude.
        peak_time = rng.uniform(70, 95)
        floor = rng.uniform(0, 1)
        factor = min(1, elapsed / peak_time) if elapsed <= peak_time else max(0, 1 - (elapsed - peak_time) / rng.uniform(35, 65))
        loss = floor + level * factor
        regime = 'gradual_loss' if elapsed <= peak_time else 'loss_recovery'
    elif link_index == 1:
        # A hidden step is not predictable in advance; retain it in evaluation.
        begin, end = rng.uniform(20, 45), rng.uniform(95, 115)
        loss = level if begin <= elapsed < end else 0.0
        regime = 'sustained_loss' if loss else 'normal'
    else:
        # Intermittent bursts mixed with a lower, sustained background loss.
        period = rng.uniform(25, 45)
        phase = (elapsed + rng.uniform(0, period)) % period
        loss = level if phase < period * .4 else rng.uniform(.5, 2.0)
        regime = 'loss_burst' if phase < period * .4 else 'low_loss'
    return {
        'delay_ms': (6.0, 10.0, 14.0)[link_index],
        'jitter_ms': 1.0,
        'loss_pct': loss,
        'load_fraction': (.12, .18, .22)[link_index],
        'regime': regime,
    }


def plan(seed_base=DEFAULT_SEED, counts=None):
    result = ORIGINAL_PLAN(seed_base, DEFAULT_COUNTS if counts is None else counts)
    result.update({
        'campaign': 'nonzero_packet_loss_confirmation',
        'generator_sha256': hashlib.sha256(inspect.getsource(conditions).encode()).hexdigest(),
        'scenarios': ['normal', 'gradual_loss', 'sustained_loss', 'loss_burst', 'loss_recovery'],
        'loss_definition': 'NetEm loss probability on each direction; target is measured round-trip ICMP loss, not configured probability.',
        'model_changes': 'loss head only; keep RTT, jitter, utilization weights of frozen v2 unchanged',
        'search': 'existing predeclared 5 Ridge alphas and 4 XGBoost configurations; residual weights 0/.25/.5/.75/1; Tune only',
        'coverage': 'Report nonzero targets, MAE on all/zero/nonzero targets and every episode; never choose windows based on prediction error.',
        'holdout_policy': 'One evaluation after freezing selection/gate; no retraining after viewing holdout.',
    })
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--plan', action='store_true')
    parser.add_argument('--seed-base', type=int, default=DEFAULT_SEED)
    parser.add_argument('--counts', nargs=4, type=int, default=DEFAULT_COUNTS)
    args = parser.parse_args()
    if args.plan:
        print(json.dumps(plan(args.seed_base, args.counts), indent=2))
    elif args.output:
        collector.conditions = conditions
        collector.plan = plan
        collector.run(args.output.resolve(), seed_base=args.seed_base, counts=args.counts)
    else:
        parser.error('--output or --plan required')


if __name__ == '__main__':
    main()
