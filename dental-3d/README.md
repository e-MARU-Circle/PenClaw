# dental-3d プラグイン

歯科3Dワークフロー用プラグイン。スキル **blender-dental** を提供する。

## 収録スキル

### blender-dental
1. **中空オープン模型パイプライン**（主力）: 口腔内スキャン(STL)から3Dプリント用の中空・オープン底模型を1コマンド生成。
   歯/歯肉ML分割 → 口蓋カット放物線平滑化 → 咬合平面に垂直な土台 → ブーリアン中空化 → 底開放。
   ```
   python3 pipeline/run_pipeline.py --in SCAN.stl --out model.stl --arch upper
   ```
2. **Blender歯牙ライブラリ運用**: 歯牙3Dライブラリの読み込み・FDI命名整列・歯冠のみ化・レンダリング確認。

## 導入

Claude Code / Cowork のプラグイン設定で、本マーケットプレイス（`e-MARU-Circle/PenClaw`）を追加し `dental-3d` を有効化。

## 前提（同梱しない外部依存）

- **Model Segmentator** リポジトリ＋学習済み重み（歯/歯肉分割モデル本体）。`--repo` で指定。
- Python依存: `skills/blender-dental/pipeline/requirements.txt`。
- 確認レンダリング時のみ Blender（MCPアドオン）。

詳細は `skills/blender-dental/SKILL.md` と `skills/blender-dental/pipeline/README.md` を参照。
