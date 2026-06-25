# dental-cbct プラグイン

歯科CT/CBCT処理の独立軸プラグイン。スキル **dicom-to-stl-pipeline** を提供する。
（口腔内スキャン(STL)軸の `dental-3d` とは別軸。生成 STL は dental-3d の下流へ繋げられる）

## 収録スキル

### dicom-to-stl-pipeline
歯科CT(DICOM)を AI で 5 ラベル（上顎/下顎骨/上顎歯/下顎歯/下顎管）に分割し STL を生成する
ヘッドレス・パイプライン。

```
# 1) 匿名化（PHI除去・元データ不変）
python3 pipeline/anonymize_dicom.py --in RAW_CASE --out ANON_CASE --pseudo-id CASE001
# 2) 変換（DICOM→NIfTI→nnU-Netv2→STL）
python3 pipeline/run_pipeline.py --in ANON_CASE --out STL_OUT \
    --model-dir /path/to/nnUNet_results --device auto --require-anonymized --accept-disclaimer
```

## 導入

Claude Code / Cowork のプラグイン設定で、本マーケットプレイス（`e-MARU-Circle/PenClaw`）を追加し `dental-cbct` を有効化。

## 前提（同梱しない外部依存）

- **学習モデル（nnU-Netv2 重み .pth）**: 各自で用意・配置。**再配布しない**。手順は
  `skills/dicom-to-stl-pipeline/references/model_acquisition.md`。
- **実行環境（venv）**: 各自で構築（Mac MPS/CPU・Win CUDA・汎用CPU）。手順は
  `skills/dicom-to-stl-pipeline/references/environment_setup.md`。
- **dcm2niix**: DICOM→NIfTI 変換バイナリ（pip 不可・別途）。
- Python依存: `skills/dicom-to-stl-pipeline/pipeline/requirements.txt`（torch/nnunetv2 は別途）。

## プライバシー

DICOM はヘッダに患者個人情報(PHI)を含む。本スキルは二段で分離する。
匿名化スクリプトで PHI を除去し、エージェントは DICOM の中身を読まずパス渡しのみで実行する。
スクリプトはタグ値を標準出力に出さない。詳細は `skills/dicom-to-stl-pipeline/SKILL.md`。
