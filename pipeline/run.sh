#!/bin/bash
# run.sh — Process all CCTV clips or run simulation if clips are absent.
# Usage:
#   ./pipeline/run.sh                            # auto-detect clips or simulate
#   ./pipeline/run.sh --real                     # require real clips
#   ./pipeline/run.sh --simulate                 # always simulate
#   ./pipeline/run.sh --api http://localhost:8000 # stream events to API

set -e

CLIP_DIR="${CLIP_DIR:-data/clips}"
EVENTS_DIR="${EVENTS_DIR:-data/events}"
API_URL="${API_URL:-http://localhost:8000}"
LAYOUT="${LAYOUT:-data/store_layout.json}"
MODE="${MODE:-auto}"

for arg in "$@"; do
  case $arg in
    --real)     MODE="real" ;;
    --simulate) MODE="simulate" ;;
    --api=*)    API_URL="${arg#*=}" ;;
    --live)     LIVE="--live" ;;
  esac
done

mkdir -p "$EVENTS_DIR"

if [ "$MODE" = "auto" ] && [ ! -d "$CLIP_DIR" ]; then
  MODE="simulate"
fi

if [ "$MODE" = "simulate" ]; then
  echo "==> No clips found. Running simulation mode..."
  python3 -m pipeline.simulate \
    --store STORE_BLR_002 \
    --visitors 50 \
    --output "$EVENTS_DIR/sim_STORE_BLR_002.jsonl" \
    --api "$API_URL" \
    ${LIVE:-}

  python3 -m pipeline.simulate \
    --store STORE_MUM_005 \
    --visitors 35 \
    --output "$EVENTS_DIR/sim_STORE_MUM_005.jsonl" \
    --api "$API_URL" \
    ${LIVE:-}

  echo "==> Simulation complete. Events in $EVENTS_DIR"
  exit 0
fi

# Real clip processing
echo "==> Processing real CCTV clips from $CLIP_DIR"

for store_dir in "$CLIP_DIR"/*/; do
  store_id=$(basename "$store_dir")
  echo "  Store: $store_id"

  for clip in "$store_dir"*.mp4; do
    [ -f "$clip" ] || continue
    filename=$(basename "$clip" .mp4)
    camera_id="${filename}"
    output="$EVENTS_DIR/${store_id}_${camera_id}.jsonl"

    echo "    Camera: $camera_id -> $output"

    python3 -m pipeline.detect \
      --video "$clip" \
      --store "$store_id" \
      --camera "$camera_id" \
      --output "$output" \
      --layout "$LAYOUT" \
      --api "$API_URL"
  done
done

echo "==> All clips processed. Events in $EVENTS_DIR"
