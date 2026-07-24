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
preferences, requested set/collector numbers, basic land filtering, deck
quantities, print-only duplication/deletion, bleed, cut guides, Letter/A4
layout, and PDF generation work the same as in the classic version.

Preference categories
---------------------
The preference-category panel controls both visibility and priority for:
custom art, borderless, extended art, old border, new border, foreign, promo,
and The List.

Drag categories by their grab handles to arrange them. Enabled categories group
arts in that order, overriding the main oldest/newest art order. Within each
category, arts still follow the selected oldest/newest order. Turning a category
off hides all arts that belong to it.

Saved preference profiles
-------------------------
The Cache Folder section includes a "Use saved preferences" switch and a
profile selector. Create profiles such as Premodern, Commander, Old Border, or
Borderless, then use "Save preferences" to save the currently selected card
arts into the active profile.

Each profile has its own saved card choices. Rename or delete profiles from the
same control row. Disabling saved preferences stops the app from automatically
selecting saved card choices without deleting any profile or preference.

The original Run_MTG_Art_Picker_Web.bat remains the classic download-first
version. Both versions use the same settings, preferences, custom art, and
cache folders.
