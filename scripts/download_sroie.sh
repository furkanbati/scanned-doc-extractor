#!/usr/bin/env bash
# Downloads SROIE with the corrected labels from zzzDavid/ICDAR-2019-SROIE.
# Only the repo's data/ folder is fetched, file by file, pinned to a commit so
# every run gets the same images and labels. Files already on disk are
# skipped, so an interrupted run can simply be started again.
#
# Usage: bash scripts/download_sroie.sh [target_dir]   (default: data/sroie)

set -euo pipefail

COMMIT="27be4271b251c256f695acbade9a801bffe85994"
BASE_URL="https://raw.githubusercontent.com/zzzDavid/ICDAR-2019-SROIE/$COMMIT/data"
COUNT=626
TARGET="${1:-data/sroie}"
JOBS="${JOBS:-8}"

mkdir -p "$TARGET/img" "$TARGET/key" "$TARGET/box"

download() {
    local path="$1"
    local dest="$TARGET/$path"

    if [ -s "$dest" ]; then
        return 0
    fi

    curl --fail --silent --show-error --location \
        --retry 5 --retry-all-errors --retry-delay 2 \
        --output "$dest.part" "$BASE_URL/$path"
    mv "$dest.part" "$dest"
}

export -f download
export TARGET BASE_URL

for i in $(seq 0 $((COUNT - 1))); do
    id="$(printf "%03d" "$i")"
    echo "img/$id.jpg"
    echo "key/$id.json"
    echo "box/$id.csv"
done | xargs -P "$JOBS" -I {} bash -c 'download "$1"' _ {}

echo "SROIE downloaded to $TARGET:"
for sub in img key box; do
    echo "  $sub: $(find "$TARGET/$sub" -type f ! -name '*.part' | wc -l) files"
done
