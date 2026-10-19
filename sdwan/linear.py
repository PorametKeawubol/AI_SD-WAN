"""JSON-serializable linear residual heads with training-only scaling."""

import numpy as np

from .features import FEATURE_NAMES


def relative_features(matrix):
    result = np.array(matrix, dtype=np.float64, copy=True)
    for index in range(4):
        start = index * 6
        result[:, start + 1 : start + 4] -= result[:, start : start + 1]
        result[:, start + 5] *= 30  # observed slope over the forecast horizon
    return result


class LinearHead:
    def __init__(self, specification):
        if not isinstance(specification, dict) or specification.get("schema") != "relative_lags_v1":
            raise ValueError("incompatible linear head schema")
        try:
            self.center = np.asarray(specification["center"], dtype=np.float64)
            self.scale = np.asarray(specification["scale"], dtype=np.float64)
            self.coefficients = np.asarray(specification["coefficients"], dtype=np.float64)
            self.intercept = float(specification["intercept"])
        except (KeyError, ValueError, TypeError) as error:
            raise ValueError("incompatible linear head values") from error
        if (
            any(
                values.shape != (len(FEATURE_NAMES),) or not np.all(np.isfinite(values))
                for values in (self.center, self.scale, self.coefficients)
            )
            or not np.all(self.scale > 0)
            or not np.isfinite(self.intercept)
        ):
            raise ValueError("incompatible linear head values")
        self.specification = specification

    def predict(self, matrix):
        return (
            (relative_features(matrix) - self.center) / self.scale
        ) @ self.coefficients + self.intercept
