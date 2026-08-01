---
name: manim-motion-dental
description: "Manim（3Blue1Brown製アニメーションエンジン）で歯科向け解説アニメ動画を自動生成するスキル。「アニメーション作って」「解説動画」「モーショングラフィックス」「Manim」「動画で説明」「リール用アニメ」「患者説明動画」「進行を動画で」と言われたら発動。リン（IGリール）・ナナ（患者向け・薬機法ゲート）・マコト（LP/Web埋込）連携前提。実行担当はコード（penclaw-ml）。"
---

# manim-motion-dental — Manim歯科アニメ生成パイプライン

Manim Community版で解説アニメ動画（3Blue1Brown風）を全自動生成する。2026-07-31実証済み：「むし歯の進行」26秒・1080p60を環境構築込み約15分で納品。

## 実績・参照ファイル

動作実績のあるサンプル一式は本スキルの `examples/` に同梱（初出は PenClaw司令室/manim_demo/）。

- `examples/caries.py` — 5シーン構成のManimソース（歯の断面3層・C1→C4進行）
- `examples/tooth.svg` — SVGパスで描いた歯型（SVGMobjectで読込・scaleコピーで象牙質/歯髄レイヤー化）

新規テーマはこの caries.py を雛形に改変するのが最速。

## 環境構築（Coworkサンドボックス・root権限なし前提）

pip直はmanimpangoビルドで失敗する（pango開発ヘッダ不在・apt不可）。**micromamba経由が唯一の正解ルート**。

```bash
# 1. micromamba取得（アーキテクチャ注意：uname -m でaarch64ならlinux-aarch64）
cd /tmp && curl -sL https://micro.mamba.pm/api/micromamba/linux-aarch64/latest -o mm.tar.bz2 && tar -xjf mm.tar.bz2 bin/micromamba

# 2. 環境作成（重要：HOME側ディスクは満杯のことがある。全て/tmp側に置く）
export MAMBA_ROOT_PREFIX=/tmp/mamba XDG_CACHE_HOME=/tmp/cache HOME=/tmp TMPDIR=/tmp
/tmp/bin/micromamba create -n manim -c conda-forge -y -q python=3.11 manimpango pycairo

# 3. manim本体はpipで
/tmp/bin/micromamba run -n manim pip install --no-cache-dir -q manim
```

落とし穴：「No space left on device」が出たら `df -h` で /sessions（ホーム側）満杯を疑う。環境・キャッシュを全て /tmp（ルートFS側）へ逃がすと解決。

## レンダリング作法

bash 45秒制限があるため、動画は必ず**シーン単位（1シーン5〜8秒）に分割**して個別レンダリング→ffmpeg concatで結合。1080p60でも1シーン数秒で終わる。

```bash
/tmp/bin/micromamba run -n manim manim -qh --disable_caching -o S1.mp4 scenes.py S1_Intro
# 全シーン後：
printf "file '%s'\n" S1.mp4 S2.mp4 > list.txt && ffmpeg -f concat -safe 0 -i list.txt -c copy out.mp4
```

品質フラグは `-ql`（480p15・下書き）、`-qm`（720p30）、`-qh`（1080p60・納品用）。IGリール用の縦型は `-r 1080,1920` で9:16にし、レイアウトは縦前提で組み直す。検証は必須：`ffmpeg -ss N -vframes 1` で数フレームをPNG抽出→Readで目視確認（文字化け・レイアウト崩れ・場面転換の黒フレーム位置を見る）。

## デザイン規約

- 日本語フォント: `font="Noto Serif CJK JP"`（サンドボックス標準搭載。Sans CJKは無い）
- 江間ファミリー歯科ブランド配色: teal `#68AEBA`（主役）・gold `#DDC26A`（差し色）・背景はダークネイビー `#101E2E` が映える。`#3aa6a0` はブランド外
- **文字は最小限・大きく・短く**（先生評価：文字よりダイナミックな動きが価値。1画面1メッセージ、長文キャプション禁止）
- 縦棒使用時は前後に半角スペース（例: `C2 ｜ 象牙質まで進行`）
- 動きで語る: Transform・GrowFromCenter・Flash・Indicate を積極的に使い、静止テキスト頼みにしない

## 音声（オプション）

ナレーションは penclaw-media MCP の `el_tts`（ElevenLabs）、声クローンは `el_create_instant_voice`。BGM生成AIは未接続のため、フリー音源をffmpegでダッキング合成（`sidechaincompress` またはナレーション区間の音量 -12dB）。

## エージェント連携（必須ゲート）

| 担当 | 役割 |
|---|---|
| コード（penclaw-ml） | 実行主担当。シーン設計・レンダリング・結合 |
| ナナ（penclaw-patient-content） | **患者向けに出す場合は薬機法ゲート必須**。NG語（安心・守る・最悪の場合・おすすめ等）は rules/medical_ad_ng.md 準拠。公開前にAI臭→薬機法の順でチェック |
| リン（penclaw-instagram） | リール転用時の縦型化・尺（〜30秒推奨）・キャプション/ハッシュタグ設計 |
| マコト（penclaw-web-marketing） | LP/コラムへの動画埋込・GA4計測との整合 |

公開系アクション（IG投稿・WP埋込）は必ず先生の承認を取ってから実行する。
