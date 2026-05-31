"""
Main detection pipeline.
Processes a CCTV clip using YOLOv8 + ByteTrack and emits structured events.

Usage:
  python -m pipeline.detect \\
    --video data/clips/STORE_BLR_002/CAM_ENTRY_01.mp4 \\
    --store  STORE_BLR_002 \\
    --camera CAM_ENTRY_01 \\
    --output data/events/STORE_BLR_002_CAM_ENTRY_01.jsonl \\
    --api    http://localhost:8000

Requirements: ultralytics, opencv-python-headless, numpy
"""
import argparse
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import cv2
    from ultralytics import YOLO
except ImportError:
    print("Run: pip install -r requirements-pipeline.txt")
    sys.exit(1)

from pipeline.tracker import VisitorTracker
from pipeline.zone_classifier import ZoneClassifier, load_store_layout
from pipeline.emit import EventEmitter

PERSON_CLASS_ID = 0
PROCESS_EVERY_N_FRAMES = 3  # process every 3rd frame for speed; still 5fps at 15fps source


def process_clip(
    video_path: str,
    store_id: str,
    camera_id: str,
    output_path: str,
    clip_start_time: datetime,
    api_url: str = None,
    layout_path: str = "data/store_layout.json",
) -> int:
    """
    Process one clip. Returns number of events emitted.
    """
    store_layout = load_store_layout(store_id, layout_path)

    model = YOLO("yolov8n.pt")  # nano model: fast, ~80MB, good for retail CCTV
    tracker = VisitorTracker(camera_id=camera_id)
    zone_clf = ZoneClassifier(store_layout, camera_id)
    emitter = EventEmitter(output_path, api_url)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
    frame_idx = 0
    event_count = 0

    print(f"[detect] Processing {video_path} @ {fps:.1f}fps  store={store_id}  camera={camera_id}")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_idx += 1
        if frame_idx % PROCESS_EVERY_N_FRAMES != 0:
            continue

        frame_time = clip_start_time + timedelta(seconds=frame_idx / fps)

        # YOLOv8 tracking (ByteTrack built-in) — only detect 'person' class
        results = model.track(
            frame,
            persist=True,
            classes=[PERSON_CLASS_ID],
            verbose=False,
            conf=0.3,
        )

        if results[0].boxes is None:
            continue

        for box in results[0].boxes:
            if box.id is None:
                continue

            track_id = int(box.id.item())
            xyxy = box.xyxy[0].cpu().numpy()
            confidence = float(box.conf.item())

            cx = float((xyxy[0] + xyxy[2]) / 2)
            cy = float((xyxy[1] + xyxy[3]) / 2)

            zone = zone_clf.get_zone(cx, cy)
            sku_zone = zone_clf.get_sku_zone(zone) if zone else None
            direction = zone_clf.get_direction(track_id, cx, cy)
            is_staff = tracker.is_staff(frame, xyxy)
            visitor_id, is_reentry = tracker.get_visitor_id(track_id, frame_time)

            events = tracker.update(
                track_id=track_id,
                visitor_id=visitor_id,
                is_reentry=is_reentry,
                zone=zone,
                direction=direction,
                confidence=confidence,
                is_staff=is_staff,
                timestamp=frame_time,
                store_id=store_id,
                sku_zone=sku_zone,
            )

            for event in events:
                emitter.emit(event)
                event_count += 1

    cap.release()
    tracker.finalize_sessions(store_id, emitter)
    emitter.flush()

    print(f"[detect] Done — {event_count} events written to {output_path}")
    return event_count


def main():
    parser = argparse.ArgumentParser(description="CCTV Detection Pipeline")
    parser.add_argument("--video", required=True, help="Path to video clip")
    parser.add_argument("--store", required=True, help="Store ID (e.g. STORE_BLR_002)")
    parser.add_argument("--camera", required=True, help="Camera ID (e.g. CAM_ENTRY_01)")
    parser.add_argument("--output", required=True, help="Path to output .jsonl file")
    parser.add_argument("--api", default=None, help="API URL for real-time streaming (optional)")
    parser.add_argument("--clip-start", default=None, help="ISO-8601 clip start time (default: now)")
    parser.add_argument("--layout", default="data/store_layout.json", help="Store layout JSON path")

    args = parser.parse_args()

    if args.clip_start:
        clip_start = datetime.fromisoformat(args.clip_start.replace("Z", "+00:00"))
    else:
        clip_start = datetime.now(timezone.utc)

    process_clip(
        video_path=args.video,
        store_id=args.store,
        camera_id=args.camera,
        output_path=args.output,
        clip_start_time=clip_start,
        api_url=args.api,
        layout_path=args.layout,
    )


if __name__ == "__main__":
    main()
