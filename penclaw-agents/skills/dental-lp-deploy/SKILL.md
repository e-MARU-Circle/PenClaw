---
name: dental-lp-deploy
description: 歯科医院の診療科目LP（例：小児歯科の /kids/ 型ランディングページ）を、自己完結HTMLテンプレートとしてWordPressテーマに実装し、固定ページ化→GA4計測→Instagramフィード埋込→インデックス促進→本体HPの旧リンク移行まで一気通貫で公開するスキル。「LP公開」「ランディングページ実装」「診療科目ページ作成」「WordPressテーマにLP設置」「/kids/型LP」「page-XXX.php設置」「File Managerでテーマ配置」「GA4計測タグ」「Smash Balloon Instagram埋込」「サイトマップ再送信・インデックス登録」「旧サイトのリンク差し替え」と言われたら発動。江間ファミリー歯科（emasika.jp・テーマemashika・WP install=/wordpress/）の運用前提を含む。
---

# 診療科目LP 作成→公開 スキル（dental-lp-deploy）

完成済みの「自己完結HTMLテンプレート（`page-XXX.php`）＋画像」を、WordPressテーマに実装し公開するまでの実践プレイブック。江間ファミリー歯科で /kids/（小児歯科LP）を公開した実作業から抽出。**ハマりどころ（落とし穴）が本体**なので必ず読むこと。

## 環境前提（江間ファミリー歯科）
- サイト：`https://www.emasika.jp/`、WordPressは `/wordpress/` 配下（wp-admin = `/wordpress/wp-admin/`）。
- アクティブテーマ：`emashika`（テーマ直下＝`wp-content/themes/emashika/`）。
- テーマファイル設置手段：**WP File Manager（導入済み・有効）** を使う。`admin.php?page=wp_file_manager`。
- GA4測定ID：`G-XN8D9MQCRX`（gtag.js直貼り・GTMなし）。GA4プロパティ=420948539（アカウント297178119）。
- GSCプロパティ：`sc-domain:emasika.jp`。
- 予約/問合せフォーム：患者様お問合せフォーム（矯正と共通のGoogleフォーム）。
- 薬機法：NG語・最上級・「専門医」表記なし。認定医は「日本矯正歯科学会 認定医」の正式表記のみ。

## 大原則
1. **本番への書き込みは先生のGO後**（プラグイン導入・テーマファイル設置・公開・旧リンク置換すべて）。
2. 着手前に AskUserQuestion で要件確認（公開GO、計測有無、画像構成など）。
3. **検証は必ず本番URLで**行う（File Managerの `cmd=get` はブラウザGETキャッシュで古い内容を返すことがある）。

---

## 手順

### 0. 把握
完成テンプレートは `Template Name:` 付きの自己完結HTML（`<!DOCTYPE html>`〜`</html>`、`the_content()` 非使用）。画像は `get_template_directory_uri()` 参照 → **アクティブテーマの `assets/images/...`** へ置く。デザインは既存LP（矯正LP）の実テーマCSSを踏襲しているか確認。

### 1. テーマファイル配置（File Manager / elFinder）
REST APIではテーマPHPは置けない。WP File Managerで配置する。Chrome操作のコツ：

- **アップロード**：`fm.exec('upload')` でアップロードダイアログを開く → ダイアログ内 `input[type=file]` に `id`＋`aria-label` を付与 → `find` でref取得 → `file_upload`(outputs or 連携フォルダのパス)。`file_upload`は session の outputs/uploads/連携フォルダのファイルのみ可・合計10MB未満。
- **cwd移動が効かない問題**：`fm.exec('open',hash)` や `'up'` は失敗することがある。確実なのは **localStorageの `/wordpress/wp-admin/admin.php-elfinder-lastdirwp_file_manager` を目的フォルダのhashに書き換えてページをリロード**。
- **zip解凍のマージ落とし穴**：elFinderの「マージ解凍」は既存フォルダを温存する一方、**ネストした新規サブフォルダを取り込まない**。→ 画像用サブフォルダ（例 `lp-kids`）は `fm.request({data:{cmd:'mkdir',target:imagesHash,name:'lp-kids'}})` で手動作成し、そこをcwdにして画像を直接アップ。
- **上書き**：replace確認ダイアログの YES（複数なら「Apply to all」チェック→YES）。
- **beforeunload警告**：File Managerから離脱時に「Leave site?」が出る。`navigate` を再実行（force）で抜ける。
- **ファイル内容の読取/編集（API直）**：`cmd=get&target=hash&conv=1` で読取、`cmd=put`(POST, content) で保存。hashは `'l1_'+btoa('wp-content/themes/emashika/<相対パス>')`（標準base64でほぼ一致。確実には親をlsしてfiles[].hashを使う）。

配置先：`page-XXX.php` → テーマ直下、画像 → `themes/emashika/assets/images/<slug>/`。配置後、画像URLをHTTP200で検証。アップ用の一時zipは削除。

