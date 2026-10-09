import json

import numpy as np
import pytest

from sdwan.domain import METRICS
from sdwan.features import features
from sdwan.model import Forecaster, fingerprint, train
from sdwan.reporting import wilson_interval
from sdwan.simulator import training_episode


def test_training_roundtrip_validation_gate_and_gap_fallback(tmp_path):
    metadata = train(tmp_path, train_episodes=2, validation_episodes=1)
    assert not set(metadata["train_episodes"]) & set(metadata["validation_episodes"])
    assert metadata["training_rows"] > 1000
    model = Forecaster(tmp_path)
    history = training_episode(90000, 0)["wan1"][:12]
    predictions = model.predict(history)
    assert predictions is not None and np.all(np.isfinite(predictions))
    for index, metric in enumerate(METRICS):
        stats = metadata["metrics"][metric]
        assert stats["deployed_validation_mae"] <= stats["persistence_validation_mae"]
        if not stats["xgboost_enabled"]:
            assert predictions[index] == pytest.approx(history[-1].vector()[index])
    assert model.predict(history[:11]) is None
    old_hash = fingerprint(tmp_path)
    saved = json.loads((tmp_path / "metadata.json").read_text())
    saved["schema"] = -1
    (tmp_path / "metadata.json").write_text(json.dumps(saved))
    assert old_hash != fingerprint(tmp_path)
    with pytest.raises(ValueError, match="schema"):
        Forecaster(tmp_path)
    assert features(history).shape == (26,)


def test_wilson_interval_does_not_claim_certainty_on_thirty_successes():
    lower, upper = wilson_interval(30, 30)
    assert 0.88 < lower < 0.89 and upper == pytest.approx(1)
