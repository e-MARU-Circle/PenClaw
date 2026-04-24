#!/bin/bash
# ================================================================
# publish.sh
# ================================================================
# PenClaw Marketplace を GitHub に公開／更新する。
#
# 動作:
#   1. symlink → 実ファイル化（materialize）
#   2. git init（初回のみ）／既存 repo 確認
#   3. GitHub private repo 作成（初回のみ、gh 経由）
#   4. commit & push
#   5. symlink 復元（ローカル編集継続のため）
#
# 使い方:
#   bash /Users/ema/Desktop/VScode/PenClaw/penclaw-marketplace/publish.sh
# ================================================================

set -e

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
  git config user.name "Hideaki Ema"
  git config user.email "x820inaka@gmail.com"
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
    gh repo create "$REPO_NAME" --private --source=. --remote=origin --description "PenClaw AI Agent Marketplace for 江間ファミリー歯科"
  fi
else
  echo "▶ Step 2: origin 設定済: $(git remote get-url origin)"
fi

# ----- Step 3: symlink 実体化 -----
echo "▶ Step 3: symlink → 実ファイル化"
cd "$SKILLS_DIR"
for d in */; do
  name="${d%/}"
  if [ -L "$name" ]; then
    target=$(readlink "$name")
    # symlinkを削除して実体コピー
    rm "$name"
    # target を絶対パスに解決してコピー
    src="$SKILLS_DIR/$target"
    if [ -d "$src" ]; then
      cp -RL "$src" "$name"
      echo "  ✅ $name 実体化完了"
    else
      echo "  ❌ $name: target $src が見つかりません"
      exit 1
    fi
  fi
done
cd "$REPO_DIR"

# ----- Step 4: commit & push -----
echo "▶ Step 4: commit & push"
git add -A
if git diff --cached --quiet; then
  echo "  ℹ 差分なし（skip）"
else
  git commit -m "Publish: $(date '+%Y-%m-%d %H:%M')"
  git push -u origin main
  echo "  ✅ push 完了"
fi

# ----- Step 5: symlink 復元 -----
echo "▶ Step 5: symlink 復元（ローカル編集継続のため）"
cd "$SKILLS_DIR"
for d in */; do
  name="${d%/}"
  if [ -d "$name" ] && [ ! -L "$name" ]; then
    rm -rf "$name"
    ln -sfn "../../../skills_master/$name" "$name"
    echo "  🔗 $name → ../../../skills_master/$name"
  fi
done

echo ""
echo "=================================================="
echo "✅ Publish 完了"
echo "🌐 https://github.com/$GITHUB_USER/$REPO_NAME"
echo ""
echo "次のステップ: Cowork 設定 → プラグイン"
echo "  Marketplace URL: $GITHUB_USER/$REPO_NAME"
echo "=================================================="
