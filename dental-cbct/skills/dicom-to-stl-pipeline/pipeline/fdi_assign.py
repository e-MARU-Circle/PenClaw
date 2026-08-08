#!/usr/bin/env python3
"""歯のインスタンスセグメンテーション結果に FDI 歯番を自動付与する。

インスタンスラベルマップ（歯ごとに任意の ID が振られた NIfTI）を入力に、
各インスタンスへ FDI 歯番（11-18 / 21-28 / 31-38 / 41-48）を割り当てて
FDI 値のマルチラベル NIfTI と `.labels.json` サイドカーを書き出す。

処理の流れ:
  1. 各インスタンスの重心を voxel → mm（affine 適用・RAS）で計算
  2. 上下顎を分離（重心の上下軸 1 次元クラスタリング。詳細は split_arches）
  3. 歯列弓に沿った順序を構築（最短ハミルトン路の貪欲 + 2-opt 近似）
  4. 歯列弓ローカル frame と正中を推定（放物線当てはめ。詳細は fit_arch_frame）
  5. 隣接ペア重心変位の二変量正規分布の対数尤度を遷移コスト、
     歯クラス確率の対数と正中アンカーによる絶対位置ずれを放出コストとして
     Viterbi 型 DP で FDI 列に整合。歯列弓サイズは 1 回だけ再推定して解き直す
  6. 欠損歯はステート側のスキップ遷移で吸収（ペナルティ付き）

既知の限界: 片顎の残存歯が 8 本程度まで減ると歯列弓の形が決まらず精度が落ちる
（合成データでの実測: 欠損 3 本まで 100%、4-6 本で約 96%、8 本で約 70%）。
歯クラス確率（--probs）を併用できる場合は併用すること。この限界があるため、
割当の不確実性を `confidence`（high / medium / low）と `ambiguous` として
サイドカーに載せる（判定基準は _confidence_from を参照）。下流は数値を読まずに
この 2 つで「人の目視確認が要る症例」を選別できる。

外部ペア統計（--pair-stats）は歯列弓ローカル frame（x=患者右が正、y=前方が正、
単位 mm）で表現されている前提。読み込み時に単位スケールと軸順・符号を検査し、
想定外なら警告して内蔵統計へフォールバックする（validate_pair_stats 参照）。

プライバシー: 本モジュールが触れるのはインスタンス ID とボクセル座標のみ。
DICOM タグ・患者情報は一切読まず、標準出力にも件数と座標統計しか出さない。

参照元（**仕様のみ参照。コード・統計ファイルは一切複製していない**）:
  MIC-DKFZ/ToothSeg（Apache-2.0, DOI 10.5281/zenodo.14893540）の
  ToothSeg 後処理における FDI 割当の考え方
  （重心 → 歯列順序 → ペア変位の対数尤度 → Viterbi）を仕様として参照した。
  本家が用いる `fdi_pair_distrs.json`（CC BY 4.0）は同梱もダウンロードもせず、
  既定では理想歯列モデルから内部生成した統計を使う（外部依存ゼロ）。
  本家互換の統計 JSON を使いたい場合は `--pair-stats` で与える。

使い方の例:
  python3 fdi_assign.py --instances inst.nii.gz --out fdi.nii.gz
  python3 fdi_assign.py --instances inst.nii.gz --out fdi.nii.gz \
      --arch lower --probs probs.npz --pair-stats fdi_pair_distrs.json

終了コード: 0=成功 / 1=引数不正 / 2=処理エラー /
            5=ambiguous（--fail-on-ambiguous 指定時のみ。rescue_spacing.py と同値）/
            99=想定外
"""

from __future__ import annotations

import argparse
import dataclasses
import functools
import json
import math
import os
import re
import sys
from typing import Callable, Iterable, Optional

import numpy as np

# --------------------------------------------------------------------------- #
# 歯科的定数
# --------------------------------------------------------------------------- #

# 片顎 16 歯の正準順序。患者**右**側の最後方 → 正中 → 患者**左**側の最後方。
FDI_UPPER: list[int] = [18, 17, 16, 15, 14, 13, 12, 11,
                        21, 22, 23, 24, 25, 26, 27, 28]
FDI_LOWER: list[int] = [48, 47, 46, 45, 44, 43, 42, 41,
                        31, 32, 33, 34, 35, 36, 37, 38]

ARCH_ORDER: dict[str, list[int]] = {"upper": FDI_UPPER, "lower": FDI_LOWER}

# 全 32 歯の並び（--probs の既定列順。上顎 16 → 下顎 16）
FDI_ALL: list[int] = FDI_UPPER + FDI_LOWER

# 近遠心径（mesiodistal crown width, mm）。Wheeler の教科書的平均値。
# 歯列弓上の占有幅＝歯の位置決めに使う。左右同名歯は同値とする。
MESIODISTAL_WIDTH_MM: dict[int, float] = {
    # 上顎
    11: 8.5, 12: 6.5, 13: 7.5, 14: 7.0, 15: 6.5, 16: 10.0, 17: 9.0, 18: 8.5,
    # 下顎
    41: 5.0, 42: 5.5, 43: 7.0, 44: 7.0, 45: 7.0, 46: 11.0, 47: 10.5, 48: 10.0,
}

# 歯列弓の半幅（正中矢状面 → 第三大臼歯遠心の左右方向距離, mm）。
# 上顎第二大臼歯間幅径 ≈ 62mm、下顎 ≈ 56mm を半分にした値。
ARCH_HALF_WIDTH_MM: dict[str, float] = {"upper": 31.0, "lower": 28.0}

# 1 ステップ（隣接歯 1 個分）あたりの変位のばらつき（mm, 標準偏差）
#   along  : 歯列弓に沿う方向。近遠心径の個体差（片側 SD ≈ 0.5mm）× 2 歯分 ≈ 0.9
#   across : 歯列弓に直交する方向。歯列弓形態・歯軸傾斜のばらつき ≈ 1.1
SIGMA_STEP_ALONG_MM = 0.9
SIGMA_STEP_ACROSS_MM = 1.1
# 歯列弓の大きさ自体の個体差（変位量に比例する成分, 6%）
SIGMA_SCALE_RATIO = 0.06

# 上下顎を分離する最小の咬合間ギャップ（mm）。
# 同一顎内の隣接歯重心の上下差は通常 1-2mm（Spee 彎曲）に対し、
# 上下顎の歯冠重心間は咬合時でも 8mm 以上あく。4mm を境にする。
DEFAULT_ARCH_GAP_MM = 4.0

# 既定コスト重み
DEFAULT_PROB_WEIGHT = 4.0      # 放出コスト（歯クラス確率）の重み。本家既定に合わせた
DEFAULT_SKIP_PENALTY = 3.0     # 歯列内部の欠損 1 歯あたりのペナルティ
DEFAULT_END_SKIP_PENALTY = 0.5  # 歯列端（第三大臼歯側）の欠損 1 歯あたりのペナルティ
DEFAULT_MIN_VOXELS = 100       # これ未満のインスタンスはノイズとして除外

# 正中アンカー（絶対位置項）の設定。詳細は fit_arch_frame のドキュメント。
DEFAULT_ANCHOR_SIGMA_MM = 5.0  # 正中付近での理想歯列位置とのずれの許容標準偏差
DEFAULT_ANCHOR_WEIGHT = 1.0    # 絶対位置項の重み（0 で無効）
# 歯列弓の大きさの個体差。正中から遠い歯ほど絶対位置のずれが大きくなるため、
# 許容標準偏差を「正中からの距離 × この比率」ぶん増やす（一様スケール差を吸収）。
ANCHOR_SCALE_RATIO = 0.10

# --- 割当の信頼度判定（confidence / ambiguous）の閾値 -------------------------- #
# 採用解と対抗解（歯列の向きを逆にした解）の 1 歯あたりコスト差。総コストは
# 歯数に比例して増えるため、必ず歯数で割って比較する。これを下回ると
# 「向きすら決めきれていない＝1 歯ずれ解とも拮抗しうる」とみなす。
AMBIGUITY_MARGIN_PER_TOOTH = 1.0
# 上と同じ指標で「余裕をもって解が立っている」とみなす下限。
HIGH_MARGIN_PER_TOOTH = 3.0
# 残存歯数による減点。モジュール冒頭の合成データ実測（欠損 3 本まで 100%、
# 4-6 本で約 96%、8 本で約 70%）をそのまま閾値に写したもの。
CONFIDENCE_FULL_ARCH_MIN_TEETH = 13   # 欠損 3 本以内。減点なし
CONFIDENCE_LOW_MAX_TEETH = 9          # 残存 9 本以下。8 本残で実測 約 70% → low 固定

# --- 外部ペア統計（--pair-stats）の座標規約バリデーション ---------------------- #
# 隣接歯ペアの平均変位の大きさ（mm）として妥当な範囲。近遠心径は下顎中切歯の
# 5.0mm から下顎第一大臼歯の 11.0mm まで（MESIODISTAL_WIDTH_MM）なので、
# 隣接歯重心間距離はおおよそこの範囲に収まる。外れたら単位違い（cm / voxel）を疑う。
EXTERNAL_STATS_MIN_ADJACENT_MM = 5.0
EXTERNAL_STATS_MAX_ADJACENT_MM = 12.0
# 軸の向きが合っているかの判定に使う、参照モデルとの平均コサイン類似度の下限。
EXTERNAL_STATS_MIN_AXIS_AGREEMENT = 0.80
# 検証に使える隣接ペアの最小数。これ未満だと座標規約を判定できない。
EXTERNAL_STATS_MIN_MATCHED_PAIRS = 4

Log = Callable[[str], None]


class FDIAssignError(Exception):
    """FDI 割当処理のいずれかの工程が失敗したときに送出。"""


def _log(msg: str) -> None:
    print(msg, flush=True)


def _noop(_msg: str) -> None:
    return None


# --------------------------------------------------------------------------- #
# NIfTI 入出力（SimpleITK 優先・nibabel フォールバック）
# --------------------------------------------------------------------------- #

# LPS(DICOM/ITK) → RAS(NIfTI) 変換。x, y の符号を反転する。
_LPS_TO_RAS = np.diag([-1.0, -1.0, 1.0, 1.0])


