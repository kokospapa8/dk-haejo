#!/usr/bin/env bash
# bump_version.sh — increment VERSION file (YYYYMMDD_NN format).
# Usage: ./scripts/bump_version.sh
# Run before committing a new release.  The VERSION file is committed to git
# and copied into the Docker image so the bot can announce it on startup.
set -e

ROOT="$(git rev-parse --show-toplevel)"
VF="$ROOT/VERSION"
TODAY=$(date +%Y%m%d)

if [[ -f "$VF" ]]; then
    CUR=$(cat "$VF" | tr -d '[:space:]')
    CUR_DATE="${CUR%%_*}"
    CUR_NN="${CUR##*_}"
    if [[ "$CUR_DATE" == "$TODAY" ]]; then
        NN=$(printf "%02d" $((10#$CUR_NN + 1)))
    else
        NN="01"
    fi
else
    NN="01"
fi

NEW_VERSION="${TODAY}_${NN}"
echo "$NEW_VERSION" > "$VF"
echo "✅ VERSION bumped → $NEW_VERSION"
echo ""
echo "다음 단계:"
echo "  1. ANNOUNCE.md 에 변경사항 업데이트"
echo "  2. git add VERSION ANNOUNCE.md && git commit -m '...'"
echo "  3. git push"
