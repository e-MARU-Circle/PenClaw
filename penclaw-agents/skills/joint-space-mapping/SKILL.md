---
name: joint-space-mapping
description: "対向する2つの3Dメッシュ（STL/PLY/OBJ/VTP）の面間距離＝関節スペースを計算し、TMJカラースケールで色付けした距離マップ(VTP)を1コマンドで生成するヘッドレス・パイプライン。JointSpaceVisualizerアプリ(v2.1.0)の距離計算ロジックをGUI非依存で再構成したもの。下顎頭⇄関節窩のクリアランス、咬合・補綴のギャップ、2スキャン間の偏差カラーマップなどに使う。「関節スペース」「ジョイントスペース」「joint space」「距離マップ」「距離カラーマップ」「2面間距離」「メッシュ間距離」「クリアランス」「顎関節」「TMJ」「下顎頭」「関節窩」「ギャップ可視化」「偏差マップ」「deviation map」「vtkDistancePolyDataFilter」「カラースケール」「JointSpaceVisualizer」「run_jointspace」と言われたら発動。3D実行の主担当はコード(penclaw-ml)。"
---

# joint-space-mapping — 2メッシュ間の距離（関節スペース）カラーマップ生成

対向する2つの3D表面（例：下顎頭と関節窩、補綴と支台、矯正前後スキャン）の**最近接距離**を
点ごとに計算し、距離→色のスケールで塗り分けた**距離マップVTP**をヘッドレスで生成する。
`JointSpaceVisualizer` アプリ（v2.1.0）の距離計算・カラースケール部分を GUI・PyQt 依存なしに
再構成したもの。3D実行の主担当は**コード（penclaw-ml）**。

入力は STL / PLY / OBJ / VTP などの表面メッシュ2枚。CT(DICOM) からの STL 化が必要なら
**dicom-to-stl-pipeline** を前段に置く。

## このスキルが「やらない」こと

- **GUI は持たない。** 元アプリの 3D ビューア・スケール編集ダイアログは移植しない。
  カラースケールは組込み TMJ スケール、または JSON で渡す。
- **MR↔CBCT のレジストレーション（位置合わせ）は含まない。** 元アプリの SimpleITK 連携は
  範囲外。2枚のメッシュは**同一座標系に揃っている前提**。揃っていなければ別途整列してから渡す
  （OpenMediCAD のランドマーク→ICP 整列等）。
- **臨床診断はしない。** 出力は幾何学的な距離であって、診断・治療の根拠ではない（研究用途限定）。

## 距離の定義（元アプリと同一・重要）

`vtkDistancePolyDataFilter` の**符号なし（unsigned）距離**。source の各点から target 表面への
最近接距離[mm]を求める。元アプリの高速化設定をそのまま踏襲する。

- `SignedDistanceOff()` — 符号なし（内外を区別しない、純粋なクリアランス）
- `ComputeSecondDistanceOff()` / `ComputeCellCenterDistanceOff()` — 逆方向・セル中心の距離は計算しない（**約24倍高速・精度同一**）
- 距離スカラーは出力メッシュの point_data `"Distance"` に入る

座標単位はメッシュの単位そのまま。歯科スキャンは通常 mm なので距離も mm。

## 起動時チェック

0. **まず環境点検**: `python3 pipeline/run_jointspace.py --check` を実行。メッシュに触れず、
   依存（pyvista / vtk / numpy / matplotlib）の準備状況だけを報告する。
1. 依存が未構築なら `references/environment_setup.md` の手順を案内（venv 推奨）。
2. 入力2枚が**同一座標系か**を確認。別スキャン由来で未整列なら整列が先（範囲外・上記参照）。
3. **マッピング先を必ず質問で確認する（必須）。** 距離と色は source 上に乗る。どちらの
   モデルに乗せるかは臨床意図で変わるため、勝手に決めず **AskUserQuestion で術者に聞いてから**
   source/target を割り当てる。詳細は次節。
4. スケールを選ぶ。既定は組込み **TMJベーシックスケール**（0–5mm）。別レンジが要るなら
   `references/color_scale_guide.md` を見て JSON を用意。

