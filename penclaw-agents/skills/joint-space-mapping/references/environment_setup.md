# 環境構築

ヘッドレス距離マップ生成には GUI 依存（PyQt5 / pyvistaqt）は不要。距離計算とカラー化に
必要な `pyvista / vtk / numpy / matplotlib` だけを入れる。

## venv（推奨・全OS共通）

```bash
cd skills_master/joint-space-mapping/pipeline
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
python3 run_jointspace.py --check
```

`--check` が「依存はそろっています」と出れば準備完了。

## よくあるつまずき

- **VTK のホイールが入らない**: Python 3.13 など最新版で vtk のビルド済みホイールが無いことがある。
  その場合は Python 3.11 / 3.12 の venv を使う（元アプリも 3.13 で運用していたが、配布環境では
  3.11–3.12 が無難）。
- **ヘッドレス環境で matplotlib がエラー**: 凡例 PNG 出力は `Agg` バックエンドを内部で強制
  しているので、ディスプレイ無しでも動く。
- **大きいメッシュで遅い**: `--source-only-decimate --reduction 0.5` から試す。
  `references/parameter_tuning.md` 参照。
