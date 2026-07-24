from __future__ import annotations

import json
import os
import queue
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from types import SimpleNamespace

try:
    from tkinter import filedialog, messagebox, ttk
    import tkinter as tk
except ImportError:  # The Electron/web build does not need the legacy Tk UI.
    class _HeadlessTkRoot:
        pass

    tk = SimpleNamespace(Tk=_HeadlessTkRoot)
    filedialog = messagebox = SimpleNamespace()
    ttk = SimpleNamespace(Frame=object)

from mtg_upscaler import UpscaleCache, UpscaleError

try:
    from PIL import Image, ImageTk
except ImportError:  # pragma: no cover - handled by the UI at runtime
    Image = None
    ImageTk = None


APP_DIR = Path(__file__).resolve().parent
# Electron supplies separate resource and writable-data directories. Keeping
# these configurable preserves the existing behavior for normal Python runs.
RESOURCE_DIR = Path(os.environ.get("MTG_ART_PICKER_RESOURCE_DIR", APP_DIR)).expanduser().resolve()
DATA_DIR = Path(os.environ.get("MTG_ART_PICKER_DATA_DIR", APP_DIR)).expanduser().resolve()
SETTINGS_PATH = DATA_DIR / "mtg_art_picker_settings.json"
PREFERENCES_PATH = DATA_DIR / "mtg_art_picker_preferences.json"
DEFAULT_CACHE_DIR = Path(
    os.environ.get("MTG_ART_PICKER_CACHE_DIR", DATA_DIR / "scryfall_art_cache")
).expanduser().resolve()
DEFAULT_EXPORT_PARENT = DATA_DIR / "selected_print_arts"
DEFAULT_CUSTOM_ART_DIR = DATA_DIR / "custom_art"
UPDATE_SCRYFALL_IMAGES_SETTING = "update_scryfall_images"
DEFAULT_UPDATE_SCRYFALL_IMAGES = True
HIDE_PROMO_ARTS_SETTING = "hide_promo_arts"
DEFAULT_HIDE_PROMO_ARTS = False
HIDE_FOREIGN_ARTS_SETTING = "hide_foreign_arts"
DEFAULT_HIDE_FOREIGN_ARTS = False
HIDE_LIST_ARTS_SETTING = "hide_list_arts"
DEFAULT_HIDE_LIST_ARTS = False
IGNORE_BASICS_SETTING = "ignore_basics"
DEFAULT_IGNORE_BASICS = False
SCRYFALL_NAMED_URL = "https://api.scryfall.com/cards/named"
SCRYFALL_SEARCH_URL = "https://api.scryfall.com/cards/search"
SCRYFALL_BULK_DEFAULT_CARDS_URL = "https://api.scryfall.com/bulk-data/default_cards"
USER_AGENT = "mtg-art-picker/1.0"
REQUEST_DELAY_SECONDS = 0.5
REQUEST_MAX_ATTEMPTS = 5
REFRESH_CACHE_MAX_AGE_SECONDS = 6 * 60 * 60
BULK_DEFAULT_CARDS_NAME = "default_cards.json"
BULK_DEFAULT_CARDS_META_NAME = "default_cards_meta.json"
SECTION_NAMES = {
    "artifact",
    "artifacts",
    "battle",
    "battles",
    "commander",
    "creature",
    "creatures",
    "deck",
    "enchantment",
    "enchantments",
    "instant",
    "instants",
    "land",
    "lands",
    "mainboard",
    "maybeboard",
    "planeswalker",
    "planeswalkers",
    "sideboard",
    "sorcery",
    "sorceries",
}
TRUE_SETTING_VALUES = {"1", "true", "yes", "on"}
FALSE_SETTING_VALUES = {"0", "false", "no", "off", ""}
_SCRYFALL_REQUEST_LOCK = threading.Lock()
_SCRYFALL_LAST_REQUEST_AT = 0.0
_SCRYFALL_COOLDOWN_UNTIL = 0.0


@dataclass
class DeckEntry:
    name: str
    quantity: int
    requested_set_code: str = ""
    requested_collector_number: str = ""


@dataclass
class ArtOption:
    card_id: str
    oracle_id: str
    display_name: str
    printed_name: str
    set_code: str
    set_name: str
    collector_number: str
    released_at: str
    artist: str
    png_url: str
    cache_path: Path
    preview_url: str = ""
    preference_key: str = ""
    selected: bool = False

    @property
    def label(self) -> str:
        details = [
            self.set_code.upper(),
            f"#{self.collector_number}" if self.collector_number else "",
            self.released_at,
            self.artist,
        ]
        return " | ".join(part for part in details if part)

    @property
    def preference_id(self) -> str:
        return self.preference_key or self.cache_path.stem


@dataclass
class CardSlot:
    entry: DeckEntry
    options: list[ArtOption]
    current_index: int = 0
    requested_printing_missing: bool = False

    @property
    def current(self) -> ArtOption | None:
        if not self.options:
            return None
        return self.options[self.current_index]


def parse_deck_line(line: str) -> DeckEntry | None:
    line = line.strip()
    if not line:
        return None

    normalized_section = re.sub(r"^=+\s*|\s*=+$", "", line).strip().lower().rstrip(":")
    section_without_count = re.sub(r"\s*\(\d+\)\s*$", "", normalized_section).strip()
    if section_without_count in SECTION_NAMES:
        return None
    if line.startswith(("//", "#")):
        return None

    quantity = 1
    match = re.match(r"^\s*(?P<quantity>\d+)\s*x?\s+(?P<name>.+)$", line, flags=re.IGNORECASE)
    if match:
        quantity = int(match.group("quantity"))
        line = match.group("name").strip()

    line = re.sub(r"\s+\*[A-Z]+\*\s*$", "", line, flags=re.IGNORECASE).strip()
    requested_set_code = ""
    requested_collector_number = ""
    printing_match = re.search(
        r"\s+\((?P<set>[A-Za-z0-9]{2,8})\)\s+(?P<number>[A-Za-z0-9][A-Za-z0-9\-_.]*[A-Za-z0-9]?)\s*$",
        line,
    )
    if printing_match:
        requested_set_code = printing_match.group("set").lower()
        requested_collector_number = normalize_collector_number(printing_match.group("number"))
        line = line[: printing_match.start()].strip()

    line = re.sub(r"\s+\[[^\]]+\]\s*$", "", line).strip()

    if quantity < 1 or not line:
        return None
    return DeckEntry(
        name=line,
        quantity=quantity,
        requested_set_code=requested_set_code,
        requested_collector_number=requested_collector_number,
    )


def normalize_name(name: str) -> str:
    name = name.casefold()
    name = name.replace("&", " and ")
    name = re.sub(r"['`]", "", name)
    name = re.sub(r"[^a-z0-9]+", " ", name)
    return re.sub(r"\s+", " ", name).strip()


