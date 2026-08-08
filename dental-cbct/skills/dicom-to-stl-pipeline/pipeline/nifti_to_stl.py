"""NIfTI セグメンテーション → ラベル別 STL 変換。

DICOM_to_STL アプリ (v4.4) の nifti_to_stl.py を踏襲。
marching cubes でメッシュ抽出 → Taubin λ/μ 平滑化 → 法線を外向きに整えて
バイナリ STL を書き出す。個人情報は一切扱わない（ボクセルラベルのみ）。
"""

from __future__ import annotations

import json
import os
from typing import Optional

import numpy as np
import SimpleITK as sitk
from scipy import sparse
from skimage import measure
import vtk
from vtk.util import numpy_support


# Dataset111_453CT の 5 ラベル定義（学習時のラベルに対応）
LABEL_NAMES: dict[int, str] = {
    1: "Upper_Skull",       # 上顎・頭蓋
    2: "Mandible",          # 下顎骨
    3: "Upper_Teeth",       # 上顎歯列
    4: "Lower_Teeth",       # 下顎歯列
    5: "Mandibular_canal",  # 下顎管
}


def load_label_names(sidecar_path: str) -> dict[int, str]:
    """`.labels.json` サイドカーの `label_names` を int キーの辞書として読む。

    fdi_assign.py が書き出すサイドカーの `label_names` は JSON の制約で
    **キーが文字列**（例 `{"11": "Tooth_11"}`）になっている。一方
    `nifti_to_stl()` が扱うラベル値は int なので、そのまま渡すと一致せず
    `label_11.stl` のような既定名にフォールバックしてしまう。ここで
    int へ変換して橋渡しする。

    Args:
        sidecar_path: `.labels.json` のパス。`label_names` を持つ JSON なら可。

    Returns:
        ラベル値(int) → 名称 の辞書。`nifti_to_stl(..., label_names=...)` と
        `label_values=sorted(names)` にそのまま渡せる。

    Raises:
        ValueError: `label_names` が無い、または数値でないキーを含む場合。
    """
    with open(sidecar_path, "r", encoding="utf-8") as handle:
        sidecar = json.load(handle)

    raw = sidecar.get("label_names") if isinstance(sidecar, dict) else None
    if not isinstance(raw, dict):
        raise ValueError(
            f"サイドカーに label_names（辞書）がありません: {sidecar_path}"
        )

    names: dict[int, str] = {}
    for key, value in raw.items():
        try:
            label = int(key)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"label_names のキーが整数に変換できません: {key!r}"
            ) from exc
        names[label] = str(value)
    return names


# --------------------------------------------------------------------------- #
# 平滑化プリセット
#
# 原典: G. Taubin, "A Signal Processing Approach to Fair Surface Design",
#   SIGGRAPH '95. λ ステップ（縮む）と μ ステップ（膨らむ, μ < -λ < 0）を交互に
#   適用し、ラプラシアン平滑化で起きる体積収縮を打ち消す。
# プリセット構成（slicer_like 既定 / medium / strong と、細管・小構造の反復数上限）は
#   TotalSegmentator Wrapper for Mac 0.4.1 の surface_preview.py の設計方針を参考に、
#   仕様から再実装したもの（コードの複製ではない）。
# λ=0.5 / μ=-0.53 は 3D Slicer の Taubin 既定に相当する値。
# --------------------------------------------------------------------------- #
SMOOTH_PRESETS: dict[str, dict[str, float]] = {
    "none": {"iterations": 0, "lamb": 0.5, "mu": -0.53},
    "slicer_like": {"iterations": 10, "lamb": 0.5, "mu": -0.53},
    "medium": {"iterations": 20, "lamb": 0.5, "mu": -0.53},
    "strong": {"iterations": 30, "lamb": 0.5, "mu": -0.53},
}
DEFAULT_SMOOTH_PRESET = "slicer_like"

# 細い管状構造・小構造は平滑化で痩せる／消えるため、反復数に上限をかける。
FRAGILE_LABEL_KEYWORDS = ("pulp", "canal")  # 歯髄・（下顎）管。名前で判定
FRAGILE_MAX_ITERATIONS = 3
DEFAULT_SMALL_LABEL_VOXELS = 500  # これ未満のボクセル数のラベルも小構造扱い

# 平滑化の適用結果を残すサイドカー JSON（PHI は含まない：ラベル名と件数のみ）
SMOOTH_INFO_FILENAME = "smoothing_info.json"


