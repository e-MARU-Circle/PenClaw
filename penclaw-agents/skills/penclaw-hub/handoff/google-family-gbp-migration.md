# google-family MCP : GBP系エンドポイント移行タスク

> 作成: 2026-05-12
> 作成元: PenClaw司令室 / ハブ
> 適用先: NgraphAgent リポジトリの google-family MCP 実装
> 想定実行環境: ローカルのClaude Code（NgraphAgentフォルダを開いた状態）

---

## このドキュメントの使い方

ローカルのClaude Code（NgraphAgentリポジトリを開いた状態）で、下記「Claude Codeへのプロンプト」セクションをそのままコピペして渡してください。Claude Codeが該当ファイルを読み取り、最小diffで修正します。

---

## 背景（人間向け要約）

PenClaw司令室（Cowork）からgoogle-family MCPのGBP系ツールを呼び出すと、以下のエンドポイントで404が返るようになっています。

```
GET /v4/locations/{id}/reviews           → 404
GET /v4/locations/{id}/localPosts        → 404
GET /v4/locations/{id}/media             → 404
GET /v4/locations/{id}:reportInsights    → 404
```

原因はGoogle My Business API v4の大規模廃止（2024年4月）。MCP実装が旧v4エンドポイントを叩いたままになっている。

復旧難易度は機能ごとに大きく異なる:

| ツール | 復旧可否 | 対応 |
| --- | --- | --- |
| gbp_list_locations | ◯ 動作中 | 触らない |
| gbp_get_location | ◯ 動作中 | 触らない |
| gbp_update_location | ◯ 動作中 | 触らない |
| gbp_get_insights | ◯ 復旧可能 | Business Profile Performance API v1 に載せ替え |
| gbp_list_reviews | ✕ 代替API無し | 廃止メッセージを返すよう実装変更 |
| gbp_reply_review | ✕ 代替API無し | 同上 |
| gbp_delete_review_reply | ✕ 代替API無し | 同上 |
| gbp_list_posts | ✕ 代替API無し | 同上 |
| gbp_create_post | ✕ 代替API無し | 同上 |
| gbp_delete_post | ✕ 代替API無し | 同上 |
| gbp_list_photos | ✕ 代替API無し | 同上 |
| gbp_upload_photo | ✕ 代替API無し | 同上 |

> Reviews/Posts/Photos系はGoogleが「Business Profile Manager（ブラウザUI）で手動操作してください」と明言しており、API復旧は不可能。MCPツールは「廃止された旨を返すスタブ」に置き換える。

---

## Claude Codeへのプロンプト（ここをコピペ）

