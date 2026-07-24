MTG Scryfall Art Picker
========================

What it does
------------
Paste a Magic decklist, press "Fetch arts", review every Scryfall PNG printing/art option for each card, then export the visible art for each card into a new folder for printing.

How to run
----------
Double-click:
Run_MTG_Art_Picker.bat

Or run this from the same folder:
py -3 mtg_art_picker.py

If previews fail because Pillow is missing, install it once:
py -3 -m pip install pillow

Basic workflow
--------------
1. Paste a decklist. Lines like "1 Sol Ring", "4x Island", section headers, and set/collector suffixes like "1 Arid Mesa (MH2) 244" or "1x Demonic Tutor (PLST) DDC-49" are supported.
2. Pick a cache folder. The default is "scryfall_art_cache" next to the app.
3. Pick an export parent folder. The app creates a new numbered folder inside it each time you export.
4. Click "Fetch arts".
5. Watch the progress bar and activity text while Scryfall metadata and PNGs are fetched.
6. Scroll the page down to the card grid. The decklist and folder fields scroll away, leaving more room for art previews and navigation.
7. Use the art ordering control to choose newest first or oldest first.
8. Use < and > on each card to browse all fetched Scryfall printings.
9. The visible art on each card is the one that will be exported.
10. Click "Export selected".

Art ordering
------------
Newest/oldest controls the print order.

Caching
-------
The app keeps Scryfall metadata and PNGs in the cache folder. If a PNG already exists there, it is reused instead of downloaded again.

Every fetch checks Scryfall's default_cards bulk data once, resolves your decklist locally from that file, and keeps already-downloaded PNG files. If the bulk data has not changed, it uses the cached copy.

The app deliberately spaces out Scryfall requests. If Scryfall returns a rate-limit response, the activity text will show the pause and retry instead of skipping the card.

The app uses Scryfall printings available in paper, including foreign-language and The List printings. Use the separate "Hide Foreign Arts" and "Hide The List Arts" checkboxes to filter either group. Existing cached non-paper PNGs can be reviewed with "Clean_Unsupported_Scryfall_Cache_Dry_Run.bat" and deleted with "Clean_Unsupported_Scryfall_Cache_Apply.bat".

Custom art
----------
Put custom PNGs in "custom_art", one folder per card name. For example: "custom_art/Counterspell/my-art.png". Custom arts appear beside Scryfall printings and export normally.

Preferred arts
--------------
The app remembers your preferred art for each card in "mtg_art_picker_preferences.json".

Changing visible art does not automatically overwrite your saved defaults. To update your personal defaults, browse to the arts you want, click "Save preferences", then confirm the prompt.

If your decklist includes a specific set code and collector number, that printing is selected by default and takes priority over the remembered preference. Foil markers like "*F*" are ignored.

If that exact set/collector-number printing is not found in Scryfall's available art options, the card still appears with the other fetched arts so you can choose one manually.

Deck quantities
---------------
Exports respect the quantity in your decklist. If the visible art for "4 Island" is selected, four PNG files are copied.

Notes
-----
Scryfall image downloads require an internet connection the first time an art is fetched. Double-faced and modal cards can show separate face images when Scryfall provides separate PNGs.