def _build_uniform_adjacency(
    faces: np.ndarray, num_vertices: int
) -> "sparse.csr_matrix":
    """面リストから双方向エッジの疎隣接行列（行正規化・一様重み）を作る。

    Args:
        faces: 三角形の頂点インデックス (F, 3)。
        num_vertices: 頂点数。

    Returns:
        行和が 1 の疎行列。`adj @ verts` が各頂点の 1 近傍平均になる。
    """
    tri = np.asarray(faces, dtype=np.int64)
    edges = np.vstack([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]])
    both = np.vstack([edges, edges[:, ::-1]])  # 双方向化（無向グラフ）
    adjacency = sparse.coo_matrix(
        (np.ones(both.shape[0], dtype=np.float64), (both[:, 0], both[:, 1])),
        shape=(num_vertices, num_vertices),
    ).tocsr()
    adjacency.data[:] = 1.0  # 重複エッジを 1 に潰す（隣接回数で重み付けしない）
    degree = np.asarray(adjacency.sum(axis=1)).ravel()
    degree[degree == 0] = 1.0  # 孤立点は自分の位置を保つ
    return sparse.diags(1.0 / degree) @ adjacency


def taubin_smooth(
    verts: np.ndarray,
    faces: np.ndarray,
    iterations: int,
    lamb: float = 0.5,
    mu: float = -0.53,
) -> np.ndarray:
    """Taubin λ/μ 平滑化（Taubin 1995）。頂点だけを動かし面の接続は変えない。

    λ ステップで縮ませ、直後に μ ステップ（μ < -λ < 0）で膨らませることで、
    ラプラシアン平滑化に固有の体積収縮を相殺する。

    Args:
        verts: 頂点座標 (V, 3)。
        faces: 三角形の頂点インデックス (F, 3)。
        iterations: λ/μ の組を繰り返す回数。0 以下なら入力をそのまま返す。
        lamb: λ（正）。1 回あたりの平滑化の強さ。
        mu: μ（負・|μ| > λ）。膨らませ戻す量。

    Returns:
        平滑化後の頂点座標 (V, 3)。トポロジーは不変（watertight 性を壊さない）。
    """
    smoothed = np.array(verts, dtype=np.float64, copy=True)
    if iterations <= 0 or smoothed.size == 0 or len(faces) == 0:
        return np.ascontiguousarray(smoothed)

    adjacency = _build_uniform_adjacency(faces, smoothed.shape[0])
    for _ in range(int(iterations)):
        smoothed += lamb * (adjacency @ smoothed - smoothed)  # 縮むステップ
        smoothed += mu * (adjacency @ smoothed - smoothed)    # 膨らむステップ
    return np.ascontiguousarray(smoothed)


def resolve_smoothing_params(
    preset: str = DEFAULT_SMOOTH_PRESET,
    iterations: Optional[int] = None,
    lamb: Optional[float] = None,
    mu: Optional[float] = None,
) -> tuple[int, float, float]:
    """プリセットに個別指定を上書きして (iterations, λ, μ) を決める。

    Raises:
        ValueError: 未知のプリセット名、または λ/μ/iterations が不正な場合。
    """
    if preset not in SMOOTH_PRESETS:
        raise ValueError(
            f"未知の平滑化プリセット: {preset}（選択肢: {', '.join(SMOOTH_PRESETS)}）"
        )
    base = SMOOTH_PRESETS[preset]
    resolved_iterations = int(base["iterations"] if iterations is None else iterations)
    resolved_lamb = float(base["lamb"] if lamb is None else lamb)
    resolved_mu = float(base["mu"] if mu is None else mu)

    if resolved_iterations < 0:
        raise ValueError("--smooth-iterations は 0 以上で指定してください。")
    if resolved_lamb <= 0:
        raise ValueError("λ（--smooth-lambda）は正の値で指定してください。")
    if resolved_mu >= 0:
        raise ValueError("μ（--smooth-mu）は負の値で指定してください。")
    if abs(resolved_mu) <= resolved_lamb:
        print(
            "警告: |μ| <= λ です。Taubin 法では体積収縮を打ち消せません"
            f"（λ={resolved_lamb}, μ={resolved_mu}）。"
        )
    return resolved_iterations, resolved_lamb, resolved_mu


def resolve_label_iterations(
    label_name: str,
    voxel_count: int,
    iterations: int,
    small_label_voxels: int = DEFAULT_SMALL_LABEL_VOXELS,
) -> tuple[int, Optional[str]]:
    """ラベル別の例外規則。細い管状構造・小構造は反復数に上限をかける。

    下顎管（Mandibular_canal）や歯髄（pulp）のような細い構造、および
    ボクセル数が閾値未満の小構造は、平滑化で痩せる／消えるため
    反復数を FRAGILE_MAX_ITERATIONS 以下に制限する。

    Returns:
        (適用する反復数, 制限理由。制限なしなら None)
    """
    reasons: list[str] = []
    lowered = label_name.lower()
    if any(keyword in lowered for keyword in FRAGILE_LABEL_KEYWORDS):
        reasons.append("fragile_name")
    if voxel_count < small_label_voxels:
        reasons.append("small_volume")
    limited = min(iterations, FRAGILE_MAX_ITERATIONS)
    if not reasons or limited == iterations:
        return iterations, None  # 該当なし、または元から上限以下なら制限扱いにしない
    return limited, "+".join(reasons)


