"""
歯牙3Dライブラリ読み込み (blender-dental: ワークフローA)

models/upper|lower を走査し、1ファイル=1オブジェクトでインポート。
ファイル名 tooth_<FDI>_<英名>_crown.<ext> から FDI を抽出して命名・タグ付けし、
"Teeth_Library" コレクションへ格納する。

実行: Blender MCP の execute_blender_code に本ファイル内容を渡す。
依存: Blender 3.x / 4.x（インポータの新旧APIを自動判定）。
"""
import bpy
import os
import re

# === CONFIG ===
LIBRARY_DIR = "/Users/ema/Desktop/VScode/PenClaw/assets/3d_library/teeth/models"
COLLECTION_NAME = "Teeth_Library"
EXTS = (".stl", ".obj", ".glb", ".gltf", ".ply")


def _ensure_collection(name):
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(col)
    return col


def _import_file(path):
    """拡張子に応じてインポート。インポート直後に追加されたメッシュを返す。"""
    before = set(bpy.data.objects)
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".stl":
            try: bpy.ops.wm.stl_import(filepath=path)          # Blender 4.x
            except Exception: bpy.ops.import_mesh.stl(filepath=path)  # 3.x
        elif ext == ".obj":
            try: bpy.ops.wm.obj_import(filepath=path)
            except Exception: bpy.ops.import_scene.obj(filepath=path)
        elif ext in (".glb", ".gltf"):
            bpy.ops.import_scene.gltf(filepath=path)
        elif ext == ".ply":
            try: bpy.ops.wm.ply_import(filepath=path)
            except Exception: bpy.ops.import_mesh.ply(filepath=path)
    except Exception as e:
        print(f"[SKIP] import failed: {path} -> {e}")
        return []
    return [o for o in bpy.data.objects if o not in before]


def _fdi_from_name(filename):
    m = re.match(r"tooth_(\d{2})", filename)
    return m.group(1) if m else None


def run():
    col = _ensure_collection(COLLECTION_NAME)
    imported, skipped = 0, 0
    for root, _, files in os.walk(LIBRARY_DIR):
        for f in sorted(files):
            if not f.lower().endswith(EXTS):
                continue
            path = os.path.join(root, f)
            new_objs = _import_file(path)
            if not new_objs:
                skipped += 1
                continue
            fdi = _fdi_from_name(f)
            base = os.path.splitext(f)[0]
            for obj in new_objs:
                # 既存コレクションから外して Teeth_Library へ
                for c in list(obj.users_collection):
                    c.objects.unlink(obj)
                col.objects.link(obj)
                obj.name = f"tooth_{fdi}" if fdi else base
                if fdi:
                    obj["fdi"] = fdi
                obj["source_file"] = f
            imported += 1
    print(f"[DONE] imported={imported} skipped={skipped} into '{COLLECTION_NAME}'")


run()
