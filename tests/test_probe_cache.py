"""Cache transport must not hide stale data or alter measured values/times."""

import json

import pytest

from lab.edge import probe_cache


def payload(timestamp=100):
    return {'schema': 1, 'measurement': 'icmp_rtt_stddev', 'probe_packets_per_wan': 100,
            'samples': [{'link_id': 'wan1', 'timestamp': timestamp, 'up': True,
                         'latency_ms': 27.5, 'jitter_ms': 3.2, 'loss_pct': 4.0,
                         'utilization_pct': 18.0}]}


def test_cached_read_preserves_observation_values_and_original_timestamp(tmp_path):
    path = tmp_path / 'cache.json'
    original = payload()
    path.write_text(json.dumps(original))
    assert probe_cache.read_cached(path, now=109) == original
    assert json.loads(path.read_text()) == original


@pytest.mark.parametrize('timestamp', [89, 102])
def test_stale_or_future_cache_is_rejected_without_retiming(tmp_path, timestamp):
    path = tmp_path / 'cache.json'
    original = payload(timestamp)
    path.write_text(json.dumps(original))
    with pytest.raises(ValueError, match='stale|future'):
        probe_cache.read_cached(path, now=100)
    assert json.loads(path.read_text()) == original


def test_failed_measurement_keeps_the_old_result_to_age_out(tmp_path, monkeypatch):
    path = tmp_path / 'cache.json'
    path.write_text(json.dumps(payload(50)))
    original = path.read_bytes()

    def failure(config):
        raise RuntimeError('fixture missing actual ICMP/VPP result')

    monkeypatch.setattr(probe_cache, 'measure', failure)
    with pytest.raises(RuntimeError):
        probe_cache.collect_once({}, path)
    assert path.read_bytes() == original
    with pytest.raises(ValueError, match='stale'):
        probe_cache.read_cached(path, now=100)


def test_successful_collection_is_published_atomically_without_changing_measurement(tmp_path, monkeypatch):
    path = tmp_path / 'cache.json'
    measured = payload()
    monkeypatch.setattr(probe_cache, 'measure', lambda config: measured)
    assert probe_cache.collect_once({}, path) == measured
    assert json.loads(path.read_text()) == measured
    assert not path.with_suffix('.tmp').exists()