def _signed_volume(verts: np.ndarray, faces: np.ndarray) -> float:
    """三角メッシュの符号付き体積。負なら法線が反転している。"""
    tri = verts[faces]
    ref = tri.mean(axis=(0, 1), keepdims=True)
    tri -= ref
    v0, v1, v2 = tri[:, 0], tri[:, 1], tri[:, 2]
    return float(np.einsum("ij,ij->i", v0, np.cross(v1, v2)).sum() / 6.0)


def _orient_polydata_outward(poly: "vtk.vtkPolyData") -> "vtk.vtkPolyData":
    """法線の平均向きを重心から評価し、内向きが多数なら反転する。"""
    if poly is None or poly.GetNumberOfPoints() == 0:
        return poly

    point_data = poly.GetPointData()
    normal_array = point_data.GetNormals() if point_data is not None else None
    if normal_array is None:
        return poly

    normals_np = numpy_support.vtk_to_numpy(normal_array)
    if normals_np.size == 0:
        return poly

    points_np = numpy_support.vtk_to_numpy(poly.GetPoints().GetData())
    if points_np.size == 0:
        return poly

    centroid = points_np.mean(axis=0)
    vectors = points_np - centroid
    dots = np.einsum("ij,ij->i", normals_np, vectors)

    if np.mean(dots) >= 0:
        return poly

    flipper = vtk.vtkReverseSense()
    flipper.SetInputData(poly)
    flipper.ReverseCellsOn()
    flipper.ReverseNormalsOn()
    flipper.Update()
    flipped = flipper.GetOutput()

    normals = vtk.vtkPolyDataNormals()
    normals.SetInputData(flipped)
    normals.ConsistencyOn()
    normals.SplittingOff()
    normals.AutoOrientNormalsOn()
    normals.Update()
    return normals.GetOutput()


