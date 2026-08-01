"""カラースケール管理（距離→色のマッピング）。

JointSpaceVisualizer v2.1.0 の app/services/color_scale.py から、ヘッドレス変換に必要な
部分（ColorScale / build_lut / legend / VTP埋込）だけを抜き出し、app パッケージ非依存で
自己完結させたもの。GUI・永続化(ColorScaleManager)は移植していない。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
import pyvista as pv
from matplotlib.colors import LinearSegmentedColormap

# (距離[mm], (r, g, b))  r/g/b は 0.0–1.0
ColorPoint = Tuple[float, Tuple[float, float, float]]


@dataclass
class ColorScale:
    """名前付きの 距離→色 マッピング。"""

    name: str
    color_points: List[ColorPoint]

    def max_distance(self) -> float:
        return self.color_points[-1][0] if self.color_points else 5.0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "color_points": [[d, list(rgb)] for d, rgb in self.color_points],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ColorScale":
        return cls(
            name=data.get("name", "custom"),
            color_points=[(cp[0], tuple(cp[1])) for cp in data["color_points"]],
        )


# 組込み既定スケール（元アプリの TMJ ベーシックスケール / 0–5mm）
DEFAULT_SCALE = ColorScale(
    name="TMJベーシックスケール",
    color_points=[
        (0.0, (1.0, 0.0, 0.0)),
        (1.0, (1.0, 0.0, 0.0)),
        (1.6, (1.0, 1.0, 0.0)),
        (2.5, (0.0, 1.0, 0.0)),
        (3.3, (0.0, 1.0, 1.0)),
        (4.0, (0.0, 0.0, 1.0)),
        (5.0, (0.0, 0.0, 1.0)),
    ],
)


def load_scale(path: Optional[str], max_distance: Optional[float] = None) -> ColorScale:
    """JSON からスケールを読む。path が None なら組込み既定。

    max_distance を渡すと、color_points を 0–max_distance に線形リスケールして
    スケール上限を上書きする（元の境界比を保つ）。
    """
    if path is None:
        scale = ColorScale(DEFAULT_SCALE.name, list(DEFAULT_SCALE.color_points))
    else:
        with open(path, encoding="utf-8") as f:
            scale = ColorScale.from_dict(json.load(f))

    if max_distance is not None and max_distance > 0:
        cur_max = scale.max_distance()
        if cur_max > 0:
            factor = max_distance / cur_max
            scale.color_points = [(d * factor, rgb) for d, rgb in scale.color_points]
    return scale


def build_lut(scale: ColorScale) -> pv.LookupTable:
    """ColorScale から PyVista LookupTable を構築する（元アプリと同一ロジック）。"""
    max_d = scale.max_distance()
    if max_d <= 0:
        max_d = 1.0
    positions = [d / max_d for d, _ in scale.color_points]
    colors = [rgb for _, rgb in scale.color_points]
    cmap = LinearSegmentedColormap.from_list("custom_scale", list(zip(positions, colors)))
    lut = pv.LookupTable()
    lut.apply_cmap(cmap, n_values=256)
    lut.scalar_range = (0.0, max_d)
    return lut


def legend_segments(scale: ColorScale) -> List[Tuple[str, str]]:
    """凡例セグメント (ラベル, hexカラー) のリストを生成する。"""
    points = scale.color_points
    if not points:
        return []

    def _to_hex(rgb):
        return "#{:02x}{:02x}{:02x}".format(
            int(rgb[0] * 255), int(rgb[1] * 255), int(rgb[2] * 255)
        )

    segments: List[Tuple[str, str]] = []
    i = 0
    while i < len(points):
        d, rgb = points[i]
        hex_color = _to_hex(rgb)
        j = i + 1
        while j < len(points) and points[j][1] == rgb:
            j += 1
        if j > i + 1:
            label = f"{d:.1f}–{points[j - 1][0]:.1f} mm"
        else:
            label = f"{d:.1f} mm"
        segments.append((label, hex_color))
        i = j
    return segments


def embed_scale_in_mesh(mesh: pv.DataSet, scale: ColorScale) -> None:
    """スケール定義を field_data に書き込み VTP に持たせる（元アプリと相互運用）。"""
    name_codes = np.array([ord(c) for c in scale.name], dtype=np.int32)
    mesh.field_data["ScaleName"] = name_codes
    flat: List[float] = []
    for d, (r, g, b) in scale.color_points:
        flat.extend([float(d), float(r), float(g), float(b)])
    mesh.field_data["ScaleColorPoints"] = np.array(flat, dtype=np.float64)


def save_legend_png(scale: ColorScale, path: str) -> None:
    """スケール凡例を PNG に書き出す（任意・matplotlib）。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    segs = legend_segments(scale)
    fig, ax = plt.subplots(figsize=(2.4, max(1.5, 0.5 * len(segs))))
    ax.axis("off")
    for idx, (label, hex_color) in enumerate(reversed(segs)):
        ax.add_patch(plt.Rectangle((0, idx), 1, 0.9, facecolor=hex_color, edgecolor="#333"))
        ax.text(1.15, idx + 0.45, label, va="center", fontsize=10)
    ax.set_xlim(0, 3)
    ax.set_ylim(0, len(segs))
    ax.set_title(scale.name, fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
