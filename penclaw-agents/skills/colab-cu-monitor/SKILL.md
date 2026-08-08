---
name: "colab-cu-monitor"
description: "Google Colab Pro+のコンピュートユニット(CU)残量をChrome操作で確認し、残量が少ない場合に警告する。「Colab残量」「CU確認」「コンピュートユニット」「colab-cu-monitor」と言われたら発動。"
---

# Colab CU Monitor スキル

Google Colab Pro+ のコンピュートユニット（CU）残量を Chrome MCP 経由で読み取り、残量に応じて警告を出す。

## 前提

- ユーザーは Google Colab Pro+（月500ユニット）を契約中
- Google は CU 残量取得の公開 API を提供していない
- Chrome 拡張（Claude in Chrome）を使い、Colab の UI 上の残量表示を読み取る

## 手順

### 1. Chrome でタブを準備

```
mcp__claude-in-chrome__tabs_context_mcp（createIfEmpty: true）
```

で既存タブを確認。なければ新規タブを作成する。

### 2. Colab にアクセス

```
mcp__claude-in-chrome__navigate（url: "https://colab.research.google.com"、tabId: 取得したタブID）
```

### 3. ページ読み込みを待つ

ナビゲーション後、2〜3秒待機してからページ読み取りへ進む。

### 4. CU 残量を読み取る

まず `get_page_text` でページ全文を取得し、「compute unit」「コンピュートユニット」「remaining」等のキーワード周辺から残量数値を探す。

見つからない場合は以下を順に試す：

**方法A: リソースアイコン/メニューをクリック**
- `find` で「Resources」「リソース」「compute units」などのボタン/リンクを検索
- 見つかったらクリックしてパネルを開き、再度 `get_page_text` または `read_page` で残量を読み取る

**方法B: 設定ページへ直接アクセス**
- `navigate` で `https://colab.research.google.com/signup/computeUnits` にアクセス
- ページテキストから残量数値を抽出

**方法C: JavaScript で DOM を直接検索**
```javascript
// CU表示要素を探す
document.querySelectorAll('[class*="compute"], [class*="unit"], [class*="resource"]')
```

### 5. 残量を解析

テキストから数値を抽出する。例：
- 「342.5 compute units remaining」→ 342.5
- 「残り 150 ユニット」→ 150

数値が取得できなかった場合は、その旨を報告して手動確認を促す。

### 6. 判定・レポート

取得した残量に応じて以下のメッセージを出す：

| 残量 | レベル | メッセージ |
|------|--------|-----------|
| 100超 | 正常 | 「CU残量: {X} ユニット（十分です）」 |
| 51〜100 | 注意 | 「⚠️ CU残量: {X} ユニット — 残り少なくなっています。使用量にご注意ください。」 |
| 50以下 | 警告 | 「🚨 CU残量: {X} ユニット — 危険水域です！GPU使用を控えるか、追加購入を検討してください。」 |
| 取得不可 | エラー | 「❌ CU残量を取得できませんでした。Colabにログイン済みか確認してください。」 |

### 7. タブを片付ける

チェック完了後、開いたタブを閉じる：
```
mcp__claude-in-chrome__tabs_close_mcp（tabId）
```

## 注意事項

- Colab の UI は変更される可能性がある。読み取りに失敗した場合はその旨を報告し、手動確認を促すこと。
- ユーザーが Google にログインしていない場合、残量は表示されない。ログイン状態の確認を先に行う。
- スクリーンショットを撮って読み取り内容を目視確認することも有効（`computer` の `screenshot` アクション）。

