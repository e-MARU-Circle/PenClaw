#!/bin/bash
# ================================================================
# publish.sh
# ================================================================
# PenClaw Marketplace を GitHub に公開／更新する。
#
# 動作:
#   1. git init（初回のみ）／既存 repo 確認
#   2. GitHub private repo 作成（初回のみ、gh 経由）
#   3. 検証ゲート（壊れた symlink・不正マニフェストを公開前に止める）
#   4. symlink → 実ファイル化（materialize）
#   5. commit & push
#   6. symlink 復元（ローカル編集継続のため）
#
# 使い方:
#   bash /Users/ema/Desktop/VScode/PenClaw/penclaw-marketplace/publish.sh
#   bash .../publish.sh --no-push        # commit まで
#   bash .../publish.sh --skip-validate  # 検証ゲートを飛ばす（非常用）
# ================================================================

set -e

# --no-push: commit までで止め、push は手動（push直前まで準備）
# --skip-validate: 検証ゲートを飛ばす（非常用。既定では飛ばさない）
NO_PUSH=0
NO_VALIDATE=0
for arg in "$@"; do
  case "$arg" in
    --no-push) NO_PUSH=1 ;;
    --skip-validate) NO_VALIDATE=1 ;;
  esac
done

REPO_DIR="/Users/ema/Desktop/VScode/PenClaw/penclaw-marketplace"
REPO_NAME="PenClaw"
GITHUB_USER="e-MARU-Circle"
SKILLS_DIR="$REPO_DIR/penclaw-agents/skills"

cd "$REPO_DIR"

echo "=================================================="
echo "📦 PenClaw Marketplace Publish"
echo "  Repo: $GITHUB_USER/$REPO_NAME (private)"
echo "=================================================="

# ----- Step 1: Git 初期化 -----
if [ ! -d .git ]; then
  echo "▶ Step 1: git 初期化"
  git init -b main
  git config user.name "e-MARU-Circle"
  git config user.email "e-MARU-Circle@users.noreply.github.com"
else
  echo "▶ Step 1: git 既に初期化済"
fi

# ----- Step 2: GitHub repo 確認・作成 -----
if ! git remote get-url origin &>/dev/null; then
  if gh repo view "$GITHUB_USER/$REPO_NAME" &>/dev/null; then
    echo "▶ Step 2: 既存 repo を使用 ($GITHUB_USER/$REPO_NAME)"
    git remote add origin "https://github.com/$GITHUB_USER/$REPO_NAME.git"
  else
    echo "▶ Step 2: GitHub private repo 新規作成"
    gh repo create "$REPO_NAME" --private --source=. --remote=origin --description "PenClaw AI Agent Marketplace"
  fi
else
  echo "▶ Step 2: origin 設定済: $(git remote get-url origin)"
fi

# ----- Step 3: 検証ゲート -----
# 壊れた symlink は glob `*/` にも is_dir() にも引っかからず「静かに消える」。
# 2026-08-08、macOS の重複名（" 2"）由来の壊れリンクで 5 スキルが配布から欠落していた。
# 実体化の前に symlink 状態のまま検証する（失敗しても後片付けが不要なため）。
if [ "$NO_VALIDATE" = "1" ]; then
  echo "▶ Step 3: 検証ゲート（--skip-validate によりスキップ）"
else
  echo "▶ Step 3: 検証ゲート"
  if ! python3 "$REPO_DIR/../tools/validate_agent_plugin.py" "$REPO_DIR"/*/; then
    echo ""
    echo "  ❌ 検証に失敗しました。公開を中止します。"
    echo "     symlink は未変更のままです（後片付け不要）。上の ❌ を潰してから再実行してください。"
    echo "     どうしても急ぐ場合のみ: bash publish.sh --skip-validate"
    exit 1
  fi
fi

# ----- Step 4: symlink 実体化（全プラグインの skills/ を走査） -----
echo "▶ Step 4: symlink → 実ファイル化"
for SKILLS_DIR in "$REPO_DIR"/*/skills; do
  [ -d "$SKILLS_DIR" ] || continue
  echo "  ▷ $(basename "$(dirname "$SKILLS_DIR")")/skills"
  cd "$SKILLS_DIR"
  for d in */; do
    name="${d%/}"
    if [ -L "$name" ]; then
      target=$(readlink "$name")
      rm "$name"
      src="$SKILLS_DIR/$target"
      if [ -d "$src" ]; then
        cp -RL "$src" "$name"
        echo "    ✅ $name 実体化完了"
      else
        echo "    ❌ $name: target $src が見つかりません"
        exit 1
      fi
    fi
  done
done
cd "$REPO_DIR"

# ----- Step 5: commit & push -----
echo "▶ Step 5: commit & push"
git add -A
if git diff --cached --quiet; then
  echo "  ℹ 差分なし（skip）"
else
  git commit -m "Publish: $(date '+%Y-%m-%d %H:%M')"
  if [ "$NO_PUSH" = "1" ]; then
    echo "  ⏸ --no-push: commit のみ作成。push は手動で → (REPO_DIRで) git push -u origin main"
  else
    git push -u origin main
    echo "  ✅ push 完了"
  fi
fi

# ----- Step 6: symlink 復元（全プラグインの skills/ を走査） -----
# 【重要】復元先が skills_master に実在する時だけ symlink を張る。
# 旧版は名前を検証せず `ln -sfn ../../../skills_master/$name` を無条件で張っていたため、
# macOS の重複名（"blender-dental 2" 等）が紛れ込むと **存在しない正本を指す壊れた
# symlink** が生まれた。壊れた symlink は `for d in */` にも Python の is_dir() にも
# 引っかからず「静かに消える」ため、配布物からスキルが欠落したまま公開される。
# 2026-08-08 に5スキルが欠落した事故、2026-08-09 に " 2" ディレクトリ2件を実地で確認。
echo "▶ Step 6: symlink 復元（ローカル編集継続のため）"
ORPHANS=0
for SKILLS_DIR in "$REPO_DIR"/*/skills; do
  [ -d "$SKILLS_DIR" ] || continue
  cd "$SKILLS_DIR"
  for d in */; do
    name="${d%/}"
    [ -d "$name" ] && [ ! -L "$name" ] || continue
    if [ -d "$REPO_DIR/../skills_master/$name" ]; then
      rm -rf "$name"
      ln -sfn "../../../skills_master/$name" "$name"
      echo "  🔗 $name → ../../../skills_master/$name"
    else
      echo "  ⚠️  $(basename "$(dirname "$SKILLS_DIR")")/skills/$name : skills_master に対応する正本が無い"
      echo "      → symlink を張らず現状のまま残した（張ると壊れたリンクになる）"
      ORPHANS=$((ORPHANS + 1))
    fi
  done
done

if [ "$ORPHANS" -gt 0 ]; then
  echo ""
  echo "  🛑 正本の無いディレクトリが $ORPHANS 件あります。"
  echo "     macOS の重複名（末尾 \" 2\"）が典型。中身を確認し、不要なら手で削除してください:"
  echo "       find \"$REPO_DIR\" -maxdepth 3 -name '* [0-9]'"
  echo "     放置すると次回の公開で配布物からスキルが静かに欠落します。"
fi

echo ""
echo "=================================================="
echo "✅ Publish 完了"
echo "🌐 https://github.com/$GITHUB_USER/$REPO_NAME"
echo ""
echo "次のステップ: Cowork 設定 → プラグイン"
echo "  Marketplace URL: $GITHUB_USER/$REPO_NAME"
echo "=================================================="