BASIC_LAND_NAMES = {
    "plains",
    "island",
    "swamp",
    "mountain",
    "forest",
    "wastes",
    "snow covered plains",
    "snow covered island",
    "snow covered swamp",
    "snow covered mountain",
    "snow covered forest",
}


def is_basic_land_name(name: str) -> bool:
    return normalize_name(name) in BASIC_LAND_NAMES


def exclude_basic_lands(entries: list[DeckEntry]) -> list[DeckEntry]:
    return [entry for entry in entries if not is_basic_land_name(entry.name)]


def normalize_collector_number(number: str) -> str:
    return re.sub(r"[^a-z0-9]", "", number.casefold())


def parse_deck_list(deck_text: str) -> list[DeckEntry]:
    entries: list[DeckEntry] = []
    for line in deck_text.splitlines():
        entry = parse_deck_line(line)
        if entry is not None:
            # Each input line is an independently selectable picker slot. Keeping
            # duplicate names separate also preserves the decklist's print order.
            entries.append(entry)
    return entries


def safe_filename(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*]', "", value)
    value = re.sub(r"\s+", " ", value).strip().rstrip(".")
    return value or "card"


def safe_folder_name(value: str) -> str:
    return safe_filename(value).rstrip(". ") or "selected-print-arts"


def custom_art_id(card_name: str, path: Path) -> str:
    card_key = normalize_name(card_name).replace(" ", "_")
    file_key = normalize_name(path.stem).replace(" ", "_")
    return f"custom__{card_key}__{file_key}"


def load_settings() -> dict[str, str]:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items()}


def save_settings(settings: dict[str, object]) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def coerce_setting_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().casefold()
    if normalized in TRUE_SETTING_VALUES:
        return True
    if normalized in FALSE_SETTING_VALUES:
        return False
    return default


def load_preferences() -> dict[str, str]:
    if not PREFERENCES_PATH.exists():
        return {}
    try:
        data = json.loads(PREFERENCES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items() if isinstance(value, str)}


def save_preferences(preferences: dict[str, str]) -> None:
    PREFERENCES_PATH.parent.mkdir(parents=True, exist_ok=True)
    PREFERENCES_PATH.write_text(json.dumps(preferences, indent=2), encoding="utf-8")


def sort_art_options(
    options: list[ArtOption],
    sort_order: str = "oldest",
) -> list[ArtOption]:
    sorted_options = list(options)
    sorted_options.sort(key=lambda option: (option.released_at or "", option.set_code, option.collector_number), reverse=sort_order == "newest")
    return sorted_options


def is_allowed_scryfall_print(card: dict) -> bool:
    return "paper" in (card.get("games") or [])


def is_foreign_scryfall_print(card: dict) -> bool:
    return str(card.get("lang") or "en").casefold() != "en"


def is_list_scryfall_print(card: dict) -> bool:
    set_code = str(card.get("set") or "").casefold()
    set_name = str(card.get("set_name") or "").casefold()
    return set_code == "plst" or set_name == "the list"


def is_promo_scryfall_print(card: dict) -> bool:
    set_type = str(card.get("set_type") or "").casefold()
    return bool(card.get("promo")) or set_type == "promo"


def is_art_series_print(card: dict) -> bool:
    set_name = str(card.get("set_name") or "").casefold()
    set_type = str(card.get("set_type") or "").casefold()
    layout = str(card.get("layout") or "").casefold()
    type_line = str(card.get("type_line") or "").casefold()
    return (
        layout == "art_series"
        or set_type == "memorabilia"
        or "art series" in set_name
        or type_line == "card"
    )


