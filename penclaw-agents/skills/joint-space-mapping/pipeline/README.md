# joint-space-mapping / pipeline

2メッシュ間の距離（関節スペース）をカラーマップ VTP として生成するヘッドレス CLI。
JointSpaceVisualizer v2.1.0 の距離計算・カラースケール部分を GUI 非依存で再構成したもの。

## セットアップ

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python3 run_jointspace.py --check          # 依存点検
```

## 実行

```bash
python3 run_jointspace.py \
    --source condyle.stl --target fossa.stl \
    --out jointspace_map.vtp \
    --source-only-decimate --reduction 0.5 \
    --stats-json stats.json --legend-png legend.png \
    --accept-disclaimer
```

- 入力: STL / PLY / OBJ / VTP など pyvista が読める表面メッシュ2枚（同一座標系）。
- 出力: `Distance` スカラーと焼き込み `RGB` を持つカラー VTP。元アプリの比較タブで開ける。

詳細は親ディレクトリの `SKILL.md` と `../references/` を参照。
