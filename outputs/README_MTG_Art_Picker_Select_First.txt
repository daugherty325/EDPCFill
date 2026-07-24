MTG Art Picker — Select First
==============================

Run
---
Double-click:
Run_MTG_Art_Picker_Select_First.bat

How this version differs
------------------------
This version finds all eligible Scryfall printings and displays them as remote
previews without first downloading every full-resolution PNG or AI-upscaling
every option.

Workflow
--------
1. Paste a decklist and click "Find arts".
2. Browse the available printings and choose the art shown for each card.
3. Click "Print setup".
4. The app downloads each distinct selected art once and AI-upscales only those
   selected files. A live percentage and current phase are shown while this
   runs. Multiple deck copies reuse the same prepared image.
5. Review the print layout and download the printable PDF as usual.

PDF creation also reports a live percentage while card images are prepared and
pages are generated.

Existing cached originals and valid AI upscales are reused. Custom art,
preferences, requested set/collector numbers, sorting, promo filtering, basic
land filtering, deck quantities, print-only duplication/deletion, bleed, cut
guides, Letter/A4 layout, and PDF generation work the same as in the classic
version.

Foreign-language and The List printings are shown by default. Use the separate
"Hide Foreign Arts" and "Hide The List Arts" checkboxes to exclude either
group independently.

The original Run_MTG_Art_Picker_Web.bat remains the classic download-first
version. Both versions use the same settings, preferences, custom art, and
cache folders.
