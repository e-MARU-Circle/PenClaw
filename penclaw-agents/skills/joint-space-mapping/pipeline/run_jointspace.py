#!/usr/bin/env python3
"""2メッシュ間の距離（関節スペース）カラーマップを 1 コマンドで生成するヘッドレス・パイプライン。

JointSpaceVisualizer v2.1.0（app/services/mesh_ops.py の compute_distance /
save_colored_mesh）の距離計算ロジックを GUI・PyQt 依存なしに再構成したもの。

距離の定義（元アプリと同一）:
  - vtkDistancePolyDataFilter の符号なし距離（source 各点 → target 表面の最近接距離[mm]）
  - SignedDistanceOff / ComputeSecondDistanceOff / ComputeCellCenterDistanceOff
    （逆方向・セル中心は計算しない＝約24倍高速・精度同一）

設計上の原則:
  - メッシュの座標・スカラーを標準出力に展開しない。min/max/mean と点数のみログする。
  - 2枚のメッシュは同一座標系に揃っている前提（未整列なら別途整列してから渡す）。

使い方の例:
  python3 run_jointspace.py --check
  python3 run_jointspace.py --source condyle.stl --target fossa.stl \
      --out jointspace_map.vtp --source-only-decimate --reduction 0.5 --accept-disclaimer

終了コード: 0=成功 / 1=免責未同意 or check失敗 / 2=エラー / 99=想定外
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

DISCLAIMER = (
    "本ソフトウェアは薬機法上の医療機器ではなく、研究用途に限定されます。"
    "診断・治療の根拠として使用しないでください。出力結果について一切の保証・"
    "責任を負いません。同意する場合は --accept-disclaimer を付けて実行してください。"
)


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# 環境プリフライト（メッシュに触れない）
# ---------------------------------------------------------------------------
def run_check() -> int:
    log("=== joint-space-mapping 環境点検 ===")
    ok = True
    for mod in ("numpy", "pyvista", "vtk", "matplotlib"):
        try:
            m = __import__(mod)
            ver = getattr(m, "__version__", "?")
            log(f"  [OK]   {mod} {ver}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            log(f"  [MISS] {mod}: {exc}")
    if ok:
        log("依存はそろっています。--source/--target を指定して実行できます。")
        return 0
    log("依存が不足しています。references/environment_setup.md を参照。")
    return 1


# ---------------------------------------------------------------------------
# 距離計算（元アプリ compute_distance を移植）
# ---------------------------------------------------------------------------
def clip_source_sphere(src, roi_sphere):
    """source を球状 ROI（中心 cx,cy,cz・半径 r）の内側だけにクリップする。

    距離は source 点 → target 表面の最近接なので、source を ROI で絞っても
    残した部位の距離値は厳密に不変。target は触らない（端効果を避けるため）。
    """
    import pyvista as pv
    import vtk

    cx, cy, cz, r = roi_sphere
    if r <= 0:
        raise ValueError(f"--roi-sphere の半径は正の値が必要です: r={r}")

    sphere = vtk.vtkSphere()
    sphere.SetCenter(cx, cy, cz)
    sphere.SetRadius(r)
    clipper = vtk.vtkClipPolyData()
    clipper.SetClipFunction(sphere)
    clipper.SetInputData(src)
    clipper.InsideOutOn()  # 球の内側を残す
    clipper.Update()
    clipped = pv.wrap(clipper.GetOutput())
    if clipped.n_points == 0:
        raise RuntimeError(
            f"球状ROIに source 点が1つも入りませんでした（中心({cx},{cy},{cz}) 半径{r}）。"
            "中心座標・半径を見直してください。"
        )
    log(f"ROI球クロップ: 中心({cx:.2f},{cy:.2f},{cz:.2f}) 半径{r:.2f} → source={clipped.n_points}点")
    return clipped


def clip_source_bounds(src, xmin, xmax, ymin, ymax, zmin, zmax):
    """source を軸平行ボックス（各軸の min/max・None は無制限）でクロップする。

    下顎頭のように「Z下限だけ」で領域を切り出す用途に。指定された軸条件だけが効く。
    source 側だけを絞るので残した部位の距離値は不変。
    """
    import numpy as np

    p = src.points
    mask = np.ones(len(p), dtype=bool)
    for ax, lo, hi in ((0, xmin, xmax), (1, ymin, ymax), (2, zmin, zmax)):
        if lo is not None:
            mask &= p[:, ax] >= lo
        if hi is not None:
            mask &= p[:, ax] <= hi
    if not mask.any():
        raise RuntimeError(
            "ボックスROIに source 点が1つも入りませんでした。範囲指定を見直してください。"
        )
    kept = src.extract_points(mask, adjacent_cells=True).extract_surface(
        algorithm="dataset_surface"
    )
    desc = ", ".join(
        f"{nm}[{lo if lo is not None else '-'},{hi if hi is not None else '-'}]"
        for nm, lo, hi in (("X", xmin, xmax), ("Y", ymin, ymax), ("Z", zmin, zmax))
    )
    log(f"ROIボックス({desc}) → source={kept.n_points}点")
    return kept


def clip_source_near_target(src, tgt, margin: float):
    """target 表面から margin[mm] 以内の source 点だけを自動抽出する（座標入力不要）。

    関節スペース＝2面が近接する部位、という定義そのものを使う。術者は「関節窩(target)から
    何mm以内を見るか」のマージン1個を決めるだけ。source 側だけを絞るので距離値は不変。

    2段で速く正確に:
      1) target のバウンディングボックス + margin で粗くマスク（numpy・高速）
      2) 残った点を target 表面までの距離 <= margin で精密化（vtkImplicitPolyDataDistance）
    """
    import numpy as np
    import vtk

    if margin <= 0:
        raise ValueError(f"--roi-near-target のマージンは正の値が必要です: {margin}")

    # 1) bbox + margin で粗クリップ（箱に入る source 点だけ残す）
    xmin, xmax, ymin, ymax, zmin, zmax = tgt.bounds
    p = src.points
    inside = (
        (p[:, 0] >= xmin - margin) & (p[:, 0] <= xmax + margin)
        & (p[:, 1] >= ymin - margin) & (p[:, 1] <= ymax + margin)
        & (p[:, 2] >= zmin - margin) & (p[:, 2] <= zmax + margin)
    )
    if not inside.any():
        raise RuntimeError(
            f"target 近傍（margin={margin}mm）に source 点がありません。"
            "2枚が同一座標系か、margin が小さすぎないか確認してください。"
        )
    coarse = src.extract_points(inside, adjacent_cells=True).extract_surface()

    # 2) target 表面距離 <= margin で精密化
    imp = vtk.vtkImplicitPolyDataDistance()
    imp.SetInput(tgt)
    cp = coarse.points
    dist = np.fromiter(
        (abs(imp.EvaluateFunction(float(x), float(y), float(z))) for x, y, z in cp),
        dtype=float, count=len(cp),
    )
    mask = dist <= margin
    if not mask.any():
        raise RuntimeError(
            f"target 表面 margin={margin}mm 以内に source 点が残りませんでした。margin を見直してください。"
        )
    kept = coarse.extract_points(mask, adjacent_cells=True).extract_surface()
    log(f"ROI自動(target近傍 margin={margin:.1f}mm) → source={kept.n_points}点")
    return kept


def compute_distance_map(
    source_path: str,
    target_path: str,
    reduction: Optional[float],
    source_only_decimate: bool,
    roi_sphere: Optional[list] = None,
    roi_near_target: Optional[float] = None,
    roi_bounds: Optional[tuple] = None,
):
    import pyvista as pv
    import vtk

    if not os.path.isfile(source_path):
        raise FileNotFoundError(f"source が見つかりません: {source_path}")
    if not os.path.isfile(target_path):
        raise FileNotFoundError(f"target が見つかりません: {target_path}")

    src = pv.read(source_path)
    tgt = pv.read(target_path)
    log(f"読込: source={src.n_points}点 / target={tgt.n_points}点")

    # ROI クロップは間引きより先（点を早く捨てるほど速い・結果不変）
    if roi_bounds is not None:
        src = clip_source_bounds(src, *roi_bounds)
    elif roi_near_target is not None:
        src = clip_source_near_target(src, tgt, roi_near_target)
    elif roi_sphere is not None:
        src = clip_source_sphere(src, roi_sphere)

    if reduction is not None and reduction > 0:
        src = src.decimate(reduction)
        if source_only_decimate:
            log(f"デシメーション: source のみ reduction={reduction}")
        else:
            tgt = tgt.decimate(reduction)
            log(f"デシメーション: 両メッシュ reduction={reduction}")

    # 元アプリと同一の高速・符号なし設定
    dist_filter = vtk.vtkDistancePolyDataFilter()
    dist_filter.SetInputData(0, src)
    dist_filter.SetInputData(1, tgt)
    dist_filter.SignedDistanceOff()
    dist_filter.ComputeSecondDistanceOff()
    dist_filter.ComputeCellCenterDistanceOff()
    dist_filter.Update()

    result = pv.wrap(dist_filter.GetOutput())
    distances = result.get_array("Distance")
    return result, distances


# ---------------------------------------------------------------------------
# カラー焼き込み（元アプリ save_colored_mesh を移植）
# ---------------------------------------------------------------------------
def _set_rgb_as_colors(mesh, name: str = "RGB") -> None:
    """uchar3成分の色配列を active scalars にマークし、ビューアが直接色として扱えるようにする。

    これをしないと、保存された VTP は Distance が active のままになり、ParaView 等は
    Distance を独自カラーマップで表示してしまう（焼き込んだ TMJ 配色が無視される）。
    """
    try:
        mesh.GetPointData().SetActiveScalars(name)
    except Exception:  # noqa: BLE001 - ビューア表示の最適化なので失敗しても保存は続行
        logger_warn = f"active scalars を {name} に設定できませんでした"
        log(logger_warn)


def bake_colors_and_save(result, lut, out_path: str, scale, embed_scale: bool) -> None:
    import numpy as np

    distances = result.get_array("Distance")
    if distances is None or len(distances) == 0:
        raise RuntimeError("距離スカラーが空です。メッシュの整列・形状を確認してください。")

    colored = result.copy()
    rng_min, rng_max = lut.scalar_range
    if rng_max <= rng_min:
        rng_min, rng_max = float(np.min(distances)), float(np.max(distances))
        if rng_max <= rng_min:
            rng_max = rng_min + 1.0
    norm = (np.asarray(distances) - rng_min) / (rng_max - rng_min)
    norm = np.clip(norm, 0.0, 1.0)
    rgba = lut.cmap(norm)
    colored.point_data["RGB"] = (rgba[:, :3] * 255).astype(np.uint8)
    # 重要: 焼き込んだ色を「active scalars」にする。これをしないと汎用ビューア
    # （ParaView 等）は Distance スカラーを独自カラーマップで表示してしまい、
    # 意図した TMJ 配色（近接=赤）にならない（Distance と Z が相関するため Z 着色に見える）。
    # VTK が uchar3 成分を「直接 RGB」として扱えるよう色配列としてマークする。
    _set_rgb_as_colors(colored, "RGB")

    if embed_scale and out_path.lower().endswith(".vtp"):
        from color_scale import embed_scale_in_mesh

        embed_scale_in_mesh(colored, scale)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    if out_path.lower().endswith(".ply"):
        # 頂点カラーPLY: uchar RGB を頂点色としてネイティブ保存。MeshLab/Blender 等の
        # 汎用ビューアが設定なしで直接色を表示する（VTP より互換性が高い）。
        colored.save(out_path, texture="RGB")
    else:
        colored.save(out_path, binary=True)
    log(f"保存: {out_path}")


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="2メッシュ間距離（関節スペース）カラーマップ生成")
    p.add_argument("--check", action="store_true", help="依存の準備状況だけ点検して終了")
    p.add_argument("--source", help="基準メッシュ（距離・色はこの上に乗る）")
    p.add_argument("--target", help="対向メッシュ")
    p.add_argument("--out", help="出力 VTP パス（.vtp 推奨）")
    p.add_argument("--reduction", type=float, default=None,
                   help="デシメーション率 0.0–0.95（大きいほど高速・粗い）")
    p.add_argument("--source-only-decimate", action="store_true",
                   help="source だけ間引く（精度を保ちつつ高速）")
    p.add_argument("--roi-near-target", nargs="?", type=float, const=10.0, default=None,
                   metavar="MARGIN_MM",
                   help="【自動ROI・推奨】target表面から MARGIN mm 以内の source だけ自動抽出"
                        "（座標入力不要）。フラグのみで既定10mm、値を付ければその値")
    p.add_argument("--roi-sphere", nargs=4, type=float, metavar=("CX", "CY", "CZ", "R"),
                   default=None,
                   help="球状ROIで source を限定（中心 cx cy cz・半径 r[mm]）。手動指定版。"
                        "source側のみ絞るので残した部位の距離は不変・高速化")
    # 軸平行ボックスROI（各軸の min/max は任意。指定したものだけ効く）
    p.add_argument("--roi-xmin", type=float, default=None, metavar="MM", help="ボックスROI: X下限")
    p.add_argument("--roi-xmax", type=float, default=None, metavar="MM", help="ボックスROI: X上限")
    p.add_argument("--roi-ymin", type=float, default=None, metavar="MM", help="ボックスROI: Y下限")
    p.add_argument("--roi-ymax", type=float, default=None, metavar="MM", help="ボックスROI: Y上限")
    p.add_argument("--roi-zmin", type=float, default=None, metavar="MM",
                   help="ボックスROI: Z下限（例: 下顎頭抽出 --roi-zmin 80）")
    p.add_argument("--roi-zmax", type=float, default=None, metavar="MM", help="ボックスROI: Z上限")
    p.add_argument("--scale-json", default=None, help="カラースケール定義 JSON（未指定で組込みTMJ）")
    p.add_argument("--max-distance", type=float, default=None, help="スケール上限[mm]を上書き")
    p.add_argument("--stats-json", default=None, help="距離統計 JSON の出力先")
    p.add_argument("--legend-png", default=None, help="スケール凡例 PNG の出力先")
    p.add_argument("--no-embed-scale", action="store_true", help="VTPへのスケール埋め込みを無効化")
    p.add_argument("--accept-disclaimer", action="store_true", help="研究用途・免責に同意")
    return p


def main(argv: Optional[list] = None) -> int:
    # color_scale.py を同ディレクトリから import できるように
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    args = build_parser().parse_args(argv)

    if args.check:
        return run_check()

    if not (args.source and args.target and args.out):
        log("エラー: --source / --target / --out は必須です（または --check）。")
        return 1

    if not args.accept_disclaimer:
        log(DISCLAIMER)
        return 1

    roi_bounds_vals = (
        args.roi_xmin, args.roi_xmax, args.roi_ymin, args.roi_ymax, args.roi_zmin, args.roi_zmax
    )
    roi_bounds = roi_bounds_vals if any(v is not None for v in roi_bounds_vals) else None
    n_roi = sum(x is not None for x in (roi_bounds, args.roi_near_target, args.roi_sphere))
    if n_roi > 1:
        log("エラー: ROI指定（--roi-bounds系 / --roi-near-target / --roi-sphere）は1系統のみ。")
        return 1

    try:
        from color_scale import build_lut, load_scale, save_legend_png

        scale = load_scale(args.scale_json, max_distance=args.max_distance)
        lut = build_lut(scale)
        log(f"スケール: {scale.name}（0–{scale.max_distance():.1f}mm）")

        result, distances = compute_distance_map(
            args.source, args.target, args.reduction, args.source_only_decimate,
            roi_sphere=args.roi_sphere,
            roi_near_target=args.roi_near_target,
            roi_bounds=roi_bounds,
        )

        import numpy as np

        if distances is None or len(distances) == 0:
            log("エラー: 距離スカラーが得られませんでした。")
            return 2
        dmin, dmax, dmean = float(np.min(distances)), float(np.max(distances)), float(np.mean(distances))
        log(f"距離[mm]: min={dmin:.3f} / max={dmax:.3f} / mean={dmean:.3f} / 点数={len(distances)}")

        bake_colors_and_save(result, lut, args.out, scale, embed_scale=not args.no_embed_scale)

        if args.stats_json:
            import json

            stats = {
                "source": os.path.basename(args.source),
                "target": os.path.basename(args.target),
                "scale": scale.name,
                "scale_max_mm": scale.max_distance(),
                "min_mm": dmin,
                "max_mm": dmax,
                "mean_mm": dmean,
                "n_points": int(len(distances)),
            }
            with open(args.stats_json, "w", encoding="utf-8") as f:
                json.dump(stats, f, ensure_ascii=False, indent=2)
            log(f"統計: {args.stats_json}")

        if args.legend_png:
            save_legend_png(scale, args.legend_png)
            log(f"凡例: {args.legend_png}")

        log("完了。")
        return 0

    except FileNotFoundError as exc:
        log(f"エラー: {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001
        log(f"想定外のエラー: {exc}")
        return 99


if __name__ == "__main__":
    raise SystemExit(main())
