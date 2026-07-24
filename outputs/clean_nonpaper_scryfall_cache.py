from __future__ import annotations

import argparse
import json
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
DEFAULT_CACHE_DIR = APP_DIR / "scryfall_art_cache"


def is_allowed_scryfall_print(card: dict) -> bool:
    set_code = str(card.get("set") or "").casefold()
    set_name = str(card.get("set_name") or "").casefold()
    return (
        "paper" in (card.get("games") or [])
        and str(card.get("lang") or "en").casefold() == "en"
        and set_code != "plst"
        and set_name != "the list"
    )


def image_stems_for_card(card: dict) -> list[str]:
    card_id = str(card.get("id") or "")
    if not card_id:
        return []
    if isinstance(card.get("image_uris"), dict) and card["image_uris"].get("png"):
        return [card_id]

    stems: list[str] = []
    for face_index, face in enumerate(card.get("card_faces") or []):
        image_uris = face.get("image_uris") or {}
        if image_uris.get("png"):
            suffix = f"_face_{face_index + 1}" if face_index else ""
            stems.append(f"{card_id}{suffix}")
    return stems


def collect_known_stems(metadata_dir: Path) -> tuple[set[str], set[str]]:
    allowed_stems: set[str] = set()
    unsupported_stems: set[str] = set()

    for path in metadata_dir.glob("*.json"):
        if path.name.startswith("named_"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, list):
            continue

        for card in data:
            stems = image_stems_for_card(card)
            if not stems:
                continue
            if is_allowed_scryfall_print(card):
                allowed_stems.update(stems)
            else:
                unsupported_stems.update(stems)

    unsupported_stems.difference_update(allowed_stems)
    return allowed_stems, unsupported_stems


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Remove unsupported non-paper Scryfall PNGs from the MTG art cache."
    )
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR), help="Path to scryfall_art_cache.")
    parser.add_argument("--apply", action="store_true", help="Actually delete files. Without this, only reports.")
    parser.add_argument(
        "--delete-unknown",
        action="store_true",
        help="Also delete PNGs that are not referenced by current cached metadata.",
    )
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir).expanduser()
    metadata_dir = cache_dir / "metadata"
    png_dir = cache_dir / "png"
    if not metadata_dir.is_dir() or not png_dir.is_dir():
        raise SystemExit(f"Cache folder must contain metadata and png folders: {cache_dir}")

    allowed_stems, unsupported_stems = collect_known_stems(metadata_dir)
    known_stems = allowed_stems | unsupported_stems

    delete_paths: list[Path] = []
    unknown_paths: list[Path] = []
    for path in png_dir.glob("*.png"):
        stem = path.stem
        if stem in unsupported_stems:
            delete_paths.append(path)
        elif stem not in known_stems:
            unknown_paths.append(path)

    if args.delete_unknown:
        delete_paths.extend(unknown_paths)

    print(f"Allowed image stems known: {len(allowed_stems):,}")
    print(f"Unsupported image stems known: {len(unsupported_stems):,}")
    print(f"Unknown cached PNGs: {len(unknown_paths):,}")
    print(f"PNG files selected for deletion: {len(delete_paths):,}")

    for path in delete_paths[:30]:
        print(f"DELETE {path}")
    if len(delete_paths) > 30:
        print(f"...and {len(delete_paths) - 30:,} more")

    if not args.apply:
        print("\nDry run only. Re-run with --apply to delete selected files.")
        return 0

    deleted = 0
    for path in delete_paths:
        try:
            path.unlink()
            deleted += 1
        except OSError as exc:
            print(f"Could not delete {path}: {exc}")
    print(f"\nDeleted {deleted:,} PNG file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
