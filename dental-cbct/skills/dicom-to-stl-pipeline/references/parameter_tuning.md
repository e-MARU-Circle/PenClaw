# パラメータ調整ガイド（元アプリの実測知見）

> 元の DICOM_to_STL アプリ開発で**最も苦労したのがパラメータ調整**だった。
> その結論を「既定値」と「切替フラグ」に落とし込んである。再調整で同じ轍を踏まないための記録。
> 他者の環境（GPU 有無・メモリ・OS）で動かす前提なので、迷ったら下表の通り。

## まず迷ったら（プリセット）

| 環境 | コマンド | 中身 |
|---|---|---|
| 通常（CPU/MPS、まず動かす） | `--preset fast`（既定） | TTA 無効 + step_size 0.7 |
| GPU(CUDA) で精度重視 | `--preset quality` | TTA 有効 + step_size 0.5 |
| 低メモリ／multiprocessing 制約 | `--preset low-resource` | 逐次(npp=nps=0) + TTA 無効 + step_size 0.7 |

個別フラグ（`--step-size` `--tta/--no-tta` `--sequential` `--npp` `--nps` `--threads`）はプリセットを上書きする。

---

## 推論パラメータ（ここが本番の苦労どころ）

### TTA（Test-Time Augmentation / 8方向ミラー予測）
- **効果と代償**: 精度はわずかに上がるが **計算量が約 8 倍**。CPU だと致命的に遅い。
- **結論**: 既定は **無効（`--disable_tta` 相当）**。GPU で精度を詰めたいときだけ `--tta`。
- 元アプリでは「60 分→数分」短縮の主因がこれ。

### step_size（スライディングウィンドウの重なり）
- 小さいほど重なりが増え高精度・低速。nnU-Net 既定 0.5。
- **結論**: 既定 **0.7**（重なりを減らし約 30% 高速化、精度差は実用上軽微）。精度を詰めるなら `--step-size 0.5`。

### 逐次実行（npp / nps = 0）
- nnU-Net は前処理・出力をマルチプロセスで回すが、**権限制約・低メモリ・PyInstaller 等で
  `multiprocessing.Manager` が落ちる**ことがある（元アプリの最大のハマりどころ）。
- **対処**: `--sequential`（= `-npp 0 -nps 0`）で 1 プロセス実行に倒す。遅いが確実に動く。
- 「アプリがステップ2で固まる/無限ループ」系は、ほぼこれで回避できた。

### CPU スレッド（--threads）
- CPU 推論時、既定スレッド数が環境で極端に振れる。`--threads <コア数>` で固定すると安定。

### デバイス（--device）
- `auto` は cuda > mps > cpu の順で自動選択。
- **MPS 注意**: Apple GPU で nnU-Net が落ちる例がある。失敗時は `--device cpu`。
- `perform_everything_on_device=True` は CUDA 専用。本パイプラインは堅牢側（False 相当・標準 CLI）に倒している。

---

## DICOM→NIfTI（dcm2niix）

- `-d 9`: サブフォルダ探索を 9 階層へ（既定 5 では深い構造で「DICOM が見つからない」エラー）。
- `-i n`: 派生画像も取り込む（除外で取りこぼす症例があった）。
- `-z y`: gzip 圧縮 NIfTI。
- **1 症例 = 1 シリーズ**。複数シリーズが混ざると NIfTI が複数生成されてエラーになる。フォルダを分ける。

---

## NIfTI→STL（メッシュ品質。ここも長く苦労した）

法線・スケール／位置は `nifti_to_stl.py` に**確定値として実装済み**（再調整不要）。
平滑化のみプリセットで切替できる。

### 平滑化アルゴリズム（Taubin λ/μ・プリセット制）
- `trimesh` の laplacian / taubin / humphrey を渡り歩き、パラメータ（iterations・lamb・nu・alpha・beta）で
  形状が崩れる迷走があった。次に VTK `vtkWindowedSincPolyDataFilter`（iter 30 / PassBand 0.01）へ収束したが、
  強度が固定で症例ごとに調整できなかった。
- **結論**: **Taubin λ/μ 法**（Taubin, SIGGRAPH 1995）を自前実装し、プリセットで強度を選ぶ方式にした。
  λ ステップ（縮む）→ μ ステップ（膨らむ, μ < -λ < 0）を交互に当て、体積収縮を打ち消す。
  頂点を動かすだけで面の接続は変えないので **watertight 性が壊れない**。
- 設計は TotalSegmentator Wrapper for Mac 0.4.1 の surface_preview.py の方針を参考にした（仕様からの再実装）。

| プリセット | 反復数 | 用途 |
|---|---|---|
| `none` | 0 | 平滑化なし（marching cubes の生メッシュ。検証・他ツールで平滑化する場合） |
| `slicer_like`（既定） | 10 | 3D Slicer の Taubin 既定に相当。通常はこれ |
| `medium` | 20 | 階段状のノイズが目立つとき |
| `strong` | 30 | CT の解像度が粗い／見た目重視のとき |

λ=0.5 / μ=-0.53 が全プリセット共通の既定。`--smooth-iterations` `--smooth-lambda` `--smooth-mu` で個別上書き可。

**ラベル別の例外規則**（重要）: 名前に `pulp` / `canal` を含むラベル（当院の 5 ラベルでは
**下顎管 Mandibular_canal**）と、ボクセル数が `--small-label-voxels`（既定 500）未満の小構造は、
反復数を **最大 3 回**に制限する。細い管状構造は平滑化で痩せる／途切れるため。
合成データ（径 0.36mm の湾曲管）での実測は下表。適用結果は出力フォルダの
`smoothing_info.json` とログに残る。

| 対象 | strong を無制限に適用 | 制限あり（3 回） |
|---|---|---|
| 細管の体積 | -8.4% | -0.9% |
| 細管の平均半径 | -4.0% | -0.7% |
| 球（大構造）の体積 | +1.1%（Taubin） | — （単純ラプラシアン 30 回は -16.9%） |

### メッシュの裏表（法線）
- STL の面が裏返る問題。`vtkPolyDataNormals`（`ConsistencyOn` / `SplittingOff` / 自動向き付け）に加え、
  **符号付き体積が負なら三角形の巻き順を反転**してから平滑化することで安定化。

### スケール／位置
- marching cubes に **spacing を (z, y, x) 順**で渡す（SimpleITK は (x, y, z)）。これを忘れると拡大縮小する。
- 頂点に DICOM の `direction` 行列と `origin` を適用し、**元の物理空間**に一致させる。ラベル間の位置関係も保たれる。

---

## トラブル別・効くフラグ早見

| 症状 | まず試す |
|---|---|
| ステップ2で固まる/落ちる | `--preset low-resource`（または `--sequential`） |
| とにかく遅い（CPU） | `--preset fast` ＋ `--threads <コア数>` |
| MPS でクラッシュ | `--device cpu` |
| 精度を上げたい（GPU） | `--preset quality`（または `--tta --step-size 0.5`） |
| DICOM が見つからない | フォルダ階層/シリーズを確認（`-d 9 -i n` は適用済み） |
| STL が階段状でギザギザ | `--smooth-preset medium`（さらに `strong`） |
| 下顎管が細る/途切れる | 既に反復数 3 回に自動制限。なお細るなら `--smooth-preset none` |
| 小さい構造が消える | `--small-label-voxels` を上げて制限対象を広げる（既定 500） |
| 形状が裏返る | 既に確定処理済み。モデル・入力解像度を疑う |
