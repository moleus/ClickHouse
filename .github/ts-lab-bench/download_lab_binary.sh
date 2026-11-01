#!/usr/bin/env bash
# Download a verified ClickHouse TimeSeries lab binary from the owner's fork.
# Usage: RUN_ID=1234 bash download_lab_binary.sh /tmp/tslab
# Otherwise download the latest prerelease whose tag starts with tslab-.
set -euo pipefail
repo='moleus/ClickHouse'
out="${1:-./tslab-binary}"
mkdir -p "$out"
out="$(cd "$out" && pwd)"
for command_name in gh sha256sum zstd tar; do
  command -v "$command_name" >/dev/null || { echo "Missing $command_name" >&2; exit 2; }
done
if [[ -n "${RUN_ID:-}" ]]; then
  [[ "$RUN_ID" =~ ^[0-9]+$ ]] || { echo 'RUN_ID must be numeric' >&2; exit 2; }
  echo "Downloading CI artifact from run $RUN_ID"
  gh run download "$RUN_ID" --repo "$repo" --pattern 'tslab-clickhouse-amd64-*' --dir "$out"
else
  tag="${TAG:-$(gh api "repos/$repo/releases?per_page=100" --jq '[.[] | select(.tag_name | startswith("tslab-"))] | sort_by(.created_at) | last | .tag_name // empty')}"
  [[ -n "$tag" ]] || { echo 'No TimeSeries lab prerelease found' >&2; exit 3; }
  echo "Downloading prerelease $tag"
  gh release download "$tag" --repo "$repo" \
    --pattern 'clickhouse-linux-amd64.tar.zst' \
    --pattern 'ARCHIVE_SHA256SUMS' --dir "$out" --clobber
fi
archive_path="$(find "$out" -type f -name 'clickhouse-linux-amd64.tar.zst' -print -quit)"
[[ -f "$archive_path" ]] || { echo 'Missing archive' >&2; exit 4; }
archive_dir="$(dirname "$archive_path")"
(cd "$archive_dir" && sha256sum -c ARCHIVE_SHA256SUMS)
zstd -dc "$archive_path" | tar -xf - -C "$archive_dir" clickhouse SHA256SUMS BUILD_INFO.txt
(cd "$archive_dir" && sha256sum -c SHA256SUMS)
chmod +x "$archive_dir/clickhouse"
"$archive_dir/clickhouse" --version
printf 'Binary: %s\n' "$archive_dir/clickhouse"
cat "$archive_dir/BUILD_INFO.txt"
