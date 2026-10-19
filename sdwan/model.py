import hashlib
import json
import math
from pathlib import Path

import numpy as np
import xgboost as xgb
from sklearn.metrics import mean_absolute_error, r2_score
from xgboost import XGBRegressor

from .domain import HORIZON, INTERVAL, METRICS, WINDOW
from .features import FEATURE_NAMES, features, regular_history
from .linear import LinearHead
from .simulator import training_episode

MODEL_SCHEMA = 2


class Forecaster:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.metadata = json.loads((self.directory / "metadata.json").read_text(encoding="utf-8"))
        if (
            self.metadata["feature_names"] != FEATURE_NAMES
            or self.metadata["schema"] != MODEL_SCHEMA
        ):
            raise ValueError("incompatible model feature schema")
        if self.metadata["horizon_steps"] != HORIZON:
            raise ValueError("incompatible model horizon")
        default_weights = {
            metric: 1.0 if self.metadata["enabled_metrics"][metric] else 0.0 for metric in METRICS
        }
        self.blend_weights = self.metadata.get("blend_weights", default_weights)
        if (
            not isinstance(self.blend_weights, dict)
            or set(self.blend_weights) != set(METRICS)
            or any(
                isinstance(weight, bool)
                or not isinstance(weight, (int, float))
                or not math.isfinite(weight)
                or not 0 <= weight <= 1
                or bool(weight) != bool(self.metadata["enabled_metrics"][metric])
                for metric, weight in self.blend_weights.items()
            )
        ):
            raise ValueError("incompatible model blend weights")
        head_specs = self.metadata.get("linear_heads", {})
        if not isinstance(head_specs, dict) or not set(head_specs) <= set(METRICS):
            raise ValueError("incompatible linear head metrics")
        self.linear_heads = {metric: LinearHead(spec) for metric, spec in head_specs.items()}
        self.models = []
        for metric in METRICS:
            if metric in self.linear_heads:
                self.models.append(self.linear_heads[metric])
                continue
            model = XGBRegressor(n_jobs=2)
            model.load_model(self.directory / f"{metric}.json")
            self.models.append(model)

    def predict_matrix(self, matrix: np.ndarray) -> np.ndarray:
        matrix = np.asarray(matrix, dtype=np.float32)
        if (
            matrix.ndim != 2
            or matrix.shape[1] != len(FEATURE_NAMES)
            or not np.all(np.isfinite(matrix))
        ):
            raise ValueError("Expected a finite matrix with the model feature schema")
        if not len(matrix):
            return np.empty((0, len(METRICS)), dtype=np.float32)
        output = np.column_stack(
            [
                matrix[:, index * 6] + self.blend_weights[metric] * model.predict(matrix)
                if self.blend_weights[metric]
                else matrix[:, index * 6]
                for index, (metric, model) in enumerate(zip(METRICS, self.models))
            ]
        )
        output = np.maximum(0, output)
        output[:, 2:] = np.clip(output[:, 2:], 0, 100)
        return output

    def predict(self, history):
        if not regular_history(history):
            return None
        return self.predict_matrix(features(history)[None, :])[0].tolist()


def dataset(episodes: range, seed_offset: int):
    inputs, targets, current = [], [], []
    for episode in episodes:
        series = training_episode(seed_offset + episode, episode)
        for history in series.values():
            for tick in range(WINDOW - 1, len(history) - HORIZON):
                inputs.append(features(history[tick - WINDOW + 1 : tick + 1]))
                targets.append(history[tick + HORIZON].vector())
                current.append(history[tick].vector())
    return tuple(np.asarray(rows, dtype=np.float32) for rows in (inputs, targets, current))


def train(directory="artifacts/model", train_episodes=60, validation_episodes=15):
    if train_episodes < 1 or validation_episodes < 1:
        raise ValueError("training and validation require at least one complete episode each")
    directory = Path(directory)
    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
        raise ValueError("Existing model preserved; choose an empty model directory")
    directory.mkdir(parents=True, exist_ok=True)
    x_train, y_train, _ = dataset(range(train_episodes), 1000)
    x_val, y_val, persistence = dataset(
        range(train_episodes, train_episodes + validation_episodes), 1000
    )
    metrics, parameters, enabled = {}, {}, {}
    for index, metric in enumerate(METRICS):
        candidates = []
        for depth in (3, 5):
            model = XGBRegressor(
                n_estimators=240,
                max_depth=depth,
                learning_rate=0.05,
                subsample=0.85,
                colsample_bytree=0.9,
                reg_lambda=5,
                objective="reg:absoluteerror",
                eval_metric="mae",
                tree_method="hist",
                n_jobs=4,
                random_state=42,
                early_stopping_rounds=20,
            )
            model.fit(
                x_train,
                y_train[:, index] - x_train[:, index * 6],
                eval_set=[(x_val, y_val[:, index] - x_val[:, index * 6])],
                verbose=False,
            )
            prediction = np.maximum(0, model.predict(x_val) + x_val[:, index * 6])
            if index >= 2:
                prediction = np.clip(prediction, 0, 100)
            candidates.append((mean_absolute_error(y_val[:, index], prediction), model, prediction))
        mae, model, prediction = min(candidates, key=lambda row: row[0])
        model.save_model(directory / f"{metric}.json")
        parameters[metric] = {"max_depth": model.max_depth, "best_iteration": model.best_iteration}
        persistence_mae = float(mean_absolute_error(y_val[:, index], persistence[:, index]))
        enabled[metric] = bool(mae < persistence_mae * 0.98)
        metrics[metric] = {
            "raw_xgboost_validation_mae": float(mae),
            "deployed_validation_mae": float(mae) if enabled[metric] else persistence_mae,
            "persistence_validation_mae": persistence_mae,
            "xgboost_enabled": enabled[metric],
            "validation_r2": float(r2_score(y_val[:, index], prediction)),
        }
    metadata = {
        "schema": MODEL_SCHEMA,
        "feature_names": FEATURE_NAMES,
        "horizon_steps": HORIZON,
        "horizon_seconds": HORIZON * INTERVAL,
        "window_steps": WINDOW,
        "interval_seconds": INTERVAL,
        "training_rows": len(x_train),
        "validation_rows": len(x_val),
        "train_episodes": list(range(train_episodes)),
        "validation_episodes": list(range(train_episodes, train_episodes + validation_episodes)),
        "seed_offset": 1000,
        "xgboost_version": xgb.__version__,
        "selection": "minimum validation MAE among max_depth 3 and 5; no benchmark access",
        "split": "whole episodes in chronological order; histories and future targets never cross episodes",
        "label": "actual metric at t+30 seconds (not scenario ID or future path label)",
        "metrics": metrics,
        "selected_parameters": parameters,
        "enabled_metrics": enabled,
        "target_mode": "future metric minus current metric; MAE objective",
        "deployment_gate": "XGBoost enabled per metric only if validation MAE improves persistence by >=2%; otherwise persistence",
        "data_kind": "synthetic",
    }
    (directory / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata


def fingerprint(directory):
    digest = hashlib.sha256()
    for path in sorted(Path(directory).glob("*.json")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()
