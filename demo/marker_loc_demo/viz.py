from __future__ import annotations

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .world import MarkerSpec


def plot_trajectory(
    path: str,
    true_x: np.ndarray,
    true_y: np.ndarray,
    raw_x: np.ndarray,
    raw_y: np.ndarray,
    corr_x: np.ndarray,
    corr_y: np.ndarray,
    markers: list[MarkerSpec],
    room_w: float,
    room_d: float,
) -> None:
    fig, ax = plt.subplots(figsize=(9, 7.2))
    ax.plot([0, room_w, room_w, 0, 0], [0, 0, room_d, room_d, 0], color="0.3", lw=1.5, label="room walls")
    ax.plot(true_x, true_y, color="#1b9e77", lw=2, label="ground truth", zorder=5)
    ax.plot(raw_x, raw_y, color="#d95f02", lw=1.3, ls="--", label="raw odom (drifted)", zorder=3)
    ax.plot(corr_x, corr_y, color="#7570b3", lw=1.5, label="tag-corrected", zorder=4)
    mx = [m.translation[0] for m in markers]
    my = [m.translation[1] for m in markers]
    ax.scatter(mx, my, marker="s", s=45, color="#333", label="AprilTag markers", zorder=6)
    for m in markers:
        ax.annotate(str(m.id), (m.translation[0], m.translation[1]), fontsize=7, ha="center", va="center", color="white")
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    ax.set_title("Trajectory: ground truth vs raw odom vs tag-corrected")
    ax.set_aspect("equal")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.08), ncol=3, fontsize=9)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_error(path: str, t: np.ndarray, err_raw: np.ndarray, err_corr: np.ndarray, detected: np.ndarray) -> None:
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(t, err_raw, color="#d95f02", lw=1.3, label="raw odom error")
    ax.plot(t, err_corr, color="#7570b3", lw=1.6, label="tag-corrected error")
    ymax = max(float(err_raw.max()), float(err_corr.max())) * 1.05
    ax.fill_between(t, 0, ymax, where=detected, color="#7570b3", alpha=0.06, step="mid", label="tag(s) visible")
    ax.set_ylim(0, ymax)
    ax.set_xlabel("time (s)")
    ax.set_ylabel("position error vs ground truth (m)")
    ax.set_title("Position error over time")
    ax.legend(loc="upper left")
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def save_sample_frame(path: str, frame: np.ndarray, corners: list, ids, title: str) -> None:
    vis = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    if ids is not None and len(corners) > 0:
        cv2.aruco.drawDetectedMarkers(vis, corners, ids)
    cv2.putText(vis, title, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1, cv2.LINE_AA)
    cv2.imwrite(path, vis)
