#!/bin/bash
# 刻印テスト一括（ダブルクリックで実行・約2分）
# 内容: 依存インストール → 刻印3種テスト（ヒラギノ自動検出/日本語/高画数漢字N-1確認）→ STLを開く
cd "$(dirname "$0")"
export LANG=ja_JP.UTF-8 LC_ALL=ja_JP.UTF-8   # ターミナルの文字化け対策
echo "=== 1/3 依存パッケージ確認（初回のみ数分） ==="
python3 -m pip install --quiet shapely trimesh scipy fast_simplification pymeshfix manifold3d mapbox_earcut matplotlib || {
  echo "❌ pip失敗。この画面をそのままカイに貼ってください"; read -p "Enterで閉じる"; exit 1; }
echo "=== 2/3 刻印テスト ==="
python3 - <<'EOF'
import os, trimesh, geometry_ops as g
# カイが数値検証できるよう司令室にも出力する（Downloadsはサンドボックス外のため）
VERIFY_DIR = "/Users/ema/Documents/Claude/Projects/PenClaw司令室/engrave_test"
os.makedirs(VERIFY_DIR, exist_ok=True)
# (ラベル, リム高mm): ①ASCII回帰 ②日本語＋ヒラギノ自動検出 ③高画数漢字＋リム3mm=N-1端欠け確認
tests = [("EM-2607-014", 3.0), ("初診 No.1234", 3.0), ("鬱", 3.0)]
for i, (label, rim) in enumerate(tests, 1):
    box = trimesh.creation.box(extents=[46, 25, 8])
    out, info = g.engrave_case_code(box, label, [0, 0, 1], rim_mm=rim)
    p = f"/Users/ema/Downloads/engrave_test_{i}.stl"
    out.export(p)
    out.export(os.path.join(VERIFY_DIR, f"engrave_test_{i}.stl"))
    print(f"OK ({i}/3)「{label}」 文字高={info['height_mm']}mm watertight={info['watertight']} → {p}")
print("\n✅ 全テスト完了。Downloadsの engrave_test_1〜3.stl の文字を目視確認してください。")
print("   見るポイント: ②が細字/豆腐でないか、③の上下端が欠けていないか")
EOF
[ $? -ne 0 ] && { echo "❌ テスト失敗。この画面をそのままカイに貼ってください"; read -p "Enterで閉じる"; exit 1; }
echo "=== 3/3 STLを開きます ==="
open /Users/ema/Downloads/engrave_test_2.stl 2>/dev/null || open /Users/ema/Downloads
echo ""
echo "この画面の出力とSTLの見た目（OK/NG）をカイに貼ってください。"
read -p "Enterで閉じる"
