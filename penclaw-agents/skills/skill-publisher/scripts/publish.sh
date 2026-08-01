#!/bin/bash
# ================================================================
# publish.sh — リポジトリフォルダをGitHubでpublic公開する（事故防止つき）
# 使い方: bash publish.sh <repoフォルダ> <repo名>
#   例:   bash publish.sh ~/work/dental-3d dental-3d
# 前提: gh (GitHub CLI) がログイン済み
# ================================================================
set -e
REPO_DIR="$1"; REPO_NAME="$2"
if [ -z "$REPO_DIR" ] || [ -z "$REPO_NAME" ]; then
  echo "usage: bash publish.sh <repoフォルダ> <repo名>"; exit 1
fi
cd "$REPO_DIR"

# --- 事故防止: ここがrepoルートか検証（親で git init して巨大データ巻き込む事故を防ぐ） ---
if [ ! -e "README.md" ] || [ ! -d "skills" ]; then
  echo "❌ ここはrepoルートではない可能性。README.md と skills/ が見当たりません。"
  echo "   今いる場所: $(pwd)"; ls; exit 1
fi
echo "▶ repoルート確認OK: $(pwd)"

git init -b main
git add -A
git commit -m "Initial public release: $REPO_NAME"
gh repo create "$REPO_NAME" --public --source=. --remote=origin --push

echo ""
echo "✅ 公開完了"
echo "次にやること:"
echo "  1) GitHub → Settings → General → Social preview に docs/thumbnail.png を設定"
echo "  2) Note記事を投稿（サポートON）→ README のNote URLプレースホルダを差し替えて commit & push"
