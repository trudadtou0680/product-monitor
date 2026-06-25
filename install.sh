#!/usr/bin/env bash
set -euo pipefail

REPO="trudadtou0680/product-monitor"
REF="main"
DEST="${CODEX_HOME:-$HOME/.codex}/skills"
SKILL_NAME="theme-fund-analyzer"
PRESERVE_PRODUCT_POOL=1

usage() {
  cat <<'EOF'
Usage: install.sh [--repo owner/repo] [--ref ref] [--dest skills_dir] [--reset-product-pool]

Installs theme-fund-analyzer into:
  ${CODEX_HOME:-$HOME/.codex}/skills/theme-fund-analyzer

Options:
  --repo  GitHub repository, default: trudadtou0680/product-monitor
  --ref   Git ref, branch, or tag, default: main
  --dest  Skills directory, default: ${CODEX_HOME:-$HOME/.codex}/skills
  --reset-product-pool
          Replace local references/product-pools.md with the repository version.
          By default, an existing local product pool is preserved across updates.
  -h, --help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      REPO="${2:?--repo requires owner/repo}"
      shift 2
      ;;
    --ref)
      REF="${2:?--ref requires a value}"
      shift 2
      ;;
    --dest)
      DEST="${2:?--dest requires a directory}"
      shift 2
      ;;
    --reset-product-pool)
      PRESERVE_PRODUCT_POOL=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$REPO" != */* ]]; then
  echo "--repo must use owner/repo format: $REPO" >&2
  exit 2
fi

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Required command not found: $1" >&2
    exit 1
  fi
}

require_command curl
require_command tar
require_command find

SOURCE_DIR=""
SCRIPT_PATH="${BASH_SOURCE[0]:-}"
if [[ -n "$SCRIPT_PATH" && -f "$SCRIPT_PATH" ]]; then
  SCRIPT_DIR="$(cd -- "$(dirname -- "$SCRIPT_PATH")" && pwd -P)"
  if [[ -f "$SCRIPT_DIR/${SKILL_NAME}/SKILL.md" ]]; then
    SOURCE_DIR="$SCRIPT_DIR/${SKILL_NAME}"
    echo "Using local ${SKILL_NAME} from $SOURCE_DIR"
  fi
fi

TMP_DIR=""
cleanup() {
  if [[ -n "$TMP_DIR" ]]; then
    rm -rf "$TMP_DIR"
  fi
}
trap cleanup EXIT

if [[ -z "$SOURCE_DIR" ]]; then
  TMP_DIR="$(mktemp -d)"
  ARCHIVE="$TMP_DIR/source.tar.gz"
  EXTRACT_DIR="$TMP_DIR/source"
  URL="https://codeload.github.com/${REPO}/tar.gz/${REF}"

  echo "Downloading ${REPO}@${REF}..."
  curl -fsSL "$URL" -o "$ARCHIVE"

  mkdir -p "$EXTRACT_DIR"
  tar -xzf "$ARCHIVE" -C "$EXTRACT_DIR"

  SKILL_FILE="$(find "$EXTRACT_DIR" -path "*/${SKILL_NAME}/SKILL.md" -type f -print -quit)"
  if [[ -z "$SKILL_FILE" ]]; then
    echo "Could not find ${SKILL_NAME}/SKILL.md in ${REPO}@${REF}" >&2
    exit 1
  fi

  SOURCE_DIR="$(dirname "$SKILL_FILE")"
fi

TARGET_DIR="${DEST%/}/${SKILL_NAME}"
PRODUCT_POOL_RELATIVE_PATH="references/product-pools.md"

mkdir -p "$DEST"

BACKUP_DIR=""
if [[ -e "$TARGET_DIR" ]]; then
  BACKUP_BASE="${TARGET_DIR}.backup-$(date +%Y%m%d%H%M%S)"
  BACKUP_DIR="$BACKUP_BASE"
  BACKUP_INDEX=1
  while [[ -e "$BACKUP_DIR" ]]; do
    BACKUP_DIR="${BACKUP_BASE}.${BACKUP_INDEX}"
    BACKUP_INDEX=$((BACKUP_INDEX + 1))
  done
  echo "Backing up existing skill to $BACKUP_DIR"
  mv "$TARGET_DIR" "$BACKUP_DIR"
fi

mkdir -p "$TARGET_DIR"
cp -R "$SOURCE_DIR"/. "$TARGET_DIR"/

if [[ "$PRESERVE_PRODUCT_POOL" -eq 1 && -n "$BACKUP_DIR" && -f "$BACKUP_DIR/$PRODUCT_POOL_RELATIVE_PATH" ]]; then
  mkdir -p "$TARGET_DIR/$(dirname "$PRODUCT_POOL_RELATIVE_PATH")"
  cp "$BACKUP_DIR/$PRODUCT_POOL_RELATIVE_PATH" "$TARGET_DIR/$PRODUCT_POOL_RELATIVE_PATH"
  echo "Preserved local product pool: $TARGET_DIR/$PRODUCT_POOL_RELATIVE_PATH"
fi

echo "Installed ${SKILL_NAME} to $TARGET_DIR"
echo "Restart Codex to pick up new skills."
