"""既存の中空オープン模型 → 六角セル内部構造の後付けフロー（独立）。

中空化フロー（run_pipeline.py / run_hollow_manual.py）とは分離した単独工程。
底が開いた中空模型STLを入力に、開口側を自動検出して六角セルプレートを結合する。

開口側の検出（2系統）:
  a) 境界エッジがある（真に開いたシェル）→ 咬合法線＋境界マスク（extrude_base規約）
  b) watertightなopen模型（本パイプラインのブーリアン切断出力＝リム断面が閉じている）
     → PCA最小軸の±両方向で「最遠平面上の平坦面積」を比較し、大きい側＝切断面（底）と判定。
     歯列側は凹凸で平坦面積が小さいため確実に区別できる。

CLI:
  python3 run_hex_add.py --in hollow_open.stl --out with_hex.stl
  オプション: --cell 4.0 --rib 1.2 --floor 2.0
依存: trimesh, scipy, shapely, scikit-image, pymeshfix, manifold3d
"""
from __future__ import annotations

import argparse
import time

import numpy as np
import trimesh

import geometry_ops as g


def detect_base_dir(mesh: trimesh.Trimesh):
    """底方向を検出。境界があれば咬合法線規約、なければ平坦切断面の面積比較で判定。"""
    V = np.asarray(mesh.vertices, np.float64)
    bmask, bnd = g._boundary_vertex_mask(mesh)
    if bnd:                                    # a) 真に開いたシェル
        return g.occlusal_normal(V, bmask), "boundary"
    # b) watertightなopen模型: PCA最小軸±で最遠平面の平坦面積を比較
    c = V.mean(axis=0)
    X = V - c
    _, vec = np.linalg.eigh(X.T @ X)
    axis = vec[:, 0]                           # 最小固有値の軸＝上下方向
    FN = mesh.face_normals
    FC = mesh.triangles_center
    FA = mesh.area_faces
    best = (0.0, None)
    for s in (1.0, -1.0):
        d = axis * s
        t = FC @ d
        m = (t > t.max() - 0.5) & (FN @ d > 0.9)   # 最遠平面±0.5mmの同方向平坦面
        area = float(FA[m].sum())
        if area > best[0]:
            best = (area, d)
    if best[1] is None or best[0] < 10.0:
        raise ValueError("開口（切断面）を検出できません。底開放（open）の中空模型を入力してください。")
    return best[1], f"flat_cut(area={best[0]:.0f}mm2)"


def run(inp: str, out: str, *, cell_mm: float = 4.0, rib_mm: float = 1.2,
        floor_mm: float = 2.0) -> dict:
    t0 = time.time()
    mesh = trimesh.load(inp, process=True)
    mesh.merge_vertices()
    bd, how = detect_base_dir(mesh)
    result, hx = g.add_hex_bottom(mesh, bd, floor_mm=floor_mm,
                                  cell_mm=cell_mm, rib_mm=rib_mm)
    hx["base_detect"] = how
    result.export(out)
    hx.update(flow="hex_add", out=out, faces=int(len(result.faces)),
              base_dir=[round(float(x), 3) for x in bd],
              seconds=round(time.time() - t0, 1))
    return hx


def main() -> None:
    ap = argparse.ArgumentParser(description="中空オープン模型に六角セル底板を後付け")
    ap.add_argument("--in", dest="inp", required=True, help="中空オープン模型STL")
    ap.add_argument("--out", dest="out", required=True, help="出力STL")
    ap.add_argument("--cell", type=float, default=4.0, help="六角セル径（対辺）mm")
    ap.add_argument("--rib", type=float, default=1.2, help="セル間リブ幅mm")
    ap.add_argument("--floor", type=float, default=2.0, help="底板厚mm")
    a = ap.parse_args()
    print("DONE:", run(a.inp, a.out, cell_mm=a.cell, rib_mm=a.rib, floor_mm=a.floor))


if __name__ == "__main__":
    main()