@dataclasses.dataclass
class LabelVolume:
    """ラベルボリュームと、その幾何情報。

    Attributes:
        array: 整数ラベル配列。軸順は (i, j, k)（NIfTI/ITK のインデックス順）。
        affine: 4x4 同次行列。[i, j, k, 1] → RAS 実座標(mm) を与える。
        backend: 読み込みに使ったライブラリ名（"sitk" または "nibabel"）。
        handle: 出力時に幾何情報を引き継ぐための元画像オブジェクト。
    """

    array: np.ndarray
    affine: np.ndarray
    backend: str
    handle: object


def read_label_volume(path: str) -> LabelVolume:
    """整数ラベルの NIfTI を読み込み、RAS affine 付きで返す。

    SimpleITK（既存 nifti_to_stl.py と同じ必須依存）を優先し、
    無ければ nibabel にフォールバックする。どちらも無ければエラー。

    Args:
        path: 入力 NIfTI（.nii / .nii.gz）のパス。

    Returns:
        LabelVolume。

    Raises:
        FDIAssignError: 読み込みライブラリが無い、または読み込みに失敗した場合。
    """
    if not os.path.isfile(path):
        raise FDIAssignError(f"入力ファイルが存在しません: {path}")

    try:
        import SimpleITK as sitk  # type: ignore
    except Exception:
        sitk = None  # type: ignore

    if sitk is not None:
        image = sitk.ReadImage(path)
        array = sitk.GetArrayFromImage(image).transpose(2, 1, 0)  # (z,y,x)->(i,j,k)
        spacing = np.asarray(image.GetSpacing(), dtype=np.float64)
        origin = np.asarray(image.GetOrigin(), dtype=np.float64)
        direction = np.asarray(image.GetDirection(), dtype=np.float64).reshape(3, 3)
        affine_lps = np.eye(4, dtype=np.float64)
        affine_lps[:3, :3] = direction * spacing[np.newaxis, :]
        affine_lps[:3, 3] = origin
        affine = _LPS_TO_RAS @ affine_lps
        return LabelVolume(
            array=np.ascontiguousarray(array),
            affine=affine,
            backend="sitk",
            handle=image,
        )

    try:
        import nibabel as nib  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise FDIAssignError(
            "NIfTI を読むための SimpleITK / nibabel がどちらも見つかりません。"
            "requirements.txt の依存をインストールしてください。"
        ) from exc

    image = nib.load(path)
    array = np.asanyarray(image.dataobj)
    return LabelVolume(
        array=np.ascontiguousarray(array),
        affine=np.asarray(image.affine, dtype=np.float64),
        backend="nibabel",
        handle=image,
    )


