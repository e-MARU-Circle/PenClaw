#!/bin/bash
# ================================================================
# materialize_skills.sh
# ================================================================
# skills/ ディレクトリ配下の symlink を実ファイルに変換する。
# GitHub 公開前に実行。
#
# 動作:
#   1. skills/ 配下の symlink を一覧
#   2. 各 symlink のターゲットを同じ位置に実体コピー（symlink 置換）
#   3. .backup/ に旧 symlink 情報を保存
#
# 実行方法:
#   bash /Users/ema/Desktop/VScode/PenClaw/penclaw-agents/scripts/materialize_skills.sh
# ================================================================

set -e

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SKILLS_DIR="$PLUGIN_ROOT/skills"
BACKUP_DIR="$PLUGIN_ROOT/.symlink_backup_$(date +%Y%m%d_%H%M%S)"

echo "=================================================="
echo "materialize_skills.sh"
echo "Plugin root: $PLUGIN_ROOT"
echo "=================================================="
echo ""

mkdir -p "$BACKUP_DIR"

cd "$SKILLS_DIR"

for entry in */; do
  name="${entry%/}"
  if [ -L "$name" ]; then
    target=$(readlink "$name")
    resolved=$(cd "$SKILLS_DIR" && cd "$(dirname "$target")" && pwd)/$(basename "$target")

    echo "--- $name ---"
    echo "  symlink → $target"
    echo "  実体コピー中..."

    # symlink のメタ情報を保存
    echo "$name -> $target" >> "$BACKUP_DIR/symlinks.txt"

    # symlink 削除 → 実体コピー
    rm "$name"
    cp -r "$resolved" "$name"

    echo "  ✅ 実体化完了"
  else
    echo "--- $name ---"
    echo "  既に実体（スキップ）"
  fi
done

echo ""
echo "=================================================="
echo "✅ 実体化完了"
echo ""
echo "symlink 情報は $BACKUP_DIR に保存済"
echo "git add . && git commit -m 'materialize skills' で公開可能"
echo ""
echo "⚠ 注意: 実体化後は symlink に戻せません。"
echo "   再度編集する場合は skills_master で編集し、このスクリプトで再実体化してください。"
echo "=================================================="
