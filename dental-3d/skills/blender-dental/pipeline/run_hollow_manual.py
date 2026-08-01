"""手動トリム済みサーフェス → 中空模型フロー（MLなし）。

run_pipeline.py が「上下馬蹄形の自動化」（ML分割→口蓋カット）なのに対し、本フローは
**範囲設定（トリム・口蓋を含む/含まない）を人が済ませたSTL**を入力とし、
既存の土台リム延長→ブーリアン中空化（geometry_ops）だけを実行する。
上顎で口蓋を模型に残したいケースはビューア等でトリム→本フローへ。

工程順（確定 2026-07-06）: 本フローは中空化まで。**六角セル内部構造は後段の
run_hex_add.py で別工程として設置する**（中空化フローには含めない）。

CLI:
  python3 run_hollow_manual.py --in TRIMMED.stl --out model.stl
  オプション: --closed / --solid / --engrave CODE / --faces N（簡略化）
依存: trimesh, scipy, shapely, scikit-image, pymeshfix, manifold3d, mapbox_earcut
     （--faces 指定時のみ fast_simplification、--engrave 時のみ matplotlib）
"""
from __future__ import annotations

import argparse
import time

import numpy as np
import trimesh

import geometry_ops as g


def run(inp: str, out: str, *, rim_mm: float = 3.0, wall_mm: float = 2.0,
        pitch: float = 0.2, mode: str = "open", target_faces: int = 0,
        engrave: str | None = None,
        engrave_depth: float = 0.8, engrave_height: float | None = None,
        engrave_emboss: bool = False, engrave_azimuth: float = 0.0) -> dict:
    """トリム済みサーフェスを読み込み、土台リム→中空化→（刻印）→書き出し。六角は後段。"""
    t0 = time.time()
    mesh = trimesh.load(inp, process=True)
    mesh.merge_vertices()
    if target_faces and len(mesh.faces) > target_faces:      # 任意の簡略化
        import fast_simplification as fs
        vc, fc = fs.simplify(np.asarray(mesh.vertices, np.float32),
                             np.asarray(mesh.faces, np.int32), target_count=target_faces)
        mesh = trimesh.Trimesh(vc, fc, process=True)

    if mode == "solid":
        result, info = g.extrude_base(mesh, depth=rim_mm)
    else:
        result, info = g.make_hollow_open_model(
            mesh, rim_mm=rim_mm, wall_mm=wall_mm, pitch=pitch,
            open_bottom=(mode == "open"))

    if engrave:
        if not engrave_emboss and engrave_depth > wall_mm * 0.5:
            print(f"警告: 刻印深さ{engrave_depth}mmは壁厚{wall_mm}mmに対し深め。残肉に注意。")
        result, eg = g.engrave_case_code(
            result, engrave, info.get("base_dir_exact", info.get("base_dir", [0, 0, 1])),
            rim_mm=rim_mm, height_mm=engrave_height, depth_mm=engrave_depth,
            emboss=engrave_emboss, azimuth_deg=engrave_azimuth)
        info["engrave"] = eg

    result.export(out)
    info.update(flow="hollow_manual", mode=mode, out=out,
                faces=int(len(result.faces)),
                watertight=bool(result.is_watertight),
                seconds=round(time.time() - t0, 1))
    return info


def main() -> None:
    ap = argparse.ArgumentParser(description="手動トリム済みSTL → 中空模型（MLなし）")
    ap.add_argument("--in", dest="inp", required=True, help="トリム済みサーフェスSTL")
    ap.add_argument("--out", dest="out", required=True, help="出力STL")
    ap.add_argument("--rim", type=float, default=3.0, help="土台リム高さmm")
    ap.add_argument("--wall", type=float, default=2.0, help="肉厚mm")
    ap.add_argument("--pitch", type=float, default=0.2, help="内部キャビティ解像度mm（速度優先は0.4）")
    ap.add_argument("--faces", type=int, default=0, help="簡略化目標面数（0=簡略化なし）")
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--closed", action="store_true", help="底を閉じた中空にする")
    grp.add_argument("--solid", action="store_true", help="中空にせず中実土台にする")
    ap.add_argument("--engrave", default=None, help="症例コード刻印（匿名コードのみ）")
    ap.add_argument("--engrave-depth", type=float, default=0.8)
    ap.add_argument("--engrave-height", type=float, default=None)
    ap.add_argument("--engrave-emboss", action="store_true")
    ap.add_argument("--engrave-azimuth", type=float, default=0.0)
    a = ap.parse_args()
    mode = "solid" if a.solid else "closed" if a.closed else "open"
    info = run(a.inp, a.out, rim_mm=a.rim, wall_mm=a.wall, pitch=a.pitch, mode=mode,
               target_faces=a.faces, engrave=a.engrave,
               engrave_depth=a.engrave_depth, engrave_height=a.engrave_height,
               engrave_emboss=a.engrave_emboss, engrave_azimuth=a.engrave_azimuth)
    print("DONE:", info)
    if mode == "open":
        print("次工程: 六角セル内部構造は run_hex_add.py --in", a.out, "で設置")


if __name__ == "__main__":
    main()
