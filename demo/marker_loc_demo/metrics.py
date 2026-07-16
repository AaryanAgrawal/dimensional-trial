from __future__ import annotations

import numpy as np


def ate_rmse(true_x: np.ndarray, true_y: np.ndarray, est_x: np.ndarray, est_y: np.ndarray) -> float:
    return float(np.sqrt(np.mean((true_x - est_x) ** 2 + (true_y - est_y) ** 2)))


def per_frame_error(true_x: np.ndarray, true_y: np.ndarray, est_x: np.ndarray, est_y: np.ndarray) -> np.ndarray:
    return np.sqrt((true_x - est_x) ** 2 + (true_y - est_y) ** 2)