## 実行前の必須確認：どちらのモデルにマッピングするか

距離は **source** の各点 → target 表面の最近接距離として計算され、**色も source の上に乗る**
（target は距離を測る相手で、色は付かない）。つまり「どちらのモデルに距離マップを出すか」＝
「どちらを source にするか」。これは読影意図で変わるので、**実行前に必ず `AskUserQuestion`
で術者に質問し**、回答に応じて `--source` / `--target` を割り当てる。Claude が勝手に決めない。

質問の例（2枚が下顎頭・関節窩の場合）:

| 術者の選択 | `--source`（色が乗る面） | `--target`（基準面） |
|---|---|---|
| 下顎頭にマッピング | 下顎頭 | 関節窩 |
| 関節窩にマッピング | 関節窩 | 下顎頭 |

補綴⇄支台、矯正前⇄後などでも同じ。「見たい面＝色を乗せたい面」を source にする。
自動ROI `--roi-near-target` を使う場合、target が近接相手（＝部位限定の基準）になる点も踏まえて
質問する（例：下顎頭にマッピングなら target=関節窩なので、関節窩メッシュが部位限定されていると
ROI がよく効く）。

## 標準ワークフロー

```bash
# 環境プリフライト（メッシュに触れない）
python3 pipeline/run_jointspace.py --check

# 距離マップ生成（source=下顎頭, target=関節窩）
python3 pipeline/run_jointspace.py \
    --source /data/condyle.stl \
    --target /data/fossa.stl \
    --out    /data/jointspace_map.vtp \
    --reduction 0.5 --source-only-decimate \
    --stats-json /data/jointspace_stats.json \
    --legend-png /data/jointspace_legend.png \
    --accept-disclaimer

# 部位限定で高速化（自動ROI・術者は座標入力なし。target近傍だけ計算・距離は不変）
python3 pipeline/run_jointspace.py \
    --source /data/condyle.stl --target /data/fossa.stl \
    --out /data/jointspace_roi.vtp \
    --roi-near-target 10 \
    --accept-disclaimer

# 全顎メッシュから下顎頭だけ抽出して原精度マッピング（Z下限のみ指定）
python3 pipeline/run_jointspace.py \
    --source /data/mandible.stl --target /data/maxilla_skull.stl \
    --out /data/condyle_map.vtp \
    --roi-zmin 80 \
    --accept-disclaimer
```

全顎の下顎メッシュをそのまま渡すと、TMJスケール0–5mmでは下顎頭の関節面以外（頸部・体部）が
青に振り切れて下顎頭が埋もれる。`--roi-zmin` で下顎頭領域だけ切り出すと、source点が激減して
**原精度（間引きなし）でも数秒**で計算でき、関節面の関節スペースが鮮明に出る。Z下限の値は
症例の座標系に依存するので、一度 `--stats-json` を出すかビューアで下顎頭の Z 範囲を確認して決める。

`--roi-near-target` は target（関節窩）を部位メッシュとして渡す前提で、座標を一切指定せず
「関節窩から指定mm以内の下顎頭表面だけ」を自動で計算する。座標が既知の研究・バッチでは
手動の `--roi-sphere CX CY CZ R` も使える。

出力:
- `jointspace_map.vtp` — source メッシュに `Distance` スカラーと焼き込み `RGB`、
  およびカラースケール定義を field_data に埋め込んだ VTP（元アプリの「比較タブ」で読める）。
- `jointspace_stats.json` — min / max / mean / 点数などの要約（任意）。
- `jointspace_legend.png` — スケール凡例の PNG（任意）。

標準出力には min/max/mean 距離と点数のみを表示する（メッシュの中身は展開しない）。

### よく使うオプション（run_jointspace.py）