def write_label_volume(path: str, array: np.ndarray, ref: LabelVolume) -> None:
    """ラベル配列を参照ボリュームと同じ幾何で NIfTI として書き出す。

    Args:
        path: 出力パス（.nii / .nii.gz）。
        array: 書き出すラベル配列。軸順は ref.array と同じ (i, j, k)。
        ref: 幾何情報の参照元（read_label_volume の戻り値）。
    """
    out_dir = os.path.dirname(os.path.abspath(path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    data = np.ascontiguousarray(array.astype(np.int16))

    if ref.backend == "sitk":
        import SimpleITK as sitk  # type: ignore

        image = sitk.GetImageFromArray(data.transpose(2, 1, 0))
        image.CopyInformation(ref.handle)  # type: ignore[arg-type]
        sitk.WriteImage(image, path, True)
        return

    import nibabel as nib  # type: ignore

    header = getattr(ref.handle, "header", None)
    image = nib.Nifti1Image(data, ref.affine, header)
    image.set_data_dtype(np.int16)
    nib.save(image, path)


# --------------------------------------------------------------------------- #
# 重心・形状統計
# --------------------------------------------------------------------------- #

@dataclasses.dataclass
class Instance:
    """1 本の歯（インスタンス）の幾何サマリ。

    Attributes:
        label: 入力ラベルマップ上のインスタンス ID。
        voxels: ボクセル数。
        centroid: RAS 実座標(mm) の重心。
        z_skew: 上下軸(RAS z)方向のボクセル分布の歪度。
            歯冠は太く歯根は細いため、歯冠側に質量が偏る。
            上顎歯は歯冠が下（低 z）→ 正の歪度、下顎歯は負の歪度になる。
    """

    label: int
    voxels: int
    centroid: np.ndarray
    z_skew: float


def compute_instances(
    array: np.ndarray, affine: np.ndarray, min_voxels: int = 0
) -> list[Instance]:
    """ラベルマップからインスタンスごとの重心・ボクセル数・歪度を求める。

    Args:
        array: 整数ラベル配列（0 は背景）。
        affine: [i, j, k, 1] → RAS(mm) の 4x4 行列。
        min_voxels: これ未満のインスタンスは除外する（ノイズ対策）。

    Returns:
        ラベル ID 昇順の Instance リスト。
    """
    mask = array > 0
    if not np.any(mask):
        return []

    idx = np.argwhere(mask).astype(np.float64)          # (M, 3) = (i, j, k)
    labels = np.asarray(array[mask]).astype(np.int64)   # (M,)
    points = idx @ affine[:3, :3].T + affine[:3, 3]     # (M, 3) RAS mm

    unique = np.unique(labels)
    lookup = np.full(int(unique.max()) + 1, -1, dtype=np.int64)
    lookup[unique] = np.arange(unique.size)
    compact = lookup[labels]

    counts = np.bincount(compact, minlength=unique.size).astype(np.float64)
    sums = np.stack(
        [np.bincount(compact, weights=points[:, d], minlength=unique.size)
         for d in range(3)],
        axis=1,
    )
    centroids = sums / counts[:, np.newaxis]

    # 上下軸(z)まわりの歪度: E[(z-mu)^3] / sigma^3
    dz = points[:, 2] - centroids[compact, 2]
    m2 = np.bincount(compact, weights=dz ** 2, minlength=unique.size) / counts
    m3 = np.bincount(compact, weights=dz ** 3, minlength=unique.size) / counts
    with np.errstate(divide="ignore", invalid="ignore"):
        skew = np.where(m2 > 1e-9, m3 / np.power(m2, 1.5), 0.0)

    out: list[Instance] = []
    for pos, label in enumerate(unique):
        if counts[pos] < min_voxels:
            continue
        out.append(
            Instance(
                label=int(label),
                voxels=int(counts[pos]),
                centroid=centroids[pos].copy(),
                z_skew=float(skew[pos]),
            )
        )
    return out


# --------------------------------------------------------------------------- #
# 上下顎の判定
# --------------------------------------------------------------------------- #

def split_arches(
    instances: list[Instance],
    *,
    probs: Optional[dict[int, np.ndarray]] = None,
    gap_mm: float = DEFAULT_ARCH_GAP_MM,
    log: Log = _noop,
) -> dict[str, list[Instance]]:
    """インスタンス群を上顎・下顎に分割する。

    判定方式と根拠:
      (a) 歯クラス確率が与えられていれば、上顎 16 歯 / 下顎 16 歯への
          確率質量の合計が大きい側に割り当てる（本家 ToothSeg と同じ考え方）。
      (b) 確率が無い場合は **重心の上下軸(RAS z)座標の 1 次元クラスタリング**
          で判定する。上下顎の歯列は咬合平面で隔てられており、z を昇順に並べた
          ときの最大ギャップが咬合平面に対応する。同一顎内の隣接歯重心の z 差は
          Spee 彎曲・切歯段差でも 1-2mm 程度なのに対し、上下顎の歯冠重心間は
          咬合状態でも 8mm 以上あく。したがって「並び替えた z の中央 60% 区間に
          現れる最大ギャップが gap_mm(既定 4mm) 以上なら 2 顎、未満なら単顎」と
          判定できる。2 顎の場合、z が大きい側が上顎（RAS の z は上向き）。
      (c) 単顎と判定された場合は歯冠・歯根の形状非対称性を使う。歯冠は太く
          歯根は細いため、ボクセル分布は歯冠側に偏る。上顎歯は歯冠が下（低 z）に
          あるので z 方向の歪度が正、下顎歯は負になる。全インスタンスの歪度の
          中央値の符号で顎を決める（|中央値| が小さいときは警告を出す）。

    Args:
        instances: 対象インスタンス。
        probs: インスタンス ID → 32 歯クラス確率（FDI_ALL の並び）。省略可。
        gap_mm: 2 顎とみなす最小の咬合間ギャップ(mm)。
        log: ログ出力関数。

    Returns:
        {"upper": [...], "lower": [...]} 形式の辞書（空リストもあり得る）。
    """
    result: dict[str, list[Instance]] = {"upper": [], "lower": []}
    if not instances:
        return result

    if probs:
        n_upper = len(FDI_UPPER)
        for inst in instances:
            vec = probs.get(inst.label)
            if vec is None or vec.size != len(FDI_ALL):
                break
            key = "upper" if float(vec[:n_upper].sum()) >= float(vec[n_upper:].sum()) else "lower"
            result[key].append(inst)
        else:
            log(f"上下顎判定: 歯クラス確率で分離（上 {len(result['upper'])} / 下 {len(result['lower'])}）")
            return result
        result = {"upper": [], "lower": []}
        log("上下顎判定: 確率の形式が不正なため幾何判定にフォールバック")

    z = np.array([inst.centroid[2] for inst in instances], dtype=np.float64)
    order = np.argsort(z, kind="stable")
    z_sorted = z[order]

    best_gap, best_pos = 0.0, -1
    if z_sorted.size >= 4:
        lo = int(math.floor(z_sorted.size * 0.2))
        hi = int(math.ceil(z_sorted.size * 0.8))
        for pos in range(max(lo, 1), min(hi, z_sorted.size)):
            gap = float(z_sorted[pos] - z_sorted[pos - 1])
            if gap > best_gap:
                best_gap, best_pos = gap, pos

    if best_gap >= gap_mm and best_pos > 0:
        threshold = float((z_sorted[best_pos] + z_sorted[best_pos - 1]) / 2.0)
        for inst in instances:
            result["upper" if inst.centroid[2] >= threshold else "lower"].append(inst)
        log(
            f"上下顎判定: z ギャップ {best_gap:.1f}mm で分離"
            f"（上 {len(result['upper'])} / 下 {len(result['lower'])}）"
        )
        return result

    skew = float(np.median([inst.z_skew for inst in instances]))
    arch = "upper" if skew >= 0 else "lower"
    log(
        f"上下顎判定: 単顎とみなす（最大 z ギャップ {best_gap:.1f}mm < {gap_mm}mm）。"
        f"歯冠歪度の中央値 {skew:+.2f} から '{arch}' と推定"
    )
    if abs(skew) < 0.15:
        log("警告: 歯冠歪度が小さく顎の推定が不確実です。--arch で明示してください。")
    result[arch] = list(instances)
    return result


# --------------------------------------------------------------------------- #
# 理想歯列モデルによる既定ペア統計の生成
# --------------------------------------------------------------------------- #

def _ellipse_quarter_length(a: float, b: float, samples: int = 4096) -> float:
    """1/4 楕円弧（apex (0,b) → 端 (a,0)）の弧長を数値積分で返す。"""
    t = np.linspace(0.0, math.pi / 2.0, samples)
    x, y = a * np.sin(t), b * np.cos(t)
    return float(np.hypot(np.diff(x), np.diff(y)).sum())


def _solve_semi_minor(a: float, target_length: float) -> float:
    """1/4 楕円弧長が target_length になる半短径 b を二分法で解く。

    歯列弓を楕円で近似するとき、左右半幅 a（解剖学的既知値）を固定し、
    前後方向の深さ b を「片側 8 歯の近遠心径の合計＝弓の 1/4 周」を満たすよう
    決めることで、歯のサイズと弓の大きさが自動的に整合する。
    """
    lo, hi = 1.0, 200.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if _ellipse_quarter_length(a, mid) < target_length:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _mesiodistal_width(fdi: int) -> float:
    """FDI 歯番の近遠心径(mm)。左右同名歯は同値（第 2/3 象限は 10 を引いて参照）。"""
    key = fdi if fdi in MESIODISTAL_WIDTH_MM else fdi - 10
    return MESIODISTAL_WIDTH_MM[key]


@functools.lru_cache(maxsize=4)
def ideal_arch_ellipse(arch: str) -> tuple[float, float]:
    """理想歯列弓を近似する半楕円の (半長径 a, 半短径 b) を mm で返す。

    a は解剖学的な歯列弓半幅（ARCH_HALF_WIDTH_MM）を採用し、
    b は「片側 8 歯の近遠心径合計 ＝ 1/4 周長」を満たすよう逆算する。
    こうすると歯のサイズと弓の大きさが自動的に整合する。
    """
    if arch not in ARCH_ORDER:
        raise FDIAssignError(f"arch は upper / lower のいずれか: {arch}")
    total_arc = sum(_mesiodistal_width(f) for f in ARCH_ORDER[arch][:8])
    a = ARCH_HALF_WIDTH_MM[arch]
    return a, _solve_semi_minor(a, total_arc)


def ideal_arch_positions(arch: str) -> dict[int, np.ndarray]:
    """理想歯列モデル上の各 FDI 歯の重心位置（2D, mm）を返す。

    モデル: 歯列弓を半楕円 (x, y) = (a sin t, b cos t), t∈[-π/2, π/2] で近似し、
    正中（t=0, 最前方）から遠心方向へ各歯の近遠心径ぶんの弧長を積んで
    歯の中心位置を決める（a, b は ideal_arch_ellipse）。

    座標系は歯列弓ローカル frame:
        x 軸 = 患者右方向（+ が右 = 第 1/4 象限側）
        y 軸 = 前方（+ が前歯側）。原点は正中、前方頂点は (0, b)。
    FDI の正準順序 ARCH_ORDER[arch] は x が + から - へ進む並びになる。

    Args:
        arch: "upper" または "lower"。

    Returns:
        FDI 番号 → 位置ベクトル (2,) の辞書。
    """
    order = ARCH_ORDER[arch]
    right_half = order[:8][::-1]   # 正中側から遠心側へ（例: 11, 12, ..., 18）
    left_half = order[8:]          # 正中側から遠心側へ（例: 21, 22, ..., 28）

    arc_centers: list[float] = []
    cursor = 0.0
    for fdi in right_half:
        w = _mesiodistal_width(fdi)
        arc_centers.append(cursor + w / 2.0)
        cursor += w

    a, b = ideal_arch_ellipse(arch)
    samples = 4096
    t = np.linspace(0.0, math.pi / 2.0, samples)
    xs, ys = a * np.sin(t), b * np.cos(t)
    seg = np.hypot(np.diff(xs), np.diff(ys))
    cum = np.concatenate([[0.0], np.cumsum(seg)])

    u = np.asarray(arc_centers, dtype=np.float64)
    x_at = np.interp(u, cum, xs)
    y_at = np.interp(u, cum, ys)

    positions: dict[int, np.ndarray] = {}
    for i, fdi in enumerate(right_half):
        positions[fdi] = np.array([x_at[i], y_at[i]], dtype=np.float64)
    for i, fdi in enumerate(left_half):
        positions[fdi] = np.array([-x_at[i], y_at[i]], dtype=np.float64)
    return positions


PairStats = dict[tuple[int, int], tuple[np.ndarray, np.ndarray]]


def default_pair_stats(arch: str) -> PairStats:
    """理想歯列モデルから FDI ペアごとの変位分布（平均・共分散）を生成する。

    正準順序で先行する歯 a から後続の歯 b への重心変位について、
        平均   : 理想歯列上の位置差 P_b - P_a
        共分散 : 弓に沿う方向 / 直交方向で異方的な 2x2 行列。
                 n = b と a の間のステップ数として
                     sigma_along^2  = n * SIGMA_STEP_ALONG_MM^2  + (r*|d|)^2
                     sigma_across^2 = n * SIGMA_STEP_ACROSS_MM^2 + (r*|d|)^2
                 （n 倍はステップごとの誤差のランダムウォーク蓄積、
                   r*|d| は歯列弓の大きさ自体の個体差 SIGMA_SCALE_RATIO）
    を与える。隣接ペアだけでなく全ての順序対を作るので、欠損歯を飛ばす
    スキップ遷移にもそのままペア統計が使える。

    Args:
        arch: "upper" または "lower"。

    Returns:
        (FDI_a, FDI_b) → (mean(2,), cov(2,2)) の辞書。
    """
    order = ARCH_ORDER[arch]
    positions = ideal_arch_positions(arch)
    stats: PairStats = {}

    for i, fdi_a in enumerate(order):
        for j in range(i + 1, len(order)):
            fdi_b = order[j]
            mean = positions[fdi_b] - positions[fdi_a]
            norm = float(np.linalg.norm(mean))
            steps = float(j - i)

            scale_var = (SIGMA_SCALE_RATIO * norm) ** 2
            var_along = steps * SIGMA_STEP_ALONG_MM ** 2 + scale_var
            var_across = steps * SIGMA_STEP_ACROSS_MM ** 2 + scale_var

            if norm < 1e-6:
                rot = np.eye(2)
            else:
                u = mean / norm
                rot = np.array([[u[0], -u[1]], [u[1], u[0]]], dtype=np.float64)
            cov = rot @ np.diag([var_along, var_across]) @ rot.T
            stats[(fdi_a, fdi_b)] = (mean, cov)
    return stats


_PAIR_KEY_RE = re.compile(r"(\d{2})\D+(\d{2})")

# 2 次元の符号付き置換（軸入れ替え・符号反転）の全 8 通り。外部統計の軸規約が
# 内部と違う場合の自動整合候補として順に試す。
_AXIS_CANDIDATES: list[tuple[str, np.ndarray]] = [
    ("identity", np.array([[1.0, 0.0], [0.0, 1.0]])),
    ("flip_x", np.array([[-1.0, 0.0], [0.0, 1.0]])),
    ("flip_y", np.array([[1.0, 0.0], [0.0, -1.0]])),
    ("flip_xy", np.array([[-1.0, 0.0], [0.0, -1.0]])),
    ("swap_xy", np.array([[0.0, 1.0], [1.0, 0.0]])),
    ("swap_xy+flip_x", np.array([[0.0, -1.0], [1.0, 0.0]])),
    ("swap_xy+flip_y", np.array([[0.0, 1.0], [-1.0, 0.0]])),
    ("swap_xy+flip_xy", np.array([[0.0, -1.0], [-1.0, 0.0]])),
]


@functools.lru_cache(maxsize=1)
def _reference_adjacent_means() -> dict[tuple[int, int], tuple[float, float]]:
    """内部の理想歯列モデルから、隣接歯ペアの平均変位（両方向）を作る。

    外部統計の座標規約を照合するための参照。上下顎ぶんをまとめ、
    (a, b) と (b, a) の両方を持たせて、JSON 側がどちらの向きで持っていても
    突き合わせられるようにする。
    """
    ref: dict[tuple[int, int], tuple[float, float]] = {}
    for arch in ("upper", "lower"):
        order = ARCH_ORDER[arch]
        positions = ideal_arch_positions(arch)
        for i in range(len(order) - 1):
            a, b = order[i], order[i + 1]
            delta = positions[b] - positions[a]
            ref[(a, b)] = (float(delta[0]), float(delta[1]))
            ref[(b, a)] = (float(-delta[0]), float(-delta[1]))
    return ref


def validate_pair_stats(stats: PairStats, log: Log = _noop) -> PairStats:
    """外部ペア統計が内部の歯列弓ローカル frame と整合しているか検証する。

    期待する座標規約（内部で使う歯列弓ローカル frame。ideal_arch_positions 参照）:
        - 2 次元。x 軸 = 患者**右**方向が正、y 軸 = **前方**（前歯側）が正。
        - 単位は **mm**。
        - 値は「重心の差ベクトル」なので原点の取り方には依存しない。
    本家 `fdi_pair_distrs.json` が別の軸順・符号・単位で作られていると、遷移コスト
    が黙って狂い「外部統計を与えたのに精度が落ちる」ことになる。そこで採用前に
    次の 2 段で検査し、通らなければ内蔵の理想歯列モデルへフォールバックする。

      1. 単位スケール: 隣接歯ペアの平均変位の大きさの中央値が
         EXTERNAL_STATS_MIN/MAX_ADJACENT_MM（5-12mm）の範囲にあるか。
         近遠心径が 5.0-11.0mm である以上、隣接歯重心間距離はこの範囲に入る。
         外れていれば cm / m / voxel など単位違いとみなして棄却する。
      2. 軸順・符号: 内部モデルの隣接ペア変位との平均コサイン類似度を、
         2 次元の符号付き置換 8 通り（_AXIS_CANDIDATES）で評価する。最良でも
         EXTERNAL_STATS_MIN_AXIS_AGREEMENT 未満なら規約不明として棄却する。
         最良が恒等でなければ、その置換 T を平均に適用（共分散は T cov Tᵀ）して
         自動整合し、**必ず警告を出す**（黙って直さない）。

    Args:
        stats: load_pair_stats が読んだ生の統計。
        log: ログ出力関数。

    Returns:
        検証（必要なら軸整合）を通った統計。棄却時は空辞書（＝内蔵統計を使う）。
    """
    if not stats:
        return {}

    ref = _reference_adjacent_means()
    keys = [k for k in stats if k in ref]
    if len(keys) < EXTERNAL_STATS_MIN_MATCHED_PAIRS:
        log(
            f"警告: 外部ペア統計に隣接歯ペアが {len(keys)} 組しかなく座標規約を検証"
            "できません。内蔵の理想歯列モデルにフォールバックします。"
        )
        return {}

    ext = np.stack([stats[k][0] for k in keys])                    # (M, 2)
    refs = np.stack([np.asarray(ref[k], dtype=np.float64) for k in keys])

    ext_norm = np.linalg.norm(ext, axis=1)
    median_mm = float(np.median(ext_norm))
    if not (EXTERNAL_STATS_MIN_ADJACENT_MM
            <= median_mm
            <= EXTERNAL_STATS_MAX_ADJACENT_MM):
        ratio = median_mm / float(np.median(np.linalg.norm(refs, axis=1)))
        log(
            f"警告: 外部ペア統計の隣接歯変位が中央値 {median_mm:.3g}（内部モデル比"
            f" {ratio:.3g} 倍）で、想定範囲 {EXTERNAL_STATS_MIN_ADJACENT_MM}-"
            f"{EXTERNAL_STATS_MAX_ADJACENT_MM}mm を外れています。単位が mm でない"
            "可能性が高いため、内蔵の理想歯列モデルにフォールバックします。"
        )
        return {}

    ref_norm = np.linalg.norm(refs, axis=1)
    denom = ext_norm * ref_norm
    valid = denom > 1e-9
    if not np.any(valid):
        log("警告: 外部ペア統計の変位が全て 0 です。内蔵の理想歯列モデルを使います。")
        return {}

    best_name, best_axis, best_score = "identity", _AXIS_CANDIDATES[0][1], -2.0
    for name, axis in _AXIS_CANDIDATES:
        cos = np.einsum("ij,ij->i", ext @ axis.T, refs)[valid] / denom[valid]
        score = float(np.mean(cos))
        if score > best_score:
            best_name, best_axis, best_score = name, axis, score

    if best_score < EXTERNAL_STATS_MIN_AXIS_AGREEMENT:
        log(
            f"警告: 外部ペア統計の軸規約を判定できません（最良一致 {best_name} でも"
            f" 平均コサイン {best_score:.2f} < {EXTERNAL_STATS_MIN_AXIS_AGREEMENT}）。"
            "内蔵の理想歯列モデルにフォールバックします。"
        )
        return {}

    if best_name == "identity":
        log(f"外部ペア統計の座標規約を確認: 内部 frame と一致（一致度 {best_score:.2f}）")
        return dict(stats)

    log(
        f"警告: 外部ペア統計の軸規約が内部 frame と異なります（{best_name} で一致度"
        f" {best_score:.2f}）。この変換を適用して整合させました。統計ファイルの"
        "座標規約（x=患者右+, y=前方+, mm）を確認してください。"
    )
    return {
        key: (best_axis @ mean, best_axis @ cov @ best_axis.T)
        for key, (mean, cov) in stats.items()
    }


def load_pair_stats(path: str, log: Log = _noop, *, validate: bool = True) -> PairStats:
    """本家互換の FDI ペア統計 JSON を読み込む。

    キーは "18-17" / "18,17" / "18_17" / "(18, 17)" / "[18, 17]" のいずれでも可
    （2 桁の FDI 番号を 2 つ含む文字列として解釈する）。値は
        {"mean": [dx, dy], "cov": [[..],[..]]}
    または本家表記の {"means": ..., "covs": ...} を受け付ける。
    3 次元以上の平均・共分散が入っている場合は先頭 2 成分だけを使う。

    **座標規約**: 平均・共分散は歯列弓ローカル frame（x=患者右が正、y=前方が正、
    単位 mm）で表現されている必要がある。既定では validate_pair_stats で
    単位スケールと軸順・符号を検査し、想定外なら警告して空辞書を返す
    （呼び出し側は内蔵の理想歯列モデルへフォールバックする）。

    Args:
        path: 統計 JSON のパス。
        log: ログ出力関数。
        validate: False にすると座標規約の検査を行わず生の値を返す（試験用）。

    Returns:
        (FDI_a, FDI_b) → (mean(2,), cov(2,2)) の辞書。検証に落ちた場合は空辞書。
    """
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, dict):
        raise FDIAssignError("--pair-stats の JSON はオブジェクト（辞書）である必要があります。")

    stats: PairStats = {}
    for key, value in raw.items():
        match = _PAIR_KEY_RE.search(str(key))
        if not match or not isinstance(value, dict):
            continue
        pair = (int(match.group(1)), int(match.group(2)))

        mean_src = value.get("mean", value.get("means"))
        cov_src = value.get("cov", value.get("covs", value.get("covariance")))
        if mean_src is None or cov_src is None:
            continue
        mean = np.asarray(mean_src, dtype=np.float64).reshape(-1)[:2]
        cov = np.asarray(cov_src, dtype=np.float64)
        if cov.ndim != 2 or cov.shape[0] < 2:
            continue
        cov = cov[:2, :2]
        if mean.size != 2:
            continue
        stats[pair] = (mean, cov)

    log(f"外部ペア統計を読み込み: {len(stats)} ペア（{os.path.basename(path)}）")
    return validate_pair_stats(stats, log) if validate else stats


# --------------------------------------------------------------------------- #
# 歯列順序の構築
# --------------------------------------------------------------------------- #

def _path_length(points: np.ndarray, path: list[int]) -> float:
    if len(path) < 2:
        return 0.0
    arr = points[path]
    return float(np.hypot(*(arr[1:] - arr[:-1]).T).sum())


def _greedy_path(dist: np.ndarray, start: int) -> list[int]:
    """start から最近傍未訪問点を辿る貪欲パス。距離同点は添字の小さい方を選ぶ。"""
    n = dist.shape[0]
    visited = np.zeros(n, dtype=bool)
    visited[start] = True
    path = [start]
    for _ in range(n - 1):
        candidates = np.where(~visited, dist[path[-1]], np.inf)
        nxt = int(np.argmin(candidates))  # 同点は最小添字（決定性）
        visited[nxt] = True
        path.append(nxt)
    return path


def _two_opt(points: np.ndarray, path: list[int], max_rounds: int = 64) -> list[int]:
    """開いた経路に対する 2-opt 改善。決定的な走査順で改善が無くなるまで反復。"""
    best = list(path)
    best_len = _path_length(points, best)
    for _ in range(max_rounds):
        improved = False
        for i in range(len(best) - 1):
            for j in range(i + 2, len(best)):
                cand = best[:i + 1] + best[i + 1:j + 1][::-1] + best[j + 1:]
                cand_len = _path_length(points, cand)
                if cand_len < best_len - 1e-9:
                    best, best_len, improved = cand, cand_len, True
        if not improved:
            break
    return best


def order_along_arch(points_2d: np.ndarray) -> list[int]:
    """歯列弓に沿った並び順（点の添字列）を返す。

    歯列弓は開いた曲線なので、正しい順序は「最短ハミルトン路」に相当する。
    始点候補を全点で試し（最大 16 点なので総当たりで足りる）、貪欲最近傍 +
    2-opt で最短経路を求める。歯列の途中から出発すると必ず弓を横断する
    長い辺が生じるため、最短経路の端点は自動的に歯列の端（最後方臼歯）になる。

    決定性（入力順序に対する不変性）のため、事前に座標の辞書順でソートしてから
    処理し、最後に元の添字へ戻す。

    Args:
        points_2d: (N, 2) の平面座標。

    Returns:
        歯列弓に沿った順の添字リスト（長さ N）。
    """
    n = points_2d.shape[0]
    if n <= 2:
        return list(range(n))

    lex = np.lexsort((points_2d[:, 1], points_2d[:, 0]))
    pts = points_2d[lex]
    diff = pts[:, np.newaxis, :] - pts[np.newaxis, :, :]
    dist = np.sqrt((diff ** 2).sum(axis=2))
    np.fill_diagonal(dist, np.inf)

    best_path: list[int] = []
    best_len = math.inf
    for start in range(n):
        path = _two_opt(pts, _greedy_path(dist, start))
        length = _path_length(pts, path)
        if length < best_len - 1e-9:
            best_path, best_len = path, length

    # 端点の座標が辞書順で小さい側を先頭にして向きも決定的にする
    if best_path and (pts[best_path[0], 0], pts[best_path[0], 1]) > (
        pts[best_path[-1], 0], pts[best_path[-1], 1]
    ):
        best_path = best_path[::-1]

    return [int(lex[i]) for i in best_path]


def _rotation(theta: float) -> np.ndarray:
    """世界 RAS xy → (右, 前) の回転行列。theta=0 で右=+x, 前=+y。"""
    c, s = math.cos(theta), math.sin(theta)
    return np.array([[c, s], [-s, c]], dtype=np.float64)


def _fit_parabola(local_2d: np.ndarray) -> tuple[float, float, float, float]:
    """y = c0 + c1 x + c2 x^2 を最小二乗で当て、(c0, c1, c2, 残差二乗和) を返す。"""
    x, y = local_2d[:, 0], local_2d[:, 1]
    design = np.stack([np.ones_like(x), x, x ** 2], axis=1)
    coef, *_ = np.linalg.lstsq(design, y, rcond=None)
    residual = float(((design @ coef - y) ** 2).sum())
    return float(coef[0]), float(coef[1]), float(coef[2]), residual


# 歯列弓とみなす最小曲率（曲率半径 120mm 相当）と最小左右幅(mm)。
# これを下回る点群は「ほぼ直線の部分歯列」とみなし、幾何 frame 推定を諦める。
_MIN_ARCH_CURVATURE = 1.0 / 120.0
_MIN_ARCH_SPAN_MM = 25.0
_APEX_MARGIN_MM = 5.0


def fit_arch_frame(
    points_2d: np.ndarray, log: Log = _noop
) -> tuple[np.ndarray, Optional[np.ndarray]]:
    """歯列弓ローカル frame（回転行列）と正中頂点を同時に推定する。

    なぜ正中頂点が要るか: 隣接ペアの変位だけを見る DP は「歯列全体が 1 歯ぶん
    ずれた解」を区別できない。隣り合う歯の近遠心径の差は 1mm 前後しかなく、
    変位の分布からはほぼ等価に見えるためである（実測で誤答が出た）。一方、
    歯列弓は馬蹄形で **前方頂点＝正中** という強い幾何ランドマークを持ち、
    これは歯番の付け方に依存せず観測点だけから決まる。頂点を理想モデルの
    原点に合わせれば絶対位置が使えるようになり、1 歯ずれ解も左右反転解も
    明確に棄却できる。

    推定法: 歯列弓は「前後軸を対称軸とする放物線」でよく近似できる。そこで
    回転角 theta を振りながら y = c0 + c1 x + c2 x^2 を最小二乗で当て、
    **残差が最小になる theta** を frame とする。放物線の軸が frame の y 軸に
    揃ったときに残差が最小になるので、これは頭位の傾きに依らず歯列弓自身の
    対称軸を拾う。頂点 x* = -c1/(2 c2) も同じ当てはめから得られる。

    設計判断:
      - 上下軸だけは affine（RAS の z が上向き）に依存する。左右は右手系で
        cross(前, 上) = 右 と決まるため、2D では前方向を -90 度回した向き。
      - theta の走査範囲は RAS +y（前方）まわりの ±60 度に限る。CBCT の頭位が
        ここまで倒れることは無く、範囲を広げると「弓を横倒しに当てはめた解」を
        拾う恐れがあるため。c2 >= 0（後ろ向きに開く）しか得られない場合のみ
        180 度反転を許し、警告を出す（affine の前後が逆の可能性）。
      - 曲率が _MIN_ARCH_CURVATURE 未満、左右幅が _MIN_ARCH_SPAN_MM 未満、
        頂点が観測範囲の外側、点数 5 未満のいずれかなら推定を諦め、
        frame は RAS のまま・頂点は None（絶対位置項を使わない）にする。

    Args:
        points_2d: (N, 2) の RAS xy 平面座標。
        log: ログ出力関数。

    Returns:
        (回転行列 (2,2), 頂点のローカル座標 (2,) または None)。
    """
    identity = _rotation(0.0)
    if points_2d.shape[0] < 5:
        log("注意: 歯が少ないため歯列弓 frame は RAS のまま（絶対位置項なし）。")
        return identity, None

    def scan(base: float) -> tuple[float, tuple[float, float, float, float]]:
        best_theta, best_fit = base, None
        for step in range(-60, 61):
            theta = base + math.radians(step)
            fit = _fit_parabola(points_2d @ _rotation(theta).T)
            if best_fit is None or fit[3] < best_fit[3]:
                best_theta, best_fit = theta, fit
        assert best_fit is not None
        return best_theta, best_fit

    theta, fit = scan(0.0)
    if fit[2] >= 0.0:
        log("注意: 歯列弓が後ろ向きに開いています。前後軸を 180 度反転します（affine 要確認）。")
        theta, fit = scan(math.pi)

    rot = _rotation(theta)
    local = points_2d @ rot.T
    c0, c1, c2, _ = fit

    if c2 >= -_MIN_ARCH_CURVATURE:
        log("注意: 歯列の曲率が弱く正中を推定できないため絶対位置項を無効化します。")
        return identity, None
    if float(local[:, 0].max() - local[:, 0].min()) < _MIN_ARCH_SPAN_MM:
        log("注意: 歯列の左右幅が狭く正中を推定できないため絶対位置項を無効化します。")
        return identity, None

    apex_x = -c1 / (2.0 * c2)
    if not (local[:, 0].min() + _APEX_MARGIN_MM
            <= apex_x
            <= local[:, 0].max() - _APEX_MARGIN_MM):
        log("注意: 推定した正中が歯列の外側のため絶対位置項を無効化します。")
        return rot, None

    apex_y = c0 + c1 * apex_x + c2 * apex_x ** 2
    log(f"歯列弓 frame: 傾き {math.degrees(theta):+.1f} 度 / 正中を推定")
    return rot, np.array([apex_x, apex_y], dtype=np.float64)


# --------------------------------------------------------------------------- #
# Viterbi 型 DP による FDI 割当
# --------------------------------------------------------------------------- #

_LOG_2PI = math.log(2.0 * math.pi)


def _neg_log_gaussian(x: np.ndarray, mean: np.ndarray, cov: np.ndarray) -> float:
    """二変量正規分布の負の対数尤度。特異な共分散は微小リッジで補正する。"""
    delta = x - mean
    reg = cov + np.eye(2) * 1e-6
    try:
        chol = np.linalg.cholesky(reg)
    except np.linalg.LinAlgError:
        reg = cov + np.eye(2) * (abs(float(np.trace(cov))) * 1e-3 + 1e-3)
        chol = np.linalg.cholesky(reg)
    solved = np.linalg.solve(chol, delta)
    maha = float(solved @ solved)
    log_det = 2.0 * float(np.log(np.diag(chol)).sum())
    return 0.5 * (maha + log_det + 2.0 * _LOG_2PI)


@dataclasses.dataclass
class ViterbiResult:
    """DP の結果。

    Attributes:
        fdis: 観測（歯列順の系列）ごとに割り当てられた FDI 番号。
        cost: 総コスト（小さいほど良い）。
    """

    fdis: list[int]
    cost: float


def viterbi_assign(
    displacements: np.ndarray,
    arch: str,
    pair_stats: PairStats,
    *,
    emission: Optional[np.ndarray] = None,
    skip_penalty: float = DEFAULT_SKIP_PENALTY,
    end_skip_penalty: float = DEFAULT_END_SKIP_PENALTY,
) -> ViterbiResult:
    """歯列順の系列を片顎 16 歯の FDI 列へ最小コストで整合させる。

    ステート = 正準順序上の位置（0..15）、観測 = 歯列順に並んだ歯。
    観測は必ずステート順を単調増加でたどる（歯列弓に沿って並んでいるため）。
    ステートを飛ばす遷移＝欠損歯に対応し、飛ばした歯数 × skip_penalty を課す。
    系列の前後に残るステート（＝最後方側の欠損。第三大臼歯欠損は日常的）には
    より軽い end_skip_penalty を課す。

    Args:
        displacements: (T-1, 2) の隣接観測間の重心変位（歯列弓 frame）。
        arch: "upper" または "lower"。
        pair_stats: (FDI_a, FDI_b) → (mean, cov)。
        emission: (T, 16) の放出コスト。None なら全 0（純粋に幾何のみで解く）。
        skip_penalty: 歯列内部の欠損 1 歯あたりのペナルティ。
        end_skip_penalty: 歯列端の欠損 1 歯あたりのペナルティ。

    Returns:
        ViterbiResult。

    Raises:
        FDIAssignError: 観測数が 16 を超える場合。
    """
    order = ARCH_ORDER[arch]
    n_states = len(order)
    n_obs = displacements.shape[0] + 1

    if n_obs > n_states:
        raise FDIAssignError(
            f"{arch}: 検出された歯が {n_obs} 本で片顎 {n_states} 本を超えています。"
            "過分割の可能性があるため --min-voxels を上げて再実行してください。"
        )

    emis = np.zeros((n_obs, n_states)) if emission is None else emission
    inf = math.inf

    # 遷移コストを事前計算: trans[t][k][k'] （k' > k のみ有限）
    trans = np.full((n_obs - 1, n_states, n_states), inf)
    for t in range(n_obs - 1):
        d = displacements[t]
        for k in range(n_states):
            for k2 in range(k + 1, n_states):
                pair = pair_stats.get((order[k], order[k2]))
                if pair is None:
                    continue
                mean, cov = pair
                trans[t, k, k2] = (
                    _neg_log_gaussian(d, mean, cov) + skip_penalty * (k2 - k - 1)
                )

    cost = np.full((n_obs, n_states), inf)
    back = np.full((n_obs, n_states), -1, dtype=np.int64)
    for k in range(n_states):
        cost[0, k] = emis[0, k] + end_skip_penalty * k

    for t in range(1, n_obs):
        for k2 in range(n_states):
            column = cost[t - 1] + trans[t - 1, :, k2]
            k = int(np.argmin(column))
            if math.isfinite(column[k]):
                cost[t, k2] = column[k] + emis[t, k2]
                back[t, k2] = k

    final = cost[n_obs - 1] + end_skip_penalty * (
        np.arange(n_states)[::-1].astype(np.float64)
    )
    if not np.isfinite(final).any():
        raise FDIAssignError(f"{arch}: FDI 列への整合に失敗しました（有効な経路なし）。")

    k = int(np.argmin(final))
    total = float(final[k])
    states = [k]
    for t in range(n_obs - 1, 0, -1):
        k = int(back[t, k])
        states.append(k)
    states.reverse()

    return ViterbiResult(fdis=[order[s] for s in states], cost=total)


# --------------------------------------------------------------------------- #
# 顎単位の割当
# --------------------------------------------------------------------------- #

@dataclasses.dataclass
class ArchDiagnostics:
    """片顎ぶんの割当の不確実性指標。下流が「この症例は怪しい」と判定するための材料。

    Attributes:
        arch: "upper" / "lower"。
        n_teeth: 割り当てた歯の本数。
        cost: 採用解の総コスト。
        alternative_cost: 対抗解（歯列順を逆向きにした解）の総コスト。
        cost_margin: alternative_cost - cost。差が小さいほど解が拮抗している。
        cost_margin_per_tooth: cost_margin を歯数で割った値。総コストは歯数に
            比例して増えるため、症例間で比べられるのはこちら。
        arch_frame_fitted: fit_arch_frame が正中頂点を推定できたか（apex is not None）。
        anchor_used: 正中アンカー（絶対位置項）が実際に効いたか。これが False の
            とき「歯列全体が 1 歯ぶんずれた解」を棄却する手段が無い。
        pair_stats_source: "external"（外部統計を採用）/ "builtin-ideal-arch"。
        probs_used: 歯クラス確率の放出項が有効だったか。
        missing_internal: 歯列内部でスキップした歯数（欠損歯）。
        missing_end: 歯列端（第三大臼歯側）でスキップした歯数。
        arch_scale: 歯列弓サイズ倍率の再推定値。再推定しなかった場合は None。
        confidence: "high" / "medium" / "low"。判定根拠は _confidence_from 参照。
        ambiguous: 対抗解と拮抗していて割当を信用できない場合 True。
        warnings: 減点・警告の理由（人間可読）。
    """

    arch: str
    n_teeth: int
    cost: float
    alternative_cost: Optional[float]
    cost_margin: Optional[float]
    cost_margin_per_tooth: Optional[float]
    arch_frame_fitted: bool
    anchor_used: bool
    pair_stats_source: str
    probs_used: bool
    missing_internal: int
    missing_end: int
    arch_scale: Optional[float]
    confidence: str
    ambiguous: bool
    warnings: list[str] = dataclasses.field(default_factory=list)

    def as_dict(self) -> dict:
        """JSON 化用の辞書（PHI は含まない：本数とコストのみ）。"""
        def _round(value: Optional[float], digits: int = 3) -> Optional[float]:
            return None if value is None else round(float(value), digits)

        return {
            "arch": self.arch,
            "n_teeth": self.n_teeth,
            "cost": _round(self.cost),
            "alternative_cost": _round(self.alternative_cost),
            "cost_margin": _round(self.cost_margin),
            "cost_margin_per_tooth": _round(self.cost_margin_per_tooth),
            "arch_frame_fitted": self.arch_frame_fitted,
            "anchor_used": self.anchor_used,
            "pair_stats_source": self.pair_stats_source,
            "probs_used": self.probs_used,
            "missing_internal": self.missing_internal,
            "missing_end": self.missing_end,
            "arch_scale": _round(self.arch_scale, 4),
            "confidence": self.confidence,
            "ambiguous": self.ambiguous,
            "warnings": list(self.warnings),
        }


_CONFIDENCE_ORDER = ("low", "medium", "high")


def _demote(level: str) -> str:
    """confidence を 1 段下げる（high → medium → low）。"""
    return _CONFIDENCE_ORDER[max(0, _CONFIDENCE_ORDER.index(level) - 1)]


def _confidence_from(
    margin_per_tooth: Optional[float],
    n_teeth: int,
    anchor_used: bool,
    warnings: list[str],
) -> tuple[str, bool]:
    """割当の confidence（high / medium / low）と ambiguous を決める。

    判定基準と根拠（rescue_spacing.py の同名関数と粒度を揃えてある）:

      1. **対抗解との差**（1 歯あたり）。歯列順は「患者右始まり / 左始まり」の
         2 通りがあり、両方を解いてコストの小さい方を採用している。この差が
         小さい＝幾何的にどちらとも取れる状態で、同じ理屈で「歯列全体が 1 歯
         ぶんずれた解」とも拮抗している疑いが強い。総コストは歯数に比例して
         増えるので必ず歯数で割り、AMBIGUITY_MARGIN_PER_TOOTH (1.0) 未満なら
         ambiguous（→ confidence は low）、HIGH_MARGIN_PER_TOOTH (3.0) 以上で
         初めて high の候補とする。1 歯あたり 1.0 は二変量正規の負対数尤度で
         おおむね「1 標準偏差ぶんのずれ」に相当し、これを下回る差は誤差の範囲。

      2. **残存歯数**。モジュール冒頭に記した合成データの実測（欠損 3 本まで
         100%、4-6 本で約 96%、8 本で約 70%）をそのまま写す。残存 13 本以上
         （CONFIDENCE_FULL_ARCH_MIN_TEETH）は減点なし、残存 9 本以下
         （CONFIDENCE_LOW_MAX_TEETH）は実測 70% 台なので low 固定、
         その間（10-12 本）は 1 段下げる。

      3. **正中アンカーの有無**。fit_arch_frame が正中を取れないと絶対位置項が
         使えず、1 歯ずれ解を棄却する手段が無くなる（fit_arch_frame の docstring
         参照）。この場合も 1 段下げる。

    Args:
        margin_per_tooth: 1 歯あたりの対抗解とのコスト差。求まらない場合は None。
        n_teeth: 割り当てた歯の本数。
        anchor_used: 正中アンカーが有効だったか。
        warnings: 減点理由を追記する先（呼び出し側と共有するリスト）。

    Returns:
        (confidence, ambiguous)。
    """
    if n_teeth <= 1:
        warnings.append("歯が 1 本以下で幾何情報がない")
        return "low", True

    ambiguous = (
        margin_per_tooth is None or margin_per_tooth < AMBIGUITY_MARGIN_PER_TOOTH
    )
    if ambiguous:
        shown = "なし" if margin_per_tooth is None else f"{margin_per_tooth:.2f}"
        warnings.append(
            "対抗解（歯列の向き逆転）とコストが拮抗している"
            f"（1 歯あたり差 {shown} < {AMBIGUITY_MARGIN_PER_TOOTH}）"
        )
        return "low", True

    level = "high" if margin_per_tooth >= HIGH_MARGIN_PER_TOOTH else "medium"
    if level == "medium":
        warnings.append(
            f"対抗解とのコスト差が小さめ（1 歯あたり {margin_per_tooth:.2f}"
            f" < {HIGH_MARGIN_PER_TOOTH}）"
        )

    if not anchor_used:
        level = _demote(level)
        warnings.append("正中アンカーが無効（1 歯ずれ解を棄却できない）")

    if n_teeth <= CONFIDENCE_LOW_MAX_TEETH:
        level = "low"
        warnings.append(
            f"残存歯 {n_teeth} 本（{CONFIDENCE_LOW_MAX_TEETH} 本以下は実測精度 70% 台）"
        )
    elif n_teeth < CONFIDENCE_FULL_ARCH_MIN_TEETH:
        level = _demote(level)
        warnings.append(f"残存歯 {n_teeth} 本（欠損 4 本以上は実測精度 約 96%）")

    return level, False


def assign_arch(
    instances: list[Instance],
    arch: str,
    *,
    pair_stats: Optional[PairStats] = None,
    probs: Optional[dict[int, np.ndarray]] = None,
    prob_weight: float = DEFAULT_PROB_WEIGHT,
    skip_penalty: float = DEFAULT_SKIP_PENALTY,
    end_skip_penalty: float = DEFAULT_END_SKIP_PENALTY,
    anchor_weight: float = DEFAULT_ANCHOR_WEIGHT,
    anchor_sigma_mm: float = DEFAULT_ANCHOR_SIGMA_MM,
    log: Log = _noop,
    diagnostics_out: Optional[list["ArchDiagnostics"]] = None,
) -> tuple[dict[int, int], float]:
    """片顎ぶんのインスタンスに FDI を割り当てる。

    歯列順序は方向（患者右始まり / 左始まり）が一意に決まらないため、
    正順と逆順の両方で Viterbi を解き、コストの小さい方を採用する。
    ペア統計は左右非対称（変位の x 成分の符号が反転する）ので、
    向きを取り違えるとコストが跳ね上がり自動的に棄却される。

    放出コストは 2 種類の和で構成する。
      - 歯クラス確率がある場合の -prob_weight * log p（本家と同じ項）
      - 正中アンカーによる絶対位置項（fit_arch_frame 参照）。歯列の前方頂点を
        理想モデルの頂点に合わせ、理想位置とのずれを等方ガウスで評価する。
        許容標準偏差は正中付近で anchor_sigma_mm、遠心へ行くほど
        ANCHOR_SCALE_RATIO ぶん広げて歯列弓サイズの個体差を吸収する。
        これが「歯列全体が 1 歯ぶんずれた解」への唯一の対抗手段になる。

    Args:
        instances: 対象の顎に属するインスタンス。
        arch: "upper" または "lower"。
        pair_stats: 外部統計。None なら理想歯列モデルから生成。
        probs: インスタンス ID → 32 歯クラス確率。None なら確率項を使わない。
        prob_weight: 確率項の重み。
        skip_penalty: 歯列内部の欠損 1 歯あたりのペナルティ。
        end_skip_penalty: 歯列端の欠損 1 歯あたりのペナルティ。
        anchor_weight: 絶対位置項の重み（0 で無効）。
        anchor_sigma_mm: 絶対位置項の許容標準偏差(mm)。
        log: ログ出力関数。
        diagnostics_out: 渡すと ArchDiagnostics（不確実性指標）を 1 件 append する。
            戻り値の形は変えたくないので、nifti_to_stl.py の info_out と同じ
            「呼び出し側が入れ物を渡す」方式にしてある。

    Returns:
        (インスタンス ID → FDI の辞書, 採用された総コスト)。
    """
    if not instances:
        return {}, 0.0

    stats = default_pair_stats(arch)
    external = {k: v for k, v in (pair_stats or {}).items()
                if k[0] in ARCH_ORDER[arch] and k[1] in ARCH_ORDER[arch]}
    stats.update(external)
    stats_source = "external" if external else "builtin-ideal-arch"

    def _emit(
        n_teeth: int,
        cost: float,
        alternative: Optional[float],
        *,
        frame_fitted: bool,
        anchor_used: bool,
        probs_used: bool,
        missing_internal: int,
        missing_end: int,
        arch_scale: Optional[float],
    ) -> None:
        """診断情報を組み立てて diagnostics_out へ積む（未指定なら何もしない）。"""
        if diagnostics_out is None:
            return
        margin = None if alternative is None else float(alternative - cost)
        per_tooth = None if margin is None or n_teeth <= 0 else margin / n_teeth
        warnings: list[str] = []
        confidence, ambiguous = _confidence_from(
            per_tooth, n_teeth, anchor_used, warnings
        )
        diagnostics_out.append(
            ArchDiagnostics(
                arch=arch,
                n_teeth=n_teeth,
                cost=float(cost),
                alternative_cost=None if alternative is None else float(alternative),
                cost_margin=margin,
                cost_margin_per_tooth=per_tooth,
                arch_frame_fitted=frame_fitted,
                anchor_used=anchor_used,
                pair_stats_source=stats_source,
                probs_used=probs_used,
                missing_internal=missing_internal,
                missing_end=missing_end,
                arch_scale=arch_scale,
                confidence=confidence,
                ambiguous=ambiguous,
                warnings=warnings,
            )
        )

    points_2d = np.stack([inst.centroid[:2] for inst in instances])
    path = order_along_arch(points_2d)
    rot, apex = fit_arch_frame(points_2d, log)
    local = points_2d @ rot.T                       # (N, 2) = (右, 前)

    if len(instances) == 1:
        # 1 本だけでは幾何情報が無い。確率があればそれで、無ければ中切歯扱い。
        fdi = ARCH_ORDER[arch][7]
        vec = (probs or {}).get(instances[0].label)
        if vec is not None and vec.size == len(FDI_ALL):
            candidates = [(float(vec[FDI_ALL.index(f)]), f) for f in ARCH_ORDER[arch]]
            fdi = max(candidates)[1]
        log(f"{arch}: 1 本のみのため FDI {fdi} を割当（幾何情報なし）")
        _emit(
            1, 0.0, None,
            frame_fitted=apex is not None,
            anchor_used=False,
            probs_used=vec is not None,
            missing_internal=0,
            missing_end=len(ARCH_ORDER[arch]) - 1,
            arch_scale=None,
        )
        return {instances[0].label: fdi}, 0.0

    order_fdi = ARCH_ORDER[arch]
    ideal = ideal_arch_positions(arch)
    model = np.stack([ideal[f] for f in order_fdi])  # (16, 2)
    _, semi_minor = ideal_arch_ellipse(arch)
    model_apex = np.array([0.0, semi_minor])

    anchored: Optional[np.ndarray] = None
    anchor_var: Optional[np.ndarray] = None
    if apex is not None and anchor_weight > 0.0 and anchor_sigma_mm > 0.0:
        anchored = local - apex + model_apex
        # 正中から遠い歯ほど歯列弓サイズの個体差の影響を受けるので分散を広げる
        apex_dist = np.linalg.norm(model - model_apex, axis=1)      # (16,)
        anchor_var = anchor_sigma_mm ** 2 + (ANCHOR_SCALE_RATIO * apex_dist) ** 2

    prob_cost: Optional[np.ndarray] = None
    if probs:
        prob_cost = np.zeros((len(instances), len(order_fdi)))
        for pos, inst in enumerate(instances):
            vec = probs.get(inst.label)
            if vec is None or vec.size != len(FDI_ALL):
                log("注意: 一部インスタンスの確率が欠落しているため確率項を無効化します。")
                prob_cost = None
                break
            clipped = np.clip(vec, 1e-9, None)
            for col, fdi in enumerate(order_fdi):
                prob_cost[pos, col] = -prob_weight * math.log(
                    float(clipped[FDI_ALL.index(fdi)])
                )

    def solve(scale: float) -> list[tuple[float, list[int], list[int]]]:
        """歯列弓サイズ倍率 scale のモデルで、正順・逆順それぞれの DP を解く。"""
        scaled_model = model_apex + scale * (model - model_apex)
        scaled_stats = (
            stats if scale == 1.0
            else {k: (mu * scale, cov) for k, (mu, cov) in stats.items()}
        )
        out: list[tuple[float, list[int], list[int]]] = []
        for reverse in (False, True):
            seq = path[::-1] if reverse else path
            coords = local[seq]
            emission = np.zeros((len(seq), len(order_fdi)))
            if anchored is not None and anchor_var is not None:
                delta = anchored[seq][:, np.newaxis, :] - scaled_model[np.newaxis, :, :]
                emission += anchor_weight * 0.5 * (delta ** 2).sum(axis=2) / anchor_var
            if prob_cost is not None:
                emission += prob_cost[seq]
            res = viterbi_assign(
                coords[1:] - coords[:-1], arch, scaled_stats,
                emission=emission,
                skip_penalty=skip_penalty,
                end_skip_penalty=end_skip_penalty,
            )
            out.append((res.cost, res.fdis, seq))
        out.sort(key=lambda item: item[0])
        return out

    results = solve(1.0)
    cost, fdis, seq = results[0]
    arch_scale: Optional[float] = None

    # 歯列弓サイズの個体差補正: 初回解から一様倍率を最小二乗推定し、
    # 有意にずれていればモデルを合わせて 1 回だけ解き直す。理想歯列は
    # 平均値の集合なので、大きめ／小さめの弓では 1 歯ずれが起きやすい。
    if anchored is not None and len(seq) >= 8:
        obs = anchored[seq] - model_apex
        ref = np.stack([ideal[f] for f in fdis]) - model_apex
        denom = float((ref ** 2).sum())
        if denom > 1e-6:
            scale = float(np.clip((obs * ref).sum() / denom, 0.8, 1.25))
            if abs(scale - 1.0) > 0.03:
                log(f"{arch}: 歯列弓サイズ倍率 {scale:.2f} を推定し再割当")
                results = solve(scale)
                cost, fdis, seq = results[0]
                arch_scale = scale

    # 欠損（スキップ）本数: 割当は正準順序上で単調増加するので、両端の
    # インデックス差から内部スキップ、残りを歯列端のスキップとして数える。
    state_index = [order_fdi.index(f) for f in fdis]
    span = state_index[-1] - state_index[0] + 1
    _emit(
        len(fdis), cost, results[1][0],
        frame_fitted=apex is not None,
        anchor_used=anchored is not None,
        probs_used=prob_cost is not None,
        missing_internal=span - len(fdis),
        missing_end=len(order_fdi) - span,
        arch_scale=arch_scale,
    )

    log(
        f"{arch}: {len(instances)} 本を割当（コスト {cost:.1f} / "
        f"逆向き {results[1][0]:.1f}）"
    )
    return {instances[pos].label: fdi for pos, fdi in zip(seq, fdis)}, cost


# --------------------------------------------------------------------------- #
# 全体オーケストレーション
# --------------------------------------------------------------------------- #

@dataclasses.dataclass
class AssignResult:
    """FDI 割当の結果一式。

    Attributes:
        mapping: インスタンス ID → FDI 番号。
        arch_of: インスタンス ID → "upper" / "lower"。
        instances: 使用した Instance のリスト（除外後）。
        dropped: min_voxels で除外したインスタンス ID。
        costs: 顎ごとの総コスト。
        diagnostics: 顎ごとの ArchDiagnostics（不確実性指標）。
    """

    mapping: dict[int, int]
    arch_of: dict[int, str]
    instances: list[Instance]
    dropped: list[int]
    costs: dict[str, float]
    diagnostics: dict[str, ArchDiagnostics] = dataclasses.field(default_factory=dict)

    @property
    def confidence(self) -> str:
        """症例全体の confidence。上下顎で割り当てた場合は**悪い方**を採る。"""
        if not self.diagnostics:
            return "low"
        return min(
            (d.confidence for d in self.diagnostics.values()),
            key=_CONFIDENCE_ORDER.index,
        )

    @property
    def ambiguous(self) -> bool:
        """いずれかの顎が ambiguous なら True。"""
        return any(d.ambiguous for d in self.diagnostics.values()) if self.diagnostics else True


def assign_fdi(
    array: np.ndarray,
    affine: np.ndarray,
    *,
    arch: str = "both",
    probs: Optional[dict[int, np.ndarray]] = None,
    pair_stats: Optional[PairStats] = None,
    min_voxels: int = DEFAULT_MIN_VOXELS,
    prob_weight: float = DEFAULT_PROB_WEIGHT,
    skip_penalty: float = DEFAULT_SKIP_PENALTY,
    end_skip_penalty: float = DEFAULT_END_SKIP_PENALTY,
    anchor_weight: float = DEFAULT_ANCHOR_WEIGHT,
    anchor_sigma_mm: float = DEFAULT_ANCHOR_SIGMA_MM,
    arch_gap_mm: float = DEFAULT_ARCH_GAP_MM,
    log: Log = _noop,
) -> AssignResult:
    """インスタンスラベルマップ全体に FDI を割り当てる。

    Args:
        array: インスタンスラベル配列（0 は背景、1.. が各歯）。
        affine: [i, j, k, 1] → RAS(mm) の 4x4 行列。
        arch: "upper" / "lower" / "both"。both のとき自動で上下を分離する。
        probs: インスタンス ID → 32 歯クラス確率（FDI_ALL 順）。省略可。
        pair_stats: 外部ペア統計。省略時は理想歯列モデルから内部生成。
        min_voxels: これ未満のインスタンスは除外。
        prob_weight: 確率項の重み。
        skip_penalty: 歯列内部の欠損 1 歯あたりのペナルティ。
        end_skip_penalty: 歯列端の欠損 1 歯あたりのペナルティ。
        anchor_weight: 正中アンカーによる絶対位置項の重み（0 で無効）。
        anchor_sigma_mm: 絶対位置項の許容標準偏差(mm)。
        arch_gap_mm: 上下顎を分離する最小ギャップ(mm)。
        log: ログ出力関数。

    Returns:
        AssignResult。
    """
    if arch not in ("upper", "lower", "both"):
        raise FDIAssignError(f"--arch は upper / lower / both のいずれか: {arch}")

    all_instances = compute_instances(array, affine, min_voxels=0)
    kept = [i for i in all_instances if i.voxels >= min_voxels]
    dropped = [i.label for i in all_instances if i.voxels < min_voxels]
    if dropped:
        log(f"ノイズ除外: {len(dropped)} インスタンス（< {min_voxels} voxels）")
    if not kept:
        raise FDIAssignError("有効なインスタンスがありません（--min-voxels を下げてください）。")
    log(f"インスタンス数: {len(kept)}")

    if arch == "both":
        groups = split_arches(kept, probs=probs, gap_mm=arch_gap_mm, log=log)
    else:
        groups = {"upper": [], "lower": []}
        groups[arch] = list(kept)
        log(f"上下顎判定: --arch {arch} の指定に従う")

    mapping: dict[int, int] = {}
    arch_of: dict[int, str] = {}
    costs: dict[str, float] = {}
    diagnostics: dict[str, ArchDiagnostics] = {}
    for name in ("upper", "lower"):
        members = groups[name]
        if not members:
            continue
        collected: list[ArchDiagnostics] = []
        part, cost = assign_arch(
            members, name,
            pair_stats=pair_stats,
            probs=probs,
            prob_weight=prob_weight,
            skip_penalty=skip_penalty,
            end_skip_penalty=end_skip_penalty,
            anchor_weight=anchor_weight,
            anchor_sigma_mm=anchor_sigma_mm,
            log=log,
            diagnostics_out=collected,
        )
        mapping.update(part)
        arch_of.update({label: name for label in part})
        costs[name] = cost
        if collected:
            diagnostics[name] = collected[-1]
            diag = collected[-1]
            log(
                f"{name}: confidence={diag.confidence} / ambiguous={diag.ambiguous}"
                + (f"（{' / '.join(diag.warnings)}）" if diag.warnings else "")
            )

    return AssignResult(
        mapping=mapping,
        arch_of=arch_of,
        instances=kept,
        dropped=dropped,
        costs=costs,
        diagnostics=diagnostics,
    )


def relabel(array: np.ndarray, mapping: dict[int, int]) -> np.ndarray:
    """インスタンスラベル配列を FDI 値の配列へ置換する（未割当は 0）。"""
    if not mapping:
        return np.zeros_like(array, dtype=np.int16)
    lut = np.zeros(int(max(int(array.max()), max(mapping))) + 1, dtype=np.int16)
    for label, fdi in mapping.items():
        if 0 <= label < lut.size:
            lut[label] = fdi
    clipped = np.clip(array.astype(np.int64), 0, lut.size - 1)
    return lut[clipped]


def build_sidecar(result: AssignResult, source: str, params: dict) -> dict:
    """`.labels.json` サイドカーの内容を組み立てる。

    `label_names` は「ラベル値 → 名称」の対応表だが、JSON の制約でキーは文字列。
    nifti_to_stl.py 側は int キーを期待するので、受け渡しには
    `nifti_to_stl.load_label_names()` を使い、
    `nifti_to_stl(..., label_names=names, label_values=sorted(names))` と渡す。
    詳細情報（歯番・象限・重心）は `labels`、不確実性指標は `diagnostics` に持つ。

    Args:
        result: assign_fdi の結果。
        source: 入力ファイル名（ベース名のみ。パスは含めない）。
        params: 実行時パラメータ。

    Returns:
        JSON 化可能な辞書。
    """
    by_label = {inst.label: inst for inst in result.instances}
    entries = []
    for label in sorted(result.mapping, key=lambda x: result.mapping[x]):
        fdi = result.mapping[label]
        inst = by_label[label]
        entries.append(
            {
                "value": fdi,
                "name": f"Tooth_{fdi}",
                "fdi": fdi,
                "quadrant": fdi // 10,
                "arch": result.arch_of[label],
                "instance_id": label,
                "voxels": inst.voxels,
                "centroid_mm": [round(float(v), 3) for v in inst.centroid],
            }
        )

    return {
        "format": "penclaw.dicom-to-stl.labels/1",
        "generator": "fdi_assign.py",
        "notation": "FDI (ISO 3950)",
        "source": os.path.basename(source),
        "label_names": {str(e["value"]): e["name"] for e in entries},
        "labels": entries,
        "arch_costs": {k: round(v, 3) for k, v in result.costs.items()},
        "confidence": result.confidence,
        "ambiguous": result.ambiguous,
        "diagnostics": {k: d.as_dict() for k, d in result.diagnostics.items()},
        "dropped_instances": sorted(result.dropped),
        "params": params,
    }


# --------------------------------------------------------------------------- #
# 確率ファイルの読み込み
# --------------------------------------------------------------------------- #

def load_probs(path: str, log: Log = _noop) -> dict[int, np.ndarray]:
    """歯クラス確率の .npz を読み込む。

    期待するキー:
      probs / probabilities / arr_0 : (N, 32) の確率行列（列は FDI_ALL の順）
      instance_ids                  : (N,) の対応するインスタンス ID。
                                      無い場合は 1..N を割り当てる。
      fdi_order                     : (32,) の列順。無い場合は FDI_ALL とみなす。

    Args:
        path: .npz のパス。
        log: ログ出力関数。

    Returns:
        インスタンス ID → 長さ 32 の確率ベクトル（FDI_ALL 順に並べ替え済み）。
    """
    with np.load(path, allow_pickle=False) as data:
        matrix = None
        for key in ("probs", "probabilities", "arr_0"):
            if key in data:
                matrix = np.asarray(data[key], dtype=np.float64)
                break
        if matrix is None:
            raise FDIAssignError(
                "--probs の npz に probs / probabilities / arr_0 が見つかりません。"
            )
        if matrix.ndim != 2 or matrix.shape[1] != len(FDI_ALL):
            raise FDIAssignError(
                f"--probs の確率行列は (N, {len(FDI_ALL)}) である必要があります"
                f"（実際: {matrix.shape}）。"
            )
        ids = (
            np.asarray(data["instance_ids"], dtype=np.int64)
            if "instance_ids" in data
            else np.arange(1, matrix.shape[0] + 1, dtype=np.int64)
        )
        if "fdi_order" in data:
            src_order = [int(v) for v in np.asarray(data["fdi_order"]).reshape(-1)]
            index = [src_order.index(f) for f in FDI_ALL]
            matrix = matrix[:, index]

    log(f"歯クラス確率を読み込み: {matrix.shape[0]} インスタンス")
    return {int(i): matrix[row] for row, i in enumerate(ids)}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def default_sidecar_path(out_path: str) -> str:
    """出力 NIfTI パスから `.labels.json` サイドカーのパスを導く。"""
    base = out_path
    for suffix in (".nii.gz", ".nii"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return f"{base}.labels.json"


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="歯インスタンスセグメンテーションへの FDI 歯番自動付与"
    )
    p.add_argument("--instances", required=True, help="入力インスタンスラベル NIfTI")
    p.add_argument("--out", required=True, help="出力 FDI ラベル NIfTI")
    p.add_argument(
        "--arch", default="both", choices=["upper", "lower", "both"],
        help="対象の顎。both(既定)は上下を自動分離する",
    )
    p.add_argument("--probs", default=None, help="歯クラス確率 .npz（任意）")
    p.add_argument("--pair-stats", default=None, help="本家互換の FDI ペア統計 JSON（任意）")
    p.add_argument("--labels-json", default=None, help="サイドカーの出力先（既定は --out から導出）")
    p.add_argument(
        "--min-voxels", type=int, default=DEFAULT_MIN_VOXELS,
        help=f"このボクセル数未満のインスタンスを除外（既定 {DEFAULT_MIN_VOXELS}）",
    )
    p.add_argument(
        "--prob-weight", type=float, default=DEFAULT_PROB_WEIGHT,
        help=f"歯クラス確率項の重み（既定 {DEFAULT_PROB_WEIGHT}）",
    )
    p.add_argument(
        "--anchor-weight", type=float, default=DEFAULT_ANCHOR_WEIGHT,
        help=f"正中アンカー（絶対位置）項の重み。0 で無効（既定 {DEFAULT_ANCHOR_WEIGHT}）",
    )
    p.add_argument(
        "--anchor-sigma-mm", type=float, default=DEFAULT_ANCHOR_SIGMA_MM,
        help=f"絶対位置項の許容標準偏差 mm（既定 {DEFAULT_ANCHOR_SIGMA_MM}）",
    )
    p.add_argument(
        "--skip-penalty", type=float, default=DEFAULT_SKIP_PENALTY,
        help=f"歯列内部の欠損 1 歯あたりのペナルティ（既定 {DEFAULT_SKIP_PENALTY}）",
    )
    p.add_argument(
        "--end-skip-penalty", type=float, default=DEFAULT_END_SKIP_PENALTY,
        help=f"歯列端の欠損 1 歯あたりのペナルティ（既定 {DEFAULT_END_SKIP_PENALTY}）",
    )
    p.add_argument(
        "--arch-gap-mm", type=float, default=DEFAULT_ARCH_GAP_MM,
        help=f"上下顎を分離する最小ギャップ mm（既定 {DEFAULT_ARCH_GAP_MM}）",
    )
    p.add_argument(
        "--fail-on-ambiguous", action="store_true",
        help="ambiguous 判定なら終了コード 5 で終わる（出力は書き出したうえで通知）",
    )
    p.add_argument("--quiet", action="store_true", help="進捗ログを抑制する")
    return p.parse_args(list(argv) if argv is not None else None)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)
    log: Log = _noop if args.quiet else _log

    try:
        volume = read_label_volume(args.instances)
        probs = load_probs(args.probs, log) if args.probs else None
        stats = load_pair_stats(args.pair_stats, log) if args.pair_stats else None

        result = assign_fdi(
            volume.array,
            volume.affine,
            arch=args.arch,
            probs=probs,
            pair_stats=stats,
            min_voxels=args.min_voxels,
            prob_weight=args.prob_weight,
            skip_penalty=args.skip_penalty,
            end_skip_penalty=args.end_skip_penalty,
            anchor_weight=args.anchor_weight,
            anchor_sigma_mm=args.anchor_sigma_mm,
            arch_gap_mm=args.arch_gap_mm,
            log=log,
        )

        write_label_volume(args.out, relabel(volume.array, result.mapping), volume)

        sidecar_path = args.labels_json or default_sidecar_path(args.out)
        sidecar = build_sidecar(
            result,
            args.instances,
            {
                "arch": args.arch,
                "min_voxels": args.min_voxels,
                "prob_weight": args.prob_weight,
                "skip_penalty": args.skip_penalty,
                "end_skip_penalty": args.end_skip_penalty,
                "anchor_weight": args.anchor_weight,
                "anchor_sigma_mm": args.anchor_sigma_mm,
                "arch_gap_mm": args.arch_gap_mm,
                "pair_stats": "external" if stats else "builtin-ideal-arch",
                "probs": bool(probs),
            },
        )
        with open(sidecar_path, "w", encoding="utf-8") as fh:
            json.dump(sidecar, fh, ensure_ascii=False, indent=2)
            fh.write("\n")

        log(f"=== 完了: {len(result.mapping)} 本に FDI を付与 ===")
        log(f"  {os.path.basename(args.out)}")
        log(f"  {os.path.basename(sidecar_path)}")
        log(f"  confidence={result.confidence} / ambiguous={result.ambiguous}")

        if result.ambiguous and args.fail_on_ambiguous:
            print(
                "警告: 割当が ambiguous です（--fail-on-ambiguous 指定のため 5 で終了）。",
                file=sys.stderr,
            )
            return 5
    except FDIAssignError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"想定外のエラー: {exc}", file=sys.stderr)
        return 99
    return 0


if __name__ == "__main__":
    sys.exit(main())
