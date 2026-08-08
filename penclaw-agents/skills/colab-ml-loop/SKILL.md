---
name: "colab-ml-loop"
description: "Colab Pro+ × verification-loop による3D歯科ML開発ループ。「Colab学習」「ML開発ループ」「Colabで学習」「colab-ml-loop」「PointNet学習」「nnUNet学習」「バックグラウンド学習」と言われたら発動。コード（penclaw-ml）と連携。"
---

# colab-ml-loop — Colab Pro+ × Verification Loop ML開発ループ

## 概要

Google Colab Pro+のバックグラウンド実行とverification-loop（6段階品質ゲート）を組み合わせた、3D歯科ML開発の自動化ワークフロー。

## 前提条件

- Google Colab Pro+契約（月500 CU、A100対応、24時間バックグラウンド実行）
- Googleドライブにデータセット格納済み
- Colab MCPサーバーがローカルMacで起動済み

## ワークフロー（5フェーズ）

### Phase 1: 環境構築

Colabノートブックを初期化し、GPU確認・ドライブマウント・依存パッケージインストールを実行。

### Phase 2: データ準備 + Gate 1-2

- **Gate 1 データ検証**: STL/DICOMの頂点数・法線・水密性チェック。FAIL時はfix_normals自動実行。
- **Gate 2 前処理検証**: NaN/Inf検出、正規化範囲、ラベル整合性。FAIL時は再サンプリング。

### Phase 3: 学習実行 + Gate 3

- バックグラウンド実行でブラウザ閉じてもOK（最大24時間）
- **Gate 3 学習検証**（5エポックごと）: 勾配消失/爆発チェック、loss収束監視
- 勾配爆発2回連続 → lr自動1/10縮小
- 30エポック改善なし → 早期終了
- チェックポイントを10エポックごとにドライブ保存

### Phase 4: 評価 + Gate 4-6

- **Gate 4 評価検証**: Dice≥0.85, IoU≥0.75, Hausdorff95≤5.0mm。FAIL時は閾値10%緩和（通知付き）。
- **Gate 5 出力検証**: 予測STLの水密性・自己交差チェック。FAIL時はfill_holes+fix_winding。
- **Gate 6 差分レビュー**: ベースラインとの回帰検出。劣化時はロールバック提案。

### Phase 5: 結果報告

学習完了後、Googleドライブ `DentalML/results/` に自動保存:
- best_model.pth（最良モデル）
- latest_metrics.json（評価メトリクス）
- checkpoint_ep*.pth（チェックポイント）
- training_log.json（学習ログ・ゲート結果）

## 失敗時の自動リトライルール

| ゲート | FAIL時の対応 | 2回連続FAIL |
|--------|-------------|-------------|
| Gate 1 データ | fix_normals / fix_inversion | ユーザーに報告 |
| Gate 2 前処理 | 再サンプリング・再正規化 | ユーザーに報告 |
| Gate 3 学習 | lr 1/10に縮小して再実行 | 早期終了＋報告 |
| Gate 4 評価 | 閾値10%緩和（通知付き） | ユーザーに報告 |
| Gate 5 出力 | fill_holes + fix_winding | ユーザーに報告 |
| Gate 6 差分 | 劣化項目をハイライト | ロールバック提案 |

## 対応モデル

- PointNet / PointNet++ — 歯牙セグメンテーション
- DGCNN — メッシュ分類
- nnU-Netv2 — DICOM/CTセグメンテーション
- カスタムモデル — テンプレートを改変して対応

## Colabノートブックテンプレート

コード（ML担当）が以下のテンプレートをベースにノートブックを生成:

```python
# Cell 1: 環境セットアップ
import torch
print(f"GPU: {torch.cuda.get_device_name(0)}")
print(f"VRAM: {torch.cuda.get_device_properties(0).total_mem / 1e9:.1f} GB")
from google.colab import drive
drive.mount('/content/drive')
!pip install open3d trimesh pytorch-lightning wandb nibabel

# Cell 2: データ検証（Gate 1）
# ... Gate 1のコード ...

# Cell 3: 前処理（Gate 2）
# ... Gate 2のコード ...

# Cell 4: 学習実行（Gate 3）- バックグラウンド実行
# ... 学習ループ + Gate 3のコード ...

# Cell 5: 評価（Gate 4-6）
# ... 評価 + 品質ゲートのコード ...

# Cell 6: 結果保存・レポート
# ... ドライブ保存 + サマリー出力 ...
```

## 使い方

1. 「Colabで PointNet2 の学習を回して」と指示
2. コード（ML担当）がノートブックを準備
3. Colab MCPでセルを投入・バックグラウンド実行
4. verification-loopが自動で品質チェック
5. 結果をGoogleドライブに保存、レポートを報告