def nifti_to_stl(
    nifti_path: str,
    output_dir: str,
    label_values: list[int],
    smooth_preset: str = DEFAULT_SMOOTH_PRESET,
    smooth_iterations: Optional[int] = None,
    smooth_lambda: Optional[float] = None,
    smooth_mu: Optional[float] = None,
    small_label_voxels: int = DEFAULT_SMALL_LABEL_VOXELS,
    info_out: Optional[dict] = None,
    label_names: Optional[dict[int, str]] = None,
) -> list[str]:
    """NIfTI から指定ラベルの 3D メッシュを抽出し STL として保存する。

    Args:
        nifti_path: 入力 NIfTI（セグメンテーション）ファイルのパス。
        output_dir: STL 出力先ディレクトリ。
        label_values: 抽出対象のラベル値。
        smooth_preset: 平滑化プリセット（none / slicer_like / medium / strong）。
        smooth_iterations: 反復数の個別上書き。None ならプリセット値。
        smooth_lambda: λ の個別上書き。None ならプリセット値。
        smooth_mu: μ の個別上書き。None ならプリセット値。
        small_label_voxels: この値未満のボクセル数のラベルは小構造扱いで反復数を制限。
        info_out: 渡すと平滑化の適用結果を書き込む dict（同内容を JSON にも保存）。
        label_names: ラベル値 → STL ファイル名（拡張子なし）の対応表。省略時は
            モジュール定数 LABEL_NAMES（5 ラベル定義）を使う。FDI 歯番のような
            別のラベル体系を出力するときは、fdi_assign.py のサイドカーを
            `load_label_names()` で読んで渡す。未知のラベル値は従来どおり
            `label_<値>` にフォールバックする。

    Returns:
        書き出した STL ファイルパスのリスト。
    """
    names = LABEL_NAMES if label_names is None else label_names
    iterations, lamb, mu = resolve_smoothing_params(
        smooth_preset, smooth_iterations, smooth_lambda, smooth_mu
    )
    info: dict = info_out if info_out is not None else {}
    info.update(
        {
            "smooth_preset": smooth_preset,
            "smooth_iterations": iterations,
            "smooth_lambda": lamb,
            "smooth_mu": mu,
            "small_label_voxels": small_label_voxels,
            "fragile_max_iterations": FRAGILE_MAX_ITERATIONS,
            "labels": [],
        }
    )
    print(
        f"Smoothing preset '{smooth_preset}': iterations={iterations}, "
        f"lambda={lamb}, mu={mu}（小構造閾値 {small_label_voxels} voxels）"
    )

    print(f"Loading NIfTI: {os.path.basename(nifti_path)}")
    image = sitk.ReadImage(nifti_path)
    image_array = sitk.GetArrayFromImage(image)

    os.makedirs(output_dir, exist_ok=True)

    spacing_xyz = np.array(image.GetSpacing(), dtype=np.float64)
    origin_xyz = np.array(image.GetOrigin(), dtype=np.float64)
    direction = np.array(image.GetDirection(), dtype=np.float64).reshape(3, 3)
    spacing_for_mc = spacing_xyz[::-1]  # marching_cubes は (z, y, x)

    written: list[str] = []

    for label_value in label_values:
        label_name = names.get(label_value, f"label_{label_value}")
        output_stl_file = os.path.join(output_dir, f"{label_name}.stl")

        mask = image_array == label_value
        voxel_count = int(np.count_nonzero(mask))
        if voxel_count == 0:
            print(f"Warn: no voxels for label {label_value} ({label_name}), skip")
            continue

        label_iterations, limit_reason = resolve_label_iterations(
            label_name, voxel_count, iterations, small_label_voxels
        )
        if limit_reason:
            print(
                f"Smoothing limit for {label_name}: {iterations} -> {label_iterations} "
                f"iterations（理由: {limit_reason}, {voxel_count} voxels）"
            )
        info["labels"].append(
            {
                "label": label_value,
                "name": label_name,
                "voxels": voxel_count,
                "requested_iterations": iterations,
                "applied_iterations": label_iterations,
                "limit_reason": limit_reason,
            }
        )

        print(f"Marching cubes for {label_name}...")
        verts, faces, _, _ = measure.marching_cubes(
            mask.astype(np.uint8), level=0.5, spacing=spacing_for_mc
        )
        if len(verts) == 0 or len(faces) == 0:
            print(f"Warn: empty mesh for {label_name}, skip")
            continue

        verts_xyz = verts[:, ::-1]  # (z,y,x) -> (x,y,z)
        verts_physical = np.ascontiguousarray(
            (direction @ verts_xyz.T).T + origin_xyz
        )

        if label_iterations > 0:
            print(f"Taubin smoothing {label_name} ({label_iterations} iterations)...")
            verts_physical = taubin_smooth(
                verts_physical, faces, label_iterations, lamb, mu
            )
        else:
            print(f"Smoothing skipped for {label_name} (0 iterations)")

        if _signed_volume(verts_physical, faces) < 0:
            print(f"Orientation flipped for {label_name}; fixing winding")
            faces = faces[:, [0, 2, 1]]

        points = vtk.vtkPoints()
        points.SetData(numpy_support.numpy_to_vtk(verts_physical, deep=True))

        polys = vtk.vtkCellArray()
        vtk_faces = np.hstack((np.full((faces.shape[0], 1), 3), faces)).ravel()
        polys.SetCells(
            faces.shape[0],
            numpy_support.numpy_to_vtk(
                vtk_faces, deep=True, array_type=vtk.VTK_ID_TYPE
            ),
        )

        poly_data = vtk.vtkPolyData()
        poly_data.SetPoints(points)
        poly_data.SetPolys(polys)

        final_poly = poly_data
        if final_poly.GetNumberOfPoints() == 0:
            print(f"Warn: no mesh data for {label_name}, skip write")
            continue

        normals = vtk.vtkPolyDataNormals()
        normals.SetInputData(final_poly)
        normals.ConsistencyOn()
        normals.SplittingOff()
        normals.AutoOrientNormalsOn()
        normals.Update()

        oriented_poly = _orient_polydata_outward(normals.GetOutput())

        print(f"Writing STL: {os.path.basename(output_stl_file)}")
        writer = vtk.vtkSTLWriter()
        writer.SetFileName(output_stl_file)
        writer.SetInputData(oriented_poly)
        set_binary = getattr(writer, "SetFileModeToBinary", None)
        if callable(set_binary):
            set_binary()
        else:
            writer.SetFileTypeToBinary()
        if writer.Write() != 1 or not os.path.exists(output_stl_file):
            raise RuntimeError(f"STL の書き込みに失敗しました: {output_stl_file}")
        written.append(output_stl_file)

    info["stl_files"] = [os.path.basename(p) for p in written]
    info_path = os.path.join(output_dir, SMOOTH_INFO_FILENAME)
    with open(info_path, "w", encoding="utf-8") as handle:
        json.dump(info, handle, ensure_ascii=False, indent=2)
    print(f"Smoothing info written: {SMOOTH_INFO_FILENAME}")

    return written