class ScryfallClient:
    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir.expanduser().resolve()
        self.metadata_dir = cache_dir / "metadata"
        self.image_dir = cache_dir / "png"
        self.bulk_dir = cache_dir / "bulk"
        self.metadata_dir.mkdir(parents=True, exist_ok=True)
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self.bulk_dir.mkdir(parents=True, exist_ok=True)
        self.upscaler = UpscaleCache(cache_dir, RESOURCE_DIR)
        self.last_request_at = 0.0
        self._bulk_cards: list[dict] | None = None
        self._bulk_index: dict[str, list[dict]] | None = None

    def _custom_art_options(self, card_name: str) -> list[ArtOption]:
        """Find app-local art and art stored beside the selected cache folder."""
        custom_dirs = [self.cache_dir.parent / "custom_art", DEFAULT_CUSTOM_ART_DIR]
        options: list[ArtOption] = []
        seen_dirs: set[Path] = set()
        seen_options: set[str] = set()
        for custom_dir in custom_dirs:
            resolved_dir = custom_dir.expanduser().resolve()
            if resolved_dir in seen_dirs:
                continue
            seen_dirs.add(resolved_dir)
            for option in custom_art_options(card_name, resolved_dir):
                if option.preference_id in seen_options:
                    continue
                seen_options.add(option.preference_id)
                options.append(option)
        return options

    def fetch_options(
        self,
        card_name: str,
        status_callback,
        force_refresh: bool = False,
        hide_promos: bool = False,
        hide_foreign: bool = False,
        hide_list: bool = False,
        defer_images: bool = False,
    ) -> list[ArtOption]:
        custom_options = self._custom_art_options(card_name)
        if not defer_images:
            self._upscale_sources([option.cache_path for option in custom_options], status_callback)

        if not force_refresh:
            if custom_options:
                cached_options = self._cached_options(
                    card_name,
                    status_callback,
                    hide_promos=hide_promos,
                    hide_foreign=hide_foreign,
                    hide_list=hide_list,
                    use_bulk=False,
                    include_uncached=defer_images,
                )
                if cached_options:
                    status_callback(f"Using cached and custom art for {card_name}")
                    cached_options.extend(custom_options)
                    return cached_options
                status_callback(f"Using custom art for {card_name}")
                return custom_options

            cached_options = self._cached_options(
                card_name,
                status_callback,
                hide_promos=hide_promos,
                hide_foreign=hide_foreign,
                hide_list=hide_list,
                include_uncached=defer_images,
            )
            if cached_options:
                status_callback(f"Using cached art for {card_name}")
                return cached_options
            status_callback(f"No cached art found for {card_name}. Checking Scryfall.")
        else:
            status_callback(f"Refreshing Scryfall printings for {card_name}")

            recent_cards, recent_oracle_id = self._recent_refresh(card_name)
            if recent_cards and recent_oracle_id:
                status_callback(f"Using recent Scryfall refresh for {card_name}")
                options = self._options_from_cards(
                    recent_cards,
                    recent_oracle_id,
                    card_name,
                    status_callback,
                    download_missing=not defer_images,
                    hide_promos=hide_promos,
                    hide_foreign=hide_foreign,
                    hide_list=hide_list,
                    include_uncached=defer_images,
                )
                options.extend(custom_options)
                return options

        try:
            cards, oracle_id = self._get_prints_from_bulk_or_api(card_name, status_callback, force_refresh=force_refresh)
        except Exception:
            if custom_options:
                status_callback(f"Could not check Scryfall for {card_name}. Using custom art.")
                return custom_options
            raise
        if not oracle_id:
            return custom_options

        metadata_path = self.metadata_dir / f"{oracle_id}.json"
        metadata_path.write_text(json.dumps(cards, indent=2), encoding="utf-8")

        options = self._options_from_cards(
            cards,
            oracle_id,
            card_name,
            status_callback,
            download_missing=not defer_images,
            hide_promos=hide_promos,
            hide_foreign=hide_foreign,
            hide_list=hide_list,
            include_uncached=defer_images,
        )
        options.extend(custom_options)
        return options

    def _recent_refresh(self, card_name: str) -> tuple[list[dict], str]:
        exact = self._get_cached_exact_card(card_name)
        oracle_id = str(exact.get("oracle_id") or "")
        if not oracle_id:
            return [], ""
        metadata_path = self.metadata_dir / f"{oracle_id}.json"
        try:
            age = time.time() - metadata_path.stat().st_mtime
        except OSError:
            return [], ""
        if age < 0 or age > REFRESH_CACHE_MAX_AGE_SECONDS:
            return [], ""
        return self._load_cards_metadata(metadata_path), oracle_id

    def _cached_options(
        self,
        card_name: str,
        status_callback,
        hide_promos: bool = False,
        hide_foreign: bool = False,
        hide_list: bool = False,
        use_bulk: bool = True,
        include_uncached: bool = False,
    ) -> list[ArtOption]:
        cards, oracle_id = self._get_cached_cards(card_name, status_callback, use_bulk=use_bulk)
        if not cards:
            return []
        return self._options_from_cards(
            cards,
            oracle_id,
            card_name,
            status_callback,
            download_missing=False,
            hide_promos=hide_promos,
            hide_foreign=hide_foreign,
            hide_list=hide_list,
            include_uncached=include_uncached,
        )

    def _get_cached_cards(self, card_name: str, status_callback, use_bulk: bool = True) -> tuple[list[dict], str]:
        oracle_id = ""
        exact = self._get_cached_exact_card(card_name)
        if exact:
            oracle_id = str(exact.get("oracle_id") or "")

        if oracle_id:
            cards = self._load_cards_metadata(self.metadata_dir / f"{oracle_id}.json")
            if cards:
                return cards, oracle_id

        if not use_bulk:
            return [], oracle_id

        cards = self._cards_from_cached_bulk(card_name, status_callback)
        if cards:
            oracle_id = str(cards[0].get("oracle_id") or oracle_id)
            if oracle_id:
                metadata_cards = self._load_cards_metadata(self.metadata_dir / f"{oracle_id}.json")
                if metadata_cards:
                    return metadata_cards, oracle_id
            return cards, oracle_id

        return [], oracle_id

    def _get_cached_exact_card(self, card_name: str) -> dict:
        cache_key = safe_filename(normalize_name(card_name)).replace(" ", "_")
        cache_path = self.metadata_dir / f"named_{cache_key}.json"
        if not cache_path.exists():
            return {}
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _load_cards_metadata(self, metadata_path: Path) -> list[dict]:
        if not metadata_path.exists():
            return []
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return data if isinstance(data, list) else []

    def _cards_from_cached_bulk(self, card_name: str, status_callback) -> list[dict]:
        if self._bulk_cards is None or self._bulk_index is None:
            bulk_path = self.bulk_dir / BULK_DEFAULT_CARDS_NAME
            if not bulk_path.exists():
                return []
            status_callback("Loading cached Scryfall bulk data")
            try:
                with bulk_path.open("r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, json.JSONDecodeError):
                return []
            if not isinstance(data, list):
                return []
            self._bulk_cards = data
            self._bulk_index = self._build_bulk_index(data)

        key = normalize_name(card_name)
        cards = self._bulk_index.get(key, []) if self._bulk_index else []
        return sorted(cards, key=lambda item: (item.get("released_at") or "", item.get("set") or ""))

    def _options_from_cards(
        self,
        cards: list[dict],
        oracle_id: str,
        card_name: str,
        status_callback,
        download_missing: bool,
        hide_promos: bool = False,
        hide_foreign: bool = False,
        hide_list: bool = False,
        include_uncached: bool = False,
    ) -> list[ArtOption]:
        options: list[ArtOption] = []
        downloaded_sources: list[Path] = []
        seen_urls: set[str] = set()
        for card in sorted(cards, key=lambda item: (item.get("released_at") or "", item.get("set") or "")):
            if not is_allowed_scryfall_print(card):
                continue
            if is_art_series_print(card):
                continue
            if hide_promos and is_promo_scryfall_print(card):
                continue
            if hide_foreign and is_foreign_scryfall_print(card):
                continue
            if hide_list and is_list_scryfall_print(card):
                continue
            for face_index, face in enumerate(self._image_faces(card)):
                png_url = face.get("png_url", "")
                if not png_url or png_url in seen_urls:
                    continue
                card_id = str(card.get("id") or "")
                if not card_id:
                    continue
                suffix = f"_face_{face_index + 1}" if face_index else ""
                cache_name = f"{card_id}{suffix}.png"
                display_name = str(face.get("name") or card.get("name") or card_name)
                option = ArtOption(
                    card_id=card_id,
                    oracle_id=str(card.get("oracle_id") or oracle_id),
                    display_name=display_name,
                    printed_name=str(card.get("name") or display_name),
                    set_code=str(card.get("set") or ""),
                    set_name=str(card.get("set_name") or ""),
                    collector_number=str(card.get("collector_number") or ""),
                    released_at=str(card.get("released_at") or ""),
                    artist=str(face.get("artist") or card.get("artist") or ""),
                    png_url=png_url,
                    cache_path=self.image_dir / cache_name,
                    preview_url=str(face.get("preview_url") or png_url),
                )
                if download_missing:
                    if self._download_png(option, status_callback):
                        downloaded_sources.append(option.cache_path)
                elif not include_uncached and not self._has_cached_png(option.cache_path):
                    continue
                seen_urls.add(png_url)
                options.append(option)
        self._upscale_sources(downloaded_sources, status_callback)
        return options

    def _upscale_sources(self, sources: list[Path], status_callback) -> None:
        if not sources:
            return
        try:
            self.upscaler.ensure_batch(sources, status_callback=status_callback)
        except (OSError, UpscaleError) as exc:
            status_callback(f"AI upscaling deferred: {exc}")

    def _has_cached_png(self, path: Path) -> bool:
        try:
            return path.exists() and path.stat().st_size > 0
        except OSError:
            return False
    def _get_exact_card(self, card_name: str, status_callback=None, force_refresh: bool = False) -> dict:
        cache_key = safe_filename(normalize_name(card_name)).replace(" ", "_")
        cache_path = self.metadata_dir / f"named_{cache_key}.json"
        if cache_path.exists() and not force_refresh:
            try:
                return json.loads(cache_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass

        data = self._request_json(SCRYFALL_NAMED_URL, {"exact": card_name}, status_callback=status_callback)
        cache_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return data

    def _get_prints_from_bulk_or_api(
        self,
        card_name: str,
        status_callback,
        force_refresh: bool = False,
    ) -> tuple[list[dict], str]:
        # A per-deck refresh should not redownload Scryfall's ~550 MB bulk
        # database. Query the named-card/search APIs for current printings and
        # reserve bulk data for filling gaps during normal cached operation.
        bulk_path = self.bulk_dir / BULK_DEFAULT_CARDS_NAME
        if not force_refresh and bulk_path.exists():
            try:
                cards = self._cards_from_bulk(card_name, status_callback)
            except Exception as exc:
                status_callback(f"Bulk data unavailable for {card_name}: {exc}. Falling back to Scryfall search.")
                cards = []
            if cards:
                oracle_id = str(cards[0].get("oracle_id") or "")
                return cards, oracle_id

        exact = self._get_cached_exact_card(card_name) if force_refresh else {}
        if not exact:
            exact = self._get_exact_card(card_name, status_callback, force_refresh=force_refresh)
        oracle_id = str(exact.get("oracle_id") or "") if exact else ""
        if not oracle_id:
            return [], ""
        return self._search_prints(oracle_id, status_callback), oracle_id

    def _cards_from_bulk(self, card_name: str, status_callback, force_refresh: bool = False) -> list[dict]:
        self._ensure_bulk_loaded(status_callback, force_refresh=force_refresh)
        if not self._bulk_index:
            return []
        key = normalize_name(card_name)
        cards = self._bulk_index.get(key, [])
        return sorted(cards, key=lambda item: (item.get("released_at") or "", item.get("set") or ""))

    def _ensure_bulk_loaded(self, status_callback, force_refresh: bool = False) -> None:
        if self._bulk_cards is not None and self._bulk_index is not None:
            return

        bulk_path = self.bulk_dir / BULK_DEFAULT_CARDS_NAME
        meta_path = self.bulk_dir / BULK_DEFAULT_CARDS_META_NAME
        if force_refresh or not bulk_path.exists():
            self._refresh_bulk_file(bulk_path, meta_path, status_callback)

        status_callback("Loading Scryfall bulk data from cache")
        with bulk_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, list):
            raise RuntimeError("Scryfall bulk data did not contain a card list.")
        self._bulk_cards = data
        self._bulk_index = self._build_bulk_index(data)

    def _refresh_bulk_file(self, bulk_path: Path, meta_path: Path, status_callback) -> None:
        status_callback("Checking Scryfall bulk data")
        bulk_meta = self._request_json(SCRYFALL_BULK_DEFAULT_CARDS_URL, status_callback=status_callback)
        download_uri = str(bulk_meta.get("download_uri") or "")
        updated_at = str(bulk_meta.get("updated_at") or "")
        if not download_uri:
            raise RuntimeError("Scryfall bulk data response did not include a download URI.")

        if bulk_path.exists() and meta_path.exists():
            try:
                cached_meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cached_meta = {}
            if cached_meta.get("updated_at") == updated_at:
                status_callback("Scryfall bulk data is already current")
                return

        status_callback("Downloading Scryfall default_cards bulk data")
        self._respect_rate_limit()
        request = urllib.request.Request(download_uri, headers={"User-Agent": USER_AGENT})
        tmp_path = bulk_path.with_suffix(".tmp")
        with urllib.request.urlopen(request, timeout=180) as response, tmp_path.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
        tmp_path.replace(bulk_path)
        meta_path.write_text(json.dumps(bulk_meta, indent=2), encoding="utf-8")

    def _build_bulk_index(self, cards: list[dict]) -> dict[str, list[dict]]:
        index: dict[str, list[dict]] = {}
        seen_by_key: dict[str, set[str]] = {}
        for card in cards:
            if not is_allowed_scryfall_print(card):
                continue
            card_id = str(card.get("id") or "")
            if not card_id:
                continue
            names = [str(card.get("name") or ""), str(card.get("printed_name") or "")]
            for face in card.get("card_faces") or []:
                names.append(str(face.get("name") or ""))
                names.append(str(face.get("printed_name") or ""))
            for name in names:
                key = normalize_name(name)
                if not key:
                    continue
                seen = seen_by_key.setdefault(key, set())
                if card_id in seen:
                    continue
                seen.add(card_id)
                index.setdefault(key, []).append(card)
        return index

    def _search_prints(self, oracle_id: str, status_callback=None) -> list[dict]:
        query = f"oracleid:{oracle_id}"
        params = {"q": query, "unique": "prints", "order": "released", "include_extras": "true"}
        cards: list[dict] = []
        url = SCRYFALL_SEARCH_URL + "?" + urllib.parse.urlencode(params)
        while url:
            data = self._request_json(url, status_callback=status_callback)
            cards.extend(data.get("data", []))
            url = data.get("next_page") if data.get("has_more") else ""
        return cards

    def _download_png(self, option: ArtOption, status_callback) -> bool:
        if option.cache_path.exists() and option.cache_path.stat().st_size > 0:
            return False

        status_callback(f"Downloading {option.display_name} ({option.set_code.upper()} #{option.collector_number})")
        request = urllib.request.Request(option.png_url, headers={"User-Agent": USER_AGENT})
        for attempt in range(1, REQUEST_MAX_ATTEMPTS + 1):
            self._respect_rate_limit()
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    option.cache_path.write_bytes(response.read())
                return True
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < REQUEST_MAX_ATTEMPTS:
                    self._pause_after_rate_limit(exc, status_callback, attempt)
                    continue
                raise
        return False

    def _request_json(self, url: str, params: dict[str, str] | None = None, status_callback=None) -> dict:
        if params:
            url = url + "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        for attempt in range(1, REQUEST_MAX_ATTEMPTS + 1):
            self._respect_rate_limit()
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < REQUEST_MAX_ATTEMPTS:
                    self._pause_after_rate_limit(exc, status_callback, attempt)
                    continue
                try:
                    details = json.loads(exc.read().decode("utf-8"))
                    message = details.get("details") or details.get("message") or str(exc)
                except Exception:
                    message = str(exc)
                raise RuntimeError(message) from exc
        raise RuntimeError("Scryfall request failed after repeated retries.")

    def _respect_rate_limit(self) -> None:
        global _SCRYFALL_LAST_REQUEST_AT
        with _SCRYFALL_REQUEST_LOCK:
            now = time.monotonic()
            wait_for = max(
                0.0,
                _SCRYFALL_LAST_REQUEST_AT + REQUEST_DELAY_SECONDS - now,
                _SCRYFALL_COOLDOWN_UNTIL - now,
            )
            if wait_for > 0:
                time.sleep(wait_for)
            _SCRYFALL_LAST_REQUEST_AT = time.monotonic()
            self.last_request_at = _SCRYFALL_LAST_REQUEST_AT

    def _pause_after_rate_limit(
        self,
        exc: urllib.error.HTTPError,
        status_callback=None,
        attempt: int = 1,
    ) -> None:
        global _SCRYFALL_COOLDOWN_UNTIL
        retry_after = str(exc.headers.get("Retry-After") or "").strip() if exc.headers else ""
        wait_seconds = 0.0
        if retry_after:
            try:
                wait_seconds = float(retry_after)
            except ValueError:
                try:
                    retry_time = parsedate_to_datetime(retry_after)
                    wait_seconds = retry_time.timestamp() - time.time()
                except (TypeError, ValueError, OverflowError):
                    wait_seconds = 0.0
        if wait_seconds <= 0:
            wait_seconds = min(60.0, 5.0 * (2 ** max(0, attempt - 1)))
        wait_seconds = max(1.0, min(wait_seconds, 120.0))
        with _SCRYFALL_REQUEST_LOCK:
            _SCRYFALL_COOLDOWN_UNTIL = max(
                _SCRYFALL_COOLDOWN_UNTIL,
                time.monotonic() + wait_seconds,
            )
        if status_callback:
            status_callback(
                f"Scryfall rate limit reached. Cooling down for {int(round(wait_seconds))} seconds "
                f"before retry {attempt + 1}/{REQUEST_MAX_ATTEMPTS}."
            )
        time.sleep(wait_seconds)
        self.last_request_at = time.monotonic()

    def _image_faces(self, card: dict) -> list[dict[str, str]]:
        if isinstance(card.get("image_uris"), dict) and card["image_uris"].get("png"):
            image_uris = card["image_uris"]
            return [
                {
                    "name": str(card.get("name") or ""),
                    "artist": str(card.get("artist") or ""),
                    "png_url": str(image_uris["png"]),
                    "preview_url": str(image_uris.get("normal") or image_uris.get("small") or image_uris["png"]),
                }
            ]

        faces: list[dict[str, str]] = []
        for face in card.get("card_faces") or []:
            image_uris = face.get("image_uris") or {}
            if image_uris.get("png"):
                faces.append(
                    {
                        "name": str(face.get("name") or card.get("name") or ""),
                        "artist": str(face.get("artist") or card.get("artist") or ""),
                        "png_url": str(image_uris["png"]),
                        "preview_url": str(
                            image_uris.get("normal") or image_uris.get("small") or image_uris["png"]
                        ),
                    }
                )
        return faces


def custom_art_name_keys(card_name: str) -> set[str]:
    names = [card_name]
    names.extend(part.strip() for part in re.split(r"\s+//\s+", card_name) if part.strip())
    return {normalize_name(name) for name in names if normalize_name(name)}


def custom_art_options(card_name: str, custom_dir: Path = DEFAULT_CUSTOM_ART_DIR) -> list[ArtOption]:
    if not custom_dir.is_dir():
        return []

    keys = custom_art_name_keys(card_name)
    paths: list[Path] = []

    for path in custom_dir.glob("*.png"):
        if normalize_name(path.stem) in keys:
            paths.append(path)

    for folder in custom_dir.iterdir():
        if folder.is_dir() and normalize_name(folder.name) in keys:
            paths.extend(sorted(folder.glob("*.png"), key=lambda item: item.name.casefold()))

    options: list[ArtOption] = []
    seen: set[Path] = set()
    for index, path in enumerate(paths, start=1):
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        options.append(
            ArtOption(
                card_id=custom_art_id(card_name, path),
                oracle_id="custom",
                display_name=card_name,
                printed_name=path.stem,
                set_code="CUSTOM",
                set_name="Custom Art",
                collector_number=str(index),
                released_at="",
                artist="Custom art",
                png_url="",
                cache_path=path,
                preference_key=custom_art_id(card_name, path),
            )
        )
    return options


class ScrollFrame(ttk.Frame):
    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, bg="#17212b")
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.content = ttk.Frame(self.canvas, style="Bg.TFrame")
        self.window_id = self.canvas.create_window((0, 0), window=self.content, anchor="nw")

        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.content.bind("<Configure>", self._update_region)
        self.canvas.bind("<Configure>", self._update_width)
        self.canvas.bind_all("<MouseWheel>", self._mousewheel)

    def _update_region(self, _event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _update_width(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self.window_id, width=event.width)

    def _mousewheel(self, event: tk.Event) -> None:
        if self.winfo_viewable():
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


class ArtPickerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("MTG Scryfall Art Picker")
        self.geometry("1260x860")
        self.minsize(980, 700)
        self.configure(bg="#17212b")

        self.settings = load_settings()
        self.preferences = load_preferences()
        self.cache_dir = tk.StringVar(value=self.settings.get("cache_dir", str(DEFAULT_CACHE_DIR)))
        self.export_parent = tk.StringVar(value=self.settings.get("export_parent", str(DEFAULT_EXPORT_PARENT)))
        self.export_name = tk.StringVar(value="")
        self.sort_order = tk.StringVar(value="oldest")
        self.update_scryfall_images = tk.BooleanVar(
            value=coerce_setting_bool(
                self.settings.get(UPDATE_SCRYFALL_IMAGES_SETTING),
                DEFAULT_UPDATE_SCRYFALL_IMAGES,
            )
        )
        self.hide_promo_arts = tk.BooleanVar(
            value=coerce_setting_bool(
                self.settings.get(HIDE_PROMO_ARTS_SETTING),
                DEFAULT_HIDE_PROMO_ARTS,
            )
        )
        self.hide_foreign_arts = tk.BooleanVar(
            value=coerce_setting_bool(
                self.settings.get(HIDE_FOREIGN_ARTS_SETTING),
                DEFAULT_HIDE_FOREIGN_ARTS,
            )
        )
        self.hide_list_arts = tk.BooleanVar(
            value=coerce_setting_bool(
                self.settings.get(HIDE_LIST_ARTS_SETTING),
                DEFAULT_HIDE_LIST_ARTS,
            )
        )
        self.status = tk.StringVar(value="Paste a decklist, fetch arts, then choose one or more printings per card.")
        self.activity_text = tk.StringVar(value="Waiting for a decklist.")
        self.progress_value = tk.DoubleVar(value=0)

        self.slots: list[CardSlot] = []
        self.slot_widgets: list[dict[str, object]] = []
        self.image_refs: dict[int, ImageTk.PhotoImage] = {}
        self.work_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None

        self._configure_styles()
        self._build_layout()
        self.after(100, self._drain_queue)

    def _configure_styles(self) -> None:
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Bg.TFrame", background="#17212b")
        style.configure("Panel.TFrame", background="#2b3a45")
        style.configure("Subtle.TFrame", background="#22313b")
        style.configure("Title.TLabel", background="#17212b", foreground="#f6f3e8", font=("Segoe UI", 20, "bold"))
        style.configure("Label.TLabel", background="#17212b", foreground="#dbe7ee", font=("Segoe UI", 10))
        style.configure("Status.TLabel", background="#17212b", foreground="#76d7c4", font=("Segoe UI", 10))
        style.configure("CardTitle.TLabel", background="#2b3a45", foreground="#f6f3e8", font=("Segoe UI", 12, "bold"))
        style.configure("CardMeta.TLabel", background="#2b3a45", foreground="#cbd6dd", font=("Segoe UI", 9))
        style.configure("Missing.TLabel", background="#2b3a45", foreground="#ffcf8a", font=("Segoe UI", 10, "bold"))
        style.configure("TButton", font=("Segoe UI", 10), padding=7)
        style.configure("TCheckbutton", background="#17212b", foreground="#dbe7ee", font=("Segoe UI", 10))
        style.configure("Accent.TButton", background="#2e8f84", foreground="#ffffff", font=("Segoe UI", 10, "bold"), padding=8)
        style.map("Accent.TButton", background=[("active", "#36a99b")])

    def _build_layout(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.page_scroll = ScrollFrame(self)
        self.page_scroll.grid(row=0, column=0, sticky="nsew")
        page = self.page_scroll.content
        page.grid_columnconfigure(0, weight=1)

        header = ttk.Frame(page, style="Bg.TFrame", padding=(18, 14, 18, 8))
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(1, weight=1)
        ttk.Label(header, text="MTG Scryfall Art Picker", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        self.fetch_button = ttk.Button(header, text="Fetch arts", style="Accent.TButton", command=self.fetch_arts)
        self.fetch_button.grid(row=0, column=2, padx=(10, 0))
        self.save_preferences_button = ttk.Button(header, text="Save preferences", command=self.save_visible_preferences)
        self.save_preferences_button.grid(row=0, column=3, padx=(8, 0))
        self.export_button = ttk.Button(header, text="Export selected", command=self.export_selected)
        self.export_button.grid(row=0, column=4, padx=(8, 0))

        controls = ttk.Frame(page, style="Bg.TFrame", padding=(18, 0, 18, 10))
        controls.grid(row=1, column=0, sticky="ew")
        controls.grid_columnconfigure(1, weight=1)
        self._path_row(controls, 0, "Cache folder", self.cache_dir, self.choose_cache_dir)
        self._path_row(controls, 1, "Export parent", self.export_parent, self.choose_export_parent)
        ttk.Label(controls, text="Export folder name", style="Label.TLabel").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(controls, textvariable=self.export_name).grid(row=2, column=1, sticky="ew", padx=(10, 10), pady=(8, 0))
        ttk.Label(controls, text="Art order").grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(
            controls,
            textvariable=self.sort_order,
            values=("oldest", "newest"),
            state="readonly",
            width=12,
        ).grid(row=3, column=1, sticky="w", padx=(10, 10), pady=(8, 0))
        ttk.Checkbutton(
            controls,
            text="Update Scryfall Images",
            variable=self.update_scryfall_images,
            command=self.save_current_settings,
        ).grid(row=3, column=2, sticky="w", pady=(8, 0))
        ttk.Checkbutton(
            controls,
            text="Hide Promo Arts",
            variable=self.hide_promo_arts,
            command=self.save_current_settings,
        ).grid(row=4, column=2, sticky="w", pady=(8, 0))
        ttk.Checkbutton(
            controls,
            text="Hide Foreign Arts",
            variable=self.hide_foreign_arts,
            command=self.save_current_settings,
        ).grid(row=5, column=2, sticky="w", pady=(8, 0))
        ttk.Checkbutton(
            controls,
            text="Hide The List Arts",
            variable=self.hide_list_arts,
            command=self.save_current_settings,
        ).grid(row=6, column=2, sticky="w", pady=(8, 0))

        deck_frame = ttk.Frame(page, style="Bg.TFrame", padding=(18, 0, 18, 10))
        deck_frame.grid(row=2, column=0, sticky="ew")
        deck_frame.grid_columnconfigure(0, weight=1)
        ttk.Label(deck_frame, text="Decklist", style="Label.TLabel").grid(row=0, column=0, sticky="w")
        self.deck_text = tk.Text(deck_frame, height=8, wrap="word", undo=True, bg="#f6f8fa", fg="#18222b", font=("Consolas", 11))
        self.deck_text.grid(row=1, column=0, sticky="ew", pady=(5, 0))

        progress_frame = ttk.Frame(page, style="Bg.TFrame", padding=(18, 0, 18, 10))
        progress_frame.grid(row=3, column=0, sticky="ew")
        progress_frame.grid_columnconfigure(0, weight=1)
        ttk.Label(progress_frame, textvariable=self.status, style="Status.TLabel").grid(row=0, column=0, sticky="ew", pady=(0, 5))
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_value, maximum=1, mode="determinate")
        self.progress_bar.grid(row=1, column=0, sticky="ew")
        ttk.Label(progress_frame, textvariable=self.activity_text, style="Label.TLabel").grid(row=2, column=0, sticky="ew", pady=(6, 0))

        self.cards_frame = ttk.Frame(page, style="Bg.TFrame", padding=(18, 0, 18, 18))
        self.cards_frame.grid(row=4, column=0, sticky="ew")

    def _path_row(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar, command) -> None:
        ttk.Label(parent, text=label, style="Label.TLabel").grid(row=row, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=(10, 10), pady=(8, 0))
        ttk.Button(parent, text="Browse", command=command).grid(row=row, column=2, sticky="ew", pady=(8, 0))

    def choose_cache_dir(self) -> None:
        selected = filedialog.askdirectory(title="Choose the Scryfall art cache folder")
        if selected:
            self.cache_dir.set(selected)
            self.save_current_settings()

    def choose_export_parent(self) -> None:
        selected = filedialog.askdirectory(title="Choose where selected art folders should be created")
        if selected:
            self.export_parent.set(selected)
            self.save_current_settings()

    def save_current_settings(self) -> None:
        save_settings(
            {
                "cache_dir": self.cache_dir.get().strip(),
                "export_parent": self.export_parent.get().strip(),
                UPDATE_SCRYFALL_IMAGES_SETTING: "true" if self.update_scryfall_images.get() else "false",
                HIDE_PROMO_ARTS_SETTING: "true" if self.hide_promo_arts.get() else "false",
                HIDE_FOREIGN_ARTS_SETTING: "true" if self.hide_foreign_arts.get() else "false",
                HIDE_LIST_ARTS_SETTING: "true" if self.hide_list_arts.get() else "false",
            }
        )

    def fetch_arts(self) -> None:
        if Image is None or ImageTk is None:
            messagebox.showerror("Missing Pillow", "This tool needs Pillow for image previews. Install it with: py -m pip install pillow")
            return
        if self.worker is not None and self.worker.is_alive():
            return
        entries = parse_deck_list(self.deck_text.get("1.0", "end"))
        if not entries:
            messagebox.showerror("Missing decklist", "Paste at least one card name.")
            return

        self.save_current_settings()
        self.slots = []
        self._render_slots()
        self.fetch_button.configure(state="disabled")
        self.save_preferences_button.configure(state="disabled")
        self.export_button.configure(state="disabled")
        self.progress_value.set(0)
        self.progress_bar.configure(maximum=max(len(entries), 1), mode="determinate")
        self.activity_text.set("Starting fetch...")
        self.status.set(f"Fetching art options for {len(entries)} card(s)...")
        update_scryfall_images = self.update_scryfall_images.get()
        hide_promo_arts = self.hide_promo_arts.get()
        hide_foreign_arts = self.hide_foreign_arts.get()
        hide_list_arts = self.hide_list_arts.get()
        self.worker = threading.Thread(
            target=self._fetch_worker,
            args=(entries, update_scryfall_images, hide_promo_arts, hide_foreign_arts, hide_list_arts),
            daemon=True,
        )
        self.worker.start()

    def _fetch_worker(
        self,
        entries: list[DeckEntry],
        update_scryfall_images: bool,
        hide_promo_arts: bool,
        hide_foreign_arts: bool,
        hide_list_arts: bool,
    ) -> None:
        client = ScryfallClient(Path(self.cache_dir.get()).expanduser())
        slots: list[CardSlot] = []
        for index, entry in enumerate(entries, start=1):
            self.work_queue.put(("progress", index - 1))
            self.work_queue.put(("status", f"[{index}/{len(entries)}] Fetching {entry.name}"))
            try:
                options = client.fetch_options(
                    entry.name,
                    lambda text: self.work_queue.put(("status", text)),
                    force_refresh=update_scryfall_images,
                    hide_promos=hide_promo_arts,
                    hide_foreign=hide_foreign_arts,
                    hide_list=hide_list_arts,
                )
                options = sort_art_options(
                    options,
                    sort_order=self.sort_order.get(),
                )
                current_index, requested_printing_missing = self._apply_default_selection(entry, options)
                slots.append(
                    CardSlot(
                        entry=entry,
                        options=options,
                        current_index=current_index,
                        requested_printing_missing=requested_printing_missing,
                    )
                )
            except Exception as exc:
                self.work_queue.put(("error", f"{entry.name}: {exc}"))
            self.work_queue.put(("progress", index))
        self.work_queue.put(("done", slots))

    def _apply_default_selection(self, entry: DeckEntry, options: list[ArtOption]) -> tuple[int, bool]:
        for option in options:
            option.selected = False
        if not options:
            return 0, bool(entry.requested_set_code and entry.requested_collector_number)

        requested_index = self._requested_option_index(entry, options)
        if requested_index is not None:
            options[requested_index].selected = True
            return requested_index, False

        requested_printing_missing = bool(entry.requested_set_code and entry.requested_collector_number)

        for index, option in enumerate(options):
            if option.set_code.casefold() == "custom":
                option.selected = True
                return index, requested_printing_missing

        preferred_id = self.preferences.get(normalize_name(entry.name), "")
        for index, option in enumerate(options):
            if option.preference_id == preferred_id:
                option.selected = True
                return index, requested_printing_missing
        return 0, requested_printing_missing

    def _requested_option_index(self, entry: DeckEntry, options: list[ArtOption]) -> int | None:
        if not entry.requested_set_code or not entry.requested_collector_number:
            return None
        for index, option in enumerate(options):
            if (
                option.set_code.casefold() == entry.requested_set_code
                and normalize_collector_number(option.collector_number) == entry.requested_collector_number
            ):
                return index
        return None

    def _remember_preference(self, card_name: str, option: ArtOption) -> None:
        self.preferences[normalize_name(card_name)] = option.preference_id

    def save_visible_preferences(self) -> None:
        preference_choices: list[tuple[CardSlot, ArtOption]] = []
        for slot in self.slots:
            if slot.current is not None:
                preference_choices.append((slot, slot.current))

        if not preference_choices:
            messagebox.showinfo("No preferences to save", "Fetch arts and choose at least one card art first.")
            return

        should_save = messagebox.askyesno(
            "Save art preferences?",
            f"This will update your saved default art for {len(preference_choices)} card(s).\n\n"
            "Use this only for cards you want as your normal personal defaults.",
        )
        if not should_save:
            return

        for slot, option in preference_choices:
            self._remember_preference(slot.entry.name, option)
        save_preferences(self.preferences)
        self.status.set(f"Saved preferred art for {len(preference_choices)} card(s).")
        messagebox.showinfo("Preferences saved", f"Saved preferred art for {len(preference_choices)} card(s).")

    def _drain_queue(self) -> None:
        while True:
            try:
                kind, payload = self.work_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "status":
                self.status.set(str(payload))
                self.activity_text.set(str(payload))
            elif kind == "progress":
                self.progress_value.set(float(payload))
            elif kind == "error":
                self.status.set(f"Could not fetch {payload}")
                self.activity_text.set(f"Could not fetch {payload}")
            elif kind == "done":
                self.slots = payload  # type: ignore[assignment]
                self._render_slots()
                found = sum(1 for slot in self.slots if slot.options)
                total_options = sum(len(slot.options) for slot in self.slots)
                self.progress_value.set(len(self.slots))
                self.status.set(f"Ready: found {total_options} art option(s) across {found}/{len(self.slots)} card(s).")
                self.activity_text.set(f"Finished. Found {total_options} art option(s) across {found}/{len(self.slots)} card(s).")
                self.fetch_button.configure(state="normal")
                self.save_preferences_button.configure(state="normal")
                self.export_button.configure(state="normal")
        self.after(100, self._drain_queue)

    def _render_slots(self) -> None:
        for child in self.cards_frame.winfo_children():
            child.destroy()
        self.slot_widgets = []
        self.image_refs = {}
        for col in range(4):
            self.cards_frame.grid_columnconfigure(col, weight=1, uniform="cards")

        for index, slot in enumerate(self.slots):
            widget = self._slot_card(index, slot)
            widget["frame"].grid(row=index // 4, column=index % 4, sticky="nsew", padx=5, pady=5)
            self.slot_widgets.append(widget)

    def _slot_card(self, index: int, slot: CardSlot) -> dict[str, object]:
        frame = ttk.Frame(self.cards_frame, style="Panel.TFrame", padding=8)
        frame.grid_columnconfigure(0, weight=1)
        title = ttk.Label(frame, text=f"{slot.entry.quantity}x {slot.entry.name}", style="CardTitle.TLabel", anchor="center", wraplength=250)
        title.grid(row=0, column=0, sticky="ew")

        image_label = ttk.Label(frame, background="#111920", anchor="center")
        image_label.grid(row=1, column=0, sticky="nsew", pady=(8, 6))

        meta = ttk.Label(frame, text="", style="CardMeta.TLabel", anchor="center", wraplength=250)
        meta.grid(row=2, column=0, sticky="ew")

        warning = ttk.Label(frame, text="", style="Missing.TLabel", anchor="center", wraplength=250)
        warning.grid(row=3, column=0, sticky="ew", pady=(6, 0))

        nav = ttk.Frame(frame, style="Panel.TFrame")
        nav.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        nav.grid_columnconfigure(1, weight=1)
        prev_button = ttk.Button(nav, text="<", width=4, command=lambda i=index: self.move(i, -1))
        count_label = ttk.Label(nav, text="", style="CardTitle.TLabel", anchor="center")
        next_button = ttk.Button(nav, text=">", width=4, command=lambda i=index: self.move(i, 1))
        prev_button.grid(row=0, column=0, sticky="ew")
        count_label.grid(row=0, column=1, sticky="ew", padx=8)
        next_button.grid(row=0, column=2, sticky="ew")

        info = {
            "frame": frame,
            "image": image_label,
            "meta": meta,
            "warning": warning,
            "count": count_label,
            "prev": prev_button,
            "next": next_button,
        }
        self._refresh_slot(index, info)
        return info

    def _refresh_slot(self, index: int, info: dict[str, object] | None = None) -> None:
        if not (0 <= index < len(self.slots)):
            return
        if info is None:
            info = self.slot_widgets[index]
        slot = self.slots[index]
        image_label: ttk.Label = info["image"]  # type: ignore[assignment]
        meta: ttk.Label = info["meta"]  # type: ignore[assignment]
        warning: ttk.Label = info["warning"]  # type: ignore[assignment]
        count_label: ttk.Label = info["count"]  # type: ignore[assignment]
        prev_button: ttk.Button = info["prev"]  # type: ignore[assignment]
        next_button: ttk.Button = info["next"]  # type: ignore[assignment]
        option = slot.current
        if option is None:
            image_label.configure(image="", text="No Scryfall art found", font=("Segoe UI", 13, "bold"), foreground="#ffcf8a")
            meta.configure(text="Try a more exact card name.")
            if slot.requested_printing_missing:
                warning.configure(text=f"Requested {slot.entry.requested_set_code.upper()} #{slot.entry.requested_collector_number} was not found.")
            else:
                warning.configure(text="")
            count_label.configure(text="0 / 0")
            prev_button.configure(state="disabled")
            next_button.configure(state="disabled")
            return

        try:
            with Image.open(option.cache_path) as image:  # type: ignore[union-attr]
                image.thumbnail((232, 324), Image.Resampling.LANCZOS)  # type: ignore[union-attr]
                photo = ImageTk.PhotoImage(image.copy())  # type: ignore[union-attr]
            self.image_refs[index] = photo
            image_label.configure(image=photo, text="")
        except Exception:
            image_label.configure(image="", text="Preview unavailable", font=("Segoe UI", 12, "bold"), foreground="#ffcf8a")

        meta.configure(text=f"{option.display_name}\n{option.label}")
        if slot.requested_printing_missing:
            warning.configure(
                text=f"Requested {slot.entry.requested_set_code.upper()} #{slot.entry.requested_collector_number} was not found. Choose any available art."
            )
        else:
            warning.configure(text="")
        count_label.configure(text=f"{slot.current_index + 1} / {len(slot.options)}")
        state = "normal" if len(slot.options) > 1 else "disabled"
        prev_button.configure(state=state)
        next_button.configure(state=state)

    def move(self, index: int, direction: int) -> None:
        if not (0 <= index < len(self.slots)):
            return
        slot = self.slots[index]
        if not slot.options:
            return
        slot.current_index = (slot.current_index + direction) % len(slot.options)
        self._refresh_slot(index)

    def export_selected(self) -> None:
        export_parent = Path(self.export_parent.get().strip()).expanduser()
        if not export_parent.exists():
            try:
                export_parent.mkdir(parents=True)
            except OSError as exc:
                messagebox.showerror("Export error", f"Could not create export parent folder:\n{exc}")
                return

        base_name = safe_folder_name(self.export_name.get().strip() or "selected-print-arts")
        output_folder = export_parent / base_name
        counter = 2
        while output_folder.exists():
            output_folder = export_parent / f"{base_name}-{counter}"
            counter += 1
        output_folder.mkdir(parents=True)

        copied = 0
        skipped = 0
        for slot_index, slot in enumerate(self.slots, start=1):
            current = slot.current
            if current is None:
                skipped += 1
                continue
            for copy_number in range(1, slot.entry.quantity + 1):
                base = safe_filename(f"{slot_index:03d} {slot.entry.name} {current.set_code.upper()} {current.collector_number}")
                if slot.entry.quantity > 1:
                    base = f"{base} copy {copy_number}"
                destination = self._unique_destination(output_folder, f"{base}.png")
                shutil.copy2(current.cache_path, destination)
                copied += 1

        report_path = output_folder / "_selected_arts.txt"
        with report_path.open("w", encoding="utf-8") as report:
            report.write(f"Copied {copied} deck image file(s).\n")
            report.write(f"Cards with no selected art: {skipped}\n\n")
            for slot in self.slots:
                report.write(f"{slot.entry.quantity}x {slot.entry.name}\n")
                current = slot.current
                if current is None:
                    report.write("  - no selected art\n")
                else:
                    report.write(f"  - {current.display_name} | {current.label}\n")

        self.save_current_settings()
        self.status.set(f"Exported {copied} image(s) to {output_folder}.")
        messagebox.showinfo("Export complete", f"Copied {copied} image(s).\n\nCreated:\n{output_folder}")

    def _unique_destination(self, folder: Path, filename: str) -> Path:
        destination = folder / filename
        if not destination.exists():
            return destination
        stem = destination.stem
        suffix = destination.suffix
        counter = 2
        while True:
            candidate = folder / f"{stem} {counter}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1


if __name__ == "__main__":
    app = ArtPickerApp()
    app.mainloop()















