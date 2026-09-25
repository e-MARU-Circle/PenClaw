# PenClaw Marketplace

**PenClaw 関連プラグインを配布する marketplace（プラグイン同梱型）。**

## 構造

```
penclaw-marketplace/
  .claude-plugin/
    marketplace.json        ← プラグインカタログ
  penclaw-agents/           ← 同梱プラグイン
    .claude-plugin/
      plugin.json
    skills/                 ← skills_master へ symlink
    scripts/
    README.md
  README.md
```

## 同梱プラグイン

- `penclaw-agents` — 11体の AI エージェント＋機能スキル（skill-publisher・cv-measurement-audit・joint-space-mapping）のスキルセット

## Claude Code への追加

```bash
claude plugin marketplace add ~/Desktop/VScode/PenClaw/penclaw-marketplace
```

## プラグインインストール

```bash
claude plugin install penclaw-agents@penclaw-marketplace
```

## Cowork へ追加（GitHub 公開後）

1. このディレクトリを GitHub に private repo として push（`penclaw-marketplace` repo）
2. 事前に `penclaw-agents/scripts/materialize_skills.sh` で symlink を実体化
3. Cowork 設定 → プラグイン → マーケットプレイス追加 → GitHub URL を指定
4. penclaw-agents を install

## 重要な仕様メモ

- `plugins[*].source` は **marketplace root からの相対パス**
- **`./` で始まる**必要あり
- **`..` を含んではならない**（プラグインは必ず marketplace 配下に配置）

## 追加プラグインの登録

新規プラグイン追加時：

1. プラグインを `penclaw-marketplace/<plugin-name>/` に配置
2. `.claude-plugin/marketplace.json` の `plugins` 配列に追記
3. `claude plugin validate` で確認

## ライセンスと免責

- ライセンス: MIT（リポジトリ直下の `LICENSE`）。無保証で提供する。
- 歯科関連スキル（dental-3d・dental-cbct ほか）はすべて研究・教育用途限定。薬機法上の医療機器ではなく、診断・治療・治療計画・装置設計の根拠や手段として使用しない。
- 学習済みモデルの重みと患者データは同梱しない。
