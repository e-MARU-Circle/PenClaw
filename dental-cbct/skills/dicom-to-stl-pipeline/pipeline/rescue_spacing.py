#!/usr/bin/env python3
"""spacing（voxel 間隔）が欠落・破損したボリュームを救済する推定モジュール。

歯科用 CT ビューアから「表示用の断面画像」として書き出されたデータは、
PixelSpacing / SliceThickness が失われていることがある。mm が定まらないため
そのままでは 3D 化（STL 化）できない。本モジュールは参照断面画像
（冠状 / 矢状）を手がかりに voxel 間隔を推定して救済する。

設計は TotalSegmentator Wrapper for Mac 0.4.1 の救済パイプライン
（rescue_estimation.py / rescue_pipeline.py）の**仕様**を参考にした再実装であり、
コードの複製ではない。

本モジュールの原則:
  - **推論（セグメンテーション）は一切走らせない**。幾何情報の推定だけを行う。
  - DICOM タグに触れない。扱うのは匿名化済み NIfTI／生ボリューム配列と
    数値パラメータのみ（PHI 非接触は anonymize_dicom.py と同じ思想）。
  - 標準出力に **絶対パスを出さない**（ファイル名のみ）。画素値も出さない。
    stdout は機械可読出力（estimate の JSON）専用で、ログは stderr に出す。
  - 推定結果をそのまま確定させない。`estimate` → 人間の確認 → `finalize` の
    3 段構成で、ボリューム同一性（manifest SHA-256 の再計算）と確認トークンの
    両方が一致しないと NIfTI を書き出さない。

前提と制約（実装上の割り切り）:
  - **配列軸は (x, y, z) 順で、x=左右 / y=前後 / z=上下**を仮定する。`load_array` は
    NIfTI を (i, j, k) のまま読み、affine を見て向きを合わせる処理は入れていない。
    救済対象が「向き情報が壊れた表示用書き出し」であるための割り切りなので、
    軸順が異なるデータは呼び出し側で転置してから渡すこと。
  - **spacing 探索は 2 自由度（面内一様スケール × Z）**。x と y の異方誤差
    （面内アスペクト比の誤り）は探索では補正できない（`tri_planar_spacing_search` 参照）。

工程:
  1. foreground_bbox()          前景 bbox と焼き込みオーバーレイの疑い検出
  2. series_count_fov_seed()    シリーズ枚数 × スライス間隔から FOV 逆算 → 初期 spacing
  3. tri_planar_spacing_search() 中央断面 × 参照断面の多スケール NMI 格子探索
  4. cross_validate()           独立した再構成グループ間の相互検証（15% 以内）
  5. build_confirmation_token() / finalize()  トークン照合 → 書き出し → 書き戻し検証

使い方の例:
  python3 rescue_spacing.py estimate --volume case.nii.gz \\
      --ref-coronal cor.npy --coronal-count 200 --coronal-interval 0.4 \\
      --ref-sagittal sag.npy --sagittal-count 200 --sagittal-interval 0.4 \\
      --axial-interval 0.4
  python3 rescue_spacing.py preview --volume case.nii.gz --spacing 0.3,0.3,0.4 --out preview_dir
  python3 rescue_spacing.py finalize --volume case.nii.gz --spacing 0.3,0.3,0.4 \\
      --manifest-sha <estimate が出した値> --token <estimate が出した値> --out rescued.nii.gz

終了コード: 0=成功 / 1=引数不備 / 2=エラー / 4=確認トークン不一致 / 5=ambiguous（--fail-on-ambiguous 時）/ 99=想定外
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import itertools
import json
import os
import struct
import sys
import zlib
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

import numpy as np

# --------------------------------------------------------------------------- #
# 既定パラメータ（本家 0.4.1 の救済パイプラインに倣った値）
# --------------------------------------------------------------------------- #
SCALE_FACTORS: tuple[float, ...] = (0.75, 0.875, 1.0, 1.125, 1.25)
PYRAMID_LEVELS: tuple[float, ...] = (0.25, 0.5)
MAX_EVALUATIONS: int = 64
AMBIGUITY_MARGIN: float = 0.01
CROSS_VALIDATION_TOLERANCE: float = 0.15
BOUNDARY_COVERAGE_LIMIT: float = 0.25
NMI_BINS: int = 64

Log = Callable[[str], None]


def _log(msg: str) -> None:
    """既定ロガー。stdout は機械可読出力（JSON）専用なのでログは stderr へ流す。"""
    print(msg, file=sys.stderr, flush=True)


def _silent(_msg: str) -> None:
    """ログを完全に抑止したいとき用。"""


class RescueError(Exception):
    """spacing 救済処理のいずれかの工程が失敗したときに送出。"""


class ConfirmationError(Exception):
    """確認トークンが一致しない／書き戻し検証に失敗したときに送出。"""


# --------------------------------------------------------------------------- #
# 1. 前景 bbox
# --------------------------------------------------------------------------- #
@dataclass
class ForegroundBBox:
    """前景バウンディングボックスと背景推定の結果。"""

    bounds: tuple[tuple[int, int], ...]
    background_level: float
    threshold: float
    boundary_coverage: float
    burn_in_suspected: bool
    empty: bool

    @property
    def slices(self) -> tuple[slice, ...]:
        """numpy インデックス用の slice タプル。"""
        return tuple(slice(lo, hi) for lo, hi in self.bounds)

    @property
    def shape(self) -> tuple[int, ...]:
        """bbox のサイズ（voxel 数）。"""
        return tuple(hi - lo for lo, hi in self.bounds)


def _corner_blocks(image: np.ndarray, fraction: float = 0.125) -> list[np.ndarray]:
    """四隅（n 次元なら 2**n 個）のブロックを取り出す。"""
    spans: list[tuple[slice, slice]] = []
    for size in image.shape:
        block = max(2, int(round(size * fraction)))
        block = min(block, max(1, size // 2))
        spans.append((slice(0, block), slice(size - block, size)))
    return [image[tuple(combo)] for combo in itertools.product(*spans)]


def foreground_bbox(
    image: np.ndarray,
    *,
    corner_fraction: float = 0.125,
    deviation_percentile: float = 10.0,
    threshold_factor: float = 0.5,
    noise_floor_k: float = 3.0,
    denoise: bool = True,
    profile_fraction: float = 0.05,
    boundary_limit: float = BOUNDARY_COVERAGE_LIMIT,
) -> ForegroundBBox:
    """四隅ブロックから背景輝度を推定し、前景 bbox を求める。

    手順（本家仕様）:
      1. 四隅 2×2（3D なら 2×2×2）ブロックの中央値を背景輝度とする。
      2. 背景からの偏差 |I - bg| を作り、その 10 パーセンタイル × 0.5 を閾値にする。
      3. 閾値超えの voxel を前景として bbox を取る。
      4. 画像境界の被覆率が 25% を超えたら「焼き込みオーバーレイ
         （患者情報等が画面に焼かれた画像）の可能性」を警告フラグで返す。

    実データ対策として 2 点だけ補強してある:
      - 四隅背景の MAD × `noise_floor_k` を閾値の下限に採る（`noise_floor_k=0` で無効）。
      - 3×3 近傍の多数決フィルタで孤立ノイズを落としてから bbox を取る
        （`denoise=False` で無効）。これが無いとノイズ 1 画素で bbox が全面に広がる。

    Args:
        image: 2D／3D の輝度配列。
        corner_fraction: 隅ブロックの一辺の割合。
        deviation_percentile: 偏差のパーセンタイル（既定 10）。
        threshold_factor: パーセンタイル値に掛ける係数（既定 0.5）。
        noise_floor_k: 背景 MAD に掛ける閾値下限係数（0 で無効）。
        denoise: 多数決フィルタで孤立ノイズを除去するか。
        profile_fraction: 投影プロファイルの最大値に対する採用しきい割合。
        boundary_limit: 焼き込み判定に使う境界被覆率の上限。

    Returns:
        ForegroundBBox。前景が 1 voxel も無い場合は empty=True で全域を返す。
    """
    arr = np.asarray(image, dtype=np.float32)
    if arr.ndim < 2:
        raise RescueError("foreground_bbox は 2 次元以上の配列を想定しています。")

    corners = np.concatenate([blk.ravel() for blk in _corner_blocks(arr, corner_fraction)])
    background = float(np.median(corners))

    deviation = np.abs(arr - background)
    threshold = float(np.percentile(deviation, deviation_percentile) * threshold_factor)
    if noise_floor_k > 0:
        mad = float(np.median(np.abs(corners - background)))
        threshold = max(threshold, mad * noise_floor_k)

    mask = deviation > threshold
    if denoise:
        mask = _majority_filter(mask)
    boundary_coverage = _boundary_coverage(mask)

    if not mask.any():
        return ForegroundBBox(
            bounds=tuple((0, int(n)) for n in arr.shape),
            background_level=background,
            threshold=threshold,
            boundary_coverage=boundary_coverage,
            burn_in_suspected=False,
            empty=True,
        )

    bounds: list[tuple[int, int]] = []
    for axis in range(arr.ndim):
        other = tuple(a for a in range(arr.ndim) if a != axis)
        profile = mask.sum(axis=other)
        # 投影プロファイルの最大値の一定割合を下回る断面は前景と見なさない
        # （生き残った孤立ノイズで bbox が全面に広がるのを防ぐ）
        cut = max(1.0, float(profile.max()) * profile_fraction)
        idx = np.flatnonzero(profile >= cut)
        if idx.size == 0:
            idx = np.flatnonzero(profile > 0)
        bounds.append((int(idx[0]), int(idx[-1]) + 1))

    return ForegroundBBox(
        bounds=tuple(bounds),
        background_level=background,
        threshold=threshold,
        boundary_coverage=boundary_coverage,
        burn_in_suspected=boundary_coverage > boundary_limit,
        empty=False,
    )


def _majority_filter(mask: np.ndarray, size: int = 3) -> np.ndarray:
    """size**ndim 近傍の多数決フィルタ。孤立した閾値超え画素を落とす。"""
    acc = mask.astype(np.float32)
    half = size // 2
    for axis in range(mask.ndim):
        length = mask.shape[axis]
        if length <= size:
            continue
        pad = [(0, 0)] * mask.ndim
        pad[axis] = (half, half)
        padded = np.pad(acc, pad, mode="edge")
        total = None
        sl: list[slice] = [slice(None)] * mask.ndim
        for offset in range(size):
            sl[axis] = slice(offset, offset + length)
            part = padded[tuple(sl)]
            total = part if total is None else total + part
        acc = total / float(size)  # type: ignore[operator]
    return acc >= 0.5


def _boundary_coverage(mask: np.ndarray) -> float:
    """外周 1 voxel 分のうち、前景が占める割合。"""
    total = 0
    hit = 0
    for axis in range(mask.ndim):
        for index in (0, mask.shape[axis] - 1):
            face = np.take(mask, index, axis=axis)
            total += face.size
            hit += int(face.sum())
    return float(hit) / float(total) if total else 0.0


# --------------------------------------------------------------------------- #
# 2. シリーズ枚数 × スライス間隔 → FOV → spacing 初期値
# --------------------------------------------------------------------------- #
@dataclass
class SpacingSeed:
    """spacing 初期値と、その導出根拠。"""

    spacing: tuple[float, float, float]
    fov_mm: tuple[Optional[float], Optional[float], Optional[float]]
    sources: dict[str, str]
    warnings: list[str] = field(default_factory=list)


def series_count_fov_seed(
    volume_shape: Sequence[int],
    *,
    coronal_count: Optional[int] = None,
    coronal_interval_mm: Optional[float] = None,
    sagittal_count: Optional[int] = None,
    sagittal_interval_mm: Optional[float] = None,
    axial_interval_mm: Optional[float] = None,
    fallback_spacing_mm: float = 0.3,
) -> SpacingSeed:
    """冠状／矢状シリーズの枚数 × スライス間隔から FOV を逆算し初期 spacing を与える。

    軸の約束: volume_shape = (nx, ny, nz)。x=左右、y=前後、z=上下。この軸順は
    **前提であって検証しない**（affine から向きを判定する処理は持たない）。
    冠状シリーズは y 方向に積み上がるので FOV_y = 枚数 × 間隔、
    矢状シリーズは x 方向に積み上がるので FOV_x = 枚数 × 間隔。
    そこから x/y の spacing は FOV ÷ ボリューム側の voxel 数で決まる。

    Args:
        volume_shape: (nx, ny, nz)。
        coronal_count: 冠状シリーズの枚数。
        coronal_interval_mm: 冠状シリーズのスライス間隔 [mm]。
        sagittal_count: 矢状シリーズの枚数。
        sagittal_interval_mm: 矢状シリーズのスライス間隔 [mm]。
        axial_interval_mm: 軸位（z）方向のスライス間隔 [mm]。無ければ等方と仮定。
        fallback_spacing_mm: 何も手がかりが無いときの既定値。

    Returns:
        SpacingSeed。手がかりが欠けた軸は warnings に理由を残す。
    """
    if len(volume_shape) != 3:
        raise RescueError("volume_shape は (nx, ny, nz) の 3 要素で指定してください。")
    nx, ny, nz = (int(v) for v in volume_shape)
    if min(nx, ny, nz) < 1:
        raise RescueError("volume_shape に 0 以下の要素があります。")

    warnings: list[str] = []
    sources: dict[str, str] = {}

    fov_x: Optional[float] = None
    fov_y: Optional[float] = None
    fov_z: Optional[float] = None

    if sagittal_count and sagittal_interval_mm:
        fov_x = float(sagittal_count) * float(sagittal_interval_mm)
        spacing_x = fov_x / nx
        sources["x"] = "sagittal_series"
    else:
        spacing_x = None  # type: ignore[assignment]
        warnings.append("矢状シリーズ情報が無いため X の FOV を逆算できません。")

    if coronal_count and coronal_interval_mm:
        fov_y = float(coronal_count) * float(coronal_interval_mm)
        spacing_y = fov_y / ny
        sources["y"] = "coronal_series"
    else:
        spacing_y = None  # type: ignore[assignment]
        warnings.append("冠状シリーズ情報が無いため Y の FOV を逆算できません。")

    if axial_interval_mm:
        spacing_z = float(axial_interval_mm)
        fov_z = spacing_z * nz
        sources["z"] = "axial_interval"
    else:
        spacing_z = None  # type: ignore[assignment]
        warnings.append("軸位スライス間隔が無いため Z を等方仮定で補完します。")

    known = [v for v in (spacing_x, spacing_y, spacing_z) if v]
    default = float(np.mean(known)) if known else float(fallback_spacing_mm)
    if spacing_x is None:
        spacing_x = default
        sources["x"] = "isotropic_fallback"
    if spacing_y is None:
        spacing_y = default
        sources["y"] = "isotropic_fallback"
    if spacing_z is None:
        spacing_z = default
        sources["z"] = "isotropic_fallback"
    if fov_z is None:
        fov_z = spacing_z * nz

    return SpacingSeed(
        spacing=(float(spacing_x), float(spacing_y), float(spacing_z)),
        fov_mm=(fov_x, fov_y, fov_z),
        sources=sources,
        warnings=warnings,
    )


# --------------------------------------------------------------------------- #
# 画像ユーティリティ（外部依存を増やさないため numpy だけで実装）
# --------------------------------------------------------------------------- #
def _resize_2d(image: np.ndarray, out_shape: tuple[int, int]) -> np.ndarray:
    """バイリニア補間による 2D リサイズ。"""
    src = np.asarray(image, dtype=np.float32)
    ih, iw = src.shape
    oh, ow = int(out_shape[0]), int(out_shape[1])
    if (ih, iw) == (oh, ow):
        return src
    ys = (np.arange(oh, dtype=np.float32) + 0.5) * ih / oh - 0.5
    xs = (np.arange(ow, dtype=np.float32) + 0.5) * iw / ow - 0.5
    y0 = np.floor(ys).astype(np.int64)
    x0 = np.floor(xs).astype(np.int64)
    wy = (ys - y0).astype(np.float32)[:, None]
    wx = (xs - x0).astype(np.float32)[None, :]
    y0c, y1c = np.clip(y0, 0, ih - 1), np.clip(y0 + 1, 0, ih - 1)
    x0c, x1c = np.clip(x0, 0, iw - 1), np.clip(x0 + 1, 0, iw - 1)
    top = src[np.ix_(y0c, x0c)] * (1 - wx) + src[np.ix_(y0c, x1c)] * wx
    bottom = src[np.ix_(y1c, x0c)] * (1 - wx) + src[np.ix_(y1c, x1c)] * wx
    return (top * (1 - wy) + bottom * wy).astype(np.float32)


def _block_mean(image: np.ndarray, level: float) -> np.ndarray:
    """ピラミッド用の面積平均ダウンサンプル（level=0.5 なら 2×2 平均）。"""
    if level >= 1.0:
        return np.asarray(image, dtype=np.float32)
    factor = max(1, int(round(1.0 / float(level))))
    src = np.asarray(image, dtype=np.float32)
    h = (src.shape[0] // factor) * factor
    w = (src.shape[1] // factor) * factor
    if h < factor or w < factor:
        return src
    trimmed = src[:h, :w]
    return trimmed.reshape(h // factor, factor, w // factor, factor).mean(axis=(1, 3))


def _letterbox(image: np.ndarray, out_shape: tuple[int, int], fill: float) -> np.ndarray:
    """物理サイズを保ったまま中央合わせでパディング／クロップする（レターボックス）。"""
    src = np.asarray(image, dtype=np.float32)
    oh, ow = int(out_shape[0]), int(out_shape[1])
    canvas = np.full((oh, ow), float(fill), dtype=np.float32)

    sh, sw = src.shape
    copy_h, copy_w = min(sh, oh), min(sw, ow)
    sy = (sh - copy_h) // 2
    sx = (sw - copy_w) // 2
    dy = (oh - copy_h) // 2
    dx = (ow - copy_w) // 2
    canvas[dy:dy + copy_h, dx:dx + copy_w] = src[sy:sy + copy_h, sx:sx + copy_w]
    return canvas


def _to_unit(image: np.ndarray) -> np.ndarray:
    """ロバストな [0, 1] 正規化（外れ値の影響を抑えるため 0.5–99.5 パーセンタイル）。"""
    arr = np.asarray(image, dtype=np.float32)
    lo, hi = np.percentile(arr, (0.5, 99.5))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi - lo < 1e-12:
        return np.zeros_like(arr)
    return np.clip((arr - lo) / (hi - lo), 0.0, 1.0)


def normalized_mutual_information(
    a: np.ndarray, b: np.ndarray, *, bins: int = NMI_BINS
) -> float:
    """正規化相互情報量 NMI = (H(A) + H(B)) / H(A,B) を返す。

    同一画像同士で最大（理論上限 2.0）、無相関に近づくほど 1.0 に近づく。
    どちらかが定数画像の場合は情報が無いので 0.0 を返す。
    """
    ua = _to_unit(a).ravel()
    ub = _to_unit(b).ravel()
    if ua.size != ub.size:
        raise RescueError("NMI の 2 画像は同じ画素数である必要があります。")

    hist, _, _ = np.histogram2d(ua, ub, bins=bins, range=[[0.0, 1.0], [0.0, 1.0]])
    total = hist.sum()
    if total <= 0:
        return 0.0
    pxy = hist / total
    px = pxy.sum(axis=1)
    py = pxy.sum(axis=0)

    def _entropy(p: np.ndarray) -> float:
        nz = p[p > 0]
        return float(-np.sum(nz * np.log(nz)))

    hx, hy, hxy = _entropy(px), _entropy(py), _entropy(pxy)
    if hxy <= 1e-12:
        return 0.0
    return float((hx + hy) / hxy)


def multiscale_nmi(
    a: np.ndarray,
    b: np.ndarray,
    *,
    levels: Sequence[float] = PYRAMID_LEVELS,
    bins: int = NMI_BINS,
) -> float:
    """複数ピラミッド段の NMI の平均（多スケール NMI）。"""
    scores = [
        normalized_mutual_information(_block_mean(a, lv), _block_mean(b, lv), bins=bins)
        for lv in levels
    ]
    return float(np.mean(scores)) if scores else 0.0


# --------------------------------------------------------------------------- #
# 3. 三平面 spacing 探索
# --------------------------------------------------------------------------- #
@dataclass
class ReferenceSlice:
    """参照断面画像（ビューアから書き出された表示用画像）。"""

    image: np.ndarray
    plane: str  # "coronal" または "sagittal"
    extent_mm: Optional[tuple[float, float]] = None  # (高さ mm, 幅 mm)

    def __post_init__(self) -> None:
        if self.plane not in ("coronal", "sagittal"):
            raise RescueError("plane は 'coronal' または 'sagittal' を指定してください。")
        self.image = np.asarray(self.image, dtype=np.float32)
        if self.image.ndim != 2:
            raise RescueError("参照断面は 2 次元画像で渡してください。")


@dataclass
class SpacingCandidate:
    """格子探索の 1 候補。"""

    spacing: tuple[float, float, float]
    scale_inplane: float
    scale_z: float
    score: float


@dataclass
class SpacingEstimate:
    """spacing 推定の結果一式。"""

    spacing: tuple[float, float, float]
    score: float
    margin: float
    ambiguous: bool
    confidence: str
    n_evaluations: int
    candidates: list[SpacingCandidate]
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        """JSON 化用の辞書（パスや画素値は含めない）。"""
        return {
            "spacing_mm": [round(v, 6) for v in self.spacing],
            "score": round(self.score, 6),
            "margin": round(self.margin, 6),
            "ambiguous": self.ambiguous,
            "confidence": self.confidence,
            "n_evaluations": self.n_evaluations,
            "warnings": list(self.warnings),
            "top_candidates": [
                {
                    "spacing_mm": [round(v, 6) for v in c.spacing],
                    "scale_inplane": c.scale_inplane,
                    "scale_z": c.scale_z,
                    "score": round(c.score, 6),
                }
                for c in self.candidates[:5]
            ],
        }


def extract_plane(volume: np.ndarray, plane: str, index: Optional[int] = None) -> np.ndarray:
    """ボリュームから表示向きの断面を切り出す。

    ボリュームは (nx, ny, nz) 配列（x=左右 / y=前後 / z=上下）を前提とし
    （affine による向き合わせはしない。前提が崩れた配列は呼び出し側で転置する）、
    返す 2D 画像は「行 = 上下（上が上位）／列 = 横方向」の表示向きに揃える。
      - coronal: y 中央 → (nz, nx)
      - sagittal: x 中央 → (nz, ny)
      - axial: z 中央 → (ny, nx)
    """
    vol = np.asarray(volume, dtype=np.float32)
    if vol.ndim != 3:
        raise RescueError("volume は 3 次元配列で渡してください。")
    nx, ny, nz = vol.shape
    if plane == "coronal":
        idx = ny // 2 if index is None else int(index)
        return vol[:, np.clip(idx, 0, ny - 1), :].T[::-1, :]
    if plane == "sagittal":
        idx = nx // 2 if index is None else int(index)
        return vol[np.clip(idx, 0, nx - 1), :, :].T[::-1, :]
    if plane == "axial":
        idx = nz // 2 if index is None else int(index)
        return vol[:, :, np.clip(idx, 0, nz - 1)].T
    raise RescueError("plane は coronal / sagittal / axial のいずれかです。")


def _plane_physical_size(
    volume_shape: Sequence[int], plane: str, spacing: Sequence[float]
) -> tuple[float, float]:
    """断面画像の物理サイズ (高さ mm, 幅 mm) を返す。"""
    nx, ny, nz = (int(v) for v in volume_shape)
    sx, sy, sz = (float(v) for v in spacing)
    if plane == "coronal":
        return (nz * sz, nx * sx)
    if plane == "sagittal":
        return (nz * sz, ny * sy)
    if plane == "axial":
        return (ny * sy, nx * sx)
    raise RescueError("plane が不正です。")


def render_to_reference(
    plane_image: np.ndarray,
    physical_size_mm: tuple[float, float],
    reference_shape: tuple[int, int],
    reference_extent_mm: Optional[tuple[float, float]],
    background: float,
) -> np.ndarray:
    """断面を参照画像のグリッドへ、物理アスペクト比を保ちレターボックスで合わせる。

    参照画像の物理範囲が既知なら mm/px が確定するので絶対スケールまで比較できる。
    未知の場合は「参照枠にちょうど収まる」フィットに退避し、比（アスペクト）だけを
    比較する（絶対スケールは初期値に依存するため confidence を下げる）。
    """
    ref_h, ref_w = int(reference_shape[0]), int(reference_shape[1])
    h_mm, w_mm = float(physical_size_mm[0]), float(physical_size_mm[1])
    if h_mm <= 0 or w_mm <= 0:
        raise RescueError("断面の物理サイズが 0 以下です。")

    if reference_extent_mm:
        # 表示ピクセルは正方形前提。等方 px/mm を採る＝物理アスペクト比を保つ。
        px_per_mm = min(ref_h / float(reference_extent_mm[0]), ref_w / float(reference_extent_mm[1]))
    else:
        px_per_mm = min(ref_h / h_mm, ref_w / w_mm)

    target = (max(1, int(round(h_mm * px_per_mm))), max(1, int(round(w_mm * px_per_mm))))
    resized = _resize_2d(plane_image, target)
    return _letterbox(resized, (ref_h, ref_w), background)


def _score_candidate(
    volume: np.ndarray,
    spacing: Sequence[float],
    references: Sequence[ReferenceSlice],
    plane_cache: dict[str, np.ndarray],
    level: float,
    bins: int,
) -> float:
    """1 候補 spacing を、指定ピラミッド段で全参照断面に対して採点する。"""
    scores: list[float] = []
    for ref in references:
        ref_small = _block_mean(ref.image, level)
        background = float(np.median(np.concatenate(
            [blk.ravel() for blk in _corner_blocks(ref_small)]
        )))
        plane_image = plane_cache[ref.plane]
        physical = _plane_physical_size(volume.shape, ref.plane, spacing)
        rendered = render_to_reference(
            plane_image, physical, ref_small.shape, ref.extent_mm, background
        )
        scores.append(normalized_mutual_information(ref_small, rendered, bins=bins))
    return float(np.mean(scores)) if scores else 0.0


def tri_planar_spacing_search(
    volume: np.ndarray,
    seed_spacing: Sequence[float],
    *,
    coronal_ref: Optional[ReferenceSlice] = None,
    sagittal_ref: Optional[ReferenceSlice] = None,
    scale_factors: Sequence[float] = SCALE_FACTORS,
    pyramid_levels: Sequence[float] = PYRAMID_LEVELS,
    max_evaluations: int = MAX_EVALUATIONS,
    ambiguity_margin: float = AMBIGUITY_MARGIN,
    bins: int = NMI_BINS,
    refine: bool = True,
    log: Log = _log,
) -> SpacingEstimate:
    """中央断面と参照断面を多スケール NMI で照合し、spacing を格子探索する。

    ボリューム中央の冠状 (X,Z) / 矢状 (Y,Z) 断面を、参照 2D 画像に対し
    物理アスペクト比を保ちレターボックスで合わせ、スケール係数の格子
    （面内 × Z の 2 軸）×ピラミッド段で照合する。1 評価 = 1 候補 × 1 ピラミッド段。
    評価回数は `max_evaluations`（既定 64）で打ち切る。

    1 位と 2 位のスコア差が `ambiguity_margin`（既定 0.01）以下なら ambiguous と判定し、
    confidence を low に降格する。

    探索の自由度は 2（面内一様スケール × Z）:
        面内スケールは seed[0]（x）と seed[1]（y）に**同一倍率**を掛けるため、
        **x と y の異方誤差（面内アスペクト比の誤り）はこの探索では補正できない**。
        seed 段（`series_count_fov_seed`）で得た x/y 比が誤っていれば、その比は
        そのまま残る。歯科ビューアからの書き出しは面内等方が通例なので実害は
        限定的だが、x/y が別々にずれ得るデータでは seed 側を疑うこと。

    Args:
        volume: (nx, ny, nz) の輝度ボリューム（x=左右 / y=前後 / z=上下）。
        seed_spacing: series_count_fov_seed() 等で得た初期 spacing。
        coronal_ref / sagittal_ref: 参照断面。少なくとも一方は必須。
        refine: 格子の最良点まわりを放物線補間して 1 回だけ追検証する。

    Returns:
        SpacingEstimate。
    """
    vol = np.asarray(volume, dtype=np.float32)
    if vol.ndim != 3:
        raise RescueError("volume は 3 次元配列で渡してください。")
    references = [r for r in (coronal_ref, sagittal_ref) if r is not None]
    if not references:
        raise RescueError("参照断面（冠状 または 矢状）が少なくとも 1 枚必要です。")

    seed = tuple(float(v) for v in seed_spacing)
    if len(seed) != 3 or min(seed) <= 0:
        raise RescueError("seed_spacing は正の 3 要素で指定してください。")

    warnings: list[str] = []
    for ref in references:
        bbox = foreground_bbox(ref.image)
        if bbox.burn_in_suspected:
            warnings.append(
                f"{ref.plane} 参照断面: 境界被覆率 {bbox.boundary_coverage:.2f} "
                "＝焼き込みオーバーレイの可能性があります。"
            )
        if bbox.empty:
            warnings.append(f"{ref.plane} 参照断面: 前景が検出できませんでした。")
        if ref.extent_mm is None:
            warnings.append(
                f"{ref.plane} 参照断面: 物理範囲が未指定のため比のみの照合になります。"
            )

    plane_cache = {ref.plane: extract_plane(vol, ref.plane) for ref in references}
    levels = list(pyramid_levels)
    grid = list(itertools.product(scale_factors, scale_factors))
    budget_pairs = max(1, int(max_evaluations // max(1, len(levels))))
    reserved = 1 if refine else 0
    if len(grid) > budget_pairs - reserved:
        keep = max(1, budget_pairs - reserved)
        step = max(1, int(np.ceil(len(grid) / keep)))
        grid = grid[::step][:keep]
        warnings.append(
            f"評価回数上限 {max_evaluations} のため格子を {len(grid)} 候補に間引きました。"
        )

    evaluations = 0
    candidates: list[SpacingCandidate] = []
    score_table: dict[tuple[float, float], float] = {}
    for scale_inplane, scale_z in grid:
        spacing = (seed[0] * scale_inplane, seed[1] * scale_inplane, seed[2] * scale_z)
        per_level = []
        for level in levels:
            per_level.append(
                _score_candidate(vol, spacing, references, plane_cache, level, bins)
            )
            evaluations += 1
        score = float(np.mean(per_level))
        score_table[(scale_inplane, scale_z)] = score
        candidates.append(SpacingCandidate(spacing, scale_inplane, scale_z, score))

    candidates.sort(key=lambda c: c.score, reverse=True)
    best = candidates[0]
    margin = float(best.score - candidates[1].score) if len(candidates) > 1 else float("inf")
    ambiguous = margin <= ambiguity_margin

    # 格子の端が最良＝真値が探索範囲外にある可能性。黙って端の値を返さず警告する。
    used_scales = sorted({s for s, _ in grid} | {z for _, z in grid})
    lo, hi = used_scales[0], used_scales[-1]
    if best.scale_inplane in (lo, hi) or best.scale_z in (lo, hi):
        warnings.append(
            f"最良候補がスケール格子の端（{lo}〜{hi}）です。初期 spacing のずれが "
            "探索範囲を超えている可能性があります。FOV の枚数・間隔を見直してください。"
        )

    final = best
    if refine and not ambiguous and evaluations + len(levels) <= max_evaluations:
        refined_inplane = _parabolic_peak(
            [s for s, _ in grid], best.scale_inplane,
            lambda s: score_table.get((s, best.scale_z)),
        )
        refined_z = _parabolic_peak(
            [z for _, z in grid], best.scale_z,
            lambda z: score_table.get((best.scale_inplane, z)),
        )
        spacing = (seed[0] * refined_inplane, seed[1] * refined_inplane, seed[2] * refined_z)
        per_level = []
        for level in levels:
            per_level.append(
                _score_candidate(vol, spacing, references, plane_cache, level, bins)
            )
            evaluations += 1
        refined_score = float(np.mean(per_level))
        if refined_score >= best.score:
            final = SpacingCandidate(spacing, refined_inplane, refined_z, refined_score)
            candidates.insert(0, final)

    confidence = _confidence_from(margin, ambiguous, references, warnings)
    log(
        f"spacing 探索: 候補 {len(grid)} / 評価 {evaluations} 回 / "
        f"1位-2位差 {margin:.4f} / confidence={confidence}"
    )
    return SpacingEstimate(
        spacing=tuple(round(float(v), 6) for v in final.spacing),  # type: ignore[arg-type]
        score=float(final.score),
        margin=margin,
        ambiguous=ambiguous,
        confidence=confidence,
        n_evaluations=evaluations,
        candidates=candidates,
        warnings=warnings,
    )


def _parabolic_peak(
    axis_values: Sequence[float],
    best: float,
    score_of: Callable[[float], Optional[float]],
) -> float:
    """格子の最良点と両隣のスコアから、対数スケール上で放物線頂点を補間する。"""
    uniq = sorted(set(axis_values))
    if best not in uniq:
        return best
    i = uniq.index(best)
    if i == 0 or i == len(uniq) - 1:
        return best
    s_prev, s_here, s_next = score_of(uniq[i - 1]), score_of(best), score_of(uniq[i + 1])
    if s_prev is None or s_here is None or s_next is None:
        return best
    denom = (s_prev - 2 * s_here + s_next)
    if abs(denom) < 1e-9:
        return best
    delta = 0.5 * (s_prev - s_next) / denom
    delta = float(np.clip(delta, -0.5, 0.5))
    log_prev, log_here, log_next = np.log(uniq[i - 1]), np.log(best), np.log(uniq[i + 1])
    step = (log_next - log_prev) / 2.0
    return float(np.exp(log_here + delta * step))


def _confidence_from(
    margin: float,
    ambiguous: bool,
    references: Sequence[ReferenceSlice],
    warnings: Sequence[str],
) -> str:
    """1位-2位差・参照条件から confidence（high / medium / low）を決める。"""
    if ambiguous:
        return "low"
    level = "high" if margin >= 0.05 else "medium"
    if any(r.extent_mm is None for r in references):
        level = "medium" if level == "high" else "low"
    if any(("焼き込み" in w) or ("格子の端" in w) for w in warnings):
        level = "medium" if level == "high" else "low"
    if len(references) < 2 and level == "high":
        level = "medium"
    return level


# --------------------------------------------------------------------------- #
# 4. 相互検証
# --------------------------------------------------------------------------- #
@dataclass
class CrossValidation:
    """独立した再構成グループ間の spacing 一致検証。"""

    agrees: bool
    tolerance: float
    consensus_spacing: tuple[float, float, float]
    max_relative_deviation: float
    per_axis_deviation: tuple[float, float, float]
    group_names: list[str]


def cross_validate(
    spacings: dict[str, Sequence[float]] | Sequence[Sequence[float]],
    *,
    tolerance: float = CROSS_VALIDATION_TOLERANCE,
) -> CrossValidation:
    """独立した再構成グループ間で spacing が許容誤差（既定 15%）内に収まるか検証する。

    Args:
        spacings: グループ名 → spacing の辞書、または spacing の列。
        tolerance: 中央値からの相対偏差の許容上限（0.15 = 15%）。

    Returns:
        CrossValidation。グループが 1 つだけなら agrees=True（比較対象なし）。
    """
    if isinstance(spacings, dict):
        names = list(spacings.keys())
        values = [np.asarray(spacings[k], dtype=np.float64) for k in names]
    else:
        names = [f"group{i + 1}" for i in range(len(spacings))]
        values = [np.asarray(v, dtype=np.float64) for v in spacings]

    if not values:
        raise RescueError("cross_validate には 1 つ以上の spacing が必要です。")
    if any(v.shape != (3,) for v in values):
        raise RescueError("spacing は 3 要素で指定してください。")

    stacked = np.stack(values, axis=0)
    consensus = np.median(stacked, axis=0)
    if np.any(consensus <= 0):
        raise RescueError("spacing に 0 以下の値が含まれています。")
    deviation = np.abs(stacked - consensus) / consensus
    per_axis = deviation.max(axis=0)
    max_dev = float(per_axis.max())

    return CrossValidation(
        agrees=bool(max_dev <= tolerance),
        tolerance=float(tolerance),
        consensus_spacing=tuple(float(v) for v in consensus),  # type: ignore[arg-type]
        max_relative_deviation=max_dev,
        per_axis_deviation=tuple(float(v) for v in per_axis),  # type: ignore[arg-type]
        group_names=names,
    )


# --------------------------------------------------------------------------- #
# 5. 確認トークンと確定書き出し
# --------------------------------------------------------------------------- #
def compute_manifest_sha256(volume: np.ndarray) -> str:
    """ボリュームの内容から source manifest の SHA-256 を作る。

    DICOM タグには触れず、形状・dtype・画素バイト列だけをハッシュする。
    """
    arr = np.ascontiguousarray(np.asarray(volume))
    digest = hashlib.sha256()
    digest.update(f"shape={arr.shape};dtype={arr.dtype.str};".encode("utf-8"))
    digest.update(arr.tobytes())
    return digest.hexdigest()


def canonical_transform(transform: Optional[Sequence[Sequence[float]]] = None) -> str:
    """transform（4×4 affine 相当）を正規化文字列にする。None は RAS 単位行列。"""
    if transform is None:
        matrix = np.eye(4, dtype=np.float64)
    else:
        matrix = np.asarray(transform, dtype=np.float64)
        if matrix.shape == (3, 3):
            full = np.eye(4)
            full[:3, :3] = matrix
            matrix = full
        if matrix.shape != (4, 4):
            raise RescueError("transform は 3×3 または 4×4 で指定してください。")
    return ";".join(f"{v:.6f}" for v in matrix.ravel())


def build_confirmation_token(
    source_manifest_sha256: str,
    confirmed_spacing: Sequence[float],
    transform: Optional[Sequence[Sequence[float]]] = None,
) -> str:
    """SHA-256(source_manifest_sha256 + confirmed_spacing + transform) の確認トークン。

    実体は **チェックサム**であって暗号的な認証ではない。秘密鍵を持たない公開入力の
    ハッシュなので、同じ 3 点が分かれば誰でも同じ値を再計算できる。防げるのは
    「preview で見た spacing の写し間違い」「ボリュームの取り違え」といった事故であり、
    悪意ある改竄は防げない。

    `finalize()` は照合に `hmac.compare_digest` を使うが、これは比較時間から情報が
    漏れないようにする一般的な作法であり、上記の性質を変えるものではない。
    """
    spacing = tuple(float(v) for v in confirmed_spacing)
    if len(spacing) != 3 or min(spacing) <= 0:
        raise RescueError("confirmed_spacing は正の 3 要素で指定してください。")
    payload = "|".join(
        [
            str(source_manifest_sha256).strip().lower(),
            ";".join(f"{v:.6f}" for v in spacing),
            canonical_transform(transform),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_affine(
    spacing: Sequence[float],
    transform: Optional[Sequence[Sequence[float]]] = None,
    origin_mm: Sequence[float] = (0.0, 0.0, 0.0),
) -> np.ndarray:
    """spacing と transform から 4×4 affine を組み立てる。"""
    spacing = tuple(float(v) for v in spacing)
    if transform is None:
        direction = np.eye(3, dtype=np.float64)
    else:
        matrix = np.asarray(transform, dtype=np.float64)
        direction = matrix[:3, :3] if matrix.shape == (4, 4) else matrix
        if direction.shape != (3, 3):
            raise RescueError("transform は 3×3 または 4×4 で指定してください。")
    affine = np.eye(4, dtype=np.float64)
    affine[:3, :3] = direction @ np.diag(spacing)
    affine[:3, 3] = np.asarray(origin_mm, dtype=np.float64)
    return affine


def finalize(
    volume: np.ndarray,
    confirmed_spacing: Sequence[float],
    out_path: str,
    *,
    source_manifest_sha256: str,
    token: str,
    transform: Optional[Sequence[Sequence[float]]] = None,
    origin_mm: Sequence[float] = (0.0, 0.0, 0.0),
    dtype: str = "float32",
    log: Log = _log,
) -> str:
    """渡されたボリュームと確認トークンを照合してから NIfTI を書き出す。

    照合は 2 段で行う。

    1. **ボリューム同一性**: `volume` から manifest SHA-256 を**その場で再計算**し、
       引数 `source_manifest_sha256`（estimate が発行した値）と突き合わせる。
       不一致なら書き出さない。これが無いと、(manifest SHA, spacing, token) の
       3 点さえ自己整合していれば preview で確認したのとは**別のボリューム**を
       確定できてしまい、3 段構成の意味が失われる。
       この照合を迂回するオプションは設けない（`--force` 等は無い）。
    2. **確認トークン**: 上記が通ってからトークンを照合する。

    照合後、書き出したファイルを読み直して voxel・shape・spacing・affine の一致を
    確認する。1 つでも不一致なら出力ファイルを削除して ConfirmationError を送出する
    （壊れた mm の混入防止）。

    Returns:
        書き出したファイル名（絶対パスはログにも戻り値にも出さない）。
    """
    actual_manifest = compute_manifest_sha256(volume)
    if not hmac.compare_digest(actual_manifest,
                               str(source_manifest_sha256).strip().lower()):
        raise ConfirmationError(
            "estimate 時と異なるボリュームが渡されました"
            "（manifest SHA-256 が一致しません）。"
            "preview で確認したのと同じボリュームを指定してください。"
        )

    expected = build_confirmation_token(actual_manifest, confirmed_spacing, transform)
    if not hmac.compare_digest(str(token).strip().lower(), expected):
        raise ConfirmationError(
            "確認トークンが一致しません。preview で spacing を確認し、"
            "estimate が発行したトークンをそのまま渡してください。"
        )

    try:
        import nibabel as nib  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RescueError(f"nibabel が必要です（pip install nibabel）。詳細: {exc}")

    arr = np.ascontiguousarray(np.asarray(volume, dtype=np.dtype(dtype)))
    spacing = tuple(float(v) for v in confirmed_spacing)
    affine = build_affine(spacing, transform, origin_mm)

    parent = os.path.dirname(os.path.abspath(out_path))
    if parent:
        os.makedirs(parent, exist_ok=True)

    image = nib.Nifti1Image(arr, affine)
    image.header.set_zooms(tuple(float(v) for v in spacing))
    nib.save(image, out_path)

    name = os.path.basename(out_path)
    try:
        loaded = nib.load(out_path)
        back = np.asanyarray(loaded.dataobj, dtype=np.dtype(dtype))
        problems: list[str] = []
        if back.shape != arr.shape:
            problems.append("shape")
        elif not np.array_equal(back, arr):
            problems.append("voxel")
        if not np.allclose(np.asarray(loaded.header.get_zooms()[:3], dtype=np.float64),
                           np.asarray(spacing, dtype=np.float64), rtol=1e-5, atol=1e-6):
            problems.append("spacing")
        if not np.allclose(loaded.affine, affine, rtol=1e-5, atol=1e-5):
            problems.append("affine")
        if problems:
            raise ConfirmationError(
                f"書き戻し検証に失敗しました（不一致: {', '.join(problems)}）。"
                f"出力 {name} は削除しました。"
            )
    except ConfirmationError:
        _remove_quietly(out_path)
        raise
    except Exception as exc:  # noqa: BLE001
        _remove_quietly(out_path)
        raise ConfirmationError(f"書き戻し検証で例外が発生しました: {exc}")

    log(f"確定書き出し完了: {name}（書き戻し検証 OK）")
    return name


def _remove_quietly(path: str) -> None:
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# preview（軸位／冠状／矢状 + Z 軸 MIP）
# --------------------------------------------------------------------------- #
def _to_uint8(image: np.ndarray) -> np.ndarray:
    return (_to_unit(image) * 255.0 + 0.5).astype(np.uint8)


def write_png(path: str, image: np.ndarray) -> None:
    """8bit グレースケール PNG を標準ライブラリだけで書き出す。"""
    arr = _to_uint8(np.asarray(image))
    height, width = arr.shape
    raw = b"".join(b"\x00" + arr[row].tobytes() for row in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    blob = b"\x89PNG\r\n\x1a\n"
    blob += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
    blob += chunk(b"IDAT", zlib.compress(raw, 6))
    blob += chunk(b"IEND", b"")
    with open(path, "wb") as fh:
        fh.write(blob)


def write_pgm(path: str, image: np.ndarray) -> None:
    """8bit グレースケール PGM（P5）を書き出す（PNG が使えない環境向け）。"""
    arr = _to_uint8(np.asarray(image))
    height, width = arr.shape
    with open(path, "wb") as fh:
        fh.write(f"P5\n{width} {height}\n255\n".encode("ascii"))
        fh.write(arr.tobytes())


def write_preview(
    volume: np.ndarray,
    spacing: Sequence[float],
    out_dir: str,
    *,
    fmt: str = "png",
    log: Log = _log,
) -> list[str]:
    """推定 spacing で等方表示に直した軸位／冠状／矢状断面と Z 軸 MIP を書き出す。

    人間が「歯列の縦横比が破綻していないか」を目視確認するための材料。
    戻り値・ログにはファイル名のみを載せる（絶対パスは出さない）。
    """
    vol = np.asarray(volume, dtype=np.float32)
    if vol.ndim != 3:
        raise RescueError("volume は 3 次元配列で渡してください。")
    spacing = tuple(float(v) for v in spacing)
    if len(spacing) != 3 or min(spacing) <= 0:
        raise RescueError("spacing は正の 3 要素で指定してください。")
    if fmt not in ("png", "pgm"):
        raise RescueError("fmt は png か pgm を指定してください。")
    os.makedirs(out_dir, exist_ok=True)

    writer = write_png if fmt == "png" else write_pgm
    smallest = min(spacing)
    written: list[str] = []

    for plane in ("axial", "coronal", "sagittal"):
        image = extract_plane(vol, plane)
        h_mm, w_mm = _plane_physical_size(vol.shape, plane, spacing)
        target = (max(1, int(round(h_mm / smallest))), max(1, int(round(w_mm / smallest))))
        name = f"preview_{plane}.{fmt}"
        writer(os.path.join(out_dir, name), _resize_2d(image, target))
        written.append(name)

    mip = vol.max(axis=2).T
    h_mm, w_mm = vol.shape[1] * spacing[1], vol.shape[0] * spacing[0]
    target = (max(1, int(round(h_mm / smallest))), max(1, int(round(w_mm / smallest))))
    name = f"preview_mip_z.{fmt}"
    writer(os.path.join(out_dir, name), _resize_2d(mip, target))
    written.append(name)

    log(f"プレビュー {len(written)} 枚を出力: {', '.join(written)}")
    return written


# --------------------------------------------------------------------------- #
# 高水準 API
# --------------------------------------------------------------------------- #
def estimate_spacing(
    volume: np.ndarray,
    *,
    coronal_ref: Optional[ReferenceSlice] = None,
    sagittal_ref: Optional[ReferenceSlice] = None,
    coronal_count: Optional[int] = None,
    coronal_interval_mm: Optional[float] = None,
    sagittal_count: Optional[int] = None,
    sagittal_interval_mm: Optional[float] = None,
    axial_interval_mm: Optional[float] = None,
    transform: Optional[Sequence[Sequence[float]]] = None,
    log: Log = _log,
) -> tuple[SpacingEstimate, SpacingSeed, str, str]:
    """FOV 逆算 → 三平面探索を通し、(推定結果, 初期値, manifest SHA, トークン) を返す。"""
    seed = series_count_fov_seed(
        np.asarray(volume).shape,
        coronal_count=coronal_count,
        coronal_interval_mm=coronal_interval_mm,
        sagittal_count=sagittal_count,
        sagittal_interval_mm=sagittal_interval_mm,
        axial_interval_mm=axial_interval_mm,
    )
    for msg in seed.warnings:
        log(f"初期 spacing: {msg}")

    estimate = tri_planar_spacing_search(
        volume,
        seed.spacing,
        coronal_ref=coronal_ref,
        sagittal_ref=sagittal_ref,
        log=log,
    )
    estimate.warnings = list(seed.warnings) + list(estimate.warnings)

    manifest = compute_manifest_sha256(volume)
    token = build_confirmation_token(manifest, estimate.spacing, transform)
    return estimate, seed, manifest, token


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def load_array(path: str) -> np.ndarray:
    """.npy / .nii(.gz) / .pgm を配列として読む（DICOM は扱わない）。

    NIfTI は **(i, j, k) の並びのまま**読み、affine（qform/sform）を見て解剖学的向きへ
    並べ替える処理は行わない。本モジュールの下流（`series_count_fov_seed` /
    `extract_plane` / `tri_planar_spacing_search`）は配列軸が
    **(x, y, z) 順で x=左右 / y=前後 / z=上下**であることを前提とするので、
    向きが異なるデータは呼び出し側で転置してから渡すこと。
    """
    lower = path.lower()
    if lower.endswith(".npy"):
        return np.asarray(np.load(path), dtype=np.float32)
    if lower.endswith(".pgm"):
        return _load_pgm(path)
    if lower.endswith(".nii") or lower.endswith(".nii.gz"):
        try:
            import nibabel as nib  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise RescueError(f"nibabel が必要です（pip install nibabel）。詳細: {exc}")
        return np.asanyarray(nib.load(path).dataobj, dtype=np.float32)
    raise RescueError(
        f"未対応の拡張子です（{os.path.basename(path)}）。.npy / .nii(.gz) / .pgm に変換してください。"
    )


def _load_pgm(path: str) -> np.ndarray:
    with open(path, "rb") as fh:
        blob = fh.read()
    if not blob.startswith(b"P5"):
        raise RescueError("PGM は P5（バイナリ）形式のみ対応です。")
    fields: list[bytes] = []
    pos = 2
    while len(fields) < 3:
        while pos < len(blob) and blob[pos: pos + 1].isspace():
            pos += 1
        if blob[pos: pos + 1] == b"#":
            while pos < len(blob) and blob[pos: pos + 1] != b"\n":
                pos += 1
            continue
        start = pos
        while pos < len(blob) and not blob[pos: pos + 1].isspace():
            pos += 1
        fields.append(blob[start:pos])
    width, height, maxval = (int(f) for f in fields)
    pos += 1
    dtype = np.uint8 if maxval < 256 else ">u2"
    data = np.frombuffer(blob[pos:], dtype=dtype, count=width * height)
    return data.reshape(height, width).astype(np.float32)


def _parse_spacing(text: str) -> tuple[float, float, float]:
    parts = [p for p in text.replace(" ", "").split(",") if p]
    if len(parts) != 3:
        raise RescueError("spacing は 'sx,sy,sz' の形式で指定してください。")
    return tuple(float(p) for p in parts)  # type: ignore[return-value]


def _parse_extent(text: Optional[str]) -> Optional[tuple[float, float]]:
    if not text:
        return None
    parts = [p for p in text.replace(" ", "").split(",") if p]
    if len(parts) != 2:
        raise RescueError("extent は '高さmm,幅mm' の形式で指定してください。")
    return (float(parts[0]), float(parts[1]))


def _parse_transform(path: Optional[str]) -> Optional[np.ndarray]:
    if not path:
        return None
    matrix = np.asarray(np.load(path) if path.lower().endswith(".npy")
                        else json.loads(open(path, encoding="utf-8").read()), dtype=np.float64)
    return matrix


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="spacing が壊れた／欠落したボリュームの voxel 間隔推定（推論なし）"
    )
    sub = p.add_subparsers(dest="command", required=True)

    est = sub.add_parser("estimate", help="参照断面から spacing を推定し確認トークンを発行")
    est.add_argument("--volume", required=True, help="匿名化済みボリューム（.nii.gz/.npy）")
    est.add_argument("--ref-coronal", help="参照 冠状断面（.npy/.pgm/.nii.gz）")
    est.add_argument("--ref-sagittal", help="参照 矢状断面（.npy/.pgm/.nii.gz）")
    est.add_argument("--ref-coronal-extent", help="冠状参照の物理範囲 '高さmm,幅mm'")
    est.add_argument("--ref-sagittal-extent", help="矢状参照の物理範囲 '高さmm,幅mm'")
    est.add_argument("--coronal-count", type=int, help="冠状シリーズの枚数")
    est.add_argument("--coronal-interval", type=float, help="冠状シリーズのスライス間隔 mm")
    est.add_argument("--sagittal-count", type=int, help="矢状シリーズの枚数")
    est.add_argument("--sagittal-interval", type=float, help="矢状シリーズのスライス間隔 mm")
    est.add_argument("--axial-interval", type=float, help="軸位スライス間隔 mm")
    est.add_argument("--transform", help="4x4 affine（.npy か JSON）")
    est.add_argument("--fail-on-ambiguous", action="store_true",
                     help="ambiguous 判定なら終了コード 5 で終わる")

    pre = sub.add_parser("preview", help="推定 spacing での断面 3 枚と Z 軸 MIP を書き出す")
    pre.add_argument("--volume", required=True)
    pre.add_argument("--spacing", required=True, help="'sx,sy,sz' mm")
    pre.add_argument("--out", required=True, help="出力フォルダ")
    pre.add_argument("--format", default="png", choices=["png", "pgm"])

    fin = sub.add_parser(
        "finalize",
        help="ボリューム同一性（manifest SHA 再計算）と確認トークンを照合の上で NIfTI を確定書き出し",
    )
    fin.add_argument("--volume", required=True)
    fin.add_argument("--spacing", required=True, help="'sx,sy,sz' mm")
    fin.add_argument("--out", required=True, help="出力 NIfTI（.nii.gz）")
    fin.add_argument("--manifest-sha", required=True,
                     help="estimate が出した manifest SHA-256（--volume から再計算した値と照合）")
    fin.add_argument("--token", required=True,
                     help="estimate が出した確認トークン（写し間違い検出用のチェックサム）")
    fin.add_argument("--transform", help="4x4 affine（.npy か JSON）")
    return p


def _cmd_estimate(args: argparse.Namespace) -> int:
    volume = load_array(args.volume)
    if not args.ref_coronal and not args.ref_sagittal:
        print("--ref-coronal か --ref-sagittal のどちらかは必須です。", file=sys.stderr)
        return 1

    coronal = (
        ReferenceSlice(load_array(args.ref_coronal), "coronal",
                       _parse_extent(args.ref_coronal_extent))
        if args.ref_coronal else None
    )
    sagittal = (
        ReferenceSlice(load_array(args.ref_sagittal), "sagittal",
                       _parse_extent(args.ref_sagittal_extent))
        if args.ref_sagittal else None
    )
    transform = _parse_transform(args.transform)

    estimate, seed, manifest, token = estimate_spacing(
        volume,
        coronal_ref=coronal,
        sagittal_ref=sagittal,
        coronal_count=args.coronal_count,
        coronal_interval_mm=args.coronal_interval,
        sagittal_count=args.sagittal_count,
        sagittal_interval_mm=args.sagittal_interval,
        axial_interval_mm=args.axial_interval,
        transform=transform,
    )

    payload = estimate.as_dict()
    payload["volume"] = os.path.basename(args.volume)
    payload["seed_spacing_mm"] = [round(v, 6) for v in seed.spacing]
    payload["source_manifest_sha256"] = manifest
    payload["confirmation_token"] = token
    payload["next_step"] = (
        "preview で断面を目視確認し、spacing が妥当なら finalize に "
        "--manifest-sha と --token をそのまま渡してください（自動確定はしません）。"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if estimate.ambiguous and args.fail_on_ambiguous:
        return 5
    return 0


def _cmd_preview(args: argparse.Namespace) -> int:
    volume = load_array(args.volume)
    write_preview(volume, _parse_spacing(args.spacing), args.out, fmt=args.format)
    return 0


def _cmd_finalize(args: argparse.Namespace) -> int:
    volume = load_array(args.volume)
    finalize(
        volume,
        _parse_spacing(args.spacing),
        args.out,
        source_manifest_sha256=args.manifest_sha,
        token=args.token,
        transform=_parse_transform(args.transform),
    )
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "estimate":
            return _cmd_estimate(args)
        if args.command == "preview":
            return _cmd_preview(args)
        if args.command == "finalize":
            return _cmd_finalize(args)
        print("未知のサブコマンドです。", file=sys.stderr)
        return 1
    except ConfirmationError as exc:
        print(f"確認エラー: {exc}", file=sys.stderr)
        return 4
    except RescueError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"想定外のエラー: {exc}", file=sys.stderr)
        return 99


if __name__ == "__main__":
    sys.exit(main())
