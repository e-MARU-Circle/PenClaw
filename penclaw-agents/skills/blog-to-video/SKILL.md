---
name: blog-to-video
description: "ブログ記事→縦動画→Reels投稿パイプライン（担当: ハブ＋コード＋ナナ＋リン、統括カイ）。WPブログ記事を45〜60秒の台本に変換し、薬機法チェック→Dr.選択→ElevenLabs音声クローンTTS→Hedra口パク動画→ffmpeg結合（テロップ・9:16）→先生承認→ig_publish_reel投稿までを一気通貫で回す。「ブログ動画化」「blog-to-video」「動画にして」「リール作って」「リール動画」「口パク動画」「音声クローン」「Dr.の声で」「TTS」「ElevenLabs」「Hedra」「縦動画」「Reels投稿」「動画パイプライン」と言われたら発動。江間ファミリー歯科（emasika.jp・4 Dr.運用）の前提を含む。"
---

# blog-to-video — ブログ→縦動画→Reels投稿パイプライン

設計書（2026-06-22・先生決裁: 声はゼロから作る／Dr.4人使い分け／ElevenLabs Starter＋Hedra Basic／Reels＋TikTok）の実行スキル。**2026-07-04にE2E全工程を実証済み**（1本目 reel.mp4＝歯ブラシの選び方）。

- 実装ホーム: `/Users/ema/Desktop/VScode/blog-to-video/`（compose_reel.py・題材・consent・SETUP）
- 素材生成MCP: `/Users/ema/Desktop/VScode/penclaw-media-mcp/`（`media_health` で稼働確認。doctors.json もここ）

## 譲れないガード（毎回確認）

1. **発信主体は本人の声・顔のみ**。AIナレーター・AI生成顔は使わない
2. **薬機法**: 台本・キャプションは `rules/medical_ad_ng.md` 照合→ナナ最終監修を経る（「おすすめ」「安心」「守る」「最悪の場合」「治る」「最高」「人気」等NG。誘引・効果断定・不安喚起を避ける）
3. **承認**: 完成動画は先生の承認後にのみ投稿。承認前の `ig_publish_reel` 実行は禁止
4. **同意**: 対象Dr.の書面同意（`blog-to-video/consent/`）が未取得なら収録・生成に進まない
5. キー・音声・写真はリポジトリ非コミット（CLAUDE.md NEVER条項）

## 事前チェック

- `media_health` → `ElevenLabs: OK / Hedra: OK` を確認（NGならAPIキー未投入。`SETUP_キー投入日チェックリスト.md`）
- `media_doctor_map` → 対象Dr.が登録済み（voice_id・photo_path・署名済同意書）か確認。未登録なら「Dr.パーソナル設定」（末尾）を先に回す

## 手順（6工程）

### ① ブログ→台本化
`wp_get_post`（column CPTは `wp_get_cpt`）で記事取得 → フック→問題提起→解決3つ→相談誘導の45〜60秒構成に圧縮。テロップ案（8本前後・1本13字目安）も同時に作る。**表示用（漢字）**と**読み上げ用（かな）**の2本立てで台本を持つ（②③で使い分け）。

### ② 薬機法チェック
NGパターン照合・置き換え（「おすすめ」→「目安」等）→ ナナ監修。相談導線は患者主体（「気になる際はご相談ください」）にする。

### ③ Dr.選択 → 音声生成
`media_doctor_map` で対応表取得。既定はテーマ割当（矯正/インプラント/小児/一般・予防）、先生指定があれば手動。
`el_tts`（対象Dr.のvoice_id・**stability 0.7 / similarity_boost 0.9** 推奨）で narration.mp3 を生成。
- **【最重要】漢字は必ずかな表記で渡す**。ElevenLabs multilingual v2 は漢字読みを頻繁に外す（歯科→しきょく／歯→しか／毛→ねこ／硬さ→ケツさ／傷→せず 等）。医院名は「エマファミリーしか」。読み上げ用かな台本をそのまま投入する
- 生成後 `ffprobe -show_entries format=duration` で実尺を測り、テロップ時刻を実音声長に合わせて再配分（文字数比で文単位に割る）

### ④ 写真→口パク動画
- 顔写真を **9:16センタークロップ＋720×1280へ縮小**（PillowでOK）してから `hedra_upload_asset`（image）。narration.mp3 も `hedra_upload_asset`（audio）
- `hedra_generate`（image_asset_id＝顔／audio_asset_id＝音声／aspect_ratio `9:16`／resolution `720p`／duration_seconds 0＝音声追従）→ 返り値 `id` を generation_id として使う
- `hedra_status` をポーリング。progress 0 のまま数分→`finalizing`→`complete`（30秒尺で約7〜8分）。完了時 `download_url` 取得
- S3ダウンロードはサンドボックス帯域が細く1回で落ちきらない → `curl -C - --max-time 40` を成功まで再開DL。`ffprobe` で `moov atom` エラーが出たら未完＝再開継続
- Hedra不調時のフォールバック: script.json で `"video"` の代わりに `"image"`（静止画モード）

### ⑤ 結合・テロップ
script.json を書き（下記形式）→ `python3 compose_reel.py --script <script.json> --out reel.mp4`。出力は1080×1920・faststart。数フレーム抜いて（`ffmpeg -ss <t> -frames:v 1`）テロップ描画・9:16の収まり・口パクを目視検証。

### ⑥ 承認→投稿
完成MP4を `present_files` で先生に提示 → **承認後**、リンが `ig_publish_reel`（video_url・キャプション・ハッシュタグ）。TikTokは手動アップ。投稿前にナナの薬機法最終監修を必ず通す。

## script.json 形式

`audio`（必須）／`video` or `image`／`bgm`・`bgm_db`（任意）／`telops`: `[{start, end, text}]`。
例: `blog-to-video/projects/2026-07_歯ブラシの選び方/script.json`

## Dr.パーソナル設定（未登録Dr.の初期化）

1. **同意書**: `consent/音声顔クローン利用同意書_ドラフト.md` を印刷・署名 → `consent/consent_<Dr>_<日付>.pdf` に格納
2. **読み上げ録音**: 静かな環境で1〜2分、普段の説明トーン（mp3/wav）。m4aは `ffmpeg` でmp3変換
3. **クローン**: `el_create_instant_voice`（name・audio_file_path）→ voice_id取得
4. **顔写真**: 正面・明るめ1枚を `penclaw-media-mcp/faces/<Dr>_face.jpg` に配置
5. **登録**: `penclaw-media-mcp/doctors.json`（`{"doctors":[{id,name,specialty,voice_id,photo_path,...}]}`）に追記 → `media_doctor_map` で確認
   - specialtyはサーバー未使用のメタ情報＝複数テーマは配列可
   - 登録済み: dr2 副院長 江間（voice_id `gcnPzfPiQJKa8fv49g4Y`／インプラント・一般予防）。詳細はAuto Memory `blog-to-video-doctor-voices`

## 未稼働時の対応

`media_health` が失敗＝APIキー未投入。`SETUP_キー投入日チェックリスト.md` に従って配線。①②の台本づくりはキーなしで先行できる。

## 正本と配布

このファイル（`skills_master/blog-to-video/SKILL.md`）がマスター。配布側（plugin/skillsキャッシュ）は読み取り専用コピー。編集は必ずここで行い、配布反映はマーケットプレイス側の同期で行う。
