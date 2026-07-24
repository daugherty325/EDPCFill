from __future__ import annotations

import argparse
import atexit
import os
import sys
import time
from pathlib import Path

from mtg_upscaler import MODEL_ID, UpscaleCache, UpscaleError


APP_DIR = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI-upscale cached MTG images with resumable progress without changing originals.")
    parser.add_argument("--cache-dir", type=Path, default=APP_DIR / "scryfall_art_cache")
    parser.add_argument("--custom-dir", type=Path, default=APP_DIR / "custom_art")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--limit", type=int, default=0, help="Process at most this many pending files (0 = all).")
    parser.add_argument("--skip-custom", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cache_dir = args.cache_dir.expanduser().resolve()
    upscaler = UpscaleCache(cache_dir, APP_DIR)
    if not upscaler.available:
        print(f"Real-ESRGAN runtime is missing: {upscaler.executable}", file=sys.stderr)
        return 2
    pid_path = upscaler.root / "upscale_migration.pid"
    pid_path.write_text(str(os.getpid()), encoding="ascii")
    atexit.register(lambda: pid_path.unlink(missing_ok=True))

    sources = sorted((cache_dir / "png").glob("*.png"))
    if not args.skip_custom and args.custom_dir.exists():
        sources.extend(sorted(args.custom_dir.rglob("*.png")))
    pending = [source for source in sources if not upscaler.is_cached(source)]
    if args.limit > 0:
        pending = pending[: args.limit]
    batch_size = max(1, args.batch_size)
    print(f"Model: {MODEL_ID}", flush=True)
    print(f"Found {len(sources)} source image(s); {len(pending)} pending in this run.", flush=True)
    started = time.monotonic()
    completed = 0
    failed = 0
    try:
        for start in range(0, len(pending), batch_size):
            batch = pending[start : start + batch_size]
            try:
                results = upscaler.ensure_batch(batch)
                batch_completed = sum(1 for source in batch if results.get(source.resolve()) != source.resolve())
                completed += batch_completed
                failed += len(batch) - batch_completed
            except (OSError, UpscaleError) as exc:
                failed += len(batch)
                print(f"Batch {start // batch_size + 1} failed: {exc}", file=sys.stderr)
            elapsed = max(0.001, time.monotonic() - started)
            rate = completed / elapsed
            remaining = len(pending) - completed - failed
            eta = remaining / rate if rate else 0
            print(
                f"Progress: {completed + failed}/{len(pending)} | completed={completed} failed={failed} "
                f"| {rate:.3f} image/s | ETA {eta / 3600:.2f}h",
                flush=True,
            )
    except KeyboardInterrupt:
        print("Stopped. Completed files are preserved; run this command again to resume.")
        return 130
    stats = upscaler.stats()
    print(f"Derived cache: {stats['count']} image(s), {stats['bytes'] / (1024 ** 3):.2f} GB")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