| オプション | 意味 |
|---|---|
| `--check` | メッシュに触れず依存の準備状況だけ点検して終了（プリフライト） |
| `--source PATH` / `--target PATH` | 入力メッシュ。距離は source 点ごとに算出され色も source に乗る |
| `--out PATH` | 出力 VTP（カラー距離マップ）。拡張子 `.vtp` 推奨 |
| `--reduction 0.0–0.95` | デシメーション率（間引き）。大きいほど高速・粗い。既定なし（間引かない） |
| `--source-only-decimate` | source だけ間引く（target は原精度）。**精度を保ちつつ高速**。元アプリ既定の考え方 |
| `--roi-near-target [MARGIN_MM]` | **【自動ROI・推奨】** target表面から MARGIN mm 以内の source だけ自動抽出。**座標入力不要**（フラグのみで既定10mm）。関節スペースの定義どおり近接部位だけを高速計算。距離は不変 |
| `--roi-sphere CX CY CZ R` | 球状ROIで source を限定（中心・半径mm）の**手動**版。座標が分かっている研究・バッチ用。source側のみ絞るので距離は不変 |
| `--roi-zmin/--roi-zmax`（X・Yも同様） | **軸平行ボックスROI**。各軸の下限/上限を任意指定。**全顎メッシュから下顎頭を切り出すのに最適**（`--roi-zmin 80` の1個でOK）。指定した軸条件だけ効く。距離は不変 |
| `--scale-json PATH` | カラースケール定義 JSON を指定。未指定なら組込み TMJ スケール |
| `--max-distance MM` | スケール上限[mm]を上書き（スケール側の最大点より優先） |
| `--stats-json PATH` | 距離統計を JSON 出力 |
| `--legend-png PATH` | スケール凡例 PNG を出力 |
| `--no-embed-scale` | VTP へのスケール埋め込みを無効化 |
| `--accept-disclaimer` | 研究用途・免責に同意（未指定だと実行しない） |

終了コード: `0`成功 / `1`免責未同意 or check失敗 / `2`エラー / `99`想定外。

## TMJベーシックスケール（組込み既定 / 0–5mm）

下顎頭⇄関節窩のクリアランス読影に合わせた配色。狭い＝赤、広い＝青。

| 距離 | 色 | 目安 |
|---|---|---|
| 0–1.0mm | 赤 | 近接・狭小（圧迫の疑い） |
| 1.6mm | 黄 | 狭め |
| 2.5mm | 緑 | 標準域 |
| 3.3mm | 水 | やや広い |
| 4.0–5.0mm | 青 | 広い・離開 |

色の境界・別レンジ（補綴ギャップ用に 0–2mm など）への変更は `references/color_scale_guide.md`。

## 構成

```
joint-space-mapping/
├── SKILL.md
├── pipeline/
│   ├── run_jointspace.py    # 距離計算→LUT→カラーVTP出力 本体（CLI）
│   ├── color_scale.py       # ColorScale / build_lut / VTP埋込（元アプリから移植・自己完結）
│   ├── requirements.txt     # pyvista / vtk / numpy / matplotlib
│   └── README.md
└── references/
    ├── environment_setup.md # venv 構築手順
    ├── color_scale_guide.md # スケールJSONの書式・TMJ/補綴/偏差マップ用プリセット
    └── parameter_tuning.md  # デシメーション・速度/精度・座標系の注意
```

## 注意・免責

研究用途限定。薬機法上の医療機器ではなく、診断・治療の根拠に使わない。出力の幾何学的距離は
入力メッシュの精度・整列に依存する。患者データの取り扱いは PenClaw のハードルール（患者氏名・ID
をリポジトリに置かない）に従う。メッシュ自体は匿名の幾何形状だが、ファイル名に患者識別子を
含めないこと。

## 関連

- 前段: **dicom-to-stl-pipeline**（DICOM → STL）。CT 由来の表面を入力源にできる。
- 整列: **OpenMediCAD**（ランドマーク→ICP）。別スキャン由来の2枚を同一座標系へ。
- 元アプリ: `Archive/2025-10_JointSpaceVisualizerAppver.`（PyQt5+pyvista GUI 版・v2.1.0）。
- 研究: PointNet2 STL セグメンテーション研究（コード＋ケン）。
