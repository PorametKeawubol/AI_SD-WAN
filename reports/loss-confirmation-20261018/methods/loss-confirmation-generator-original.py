"""Loss confirmation with normal controls and WAN/profile rotation."""

import argparse
import hashlib
import inspect
import json
from pathlib import Path

from lab import link_forecast_campaign as collector
from lab.loss_forecast_campaign import ORIGINAL_PLAN, conditions as loss_conditions

DEFAULT_SEED = 2026101900
DEFAULT_COUNTS = (3, 3, 3, 3)


def conditions(seed, link_index, elapsed):
    episode_index = (seed - DEFAULT_SEED) // 7919
    profile = (episode_index + link_index) % 4
    source_profile = max(0, profile - 1)
    result = loss_conditions(seed, source_profile, elapsed)
    # The first three fresh episodes are normal training controls. Later
    # episodes rotate normal/ramp/step/bursts across WANs to avoid a WAN shortcut.
    if episode_index < 3 or profile == 0:
        result.update(loss_pct=0.0, regime='normal')
    result['delay_ms'] = (6.0, 10.0, 14.0)[link_index]
    result['load_fraction'] = (.12, .18, .22)[link_index]
    return result


def plan(seed_base=DEFAULT_SEED, counts=None):
    if seed_base != DEFAULT_SEED:
        raise ValueError('Use the frozen seed block for this confirmation campaign')
    result = ORIGINAL_PLAN(seed_base, DEFAULT_COUNTS if counts is None else counts)
    result.update({
        'campaign': 'nonzero_loss_with_normal_controls',
        'generator_sha256': hashlib.sha256((inspect.getsource(conditions) + inspect.getsource(loss_conditions)).encode()).hexdigest(),
        'scenarios': ['normal', 'gradual_loss', 'sustained_loss', 'loss_burst', 'loss_recovery'],
        'training_controls': 'First 3 fresh episodes have zero configured loss on all WANs; append to 6 prior complete training episodes only.',
        'profile_assignment': 'Normal/ramp/step/burst rotate across WANs and episodes, never supplied as model features.',
        'loss_definition': 'Measured 100-probe ICMP round-trip loss; NetEm configured probability is not a target label.',
        'runtime_promotion': False,
    })
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--plan', action='store_true')
    args = parser.parse_args()
    if args.plan:
        print(json.dumps(plan(), indent=2))
    elif args.output:
        collector.conditions = conditions
        collector.plan = plan
        collector.run(args.output.resolve(), seed_base=DEFAULT_SEED, counts=DEFAULT_COUNTS)
    else:
        parser.error('--output or --plan required')


if __name__ == '__main__':
    main()
