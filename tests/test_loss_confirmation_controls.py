"""Check the declared stimulus coverage; actual loss still requires packet audits."""

import pytest

from lab.loss_forecast_confirmation import conditions, plan


def test_training_controls_contain_complete_zero_loss_histories():
    protocol = plan()
    for seed in protocol['seeds'][:3]:
        for wan in range(3):
            trajectory = [conditions(seed, wan, tick * 5)['loss_pct'] for tick in range(25)]
            assert trajectory == [0.0] * 25


@pytest.mark.parametrize('start', [3, 6, 9])
def test_every_confirmation_partition_has_normal_and_nonzero_future_conditions(start):
    seeds = plan()['seeds'][start:start + 3]
    future = [conditions(seed, wan, tick * 5)['loss_pct']
              for seed in seeds for wan in range(3) for tick in range(17, 25)]
    assert sum(value > 0 for value in future) >= 30
    assert sum(value == 0 for value in future) >= 8


def test_each_wan_sees_every_profile_family_in_confirmation():
    for wan in range(3):
        regimes = {conditions(seed, wan, tick * 5)['regime']
                   for seed in plan()['seeds'][3:] for tick in range(25)}
        assert {'normal', 'gradual_loss', 'sustained_loss', 'loss_burst'} <= regimes


def test_confirmation_refuses_an_undeclared_seed_block():
    with pytest.raises(ValueError, match='frozen seed block'):
        plan(seed_base=1)
