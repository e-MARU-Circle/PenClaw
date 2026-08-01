---
name: skill-publisher
description: "自作のツール／パイプラインを『AIスキル（SKILL.md＋プラグイン）』としてパッケージ化し、GitHubでOSS公開→Note記事（初心者向けセットアップ手順）→サムネ作成→寄付導線（noteサポート等）まで一気通貫で用意する工程スキル。「スキル化」「スキルにして」「パイプラインをスキルに」「GitHubで公開」「OSS公開」「リポジトリ作成して」「配布パッケージ」「Note記事書いて」「サムネ作って」「寄付を募りたい」「公開したい」と言われたら発動。学習済みモデルやデータ同梱を伴う公開にも対応（ライセンス確認の手順を含む）。"
---

# skill-publisher — ツール/パイプラインをスキル化してGitHub＋Noteで公開する

自作のCLIツールや処理パイプラインを、(1) AIエージェント用スキル（プラグイン）としてパッケージ化し、(2) GitHubでOSS公開、(3) Note記事で1からのセットアップを解説、(4) サムネ作成、(5) 寄付導線の設置、までを定型化する工程スキル。

> 実例: `dental-3d`（歯科スキャン→中空オープン模型）。この構成をテンプレとする。

## 着手前に必ず確認（AskUserQuestionで聞く）

公開はやり直しが効きにくい。次の4点を**先に**確定する。

1. **公開先リポジトリ**: 新規パブリックrepo（推奨／院内privateと分離）か、既存repoのpublic化か。
2. **同梱データ/モデルの権利**: 学習済み重み・データセットの**再配布が許可されているか**。公開データセット由来なら、そのライセンス（出典明記・非商用 等）が重みにかかる。自前データのみなら公開可。不明なら公開前に精査（ケン＝academic相当の視点）。
3. **寄付の受け皿**: GitHub Sponsors / Buy Me a Coffee / note サポート / PayPal.me 等。
4. **Note記事の読者層**: 完全初心者か、Python/Gitがある程度わかる人か（説明の粒度が変わる）。

## 公開リポジトリの標準構成（テンプレ）

```
<repo>/
├── README.md                 ← 最小限（templates/README.md.tmpl）
├── LICENSE                   ← MIT想定（templates/LICENSE.mit）
├── requirements.txt          ← 実行依存
├── .gitignore                ← __pycache__/ *.pyc .venv/ .DS_Store *.stl 等
├── .claude-plugin/
│   ├── plugin.json           ← templates/plugin.json.tmpl（skills:["./skills/"]）
│   └── marketplace.json      ← templates/marketplace.json.tmpl（repoをマーケットプレイス化）
├── skills/<skill-name>/      ← スキル本体（SKILL.md＋実装）
├── model/<model_min>/        ← （任意）推論に必要な最小モデルのみ。巨大学習データは除外
└── docs/
    ├── sample.png            ← READMEヒーロー画像（既存レンダー流用可）
    └── thumbnail.png         ← Note/ソーシャル用サムネ（scripts/make_thumbnail.py）
```

`skills/` 配下に置くと、このrepoをそのまま Claude Code / Cowork にマーケットプレイスとして追加→プラグイン有効化で**自然言語から実行**できる。素のCLIとしても同じスクリプトで動く二刀流にする。

## 工程

### 1. リポジトリを組み立てる
- 実装一式を `skills/<name>/` に配置（`SKILL.md` 必須）。
- 推論モデルを同梱する場合、**最小セットのみ**抽出（コード＋必要な重みファイルのみ。学習データ・大容量チェックポイント・キャッシュは除外）。import連鎖を実際に通してテストし、取りこぼしを防ぐ。
- `.claude-plugin/plugin.json` と `.claude-plugin/marketplace.json` を置く（templates参照）。JSON妥当性を `python3 -c "import json;..."` で検証。

### 2. README（最小）
`templates/README.md.tmpl` を使用。要素: 1行説明＋ヒーロー画像／クイックスタート（CLI）／「AIスキルとして使う」節（プラグイン導入手順）／同梱物／モデル注記／ライセンス／**寄付のお願い**。詳細手順はNote記事へ誘導（プレースホルダURL）。

### 3. Note記事（セットアップ全手順）
`templates/note_article.md.tmpl` を読者層に合わせて調整。git clone→仮想環境→依存導入→実行→パラメータ調整→FAQ→（しくみ）→ライセンス/免責→**寄付のお願い**。noteサポートで受ける場合は記事末尾で誘導。

### 4. サムネ作成
`scripts/make_thumbnail.py` で1280×670を生成（タイトル3行＋強調色＋サブ＋バッジ＋3Dレンダー等の画像）。日本語は Noto Sans CJK JP を使用。`docs/thumbnail.png` に保存し、GitHub Settings→Social preview にも設定。

### 5. 公開（git / gh）— 事故防止が最重要
**必ず repo フォルダの中で実行**する。親フォルダ（巨大データを含む場所）で `git init` しないこと。

```bash
cd <repoフォルダ>
ls                       # README.md / skills / が見えることを必ず確認
git init -b main
git add -A
git commit -m "Initial public release: <name>"
gh repo create <name> --public --source=. --remote=origin --push
```

### 6. 公開後
- Settings → General → Social preview に `docs/thumbnail.png` を設定。
- Note記事を投稿し「サポート」をON、URL確定後 README のプレースホルダ2箇所を差し替えて commit & push。
- （任意）プラグイン導入の実地テスト（マーケットプレイス追加→有効化→自然言語実行）。

## Gotchas（失敗の記録）

- **誤 `git init` 事故**: `cd <repo>` が失敗したまま親（例: VScode直下、24GBのモデルを含む）で `git init && git add -A` すると巨大データを巻き込む。対策: `ls` で `skills/` を確認してから init。誤って作ったら `rm -rf <親>/.git`。
- **配布物の置き場所**: ブラウザ未保存のzipは相手の手元に無い。展開済みフォルダを**接続済みフォルダ**へ直接配置すると確実。
- **モデル同梱の最小化**: 推論に不要なデータセット・学習スクリプト・キャッシュを必ず除外（サイズと権利の両面）。
- **権利確認**: 公開データセット由来の重みは、そのライセンス条件が再配布にかかる。READMEとLICENSEに「利用元データセットのライセンスに従う」旨を明記。
- **患者情報**: ファイル名・出力・サンプルに患者氏名/IDを含めない。
- **免責**: 医療系は「医療機器ではない／臨床精度を保証しない／自己責任」を必ず記載。

## 連携
- ライセンス・データ権利の精査: ケン（penclaw-academic）
- スキル設計・配布基盤: ハブ（penclaw-hub）
- 患者向け表現・薬機法チェック（公開文面）: ナナ（penclaw-patient-content）