```
google-family MCP の GBP（Google Business Profile）系エンドポイントを修正してほしい。

# 背景
Google My Business API v4 は 2024年4月に大規模廃止された。
現状、このMCPは v4 エンドポイントを叩いており、Cowork セッションから以下のツール呼び出しが 404 を返している:

- gbp_list_reviews         (GET /v4/locations/{id}/reviews)
- gbp_list_posts           (GET /v4/locations/{id}/localPosts)
- gbp_list_photos          (GET /v4/locations/{id}/media)
- gbp_get_insights         (POST /v4/locations/{id}:reportInsights)

# 修正方針

## 1. gbp_get_insights を Business Profile Performance API v1 に移行する

旧:
  POST https://mybusiness.googleapis.com/v4/{location}:reportInsights
新:
  GET  https://businessprofileperformance.googleapis.com/v1/{location}:fetchMultiDailyMetricsTimeSeries

クエリパラメータ:
  - dailyMetrics (repeated string): 以下の主要メトリクスをデフォルトで全件取得
      BUSINESS_IMPRESSIONS_DESKTOP_MAPS
      BUSINESS_IMPRESSIONS_DESKTOP_SEARCH
      BUSINESS_IMPRESSIONS_MOBILE_MAPS
      BUSINESS_IMPRESSIONS_MOBILE_SEARCH
      BUSINESS_CONVERSATIONS
      BUSINESS_DIRECTION_REQUESTS
      CALL_CLICKS
      WEBSITE_CLICKS
      BUSINESS_BOOKINGS
  - dailyRange.startDate.year / month / day
  - dailyRange.endDate.year / month / day

入力 (start_date, end_date は "YYYY-MM-DD") はそのまま受け取り、内部で
year/month/day に分解してクエリに変換する。

レスポンスは multiDailyMetricTimeSeries[].dailyMetricTimeSeries.timeSeries.datedValues[]
の形で日次データが返る。これを以下のように集約してツールの返り値とする:

  {
    "location": "locations/...",
    "start_date": "...",
    "end_date": "...",
    "totals": { "BUSINESS_IMPRESSIONS_DESKTOP_MAPS": 123, ... },
    "daily": [
      { "date": "2026-05-01", "BUSINESS_IMPRESSIONS_DESKTOP_MAPS": 5, ... },
      ...
    ]
  }

スコープは既存の Google 認証で動くはず（business.manage / plus.business.manage が含まれていれば OK）。
不足していたら認証スコープ拡張も指示してほしい。

## 2. Reviews / Posts / Photos 系ツールはスタブ化する

対象:
  gbp_list_reviews, gbp_reply_review, gbp_delete_review_reply,
  gbp_list_posts, gbp_create_post, gbp_delete_post,
  gbp_list_photos, gbp_upload_photo

これらは Google が API を廃止しており、復旧不可能。ただし上位のスキル（penclaw-web-marketing）が
ツール名で呼んでくるため、ツール定義自体は残し、内部で以下のような構造化エラーを返すこと:

  {
    "deprecated": true,
    "reason": "Google has discontinued this endpoint in Google My Business API v4 (April 2024).",
    "alternative": "Use the Business Profile Manager UI: https://business.google.com/",
    "tool": "<tool_name>"
  }

HTTPステータスとしては成功（200）で構造化レスポンスを返す方が、上位がgracefulに分岐しやすい。
例外を投げる実装になっているなら、戻り値方式に変更すること。

## 3. 触らないツール

以下は現状動作しているので一切変更しないこと:
  gbp_list_locations, gbp_get_location, gbp_update_location

## 4. 制約

- 最小diff原則。GBP関連の修正だけに集中し、他のGoogleファミリーツール
  （GA4, Search Console, Google Ads, WordPress, Instagram, Maps）は触らない。
- 既存のテストがあれば壊さない。GBP用のテストが無い場合、簡易な smoke test を1本追加してOK。
- README/ドキュメントがあれば、変更箇所だけ更新する。
- 認証スコープを変更した場合は、その旨を変更サマリーに明記。

# 期待する作業手順

1. まず該当ファイル（GBP系ツールの実装が書かれているファイル）を Read で確認する
2. 修正対象関数を特定する
3. 変更計画を簡潔に提示（私の承認は不要、そのまま進めてOK）
4. 修正を Edit で適用
5. 修正後、サマリーを出力:
   - 変更したファイル一覧
   - 変更した関数一覧
   - スコープ変更の有無
   - 動作確認の手順（curl コマンド等）

# 動作確認

修正後、以下が正しく動くことを確認するためのcurlコマンドを最後に提示してほしい:

  - gbp_get_insights 相当: 期間内のBUSINESS_IMPRESSIONS系メトリクス取得
  - gbp_list_reviews 相当: 構造化された deprecated レスポンスが返る

実テストの代わりに「ローカル起動後にこのcurlで叩いて200/200が返ればOK」というレベルの確認手順でよい。

以上。それでは進めてください。
```

---

## 補足: テスト用データ

修正後、Cowork側から以下のロケーションIDで疎通確認可能:

```
location_name: locations/3651533522307436012
施設名: 江間ファミリー歯科・矯正歯科
期間例: 2026-04-12 〜 2026-05-11
```

---

## 補足: 公式ドキュメントへのリンク

- Business Profile Performance API v1: https://developers.google.com/my-business/reference/performance/rest
- Business Profile APIs Migration (v4 deprecation): https://developers.google.com/my-business/content/discontinued-apis
- API Discovery: https://developers.google.com/my-business/content/overview

---

## 完了後の Cowork 側でのリトライ

修正・デプロイ完了後、Cowork（PenClaw司令室）で以下を実行して動作確認:

1. `gbp_get_insights` を5/12時点で叩く → 数値が返ればOK
2. `gbp_list_reviews` を叩く → deprecated レスポンスが返ればOK
3. その結果を受けて、マコトの SKILL.md にChrome経由フローを追記（ハブBタスク）

修正が完了したら、本ファイルの末尾に「完了日: YYYY-MM-DD」と追記してください。

---

## 完了記録

**完了日: 2026-05-12**

実施者: ローカルのClaude Code（NgraphAgentリポジトリ）

変更ファイル:
1. `src/gbp/client.ts` — `gbpPerformanceFetch()` と `deprecatedToolResponse()` を追加
2. `src/gbp/tools/insights.ts` — Performance API 移行 + 写真系stub
3. `src/gbp/tools/reviews.ts` — 全stub
4. `src/gbp/tools/posts.ts` — 全stub

変更サマリー:
- `gbp_get_insights` → Business Profile Performance API v1（`fetchMultiDailyMetricsTimeSeries`）に移行。日次データを `totals + daily[]` 形式に集約して返却
- Reviews/Posts/Photos系の8ツール → `{deprecated:true, reason, alternative, tool}` を200相当で返すスタブに変更
- Locations系3ツール → 変更なし
- 認証スコープ変更なし（既存の `business.manage` で動作）
- `npx tsc --noEmit` 通過

次のステップ:
1. ローカルで `npm run build && node dist/index.js` 起動
2. Cowork側のMCP再接続を確認
3. Coworkから `gbp_get_insights` を実行し `totals/daily` が返ることを確認
4. 確認後、マコトのSKILL.mdに「GBPの口コミ・投稿・写真はChrome MCP経由」のフローを追記（ハブBタスク）
