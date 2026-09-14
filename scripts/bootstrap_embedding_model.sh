#!/bin/sh
set -eu

# FastEmbed's official prebuilt ONNX artifact for the model pinned in M4.
# Keep weights under .local/ (ignored by Git), then run the ingestion worker.
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cache_dir="${KNOWLEDGE_EMBEDDING_CACHE_DIR:-$project_dir/.local/models/fastembed}"
model_dir="$cache_dir/fast-bge-small-zh-v1.5"

if [ -f "$model_dir/model_optimized.onnx" ]; then
  echo "embedding model already present: $model_dir"
  exit 0
fi

mkdir -p "$cache_dir"
temp_file=$(mktemp "$cache_dir/bge-small-zh.XXXXXX.tar.gz")
trap 'rm -f "$temp_file"' EXIT
curl -fL --retry 3 --connect-timeout 15 \
  'https://storage.googleapis.com/qdrant-fastembed/fast-bge-small-zh-v1.5.tar.gz' \
  -o "$temp_file"
tar -xzf "$temp_file" -C "$cache_dir"
test -f "$model_dir/model_optimized.onnx"
echo "embedding model installed: $model_dir"
