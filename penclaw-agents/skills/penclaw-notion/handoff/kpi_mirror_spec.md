# Notion KPIミラー仕様書（ソラへの引き継ぎ）

**作成**: 2026-05-01（カイ）
**実装担当**: ソラ（penclaw-notion）
**実装タイミング**: 別セッションで対応

---

## 目的

PenClawチーム3エージェント（マコト・リン・ナナ）のKPIダッシュボードを、Notion「PenClaw Command Center」上にミラーする。先生がNotionでも数値を確認できるようにする（モバイルからも閲覧可）。

---

## ソースデータ（読み取り元）

```
~/.claude/skills/penclaw-web-marketing/charter/kpi_dashboard.json
~/.claude/skills/penclaw-instagram/charter/ig_dashboard.json
~/.claude/skills/penclaw-patient-content/charter/content_dashboard.json
```

各JSONの構造は固定。`fixed_keywords`（または`fixed_metrics`）と`monthly_history`の配列を読む。

---

## Notion側の構造（推奨）

「PenClaw Command Center」配下に新規ページを作成：

### 親ページ：「📊 PenClaw KPI Dashboard」

子ページ／DBを以下の構成で：

1. **DB「マコト KPI（SEO/MEO）」**
   - プロパティ：月（タイトル）／カテゴリ（select）／キーワード（text）／順位（number）／クリック（number）／表示（number）／CTR（number）／目標（text）
   - 月次でレコード追加（追記式）

2. **DB「リン KPI（Instagram）」**
   - プロパティ：月／指標／実績／目標／達成率
   - 月次追記

3. **DB「ナナ KPI（患者コンテンツ）」**
   - プロパティ：月／媒体／本数／薬機法NG件数
   - 月次追記

4. **ページ「進行中プロジェクト」**
   - 横断的な進行中プロジェクト（P-1〜P-4）をToDo形式で表示

5. **ページ「北極星KPI 進捗ボード」**
   - 6ヶ月後（2026-11）目標に対する各エージェントの進捗を一覧

---

## 更新フロー（運用）

### 月次更新（毎月1日）

1. マコトが kpi_dashboard.json を更新（前月実績入力）
2. ソラが Notion DB に当月レコードを追加
3. ヒナタが朝ブリーフィングでKPIスナップショットを表示

### リアルタイム共有

- ローカルHTML：`/Users/ema/Documents/Claude/Projects/PenClaw司令室/PenClaw_KPI_Dashboard.html`（先生のPCで閲覧）
- Notion：先生のスマホ・タブレットから閲覧

---

## 実装時の注意事項

- **書き込みは先生承認後**（CLAUDE.mdルール8：外部システム書き込みはCowork側で先生確認）
- 既存のPenClaw Command Centerのページ構造を必ず確認してから新規ページ追加
- Notion APIで作成したDBには、追記しやすいビュー（テーブル＋月次フィルター）をデフォルト設定
- 既存データを上書きしない（追記専用）

---

## ソラへの依頼コマンド（先生がいつでも発動可能）

「ソラ、PenClaw Command CenterにKPIミラーを構築して」と言えば、この仕様書を読んで実装する。
