#!/usr/bin/env bash
set -euo pipefail

REPO="trudadtou0680/product-monitor"
REF="main"
ORIGINAL_ARGS=("$@")

SCRIPT_PATH="${BASH_SOURCE[0]:-}"
if [[ -n "$SCRIPT_PATH" && -f "$SCRIPT_PATH" ]]; then
  SCRIPT_DIR="$(cd -- "$(dirname -- "$SCRIPT_PATH")" && pwd -P)"
  if [[ -f "$SCRIPT_DIR/install.sh" ]]; then
    exec bash "$SCRIPT_DIR/install.sh" "${ORIGINAL_ARGS[@]}"
  fi
fi

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
      : "${2:?--dest requires a directory}"
      shift 2
      ;;
    -h|--help)
      cat <<'EOF'
Usage: update.sh [--target codex|claude-code|openclaw|agents|generic] [--repo owner/repo] [--ref ref] [--dest skills_dir] [--reset-product-pool]

Updates theme-fund-analyzer by running install.sh from the selected GitHub ref.
The existing installed skill is backed up before replacement. Existing local
references/product-pools.md is preserved by default; use --reset-product-pool
to replace it with the repository version.
EOF
      exit 0
      ;;
    *)
      shift
      ;;
  esac
done

if [[ "$REPO" != */* ]]; then
  echo "--repo must use owner/repo format: $REPO" >&2
  exit 2
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "Required command not found: curl" >&2
  exit 1
fi

RAW_URL="https://raw.githubusercontent.com/${REPO}/${REF}/install.sh"
echo "Updating theme-fund-analyzer with ${REPO}@${REF}..."
curl -fsSL "$RAW_URL" | bash -s -- "${ORIGINAL_ARGS[@]}"
