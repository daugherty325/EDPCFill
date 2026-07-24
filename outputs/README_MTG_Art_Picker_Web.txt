MTG Scryfall Art Picker Web
===========================

Run
---
Double-click:
Run_MTG_Art_Picker_Web.bat

It starts a local server and opens the app at:
http://127.0.0.1:8765/

What changed from the Tkinter version
-------------------------------------
This version keeps the same cache, settings, preference file, deck parser, and Scryfall fetch behavior, but the interface runs in your browser. Selected images go directly into a print-layout tool instead of being copied to an export folder.

Workflow
--------
1. Paste a decklist.
2. Confirm the cache folder.
3. Choose whether fetched arts should be ordered newest first or oldest first.
4. Click "Fetch arts".
5. Watch the progress bar and recent activity feed while metadata and images are fetched.
6. Browse each card's arts with < and >, or click the art to open a scrollable thumbnail picker.
7. The visible art on each card is the one that will be printed. Deck quantities are included automatically.
8. Click "Print setup".
9. Choose Letter or A4, portrait or landscape, card dimensions, bleed, and cut-guide settings. Card dimensions default to 63 x 88 mm, bleed defaults to 1.5 mm, and guide width defaults to 0.3 mm. The PDF filename starts empty.
10. Review the page preview and click "Download printable PDF".
11. Print the PDF at 100% / Actual size with "Fit to page" turned off.

Each card in Print setup has a + button to duplicate it and an X button to remove it from that PDF. These print-only edits update the page count and generated PDF without changing the original decklist quantities.

Bleed and guides
----------------
Bleed follows devprint's edge preparation: rounded transparent/light corners are filled from a nearby 10 x 10 average color sample, near-black border pixels are normalized, mostly-black cards stretch fixed 8-pixel edge and corner slices, and other cards use mirrored strips with a 4-pixel overscan. Each black card corner forces its corresponding bleed corner to solid black so inset artwork cannot introduce a mismatched color. The card itself remains at the requested trim dimensions; the extra edge area is intended to be cut away and prevents white slivers when a cut is slightly off.

Corner guides are shown as 2 mm bright-green marks at the original card corners. Cut lines are solid black: they run outward from the trim corners through the disposable bleed and continue through the page margins to the physical edges of the page. They never cross the printable face of a card.

The PDF button shows an indeterminate progress bar and elapsed time while the document is being prepared. Large decks may still take several seconds, especially when many unique images need bleed processing.

AI upscaling
------------
Newly downloaded Scryfall images and custom PNGs are AI-upscaled once with the bundled Real-ESRGAN Vulkan runtime. Originals remain unchanged. Printing automatically prefers the 2x lossless-WebP derivative and falls back to the original when no valid derivative is available.

Derived images are stored under "scryfall_art_cache/upscaled/realesr-animevideov3-x2-detail-v1". This native 2x model preserves substantially more line, texture, and scan detail than the previous general 4x restoration pass. SQLite metadata records the source size, modification time, SHA-256, model version, scale, and output path so unchanged files are never processed twice.

Double-click "Upscale_Cached_Images.bat" to migrate the existing cache. The operation is resumable: press Ctrl+C to stop it and run the file again later. Completed images are retained and skipped. The original PNG cache is never overwritten or deleted.

"AI_Upscale_Status.bat" displays the latest migration progress. "Stop_AI_Upscale_Migration.bat" stops a managed background migration; double-click "Upscale_Cached_Images.bat" later to resume it.

Art ordering
------------
Newest/oldest controls the print order.

Refreshing metadata
-------------------
Every fetch checks Scryfall's default_cards bulk data once, resolves your decklist locally from that file, and still reuses PNG files it already downloaded. If the bulk data has not changed, it uses the cached copy.

The app deliberately spaces out Scryfall requests. If Scryfall returns a rate-limit response, the activity feed will show the pause and retry instead of skipping the card.

Eligible art
------------
The app uses Scryfall printings available in paper, including foreign-language and The List printings. Use the separate "Hide Foreign Arts" and "Hide The List Arts" checkboxes to filter either group. Existing cached non-paper PNGs can be reviewed with "Clean_Unsupported_Scryfall_Cache_Dry_Run.bat" and deleted with "Clean_Unsupported_Scryfall_Cache_Apply.bat".

Custom art
----------
Put custom PNGs in "custom_art", one folder per card name. For example: "custom_art/Counterspell/my-art.png". Custom arts appear beside Scryfall printings and print normally.

Preferences
-----------
Preferences are not saved automatically. Click "Save preferences" and confirm when you want the current choices to become your personal defaults.

Notes
-----
Leave the command window open while using the app. Closing it stops the local server.