### 2. 固定ページ作成（REST）
- `wp_create_page`：`title`, `slug`(例 kids), `template='page-XXX.php'`, `status='draft'`, `content`=プレースホルダ（テンプレが全描画なので本文は未使用）。
- テンプレ名が `Template Name:` に一致すればテンプレ選択に出る。`template` フィールドで指定。
- プレビュー（要admin login）：`https://www.emasika.jp/wordpress/?page_id=<ID>&preview=true`。
- プレビュー検証→先生GO→`wp_update_page` `status='publish'`。pretty permalinkで `/<slug>/` 公開。

### 3. 自己完結テンプレの落とし穴（最重要）
このテンプレは **`wp_head()`/`wp_footer()` を呼ばない** → GA4・Yoast・各プラグインのenqueueアセットが一切載らない。対処：

- **GA4基本タグ**：`<head>`内に gtag.js を直書き（測定IDはトップページのソースから取得）。canonicalはテンプレ手書きの `/<slug>/` を維持（`wp_head()`を呼ぶとYoastの`?page_id=`canonicalが混入し競合するので呼ばない）。
- **CVイベント**：計測したいクリック（予約ボタン等）に `gtag('event','generate_lead',{form_location:'<slug>'})` を付与。遷移先が他科と同一フォームでもGA4側で由来を区別できる。
- **プラグインのショートコード（例 Smash Balloon `[instagram-feed]`）**：
  ```php
  <?php
  if (function_exists('do_shortcode')) {
    if (!did_action('wp_enqueue_scripts')) { do_action('wp_enqueue_scripts'); } // 手動発火しないとアセット未登録
    echo do_shortcode('[instagram-feed feed="1" num="8" cols="4"]');
    wp_print_styles(array('sbi_styles'));   // SB固有CSS
    wp_print_scripts(array('sbi_scripts')); // SB固有JS（ローカライズ変数含む）
  }
  ?>
  ```
  併せてCSS実ファイル直リンクも保険に：`<link rel="stylesheet" href=".../plugins/instagram-feed/css/sbi-styles.min.css">`。これを忘れるとフィードが巨大アイコン化する。

### 4. GA4 Key Event登録
`generate_lead` をコンバージョン化：GA4管理 → 該当プロパティに切替 → 管理 → イベント → **「最近のイベント」タブ** → 対象イベントの☆をクリック。**名前指定の事前登録は不可**、受信済みイベントが一覧に出ている必要 → 本番でテスト発火を数回してから星付け。

### 5. インデックス促進（GSC）
- `gsc_submit_sitemap`：Yoastの `sitemap_index.xml` を再送信。`page-sitemap.xml` に新URLが含まれることを確認。
- URL検査→**インデックス登録リクエスト**はAPI不可・UI操作。Search ConsoleでURL検査→「インデックス登録をリクエスト」クリック（ライブテスト1〜2分）。

### 6. 旧ページからの移行（SEOカニバリ対策）
- 本体HPで旧URL（例 `kofu.emasika.jp/shonisika`）へリンクしている箇所を**テーマ全PHPから横断検索**（elFinder `cmd=get`で各PHP読取→grep）。実績：footer.php / front-page.php / first.php / contents.php / price.php / sitemap.php に1箇所ずつ。
- 各ファイルを `.bak-YYYYMMDD` でバックアップ（`cmd=mkfile`→`cmd=put`原本）してから、旧URL→新LP URL を `cmd=put` で置換。
- **対象は該当科目のリンクのみ**（他科のリンクは触らない）。`target="_blank"` は要件次第（内部リンクなので同タブ化も可）。
- 旧ページ自体（外部Google Sites等）のnoindex/リダイレクト・ナビ非表示は先生管轄で別途。これで新LPに評価が一本化。

### 7. 最終検証（本番URLで）
- 全画像HTTP200、KVスライダー動作、FAQ構造化データ（JSON-LD）。
- GA4タグ読込（`gtag/js?id=...`）、CVイベント発火（クリック擬似発火で `gtag` 呼出を捕捉）。
- canonical=`/<slug>/`、Yoast重複なし。
- 旧リンク0・新LPリンクが全ページで `/<slug>/` に遷移。

## チェックリスト
- [ ] テンプレ＋画像をテーマに配置、画像200
- [ ] 固定ページ（draft, template指定）→プレビュー→先生GO→公開
- [ ] GA4基本タグ＋CVイベント＋Key Event登録
- [ ] （あれば）Instagram等プラグインフィード：do_shortcode＋アセット明示出力
- [ ] サイトマップ再送信＋URL検査・インデックス登録
- [ ] 本体HPの旧リンクを新LPへ置換（バックアップ後）
- [ ] 本番URLで全検証
- [ ] 重要決定・状態を memory/ に記録

## 関連
- 計測実装の詳細・連携トラブルは「ハブ」、Web/SEO戦略は「マコト」、患者向け文言・薬機法チェックは「ナナ」と連携。
