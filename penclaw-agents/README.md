# PenClaw Agents

**江間ファミリー歯科の AI エージェントチーム（全11体）を束ねる Claude プラグイン。**

## 同梱エージェント

| 名前 | 専門 | スキルID |
|---|---|---|
| カイ（海） | 統括PM・戦略 | penclaw-boss |
| マコト | Webマーケ（SEO/MEO/LP/広告/WP/GSC） | penclaw-web-marketing |
| リン | Instagram運用・分析 | penclaw-instagram |
| ヒナタ | Googleカレンダー・Gmail・ブリーフィング | penclaw-daily |
| ソラ | Notion・DB設計・ナレッジベース | penclaw-notion |
| ハブ | 技術基盤・API連携・自動化 | penclaw-hub |
| コード | ML・AI・3D歯科STL | penclaw-ml |
| ケン | 学術・EBD・薬機法 | penclaw-academic |
| ナナ | 院内POP・患者説明資料 | penclaw-patient-content |
| チャト | Chatwork連携 | penclaw-chatwork |
| デックス | Codex連携・セカンドオピニオン | penclaw-codex |

## インストール

### Claude Code

```bash
# ローカル marketplace 経由
claude plugin marketplace add /Users/ema/Desktop/VScode/PenClaw/penclaw-marketplace
claude plugin install penclaw-agents@penclaw-marketplace
```

### Cowork

Cowork 設定 → プラグイン → マーケットプレイス追加 → `<GitHub URL>` を指定 → penclaw-agents を install。

## 構造

```
penclaw-agents/
  .claude-plugin/
    plugin.json            ← マニフェスト
  skills/                  ← 11スキル
    penclaw-boss/
    penclaw-daily/
    ...
```

## 更新・公開フロー

1. **master の場所**: `/Users/ema/Desktop/VScode/PenClaw/skills_master/`
2. 編集は master で行う
3. GitHub 公開前に `scripts/materialize_skills.sh` で symlink を実ファイル化
4. commit & push
5. Claude Code / Cowork 側は `claude plugin update penclaw-agents` で最新化

## 連絡

- Owner: 秀明先生（江間ファミリー歯科 副院長）
- PM: カイ（海）
- 技術責任: ハブ

---

**注記**: このプラグインはスキルのみを同梱。MCP サーバー設定（chatwork / GoogleFamily / NgraphAgent）は `~/Library/Application Support/Claude/claude_desktop_config.json` で別管理。
