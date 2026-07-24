# MTG Art Picker — Select First

A Windows desktop and local-web app for browsing Magic: The Gathering printings,
choosing artwork, AI-upscaling only the selected images, and creating
print-ready proxy PDFs.

## Select-first workflow

1. Paste a decklist and select **Find arts**.
2. Browse lightweight Scryfall previews and choose one printing per card.
3. Open **Print setup**.
4. The app downloads each distinct selected PNG once and upscales only those
   files with the bundled Real-ESRGAN Vulkan runtime.
5. Review the printable layout and download the PDF.

Deck quantities reuse a prepared image instead of downloading or upscaling it
more than once.

## Features

- Oldest-first and newest-first art ordering
- Separate filters for promo, foreign-language, and The List printings
- Custom artwork and saved per-card preferences
- Requested set and collector-number matching
- Optional basic-land filtering
- Live percentage and stage reporting for downloads, AI upscaling, and PDF creation
- Letter and A4 layouts in portrait or landscape
- Configurable card size, bleed, cut guides, and guide width
- Print-only card duplication, deletion, and undo
- Persistent non-destructive AI-upscale cache

## Windows installer

Download the current installer from this repository's **Releases** page.

## Run from source

Requirements:

- Windows
- Python 3.12 with Pillow and ReportLab
- Node.js and pnpm for the Electron desktop shell

For the select-first browser version:

```powershell
python outputs/mtg_art_picker_web.py --select-first
```

Then open `http://127.0.0.1:8765/`.

You can also double-click:

```text
outputs/Run_MTG_Art_Picker_Select_First.bat
```

## Build

```powershell
pnpm install
$env:MTG_ART_PICKER_BUILD_PYTHON = "C:\path\to\python.exe"
pnpm run build:backend
pnpm run dist
```

The installer is written to `release/`.

## Tests

```powershell
python -m unittest discover -s outputs -p "test_*.py" -v
```

## Data and privacy

Card images, caches, selected-art exports, preferences, settings, build output,
and generated PDFs are intentionally excluded from source control.

Scryfall card data and images are provided by Scryfall. This project is not
affiliated with or endorsed by Wizards of the Coast.

## License

No open-source license has been granted. All rights are reserved.
