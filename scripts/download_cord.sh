#!/usr/bin/env bash
# Downloads CORD v2 (naver-clova-ix/cord-v2 on Hugging Face, CC-BY-4.0) as
# parquet files, pinned to a commit so every run gets the same receipts.
# About 2.3 GB. Files already on disk are skipped, so an interrupted run can
# simply be started again.
#
# Usage: bash scripts/download_cord.sh [target_dir]   (default: data/cord)

set -euo pipefail

COMMIT="7f0115a4b758a71d6473b8d085751692da2fef98"
BASE_URL="https://huggingface.co/datasets/naver-clova-ix/cord-v2/resolve/$COMMIT/data"
TARGET="${1:-data/cord}"

FILES=(
    "train-00000-of-00004-b4aaeceff1d90ecb.parquet"
    "train-00001-of-00004-7dbbe248962764c5.parquet"
    "train-00002-of-00004-688fe1305a55e5cc.parquet"
    "train-00003-of-00004-2d0cd200555ed7fd.parquet"
    "validation-00000-of-00001-cc3c5779fe22e8ca.parquet"
    "test-00000-of-00001-9c204eb3f4e11791.parquet"
)

mkdir -p "$TARGET"

for name in "${FILES[@]}"; do
    dest="$TARGET/$name"
    if [ -s "$dest" ]; then
        echo "have $name"
        continue
    fi

    echo "get  $name"
    curl --fail --silent --show-error --location \
        --retry 5 --retry-all-errors --retry-delay 2 \
        --output "$dest.part" "$BASE_URL/$name"
    mv "$dest.part" "$dest"
done

echo "CORD v2 in $TARGET"
