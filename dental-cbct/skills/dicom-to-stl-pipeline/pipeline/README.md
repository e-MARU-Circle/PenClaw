# pipeline/ — DICOM → STL 変換の実体

このフォルダのスクリプトがパイプライン本体です。**学習モデルと venv は同梱しません**
（各自で用意。`references/` の手順を参照）。

## 構成

| ファイル | 役割 |
|---|---|
| `run_pipeline.py` | DICOM → NIfTI(dcm2niix) → nnU-Netv2 推論 → STL を 1 コマンド実行 |
| `anonymize_dicom.py` | DICOM から PHI を除去（**二段プライバシーの 1 段目**） |
| `nifti_to_stl.py` | セグメンテーション NIfTI から 5 ラベル別 STL を生成（marching cubes + 平滑化） |
| `fdi_assign.py` | 歯のインスタンスセグメンテーションに FDI 歯番を自動付与（後述） |
| `requirements.txt` | 後段・匿名化の共通依存（torch/nnunetv2 は別途） |

## 標準フロー（匿名化 → 変換）

```bash
# 1) 匿名化（PHI 除去・元データは不変、別フォルダにコピー）
python3 anonymize_dicom.py --in /data/RAW_CASE --out /data/ANON_CASE --pseudo-id CASE001

# 2) 変換（匿名化済みのみ許可）
python3 run_pipeline.py \
    --in  /data/ANON_CASE \
    --out /data/STL_OUT \
    --model-dir /path/to/nnUNet_results \
    --device auto \
    --require-anonymized \
    --accept-disclaimer
```

出力: `/data/STL_OUT/CASE001_stl/` 配下に
`Upper_Skull.stl` `Mandible.stl` `Upper_Teeth.stl` `Lower_Teeth.stl` `Mandibular_canal.stl`。

## ラベル定義（DentalSegmentator 互換 / 5 クラス）

| ラベル | 名称 |
|---|---|
| 1 | Upper_Skull（上顎・頭蓋） |
| 2 | Mandible（下顎骨） |
| 3 | Upper_Teeth（上顎歯列） |
| 4 | Lower_Teeth（下顎歯列） |
| 5 | Mandibular_canal（下顎管） |

## FDI 歯番の自動付与（fdi_assign.py）

歯ごとに ID が振られたインスタンスラベル NIfTI を入力に、各歯へ FDI 歯番
（11-18 / 21-28 / 31-38 / 41-48）を割り当てる。上の 5 ラベル分割とは独立した
後処理で、`Upper_Teeth` / `Lower_Teeth` を歯単位に分けたあとに使う。

```bash
python3 fdi_assign.py --instances INSTANCES.nii.gz --out FDI.nii.gz --arch both
```

出力は FDI 値のマルチラベル NIfTI と、同名の `.labels.json` サイドカー。
主なオプションは `--probs`（歯クラス確率 npz）、`--pair-stats`（FDI ペア統計
JSON）、`--min-voxels`、`--arch upper|lower|both`。統計ファイルを与えない場合は
理想歯列モデルから内部生成した統計を使うので、外部依存なしで動く。

### 歯番名の STL を出す（サイドカー → nifti_to_stl.py）

サイドカーの `label_names` は JSON の制約でキーが文字列（`{"11": "Tooth_11"}`）
なので、`nifti_to_stl.load_label_names()` で int キーに変換してから渡す。渡さない
場合は従来どおり 5 ラベル定義（`LABEL_NAMES`）が使われ、FDI 値は `label_11.stl`
のような既定名になる。

```bash
# 1) FDI 付与（サイドカーも同時に出る）
python3 fdi_assign.py --instances INSTANCES.nii.gz --out FDI.nii.gz --arch both

# 2) 歯番名で STL 化（Tooth_11.stl … Tooth_48.stl が出る）
python3 -c "
import nifti_to_stl as n
names = n.load_label_names('FDI.labels.json')
n.nifti_to_stl('FDI.nii.gz', 'STL_OUT', sorted(names), label_names=names)
"
```

### 割当の信頼度（confidence / ambiguous）

残存歯が減ると精度が落ちる（実測: 欠損 3 本まで 100%、4-6 本で約 96%、8 本で
約 70%）ため、サイドカーに判断材料を載せている。下流は数値を読まずにこの 2 つで
「人の目視確認が要る症例」を選別できる。

| キー | 内容 |
|---|---|
| `confidence` | `high` / `medium` / `low`（上下顎で解いた場合は悪い方） |
| `ambiguous` | 対抗解（歯列の向きを逆にした解）とコストが拮抗して信用できない |
| `diagnostics.<arch>` | 対抗解とのコスト差（1 歯あたり）、歯列弓 frame の推定成否、正中アンカーの有無、使った統計の出所、欠損スキップ数、減点理由 |

`--fail-on-ambiguous` を付けると ambiguous 判定で終了コード `5` を返す
（`rescue_spacing.py` と同値。出力ファイルは書き出したうえで通知する）。
付けなければ従来どおり `0` / `1` / `2` / `99` のみ。

### `--pair-stats` の座標規約

外部統計の平均・共分散は**歯列弓ローカル frame**（x = 患者右が正、y = 前方が正、
単位 mm、値は重心の差ベクトルなので原点非依存）で表現されている必要がある。
読み込み時に単位スケール（隣接歯ペアの平均変位が 5-12mm か）と軸順・符号を
検査し、軸の入れ替え・符号反転なら**警告を出したうえで自動整合**、単位違いや
規約不明なら**警告して内蔵の理想歯列モデルにフォールバック**する。黙って精度を
落とすことはない。

検証（合成データ）は成果物に含めない。再現するには、楕円歯列弓上に球状の
インスタンスを 16 個配置したラベルマップを一時ディレクトリに作り、
`assign_fdi()` の戻り値が正しい FDI 列になるかを確認する。確認すべき観点は
(1) 16 歯フルの復元、(2) 欠損歯を含む場合の残存歯の歯番、(3) インスタンス ID の
順序をシャッフルしても結果が不変であること、(4) 残存歯を減らしたとき
`confidence` が下がること、(5) 座標規約を崩した `--pair-stats` で警告と
フォールバックが働くこと、の 5 点。

## 終了コード（run_pipeline.py）

`0`=成功 / `1`=免責未同意 / `2`=パイプラインエラー / `3`=PHI ガード違反 / `99`=想定外

## プライバシー上の約束

- どちらのスクリプトも **DICOM のタグ値・画素を標準出力に出さない**（ファイル名・件数・進捗のみ）。
- そのため、呼び出し側エージェントは中身を読まずに終了コードと出力ファイル名だけで完了確認できる。
- 後段の STL はボクセルラベル由来の形状のみで、患者氏名等は含まれない。
