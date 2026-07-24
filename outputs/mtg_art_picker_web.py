from __future__ import annotations

import json
import io
import os
import shutil
import sys
import threading
import time
import urllib.parse
import webbrowser
from functools import lru_cache
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import mtg_art_picker as core


APP_DIR = Path(__file__).resolve().parent
HOST = os.environ.get("MTG_ART_PICKER_HOST", "127.0.0.1")
PORT = int(os.environ.get("MTG_ART_PICKER_PORT", "8765"))
SELECT_FIRST_MODE = "--select-first" in sys.argv or os.environ.get("MTG_ART_PICKER_SELECT_FIRST", "").casefold() in {
    "1",
    "true",
    "yes",
    "on",
}
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
OPERATIONS: dict[str, dict] = {}
OPERATIONS_LOCK = threading.Lock()


HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MTG Scryfall Art Picker</title>
  <link rel="stylesheet" href="/app.css?v=priority-dropdown16" />
</head>
<body>
  <header class="topbar">
    <div>
      <h1>MTG Scryfall Art Picker</h1>
      <p id="status">Paste a decklist, fetch arts, then choose printings.</p>
    </div>
    <div class="actions">
      <button id="fetchButton" class="primary">Fetch arts</button>
      <button id="undoCardDelete" disabled>Undo delete</button>
      <button id="savePreferencesButton">Save preferences</button>
      <button id="exportButton">Print setup</button>
    </div>
  </header>

  <main>
    <section class="panel setup">
      <label>
        <span>Cache folder</span>
        <input id="cacheDir" />
      </label>
      <div class="profile-field">
        <span>Saved preference profile</span>
        <div class="profile-controls">
          <select id="preferenceProfile" aria-label="Saved preference profile"></select>
          <button id="newProfile" type="button">New</button>
          <button id="renameProfile" type="button">Rename</button>
          <button id="deleteProfile" type="button">Delete</button>
        </div>
      </div>
      <label class="check-field">
        <input id="useSavedPreferences" type="checkbox" />
        <span>Use saved preferences</span>
      </label>
      <label class="check-field">
        <input id="updateScryfallImages" type="checkbox" />
        <span>Update Scryfall Images</span>
      </label>
      <label class="check-field">
        <input id="ignoreBasics" type="checkbox" />
        <span>Ignore Basics</span>
      </label>
    </section>

    <section class="panel ordering-panel">
      <label>
        <span>Art order</span>
        <select id="sortOrder">
          <option value="oldest">Oldest first</option>
          <option value="newest">Newest first</option>
        </select>
      </label>
      <details class="preference-editor">
        <summary>
          <span>Priority order</span>
          <small>Open to reorder or hide art categories</small>
        </summary>
        <div class="preference-editor-body">
          <p>Enabled categories override art order. Art order is applied within each category.</p>
          <div class="preference-heading" aria-hidden="true">
            <span>Enabled</span><span>Category</span><span>Drag</span>
          </div>
          <div id="preferenceCategories" class="preference-categories"></div>
        </div>
      </details>
    </section>

    <section class="panel deck-panel">
      <label>
        <span>Decklist</span>
        <textarea id="deckText" spellcheck="false"></textarea>
      </label>
      <div class="progress-row">
        <progress id="progress" value="0" max="1"></progress>
        <span id="progressText">Idle</span>
      </div>
      <div id="activityLog" class="activity-log">Waiting for a decklist.</div>
    </section>

    <section id="grid" class="grid"></section>
  </main>

  <div id="toast" class="toast" hidden></div>
  <div id="operationProgress" class="operation-progress" hidden aria-live="polite">
    <div class="operation-progress-card">
      <h2 id="operationTitle">Preparing selected arts</h2>
      <progress id="operationBar" value="0" max="100"></progress>
      <div class="operation-progress-row">
        <strong id="operationPercent">0%</strong>
      </div>
      <p id="operationStatus">Starting...</p>
    </div>
  </div>
  <div id="profileModal" class="modal" hidden>
    <div class="modal-panel profile-modal-panel" role="dialog" aria-modal="true" aria-labelledby="profileModalTitle">
      <div class="modal-head">
        <div>
          <h2 id="profileModalTitle">Preference profile</h2>
          <p id="profileModalMessage"></p>
        </div>
        <button id="profileModalClose" class="icon-button" aria-label="Close profile dialog">X</button>
      </div>
      <div class="profile-modal-body">
        <label id="profileNameField">
          <span>Profile name</span>
          <input id="profileNameInput" maxlength="80" autocomplete="off" />
        </label>
        <div class="profile-modal-actions">
          <button id="profileModalCancel" type="button">Cancel</button>
          <button id="profileModalConfirm" class="primary" type="button">Save</button>
        </div>
      </div>
    </div>
  </div>
  <div id="artModal" class="modal" hidden>
    <div class="modal-panel" role="dialog" aria-modal="true" aria-labelledby="modalTitle">
      <div class="modal-head">
        <div>
          <h2 id="modalTitle">Select version</h2>
          <p id="modalMeta"></p>
        </div>
        <button id="modalClose" class="icon-button" aria-label="Close">X</button>
      </div>
      <div id="modalThumbs" class="modal-thumbs"></div>
    </div>
  </div>
  <div id="printModal" class="modal" hidden>
    <div class="modal-panel print-panel" role="dialog" aria-modal="true" aria-labelledby="printTitle">
      <div class="modal-head">
        <div>
          <h2 id="printTitle">Print setup</h2>
          <p id="printSummary">Arrange the selected cards on printable pages.</p>
        </div>
        <button id="printClose" class="icon-button" aria-label="Close print setup">X</button>
      </div>
      <div class="print-layout">
        <aside class="print-controls">
          <label><span>PDF filename</span><input id="pdfFilename" value="" /></label>
          <label><span>Page size</span><select id="pageSize"><option value="letter">Letter (8.5 x 11 in)</option><option value="a4">A4 (210 x 297 mm)</option></select></label>
          <label><span>Orientation</span><select id="orientation"><option value="portrait">Portrait</option><option value="landscape">Landscape</option></select></label>
          <div class="print-control-pair">
            <label><span>Card width (mm)</span><input id="cardWidth" type="number" min="20" max="200" step="0.1" value="63" /></label>
            <label><span>Card height (mm)</span><input id="cardHeight" type="number" min="20" max="200" step="0.1" value="88" /></label>
          </div>
          <label><span>Bleed edge (mm)</span><input id="bleed" type="number" min="0" max="10" step="0.1" value="1.5" /></label>
          <label class="check-field compact"><input id="cutLines" type="checkbox" checked /><span>Draw cut lines</span></label>
          <label><span>Cut-line width (mm)</span><input id="guideWidth" type="number" min="0.05" max="2" step="0.05" value="0.3" /></label>
          <button id="undoPrintEdit" type="button" disabled>Undo last card change</button>
          <button id="downloadPdf" class="primary">Download printable PDF</button>
          <div id="pdfProgress" class="pdf-progress" hidden aria-live="polite">
            <progress id="pdfProgressBar" value="0" max="100"></progress>
            <span id="pdfProgressText">Preparing PDF...</span>
          </div>
          <p class="print-note">Print at 100% / Actual size. Turn off “Fit to page” in the print dialog.</p>
        </aside>
        <section id="printPreview" class="print-preview" aria-label="Print preview"></section>
      </div>
    </div>
  </div>
  <script src="/app.js?v=priority-dropdown16"></script>
</body>
</html>
"""

if SELECT_FIRST_MODE:
    HTML = (
        HTML.replace("<title>MTG Scryfall Art Picker</title>", "<title>MTG Art Picker — Select First</title>")
        .replace("<h1>MTG Scryfall Art Picker</h1>", "<h1>MTG Art Picker — Select First</h1>")
        .replace(
            "Paste a decklist, fetch arts, then choose printings.",
            "Browse first; only your selected printings are downloaded and AI-upscaled.",
        )
        .replace(">Fetch arts</button>", ">Find arts</button>")
        .replace(">Update Scryfall Images</span>", ">Update Scryfall print listings</span>")
    )


CSS = r""":root {
  color-scheme: dark;
  --bg: #111820;
  --panel: #1b2630;
  --panel-2: #243340;
  --line: #344757;
  --text: #f5f2e9;
  --muted: #b7c4cd;
  --accent: #3fb9a8;
  --warn: #ffd082;
  --danger: #ff917d;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: "Segoe UI", system-ui, sans-serif;
}

.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: center;
  padding: 14px 20px;
  background: rgba(17, 24, 32, 0.94);
  border-bottom: 1px solid var(--line);
  backdrop-filter: blur(10px);
}

h1 {
  margin: 0 0 3px;
  font-size: 22px;
  letter-spacing: 0;
}

p { margin: 0; color: var(--muted); }

main {
  width: min(1680px, calc(100vw - 32px));
  margin: 16px auto 28px;
}

.panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 14px;
  margin-bottom: 14px;
}

.setup {
  display: grid;
  grid-template-columns: minmax(280px, 2fr) minmax(430px, 3fr) repeat(3, minmax(145px, 1fr));
  gap: 12px;
}

label span,
.profile-field > span {
  display: block;
  margin: 0 0 6px;
  color: var(--muted);
  font-size: 13px;
}

.profile-controls {
  display: grid;
  grid-template-columns: minmax(140px, 1fr) repeat(3, auto);
  gap: 6px;
}

.profile-controls button {
  min-height: 36px;
  padding-inline: 10px;
}

.check-field {
  display: flex;
  align-items: end;
  gap: 8px;
  min-height: 58px;
}

.check-field input {
  width: 18px;
  height: 18px;
  margin: 0 0 9px;
}

.check-field span {
  margin: 0 0 7px;
  color: var(--text);
  font-size: 14px;
}

input, textarea {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #0f151c;
  color: var(--text);
  font: inherit;
  outline: none;
}

input { height: 36px; padding: 7px 9px; }
select {
  width: 100%;
  height: 36px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #0f151c;
  color: var(--text);
  font: inherit;
  padding: 7px 9px;
}
textarea {
  min-height: 130px;
  resize: vertical;
  padding: 10px;
  font-family: Consolas, "Courier New", monospace;
}

.ordering-panel {
  display: grid;
  grid-template-columns: minmax(160px, 220px) minmax(440px, 1fr);
  gap: 20px;
  align-items: start;
}

.preference-editor {
  border: 1px solid var(--line);
  border-radius: 7px;
  background: #17212a;
  overflow: hidden;
}

.preference-editor summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  min-height: 46px;
  padding: 9px 12px;
  cursor: pointer;
  user-select: none;
  font-weight: 750;
  list-style: none;
}

.preference-editor summary::-webkit-details-marker {
  display: none;
}

.preference-editor summary::after {
  content: "▾";
  color: var(--accent);
  font-size: 17px;
  transition: transform 140ms ease;
}

.preference-editor[open] summary::after {
  transform: rotate(180deg);
}

.preference-editor summary:hover {
  background: var(--panel-2);
}

.preference-editor summary small {
  margin-left: auto;
  color: var(--muted);
  font-size: 12px;
  font-weight: 500;
}

.preference-editor-body {
  display: grid;
  gap: 9px;
  padding: 10px;
  border-top: 1px solid var(--line);
}

.preference-editor-body p {
  font-size: 12px;
}

.preference-heading,
.preference-row {
  display: grid;
  grid-template-columns: 92px minmax(150px, 1fr) 92px;
  gap: 10px;
  align-items: center;
}

.preference-heading {
  padding: 0 10px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
}

.preference-categories {
  display: grid;
  gap: 5px;
}

.preference-row {
  min-height: 48px;
  padding: 6px 10px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--panel-2);
  transition: opacity 120ms ease, box-shadow 120ms ease, transform 120ms ease;
}

.preference-row.is-disabled {
  opacity: 0.62;
}

.preference-row.is-dragging {
  opacity: 0.35;
}

.preference-row.drop-before {
  box-shadow: inset 0 3px 0 var(--accent);
}

.preference-row.drop-after {
  box-shadow: inset 0 -3px 0 var(--accent);
}

.category-toggle {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 13px;
}

.category-toggle input {
  width: 18px;
  height: 18px;
  margin: 0;
}

.category-name {
  font-weight: 650;
}

.category-drag {
  display: flex;
  justify-content: flex-end;
}

.drag-handle {
  display: grid;
  place-items: center;
  width: 42px;
  min-height: 32px;
  border: 1px solid var(--line);
  border-radius: 5px;
  color: var(--muted);
  cursor: grab;
  font-size: 22px;
  font-weight: 800;
  letter-spacing: -2px;
  line-height: 1;
  user-select: none;
}

.drag-handle:hover {
  color: var(--text);
  border-color: var(--accent);
}

.drag-handle:active {
  cursor: grabbing;
}

button {
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #e9e4da;
  color: #18222b;
  min-height: 36px;
  padding: 7px 12px;
  font-weight: 650;
  cursor: pointer;
}

button.primary { background: var(--accent); color: #071411; border-color: #56d1bf; }
button.danger { background: #3a2020; color: #ffd7cf; border-color: #82483f; }
button:disabled { opacity: 0.55; cursor: default; }

.actions { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }

.progress-row {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 12px;
  align-items: center;
  margin-top: 12px;
}

progress { width: 100%; height: 14px; accent-color: var(--accent); }
#progressText { color: var(--muted); font-size: 13px; min-width: 96px; text-align: right; }

.activity-log {
  margin-top: 10px;
  min-height: 58px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #0f151c;
  color: var(--muted);
  padding: 8px 10px;
  font-family: Consolas, "Courier New", monospace;
  font-size: 12px;
  line-height: 1.45;
  white-space: pre-wrap;
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(245px, 1fr));
  gap: 14px;
}

.card {
  background: var(--panel-2);
  border: 1px solid var(--line);
  border-radius: 8px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  min-height: 520px;
}

.card-head {
  min-height: 58px;
  padding: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
  font-weight: 800;
}

.image-wrap {
  height: 344px;
  background: #0b1117;
  display: grid;
  place-items: center;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}

.image-wrap img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  display: block;
  cursor: zoom-in;
}

.missing {
  color: var(--warn);
  font-weight: 800;
  padding: 12px;
  text-align: center;
}

.meta {
  min-height: 68px;
  padding: 9px 10px 0;
  text-align: center;
  color: var(--muted);
  font-size: 13px;
}

.warning {
  min-height: 18px;
  padding: 0 10px;
  color: var(--warn);
  font-size: 12px;
  text-align: center;
}

.card-actions {
  margin-top: auto;
  padding: 10px;
  display: grid;
  gap: 8px;
}

.nav {
  display: grid;
  grid-template-columns: 42px 1fr 42px;
  gap: 8px;
  align-items: center;
}

.counter { text-align: center; font-weight: 800; }

.toast {
  position: fixed;
  right: 18px;
  bottom: 18px;
  background: #edf7f4;
  color: #10211f;
  border-radius: 8px;
  padding: 12px 14px;
  box-shadow: 0 12px 35px rgba(0, 0, 0, 0.35);
  max-width: min(440px, calc(100vw - 36px));
  z-index: 20;
}

.operation-progress {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(4, 8, 12, 0.78);
  backdrop-filter: blur(4px);
}
.operation-progress[hidden] { display: none; }
.operation-progress-card {
  width: min(560px, 100%);
  padding: 24px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--panel);
  box-shadow: 0 18px 60px rgba(0, 0, 0, 0.55);
}
.operation-progress-card h2 { margin: 0 0 16px; }
.operation-progress-card progress { width: 100%; height: 22px; }
.operation-progress-row {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  margin-top: 8px;
  color: var(--muted);
}
.operation-progress-row strong { color: var(--accent); }
.operation-progress-card p { margin: 14px 0 0; color: var(--text); }

.modal {
  position: fixed;
  inset: 0;
  z-index: 30;
  display: grid;
  place-items: center;
  padding: 20px;
  background: rgba(5, 8, 12, 0.78);
}

.modal[hidden] { display: none; }

.modal-panel {
  width: min(1760px, calc(100vw - 40px));
  max-height: calc(100vh - 40px);
  display: block;
  overflow-y: auto;
  overflow-x: hidden;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
  box-shadow: 0 22px 70px rgba(0, 0, 0, 0.55);
}

.profile-modal-panel {
  width: min(520px, calc(100vw - 40px));
}

.profile-modal-body {
  display: grid;
  gap: 18px;
  padding: 16px;
}

.profile-modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.modal-head {
  position: sticky;
  top: 0;
  z-index: 50;
  display: flex;
  align-items: start;
  justify-content: space-between;
  gap: 14px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--line);
  background: var(--panel);
}

.modal-head h2 {
  margin: 0 0 4px;
  font-size: 18px;
  letter-spacing: 0;
}

.icon-button {
  min-width: 38px;
  padding: 6px 10px;
}

.modal-thumbs {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(207px, 1fr));
  grid-auto-rows: max-content;
  align-items: start;
  gap: 14px;
  overflow: visible;
  padding: 18px;
}

.modal-thumb {
  min-width: 0;
  align-self: start;
  display: flex;
  flex-direction: column;
  gap: 8px;
  border: 2px solid transparent;
  border-radius: 6px;
  padding: 8px;
  overflow: hidden;
  background: #0b1117;
  color: var(--text);
  min-height: 0;
}

.modal-thumb img {
  flex: 0 0 auto;
  width: 100%;
  aspect-ratio: 488 / 680;
  object-fit: contain;
  display: block;
  background: #05080c;
  border-radius: 4px;
}

.modal-thumb span {
  min-height: 34px;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.25;
  overflow-wrap: anywhere;
}

.modal-thumb.active {
  border-color: var(--accent);
}

.print-panel {
  width: min(1420px, calc(100vw - 40px));
  height: calc(100vh - 40px);
  max-height: calc(100vh - 40px);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  overflow-anchor: none;
}
.print-panel > .modal-head {
  position: relative;
  flex: 0 0 auto;
  top: auto;
}
.print-layout {
  flex: 1 1 auto;
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr);
  min-height: 0;
  overflow: hidden;
}
.print-controls {
  display: flex;
  flex-direction: column;
  gap: 13px;
  padding: 16px;
  border-right: 1px solid var(--line);
  background: #16212a;
  overflow-y: auto;
  overscroll-behavior: contain;
}
.print-control-pair { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.check-field.compact { min-height: 34px; align-items: center; }
.check-field.compact input, .check-field.compact span { margin: 0; }
.print-note { font-size: 12px; line-height: 1.45; }
.pdf-progress { display: grid; gap: 6px; color: var(--muted); font-size: 12px; }
.pdf-progress[hidden] { display: none; }
.pdf-progress progress { width: 100%; }
.print-preview {
  display: flex;
  flex-wrap: wrap;
  align-content: flex-start;
  justify-content: center;
  gap: 22px;
  overflow: auto;
  min-width: 0;
  min-height: 0;
  overscroll-behavior: contain;
  padding: 24px;
  background: #0b1117;
  position: relative;
  z-index: 0;
}
.print-page {
  position: relative;
  display: grid;
  background: white;
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.5);
  overflow: hidden;
}
.print-card {
  min-width: 0;
  min-height: 0;
  width: 100%;
  height: 100%;
  object-fit: fill;
  display: block;
}
.print-cell {
  position: relative;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  isolation: isolate;
  contain: paint;
}
.print-cut-lines {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 2;
}
.cut-mark {
  position: absolute;
  background-color: #adff2f;
  filter: drop-shadow(0 0 1px rgba(0, 0, 0, 0.9));
}
.cut-mark.horizontal {
  width: var(--cut-x);
  height: var(--cut-width);
}
.cut-mark.vertical {
  width: var(--cut-width);
  height: var(--cut-y);
}
.cut-mark.tl.horizontal { left: 0; top: var(--trim-top); }
.cut-mark.tl.vertical { left: var(--trim-left); top: 0; }
.cut-mark.tr.horizontal { right: 0; top: var(--trim-top); }
.cut-mark.tr.vertical { right: var(--trim-right); top: 0; }
.cut-mark.bl.horizontal { left: 0; bottom: var(--trim-bottom); }
.cut-mark.bl.vertical { left: var(--trim-left); bottom: 0; }
.cut-mark.br.horizontal { right: 0; bottom: var(--trim-bottom); }
.cut-mark.br.vertical { right: var(--trim-right); bottom: 0; }
.page-edge-guides {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 3;
}
.page-edge-line {
  position: absolute;
  background: #adff2f;
  filter: drop-shadow(0 0 1px rgba(0, 0, 0, 0.9));
}
.page-edge-line.vertical { width: var(--cut-width); }
.page-edge-line.horizontal { height: var(--cut-width); }
.print-card-actions {
  position: absolute;
  top: 5px;
  right: 5px;
  z-index: 6;
  display: flex;
  gap: 4px;
}
.print-card-action {
  width: 28px;
  min-width: 28px;
  height: 28px;
  min-height: 28px;
  padding: 0;
  border: 1px solid rgba(255, 255, 255, 0.75);
  border-radius: 5px;
  background: rgba(20, 27, 34, 0.9);
  color: #fff;
  font: 700 17px/1 system-ui, sans-serif;
  box-shadow: 0 2px 7px rgba(0, 0, 0, 0.55);
}
.print-card-action:hover { background: #263746; }
.print-card-action.remove:hover { background: #8b2d2d; }
.print-quality-badge {
  position: absolute;
  left: 5px;
  bottom: 5px;
  z-index: 6;
  padding: 3px 6px;
  border: 1px solid rgba(255, 255, 255, 0.82);
  border-radius: 999px;
  color: #071018;
  font: 800 9px/1.1 system-ui, sans-serif;
  letter-spacing: 0.03em;
  box-shadow: 0 2px 7px rgba(0, 0, 0, 0.55);
  pointer-events: none;
}
.print-quality-badge.upscaled { background: #62e6c5; }
.print-quality-badge.original { background: #ffc966; }
.print-card.refining { opacity: 0.92; }
.print-card.ready { opacity: 1; }
.print-page-number {
  position: absolute;
  right: 5px;
  bottom: 3px;
  color: #777;
  font: 10px system-ui, sans-serif;
}

@media (max-width: 1250px) {
  .setup { grid-template-columns: minmax(280px, 1fr) minmax(430px, 1.5fr); }
}

@media (max-width: 840px) {
  .topbar { align-items: flex-start; flex-direction: column; }
  .actions { justify-content: flex-start; }
  .setup, .ordering-panel { grid-template-columns: 1fr; }
  .preference-heading, .preference-row {
    grid-template-columns: 80px minmax(120px, 1fr) 88px;
  }
  .modal { padding: 10px; }
  .modal-panel {
    width: calc(100vw - 20px);
    max-height: calc(100vh - 20px);
  }
  .modal-thumbs { grid-template-columns: repeat(auto-fill, minmax(168px, 1fr)); }
  .print-panel {
    width: calc(100vw - 20px);
    height: calc(100vh - 20px);
    max-height: calc(100vh - 20px);
  }
  .print-layout {
    grid-template-columns: 1fr;
    grid-template-rows: minmax(210px, 42vh) minmax(280px, 1fr);
  }
  .print-controls { border-right: 0; border-bottom: 1px solid var(--line); }
}
"""


JS = r"""const state = {
  jobId: null,
  slots: [],
  busy: false,
  modal: null,
  previewRenderId: 0,
  printCards: [],
  printHistory: [],
  cardHistory: [],
  pollFailures: 0,
  preferenceCategories: [],
  profileDialogMode: null,
  draggedCategoryKey: null,
};

let previewRenderTimer = null;
let categorySaveTimer = null;

const CATEGORY_LABELS = {
  custom_art: "Custom art",
  borderless: "Borderless",
  extended_art: "Extended art",
  old_border: "Old border",
  new_border: "New border",
  foreign: "Foreign",
  promo: "Promo",
  the_list: "The List",
};

const els = {
  status: document.querySelector("#status"),
  cacheDir: document.querySelector("#cacheDir"),
  preferenceProfile: document.querySelector("#preferenceProfile"),
  useSavedPreferences: document.querySelector("#useSavedPreferences"),
  newProfile: document.querySelector("#newProfile"),
  renameProfile: document.querySelector("#renameProfile"),
  deleteProfile: document.querySelector("#deleteProfile"),
  profileModal: document.querySelector("#profileModal"),
  profileModalTitle: document.querySelector("#profileModalTitle"),
  profileModalMessage: document.querySelector("#profileModalMessage"),
  profileModalClose: document.querySelector("#profileModalClose"),
  profileModalCancel: document.querySelector("#profileModalCancel"),
  profileModalConfirm: document.querySelector("#profileModalConfirm"),
  profileNameField: document.querySelector("#profileNameField"),
  profileNameInput: document.querySelector("#profileNameInput"),
  updateScryfallImages: document.querySelector("#updateScryfallImages"),
  ignoreBasics: document.querySelector("#ignoreBasics"),
  sortOrder: document.querySelector("#sortOrder"),
  preferenceCategories: document.querySelector("#preferenceCategories"),
  deckText: document.querySelector("#deckText"),
  progress: document.querySelector("#progress"),
  progressText: document.querySelector("#progressText"),
  activityLog: document.querySelector("#activityLog"),
  grid: document.querySelector("#grid"),
  fetchButton: document.querySelector("#fetchButton"),
  undoCardDelete: document.querySelector("#undoCardDelete"),
  exportButton: document.querySelector("#exportButton"),
  savePreferencesButton: document.querySelector("#savePreferencesButton"),
  toast: document.querySelector("#toast"),
  artModal: document.querySelector("#artModal"),
  modalTitle: document.querySelector("#modalTitle"),
  modalMeta: document.querySelector("#modalMeta"),
  modalClose: document.querySelector("#modalClose"),
  modalThumbs: document.querySelector("#modalThumbs"),
  printModal: document.querySelector("#printModal"),
  printClose: document.querySelector("#printClose"),
  printSummary: document.querySelector("#printSummary"),
  printPreview: document.querySelector("#printPreview"),
  pdfFilename: document.querySelector("#pdfFilename"),
  pageSize: document.querySelector("#pageSize"),
  orientation: document.querySelector("#orientation"),
  cardWidth: document.querySelector("#cardWidth"),
  cardHeight: document.querySelector("#cardHeight"),
  bleed: document.querySelector("#bleed"),
  cutLines: document.querySelector("#cutLines"),
  guideWidth: document.querySelector("#guideWidth"),
  undoPrintEdit: document.querySelector("#undoPrintEdit"),
  downloadPdf: document.querySelector("#downloadPdf"),
  pdfProgress: document.querySelector("#pdfProgress"),
  pdfProgressBar: document.querySelector("#pdfProgressBar"),
  pdfProgressText: document.querySelector("#pdfProgressText"),
  operationProgress: document.querySelector("#operationProgress"),
  operationTitle: document.querySelector("#operationTitle"),
  operationBar: document.querySelector("#operationBar"),
  operationPercent: document.querySelector("#operationPercent"),
  operationStatus: document.querySelector("#operationStatus"),
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok || data.ok === false) throw new Error(data.error || "Request failed");
  return data;
}

function toast(message) {
  els.toast.textContent = message;
  els.toast.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { els.toast.hidden = true; }, 4200);
}

function updateOperationDisplay(data) {
  const percent = Math.max(0, Math.min(100, Number(data.percent) || 0));
  els.operationBar.value = percent;
  els.operationPercent.textContent = `${Math.round(percent)}%`;
  els.operationStatus.textContent = data.status || "Working...";
}

async function waitForOperation(operationId, onProgress) {
  while (true) {
    const data = await api(`/api/operation?id=${encodeURIComponent(operationId)}`);
    onProgress(data);
    if (data.state === "done") return data;
    if (data.state === "error") throw new Error(data.error || data.status || "Operation failed.");
    await new Promise(resolve => setTimeout(resolve, 350));
  }
}

function setBusy(busy) {
  state.busy = busy;
  els.fetchButton.disabled = busy;
  els.undoCardDelete.disabled = busy || !state.cardHistory.length;
  els.exportButton.disabled = busy || !state.slots.length;
  els.savePreferencesButton.disabled = busy || !state.slots.length;
  els.preferenceProfile.disabled = busy;
  els.useSavedPreferences.disabled = busy;
  els.newProfile.disabled = busy;
  els.renameProfile.disabled = busy;
  els.deleteProfile.disabled = busy || els.preferenceProfile.options.length <= 1;
  els.updateScryfallImages.disabled = busy;
  els.ignoreBasics.disabled = busy;
  els.preferenceCategories.querySelectorAll("input, button").forEach(control => {
    control.disabled = busy;
  });
  els.preferenceCategories.querySelectorAll(".drag-handle").forEach(handle => {
    handle.draggable = !busy;
    handle.setAttribute("aria-disabled", busy ? "true" : "false");
  });
}

async function loadSettings() {
  const data = await api("/api/settings");
  els.cacheDir.value = data.settings.cache_dir;
  els.useSavedPreferences.checked = Boolean(data.settings.use_saved_preferences);
  renderProfileOptions(data.settings.preference_profiles, data.settings.preference_profile);
  els.updateScryfallImages.checked = Boolean(data.settings.update_scryfall_images);
  els.ignoreBasics.checked = Boolean(data.settings.ignore_basics);
  state.preferenceCategories = data.settings.art_preference_categories || [];
  renderPreferenceCategories();
}

function renderProfileOptions(profiles, activeProfile) {
  els.preferenceProfile.innerHTML = "";
  (profiles || []).forEach(profile => {
    const option = document.createElement("option");
    option.value = profile;
    option.textContent = profile;
    option.selected = profile === activeProfile;
    els.preferenceProfile.append(option);
  });
  els.deleteProfile.disabled = state.busy || els.preferenceProfile.options.length <= 1;
}

async function updatePreferenceProfiles(action, extra = {}) {
  const data = await api("/api/preference-profiles", {
    method: "POST",
    body: JSON.stringify({
      action,
      profile: els.preferenceProfile.value,
      enabled: els.useSavedPreferences.checked,
      ...extra,
    }),
  });
  els.useSavedPreferences.checked = Boolean(data.use_saved_preferences);
  renderProfileOptions(data.preference_profiles, data.preference_profile);
  return data;
}

function openProfileDialog(mode) {
  state.profileDialogMode = mode;
  const current = els.preferenceProfile.value;
  const deleting = mode === "delete";
  els.profileModalTitle.textContent = mode === "create"
    ? "Create preference profile"
    : mode === "rename"
      ? "Rename preference profile"
      : "Delete preference profile";
  els.profileModalMessage.textContent = deleting
    ? `This permanently deletes “${current}” and all saved card choices in it.`
    : mode === "create"
      ? "The new profile starts empty. Save card choices into it with Save preferences."
      : `Enter a new name for “${current}”.`;
  els.profileNameField.hidden = deleting;
  els.profileNameInput.value = mode === "rename" ? current : "";
  els.profileModalConfirm.textContent = deleting ? "Delete profile" : mode === "create" ? "Create profile" : "Rename profile";
  els.profileModalConfirm.classList.toggle("danger", deleting);
  els.profileModalConfirm.classList.toggle("primary", !deleting);
  els.profileModal.hidden = false;
  if (!deleting) {
    els.profileNameInput.focus();
    els.profileNameInput.select();
  }
}

function closeProfileDialog() {
  state.profileDialogMode = null;
  els.profileModal.hidden = true;
}

async function submitProfileDialog() {
  const mode = state.profileDialogMode;
  if (!mode) return;
  const current = els.preferenceProfile.value;
  const name = els.profileNameInput.value.trim();
  if (mode !== "delete" && !name) {
    toast("Enter a profile name.");
    return;
  }
  try {
    const data = await updatePreferenceProfiles(mode, mode === "delete" ? {} : { name });
    closeProfileDialog();
    if (mode === "create") toast(`Created preference profile “${data.preference_profile}”.`);
    else if (mode === "rename") toast(`Renamed preference profile to “${data.preference_profile}”.`);
    else toast(`Deleted “${current}”. Active profile: “${data.preference_profile}”.`);
  } catch (error) {
    toast(error.message);
  }
}

function clearDropIndicators() {
  els.preferenceCategories.querySelectorAll(".preference-row").forEach(row => {
    row.classList.remove("drop-before", "drop-after");
  });
}

function renderPreferenceCategories() {
  els.preferenceCategories.innerHTML = "";
  state.preferenceCategories.forEach(category => {
    const row = document.createElement("div");
    row.className = `preference-row${category.enabled ? "" : " is-disabled"}`;
    row.dataset.categoryKey = category.key;
    row.innerHTML = `
      <label class="category-toggle">
        <input type="checkbox" ${category.enabled ? "checked" : ""} aria-label="Enable ${escapeHtml(CATEGORY_LABELS[category.key] || category.key)}" />
        <span>${category.enabled ? "On" : "Off"}</span>
      </label>
      <span class="category-name">${escapeHtml(CATEGORY_LABELS[category.key] || category.key)}</span>
      <span class="category-drag">
        <span class="drag-handle" draggable="${state.busy ? "false" : "true"}" role="button" tabindex="0" aria-label="Drag ${escapeHtml(CATEGORY_LABELS[category.key] || category.key)}" title="Drag to reorder">⠿</span>
      </span>`;
    const toggle = row.querySelector("input");
    const handle = row.querySelector(".drag-handle");
    toggle.addEventListener("change", () => {
      category.enabled = toggle.checked;
      renderPreferenceCategories();
      scheduleCategorySave();
    });
    handle.addEventListener("dragstart", event => {
      state.draggedCategoryKey = category.key;
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", category.key);
      row.classList.add("is-dragging");
    });
    handle.addEventListener("dragend", () => {
      state.draggedCategoryKey = null;
      clearDropIndicators();
      row.classList.remove("is-dragging");
    });
    handle.addEventListener("keydown", event => {
      if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
      event.preventDefault();
      const index = state.preferenceCategories.findIndex(item => item.key === category.key);
      const targetIndex = event.key === "ArrowUp" ? index - 1 : index + 1;
      if (targetIndex < 0 || targetIndex >= state.preferenceCategories.length) return;
      const target = state.preferenceCategories[targetIndex];
      dropPreferenceCategory(category.key, target.key, event.key === "ArrowDown");
      const movedHandle = els.preferenceCategories.querySelector(`[data-category-key="${category.key}"] .drag-handle`);
      if (movedHandle) movedHandle.focus();
    });
    row.addEventListener("dragover", event => {
      if (!state.draggedCategoryKey || state.draggedCategoryKey === category.key) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      const after = event.clientY >= row.getBoundingClientRect().top + row.offsetHeight / 2;
      clearDropIndicators();
      row.classList.add(after ? "drop-after" : "drop-before");
    });
    row.addEventListener("drop", event => {
      event.preventDefault();
      const draggedKey = state.draggedCategoryKey || event.dataTransfer.getData("text/plain");
      const after = event.clientY >= row.getBoundingClientRect().top + row.offsetHeight / 2;
      dropPreferenceCategory(draggedKey, category.key, after);
    });
    els.preferenceCategories.append(row);
  });
  if (state.busy) setBusy(true);
}

function dropPreferenceCategory(draggedKey, targetKey, after) {
  if (!draggedKey || draggedKey === targetKey) {
    clearDropIndicators();
    return;
  }
  const sourceIndex = state.preferenceCategories.findIndex(category => category.key === draggedKey);
  let targetIndex = state.preferenceCategories.findIndex(category => category.key === targetKey);
  if (sourceIndex < 0 || targetIndex < 0) return;
  const [dragged] = state.preferenceCategories.splice(sourceIndex, 1);
  if (sourceIndex < targetIndex) targetIndex -= 1;
  if (after) targetIndex += 1;
  state.preferenceCategories.splice(targetIndex, 0, dragged);
  state.draggedCategoryKey = null;
  renderPreferenceCategories();
  scheduleCategorySave();
}

function scheduleCategorySave() {
  clearTimeout(categorySaveTimer);
  categorySaveTimer = setTimeout(async () => {
    try {
      await api("/api/category-preferences", {
        method: "POST",
        body: JSON.stringify({ art_preference_categories: state.preferenceCategories }),
      });
    } catch (error) {
      toast(`Could not save category preferences: ${error.message}`);
    }
  }, 250);
}

async function fetchArts() {
  setBusy(true);
  state.slots = [];
  state.cardHistory = [];
  render();
  els.progress.value = 0;
  els.progress.max = 1;
  els.progressText.textContent = "Starting";
  els.activityLog.textContent = "Starting fetch...";
  els.status.textContent = "Starting fetch...";
  try {
    const data = await api("/api/fetch", {
      method: "POST",
      body: JSON.stringify({
        deck_text: els.deckText.value,
        cache_dir: els.cacheDir.value,
        use_saved_preferences: els.useSavedPreferences.checked,
        preference_profile: els.preferenceProfile.value,
        update_scryfall_images: els.updateScryfallImages.checked,
        art_preference_categories: state.preferenceCategories,
        ignore_basics: els.ignoreBasics.checked,
        sort_order: els.sortOrder.value,
      }),
    });
    state.jobId = data.job_id;
    state.pollFailures = 0;
    pollJob();
  } catch (error) {
    setBusy(false);
    els.status.textContent = error.message;
    toast(error.message);
  }
}

async function pollJob() {
  try {
    const data = await api(`/api/job?id=${encodeURIComponent(state.jobId)}`);
    state.pollFailures = 0;
    els.status.textContent = data.status;
    els.progress.max = Math.max(data.total, 1);
    els.progress.value = data.done;
    els.progressText.textContent = `${data.done} / ${data.total}`;
    renderActivity(data.log || [data.status]);
    if (data.state === "done") {
      state.slots = data.slots;
      render();
      setBusy(false);
      toast(`Found ${data.total_options} art option(s).`);
      return;
    }
    if (data.state === "error") throw new Error(data.status);
    setTimeout(pollJob, 300);
  } catch (error) {
    if (/failed to fetch|networkerror|load failed/i.test(error.message) && state.pollFailures < 20) {
      state.pollFailures += 1;
      els.status.textContent = `Connection interrupted. Retrying (${state.pollFailures}/20)...`;
      if (state.pollFailures === 1) toast("Connection interrupted. Retrying automatically...");
      setTimeout(pollJob, 750);
      return;
    }
    setBusy(false);
    const message = /unknown job/i.test(error.message)
      ? "Fetch was interrupted. Press Fetch arts to retry."
      : error.message;
    els.status.textContent = message;
    toast(message);
  }
}

function renderActivity(log) {
  const lines = log.slice(-7);
  els.activityLog.textContent = lines.length ? lines.join("\n") : "Working...";
}

function render() {
  els.grid.textContent = "";
  const fragment = document.createDocumentFragment();
  state.slots.forEach((slot, slotIndex) => fragment.appendChild(renderCard(slot, slotIndex)));
  els.grid.appendChild(fragment);
}

function renderCard(slot, slotIndex) {
  const card = document.createElement("article");
  card.className = "card";

  const title = document.createElement("div");
  title.className = "card-head";
  title.textContent = `${slot.quantity}x ${slot.name}`;
  card.appendChild(title);

  const imageWrap = document.createElement("div");
  imageWrap.className = "image-wrap";
  const current = slot.options[slot.current_index] || null;
  if (current) {
    const img = document.createElement("img");
    img.loading = "lazy";
    img.decoding = "async";
    img.src = current.image_url;
    img.alt = current.display_name;
    img.title = "Click to browse all arts";
    img.addEventListener("click", () => openArtModal(slotIndex));
    imageWrap.appendChild(img);
  } else {
    const missing = document.createElement("div");
    missing.className = "missing";
    missing.textContent = "No Scryfall art found";
    imageWrap.appendChild(missing);
  }
  card.appendChild(imageWrap);

  const meta = document.createElement("div");
  meta.className = "meta";
  meta.innerHTML = current
    ? `<strong>${escapeHtml(current.display_name)}</strong><br>${escapeHtml(current.label)}`
    : "Try a more exact card name.";
  card.appendChild(meta);

  const warning = document.createElement("div");
  warning.className = "warning";
  if (slot.requested_printing_missing) {
    warning.textContent = `Requested ${slot.requested_set_code.toUpperCase()} #${slot.requested_collector_number} was not found. Choose any available art.`;
  }
  card.appendChild(warning);

  const actions = document.createElement("div");
  actions.className = "card-actions";

  const nav = document.createElement("div");
  nav.className = "nav";
  const prev = document.createElement("button");
  prev.textContent = "<";
  const counter = document.createElement("div");
  counter.className = "counter";
  counter.textContent = current ? `${slot.current_index + 1} / ${slot.options.length}` : "0 / 0";
  const next = document.createElement("button");
  next.textContent = ">";
  prev.disabled = next.disabled = slot.options.length < 2;
  prev.addEventListener("click", () => move(slotIndex, -1));
  next.addEventListener("click", () => move(slotIndex, 1));
  nav.append(prev, counter, next);
  actions.appendChild(nav);

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "danger";
  remove.textContent = "Delete card";
  remove.title = `Remove ${slot.name} from the art picker`;
  remove.setAttribute("aria-label", `Delete ${slot.name}`);
  remove.addEventListener("click", () => deleteCard(slotIndex));
  actions.appendChild(remove);

  card.appendChild(actions);
  return card;
}

function deleteCard(slotIndex) {
  const slot = state.slots[slotIndex];
  if (!slot) return;
  state.cardHistory.push({ slot, index: slotIndex });
  state.slots.splice(slotIndex, 1);
  if (state.modal) closeArtModal();
  render();
  setBusy(state.busy);
  toast(`Deleted ${slot.quantity}x ${slot.name}. Use Undo delete to restore it.`);
}

function undoCardDelete() {
  const deleted = state.cardHistory.pop();
  if (!deleted) return;
  const restoreIndex = Math.max(0, Math.min(deleted.index, state.slots.length));
  state.slots.splice(restoreIndex, 0, deleted.slot);
  render();
  setBusy(state.busy);
  toast(`Restored ${deleted.slot.quantity}x ${deleted.slot.name}.`);
}

function move(slotIndex, direction) {
  const slot = state.slots[slotIndex];
  if (!slot || slot.options.length < 2) return;
  slot.current_index = (slot.current_index + direction + slot.options.length) % slot.options.length;
  render();
}

function openArtModal(slotIndex) {
  const slot = state.slots[slotIndex];
  if (!slot || !slot.options.length) return;
  state.modal = { slotIndex };
  els.artModal.hidden = false;
  document.body.style.overflow = "hidden";
  renderModal();
}

function closeArtModal() {
  els.artModal.hidden = true;
  document.body.style.overflow = "";
  state.modal = null;
}

function renderModal() {
  if (!state.modal) return;
  const slot = state.slots[state.modal.slotIndex];
  if (!slot) return;

  els.modalTitle.textContent = `Select version: ${slot.quantity}x ${slot.name}`;
  els.modalMeta.textContent = `${slot.options.length} eligible art option(s). Click a thumbnail to use it.`;

  els.modalThumbs.textContent = "";
  const fragment = document.createDocumentFragment();
  slot.options.forEach((thumbOption, index) => {
    const button = document.createElement("button");
    button.className = `modal-thumb${index === slot.current_index ? " active" : ""}`;
    button.title = `${thumbOption.display_name} | ${thumbOption.label}`;
    button.addEventListener("click", () => {
      slot.current_index = index;
      render();
      closeArtModal();
    });
    const img = document.createElement("img");
    img.loading = "lazy";
    img.decoding = "async";
    img.src = thumbOption.image_url;
    img.alt = thumbOption.display_name;
    button.appendChild(img);
    const label = document.createElement("span");
    label.textContent = thumbOption.label;
    button.appendChild(label);
    fragment.appendChild(button);
  });
  els.modalThumbs.appendChild(fragment);
}

function selectedPrintCards() {
  const cards = [];
  state.slots.forEach(slot => {
    const selected = slot.options[slot.current_index];
    if (!selected) return;
    const quantity = Math.max(1, Number(slot.quantity) || 1);
    for (let copy = 0; copy < quantity; copy += 1) {
      cards.push({
        preference_id: selected.preference_id,
        image_url: selected.image_url,
        name: selected.display_name || slot.name || "card",
        print_upscaled: Boolean(selected.print_upscaled),
      });
    }
  });
  return cards;
}

function selectedImages() {
  return selectedPrintCards().map(card => card.image_url);
}

function printSettings() {
  return {
    page_size: els.pageSize.value,
    orientation: els.orientation.value,
    card_width_mm: Number(els.cardWidth.value),
    card_height_mm: Number(els.cardHeight.value),
    bleed_mm: Number(els.bleed.value),
    cut_lines: els.cutLines.checked,
    guide_width_mm: Number(els.guideWidth.value),
  };
}

function layoutForPrint(settings) {
  const sizes = { letter: [215.9, 279.4], a4: [210, 297] };
  let [pageWidth, pageHeight] = sizes[settings.page_size] || sizes.letter;
  if (settings.orientation === "landscape") [pageWidth, pageHeight] = [pageHeight, pageWidth];
  const cellWidth = settings.card_width_mm + (2 * settings.bleed_mm);
  const cellHeight = settings.card_height_mm + (2 * settings.bleed_mm);
  const columns = Math.floor(pageWidth / cellWidth);
  const rows = Math.floor(pageHeight / cellHeight);
  if (columns < 1 || rows < 1) throw new Error("The card dimensions do not fit on the selected page.");
  return { pageWidth, pageHeight, cellWidth, cellHeight, columns, rows, perPage: columns * rows };
}

function printImageUrl(src, settings) {
  const url = new URL(src, window.location.origin);
  url.pathname = "/api/print-image";
  url.searchParams.set("card_width_mm", settings.card_width_mm);
  url.searchParams.set("card_height_mm", settings.card_height_mm);
  url.searchParams.set("bleed_mm", settings.bleed_mm);
  return `${url.pathname}${url.search}`;
}

function renderPrintPreview() {
  const renderId = ++state.previewRenderId;
  const cards = state.printCards;
  const settings = printSettings();
  const previousScrollTop = els.printPreview.scrollTop;
  const previousScrollLeft = els.printPreview.scrollLeft;
  try {
    const layout = layoutForPrint(settings);
    const previewWidth = 520;
    const previewHeight = previewWidth * layout.pageHeight / layout.pageWidth;
    const pageCount = Math.ceil(cards.length / layout.perPage);
    const previewFragment = document.createDocumentFragment();
    els.downloadPdf.disabled = !cards.length;
    els.undoPrintEdit.disabled = !state.printHistory.length;
    const upscaledCount = cards.filter(card => card.print_upscaled).length;
    const originalCount = cards.length - upscaledCount;
    els.printSummary.textContent = `${cards.length} card(s): ${upscaledCount} AI 2×, ${originalCount} original fallback. ${layout.columns} x ${layout.rows} per page, ${pageCount} page(s).`;
    if (!cards.length) {
      const message = document.createElement("p");
      message.textContent = "No cards in this PDF. Close Print setup to restore the deck selection.";
      els.printPreview.replaceChildren(message);
      return;
    }
    for (let pageIndex = 0; pageIndex < pageCount; pageIndex += 1) {
      const page = document.createElement("div");
      page.className = "print-page";
      page.style.width = `${previewWidth}px`;
      page.style.height = `${previewHeight}px`;
      page.style.gridTemplateColumns = `repeat(${layout.columns}, ${layout.cellWidth}fr)`;
      page.style.gridTemplateRows = `repeat(${layout.rows}, ${layout.cellHeight}fr)`;
      page.style.padding = `${(layout.pageHeight - layout.rows * layout.cellHeight) / layout.pageHeight * previewHeight / 2}px ${(layout.pageWidth - layout.columns * layout.cellWidth) / layout.pageWidth * previewWidth / 2}px`;
      const start = pageIndex * layout.perPage;
      cards.slice(start, start + layout.perPage).forEach((card, pageCardIndex) => {
        const cardIndex = start + pageCardIndex;
        const src = card.image_url;
        const cell = document.createElement("div");
        cell.className = "print-cell";
        const img = document.createElement("img");
        img.className = "print-card";
        img.loading = pageIndex < 2 ? "eager" : "lazy";
        img.decoding = "async";
        img.alt = "Selected card";
        if (settings.bleed_mm > 0) {
          img.classList.add("refining");
          img.addEventListener("load", () => {
            if (renderId !== state.previewRenderId || !img.isConnected) return;
            const refined = new Image();
            refined.onload = () => {
              if (renderId !== state.previewRenderId || !img.isConnected) return;
              img.src = refined.src;
              img.classList.remove("refining");
              img.classList.add("ready");
            };
            refined.onerror = () => img.classList.remove("refining");
            refined.src = printImageUrl(src, settings);
          }, { once: true });
        }
        img.src = src;
        cell.appendChild(img);
        if (settings.cut_lines && settings.bleed_mm > 0) {
          const cutLines = document.createElement("div");
          cutLines.className = "print-cut-lines";
          const trimLeft = settings.bleed_mm / layout.cellWidth * 100;
          const trimTop = settings.bleed_mm / layout.cellHeight * 100;
          cutLines.style.setProperty("--trim-left", `${trimLeft}%`);
          cutLines.style.setProperty("--trim-right", `${trimLeft}%`);
          cutLines.style.setProperty("--trim-top", `${trimTop}%`);
          cutLines.style.setProperty("--trim-bottom", `${trimTop}%`);
          cutLines.style.setProperty("--cut-x", `${trimLeft}%`);
          cutLines.style.setProperty("--cut-y", `${trimTop}%`);
          cutLines.style.setProperty("--cut-width", `${Math.max(1, settings.guide_width_mm / layout.pageWidth * previewWidth)}px`);
          ["tl", "tr", "bl", "br"].forEach(position => {
            ["horizontal", "vertical"].forEach(direction => {
              const mark = document.createElement("span");
              mark.className = `cut-mark ${position} ${direction}`;
              cutLines.appendChild(mark);
            });
          });
          cell.appendChild(cutLines);
        }
        const actions = document.createElement("div");
        actions.className = "print-card-actions";
        const duplicate = document.createElement("button");
        duplicate.type = "button";
        duplicate.className = "print-card-action duplicate";
        duplicate.textContent = "+";
        duplicate.title = `Duplicate ${card.name}`;
        duplicate.setAttribute("aria-label", `Duplicate ${card.name}`);
        duplicate.addEventListener("click", () => {
          rememberPrintCards();
          state.printCards.splice(cardIndex + 1, 0, { ...card });
          renderPrintPreview();
        });
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "print-card-action remove";
        remove.textContent = "X";
        remove.title = `Remove ${card.name} from PDF`;
        remove.setAttribute("aria-label", `Remove ${card.name} from PDF`);
        remove.addEventListener("click", () => {
          rememberPrintCards();
          state.printCards.splice(cardIndex, 1);
          renderPrintPreview();
        });
        actions.append(duplicate, remove);
        cell.appendChild(actions);
        const qualityBadge = document.createElement("span");
        qualityBadge.className = `print-quality-badge ${card.print_upscaled ? "upscaled" : "original"}`;
        qualityBadge.textContent = card.print_upscaled ? "AI 2×" : "Original";
        qualityBadge.title = card.print_upscaled
          ? "The upscaled 2× image will be used for this card in the PDF."
          : "No cached upscale was available when this deck was fetched; the original image will be used.";
        cell.appendChild(qualityBadge);
        page.appendChild(cell);
      });
      if (settings.cut_lines) {
        const edgeGuides = document.createElement("div");
        edgeGuides.className = "page-edge-guides";
        const cutWidth = Math.max(1, settings.guide_width_mm / layout.pageWidth * previewWidth);
        edgeGuides.style.setProperty("--cut-width", `${cutWidth}px`);
        const originX = (layout.pageWidth - layout.columns * layout.cellWidth) / 2;
        const originY = (layout.pageHeight - layout.rows * layout.cellHeight) / 2;
        const addEdgeLine = (direction, position, start, length) => {
          const line = document.createElement("span");
          line.className = `page-edge-line ${direction}`;
          if (direction === "vertical") {
            line.style.left = `${position / layout.pageWidth * 100}%`;
            line.style.top = `${start / layout.pageHeight * 100}%`;
            line.style.height = `${length / layout.pageHeight * 100}%`;
          } else {
            line.style.top = `${position / layout.pageHeight * 100}%`;
            line.style.left = `${start / layout.pageWidth * 100}%`;
            line.style.width = `${length / layout.pageWidth * 100}%`;
          }
          edgeGuides.appendChild(line);
        };
        for (let column = 0; column < layout.columns; column += 1) {
          const trimLeft = originX + column * layout.cellWidth + settings.bleed_mm;
          const trimRight = trimLeft + settings.card_width_mm;
          [trimLeft, trimRight].forEach(x => {
            addEdgeLine("vertical", x, 0, originY + settings.bleed_mm);
            addEdgeLine("vertical", x, layout.pageHeight - originY - settings.bleed_mm, originY + settings.bleed_mm);
          });
        }
        for (let row = 0; row < layout.rows; row += 1) {
          const trimTop = originY + row * layout.cellHeight + settings.bleed_mm;
          const trimBottom = trimTop + settings.card_height_mm;
          [trimTop, trimBottom].forEach(y => {
            addEdgeLine("horizontal", y, 0, originX + settings.bleed_mm);
            addEdgeLine("horizontal", y, layout.pageWidth - originX - settings.bleed_mm, originX + settings.bleed_mm);
          });
        }
        page.appendChild(edgeGuides);
      }
      const number = document.createElement("span");
      number.className = "print-page-number";
      number.textContent = `${pageIndex + 1} / ${pageCount}`;
      page.appendChild(number);
      previewFragment.appendChild(page);
    }
    els.printPreview.replaceChildren(previewFragment);
    const restoreScroll = () => {
      if (renderId !== state.previewRenderId) return;
      els.printPreview.scrollTop = previousScrollTop;
      els.printPreview.scrollLeft = previousScrollLeft;
    };
    requestAnimationFrame(() => {
      restoreScroll();
      requestAnimationFrame(restoreScroll);
      setTimeout(restoreScroll, 120);
    });
  } catch (error) {
    els.printSummary.textContent = error.message;
    const message = document.createElement("p");
    message.textContent = error.message;
    els.printPreview.replaceChildren(message);
  }
}

function schedulePrintPreview() {
  clearTimeout(previewRenderTimer);
  previewRenderTimer = setTimeout(renderPrintPreview, 180);
}

function rememberPrintCards() {
  state.printHistory.push(state.printCards.map(card => ({ ...card })));
  if (state.printHistory.length > 100) state.printHistory.shift();
}

function undoPrintEdit() {
  const previous = state.printHistory.pop();
  if (!previous) return;
  state.printCards = previous;
  renderPrintPreview();
}

function openPrintSetup() {
  prepareAndOpenPrintSetup();
}

async function prepareAndOpenPrintSetup() {
  const cards = selectedPrintCards();
  if (!cards.length) return;
  setBusy(true);
  els.exportButton.textContent = "Preparing selected arts...";
  els.status.textContent = `Downloading and AI-upscaling ${new Set(cards.map(card => card.preference_id)).size} selected art(s)...`;
  els.operationTitle.textContent = "Downloading and AI-upscaling selected arts";
  els.operationBar.value = 0;
  els.operationPercent.textContent = "0%";
  els.operationStatus.textContent = "Starting...";
  els.operationProgress.hidden = false;
  try {
    const started = await api("/api/prepare-selected", {
      method: "POST",
      body: JSON.stringify({
        job_id: state.jobId,
        print_items: cards.map(card => ({ preference_id: card.preference_id })),
      }),
    });
    const operation = await waitForOperation(started.operation_id, updateOperationDisplay);
    const data = operation.result || {};
    const prepared = data.prepared || {};
    state.slots.forEach(slot => slot.options.forEach(option => {
      const result = prepared[option.preference_id];
      if (!result) return;
      option.image_url = result.image_url;
      option.print_upscaled = Boolean(result.print_upscaled);
    }));
    state.printCards = selectedPrintCards();
    state.printHistory = [];
    els.printModal.hidden = false;
    document.body.style.overflow = "hidden";
    render();
    renderPrintPreview();
    els.status.textContent = `Prepared ${data.prepared_count} selected art(s); ${data.upscaled_count} AI-upscaled.`;
    if (data.warning) toast(data.warning);
  } catch (error) {
    els.status.textContent = error.message;
    toast(error.message);
  } finally {
    els.operationProgress.hidden = true;
    els.exportButton.textContent = "Print setup";
    setBusy(false);
  }
}

function closePrintSetup() {
  els.printModal.hidden = true;
  document.body.style.overflow = "";
}

async function downloadPrintablePdf() {
  els.downloadPdf.disabled = true;
  els.downloadPdf.textContent = "Preparing PDF...";
  els.pdfProgress.hidden = false;
  const cardCount = state.printCards.length;
  els.pdfProgressBar.value = 0;
  els.pdfProgressText.textContent = `Preparing ${cardCount} card(s)... 0%`;
  try {
    const started = await api("/api/start-pdf", {
      method: "POST",
      body: JSON.stringify({
        job_id: state.jobId,
        filename: els.pdfFilename.value,
        slots: state.slots,
        print_items: state.printCards.map(card => ({ preference_id: card.preference_id })),
        ...printSettings(),
      }),
    });
    const operation = await waitForOperation(started.operation_id, data => {
      const percent = Math.max(0, Math.min(100, Number(data.percent) || 0));
      els.pdfProgressBar.value = percent;
      els.pdfProgressText.textContent = `${data.status || `Preparing ${cardCount} card(s)`} — ${Math.round(percent)}%`;
    });
    const response = await fetch(`/api/pdf-download?id=${encodeURIComponent(started.operation_id)}`);
    if (!response.ok) {
      const data = await response.json();
      throw new Error(data.error || operation.error || "Could not download the PDF.");
    }
    const blob = await response.blob();
    els.pdfProgressText.textContent = "PDF ready. Starting download...";
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="([^"]+)"/);
    link.href = url;
    link.download = match ? match[1] : "cards.pdf";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    els.status.textContent = "Printable PDF created.";
    toast("Printable PDF downloaded. Print it at 100% / Actual size.");
  } catch (error) {
    els.pdfProgressText.textContent = `PDF failed: ${error.message}`;
    toast(error.message);
  } finally {
    els.downloadPdf.disabled = !state.printCards.length;
    els.downloadPdf.textContent = "Download printable PDF";
    setTimeout(() => { els.pdfProgress.hidden = true; }, 1800);
  }
}

async function savePreferences() {
  const choices = state.slots
    .map(slot => {
      const selected = slot.options[slot.current_index];
      return selected ? { name: slot.name, preference_id: selected.preference_id } : null;
    })
    .filter(Boolean);
  if (!choices.length) {
    toast("No preferences to save.");
    return;
  }
  const profile = els.preferenceProfile.value;
  if (!confirm(`Save preferred art for ${choices.length} card(s) to the “${profile}” profile?`)) return;
  try {
    const data = await api("/api/preferences", {
      method: "POST",
      body: JSON.stringify({ choices, profile }),
    });
    toast(`Saved preferred art for ${data.saved} card(s) in “${data.profile}”.`);
  } catch (error) {
    toast(error.message);
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

els.fetchButton.addEventListener("click", fetchArts);
els.undoCardDelete.addEventListener("click", undoCardDelete);
els.exportButton.addEventListener("click", openPrintSetup);
els.savePreferencesButton.addEventListener("click", savePreferences);
els.newProfile.addEventListener("click", () => openProfileDialog("create"));
els.renameProfile.addEventListener("click", () => openProfileDialog("rename"));
els.deleteProfile.addEventListener("click", () => openProfileDialog("delete"));
els.profileModalClose.addEventListener("click", closeProfileDialog);
els.profileModalCancel.addEventListener("click", closeProfileDialog);
els.profileModalConfirm.addEventListener("click", submitProfileDialog);
els.profileNameInput.addEventListener("keydown", event => {
  if (event.key === "Enter") submitProfileDialog();
});
els.preferenceProfile.addEventListener("change", () => {
  updatePreferenceProfiles("select").catch(error => toast(error.message));
});
els.useSavedPreferences.addEventListener("change", () => {
  updatePreferenceProfiles("configure").then(data => {
    toast(data.use_saved_preferences ? "Saved preferences enabled." : "Saved preferences disabled.");
  }).catch(error => toast(error.message));
});
els.modalClose.addEventListener("click", closeArtModal);
els.printClose.addEventListener("click", closePrintSetup);
els.undoPrintEdit.addEventListener("click", undoPrintEdit);
els.downloadPdf.addEventListener("click", downloadPrintablePdf);
[els.pageSize, els.orientation, els.cardWidth, els.cardHeight, els.bleed, els.cutLines, els.guideWidth]
  .forEach(control => control.addEventListener("input", schedulePrintPreview));
els.artModal.addEventListener("click", event => {
  if (event.target === els.artModal) closeArtModal();
});
els.profileModal.addEventListener("click", event => {
  if (event.target === els.profileModal) closeProfileDialog();
});
document.addEventListener("keydown", event => {
  if (event.key !== "Escape") return;
  if (!els.profileModal.hidden) closeProfileDialog();
  if (state.modal) closeArtModal();
  if (!els.printModal.hidden) closePrintSetup();
});
setBusy(false);
loadSettings().catch(error => toast(error.message));
"""


def option_to_json(option: core.ArtOption, job_id: str, upscaled_ids: set[str] | None = None) -> dict:
    image_url = (
        f"/api/image?job={urllib.parse.quote(job_id)}&id={urllib.parse.quote(option.preference_id)}"
        if option.cache_path.exists()
        else option.preview_url or option.png_url
    )
    return {
        "preference_id": option.preference_id,
        "display_name": option.display_name,
        "set_code": option.set_code,
        "collector_number": option.collector_number,
        "label": option.label,
        "selected": option.selected,
        "print_upscaled": option.preference_id in (upscaled_ids or set()),
        "image_url": image_url,
        "preference_categories": list(option.preference_categories),
    }


def option_source_to_json(option: core.ArtOption) -> dict:
    return {
        "card_id": option.card_id,
        "oracle_id": option.oracle_id,
        "display_name": option.display_name,
        "printed_name": option.printed_name,
        "set_code": option.set_code,
        "set_name": option.set_name,
        "collector_number": option.collector_number,
        "released_at": option.released_at,
        "artist": option.artist,
        "png_url": option.png_url,
        "preview_url": option.preview_url,
        "cache_path": str(option.cache_path),
        "preference_key": option.preference_key,
        "preference_categories": list(option.preference_categories),
    }


def option_from_source(data: dict) -> core.ArtOption:
    return core.ArtOption(
        card_id=str(data.get("card_id") or ""),
        oracle_id=str(data.get("oracle_id") or ""),
        display_name=str(data.get("display_name") or ""),
        printed_name=str(data.get("printed_name") or ""),
        set_code=str(data.get("set_code") or ""),
        set_name=str(data.get("set_name") or ""),
        collector_number=str(data.get("collector_number") or ""),
        released_at=str(data.get("released_at") or ""),
        artist=str(data.get("artist") or ""),
        png_url=str(data.get("png_url") or ""),
        cache_path=Path(str(data.get("cache_path") or "")).expanduser(),
        preview_url=str(data.get("preview_url") or ""),
        preference_key=str(data.get("preference_key") or ""),
        preference_categories=tuple(str(value) for value in (data.get("preference_categories") or [])),
    )


def slot_to_json(slot: core.CardSlot, job_id: str, upscaled_ids: set[str] | None = None) -> dict:
    return {
        "name": slot.entry.name,
        "quantity": slot.entry.quantity,
        "requested_set_code": slot.entry.requested_set_code,
        "requested_collector_number": slot.entry.requested_collector_number,
        "requested_printing_missing": slot.requested_printing_missing,
        "current_index": slot.current_index,
        "options": [option_to_json(option, job_id, upscaled_ids) for option in slot.options],
    }


def apply_default_selection(entry: core.DeckEntry, options: list[core.ArtOption], preferences: dict[str, str]) -> tuple[int, bool]:
    for option in options:
        option.selected = False
    if not options:
        return 0, bool(entry.requested_set_code and entry.requested_collector_number)

    for index, option in enumerate(options):
        if (
            entry.requested_set_code
            and entry.requested_collector_number
            and option.set_code.casefold() == entry.requested_set_code
            and core.normalize_collector_number(option.collector_number) == entry.requested_collector_number
        ):
            option.selected = True
            return index, False

    requested_missing = bool(entry.requested_set_code and entry.requested_collector_number)
    for index, option in enumerate(options):
        if option.set_code.casefold() == "custom":
            option.selected = True
            return index, requested_missing

    preferred_id = preferences.get(core.normalize_name(entry.name), "")
    for index, option in enumerate(options):
        if option.preference_id == preferred_id:
            option.selected = True
            return index, requested_missing
    return 0, requested_missing


def start_fetch_job(payload: dict) -> str:
    entries = core.parse_deck_list(str(payload.get("deck_text", "")))
    if not entries:
        raise ValueError("Paste at least one card name.")

    ignore_basics = core.coerce_setting_bool(payload.get("ignore_basics"), core.DEFAULT_IGNORE_BASICS)
    ignored_basic_count = 0
    if ignore_basics:
        original_count = len(entries)
        entries = core.exclude_basic_lands(entries)
        ignored_basic_count = original_count - len(entries)
        if not entries:
            raise ValueError("Every listed card was a basic land and Ignore Basics is enabled.")

    update_scryfall_images = core.coerce_setting_bool(
        payload.get("update_scryfall_images"),
        core.DEFAULT_UPDATE_SCRYFALL_IMAGES,
    )
    current_settings = core.load_settings()
    use_saved_preferences = core.coerce_setting_bool(
        payload.get("use_saved_preferences"),
        core.DEFAULT_USE_SAVED_PREFERENCES,
    )
    preference_profiles = core.load_preference_profiles()
    preference_profile = core.resolve_preference_profile_name(
        payload.get("preference_profile") or current_settings.get(core.PREFERENCE_PROFILE_SETTING),
        preference_profiles,
    )
    preference_categories = core.normalize_art_preference_categories(
        payload.get("art_preference_categories"),
        current_settings,
    )
    settings = {
        "cache_dir": str(payload.get("cache_dir") or core.DEFAULT_CACHE_DIR),
        "export_parent": str(payload.get("export_parent") or core.DEFAULT_EXPORT_PARENT),
        core.USE_SAVED_PREFERENCES_SETTING: "true" if use_saved_preferences else "false",
        core.PREFERENCE_PROFILE_SETTING: preference_profile,
        core.UPDATE_SCRYFALL_IMAGES_SETTING: "true" if update_scryfall_images else "false",
        core.ART_PREFERENCE_CATEGORIES_SETTING: json.dumps(preference_categories),
        core.IGNORE_BASICS_SETTING: "true" if ignore_basics else "false",
    }
    core.save_settings(settings)

    job_id = uuid4().hex
    initial_log = []
    if ignored_basic_count:
        initial_log.append(f"Ignored {ignored_basic_count} basic land line(s).")
    initial_log.append(f"Queued {len(entries)} card(s).")
    job = {
        "state": "running",
        "status": f"Fetching art options for {len(entries)} card(s)...",
        "done": 0,
        "total": len(entries),
        "total_options": 0,
        "cache_dir": settings["cache_dir"],
        "sort_order": str(payload.get("sort_order") or "oldest"),
        "log": initial_log,
        "slots": [],
    }
    with JOBS_LOCK:
        JOBS[job_id] = job

    ordering = {
        "sort_order": str(payload.get("sort_order") or "oldest"),
        "preference_categories": preference_categories,
        "use_saved_preferences": use_saved_preferences,
        "preference_profile": preference_profile,
    }
    thread = threading.Thread(
        target=run_fetch_job,
        args=(
            job_id,
            entries,
            settings,
            update_scryfall_images,
            False,
            False,
            False,
            ordering,
        ),
        daemon=True,
    )
    thread.start()
    return job_id


def run_fetch_job(
    job_id: str,
    entries: list[core.DeckEntry],
    settings: dict[str, str],
    force_refresh: bool = False,
    hide_promo_arts: bool = False,
    hide_foreign_arts: bool = False,
    hide_list_arts: bool = False,
    ordering: dict | None = None,
) -> None:
    ordering = ordering or {}
    preferences = (
        core.load_preferences(ordering.get("preference_profile"))
        if core.coerce_setting_bool(ordering.get("use_saved_preferences"), True)
        else {}
    )
    client = core.ScryfallClient(Path(settings["cache_dir"]).expanduser())
    slots: list[core.CardSlot] = []
    try:
        for index, entry in enumerate(entries, start=1):
            verb = "Refreshing" if force_refresh else "Fetching"
            update_job(job_id, done=index - 1, status=f"[{index}/{len(entries)}] {verb} {entry.name}")
            try:
                options = client.fetch_options(
                    entry.name,
                    lambda text: update_job(job_id, status=text),
                    force_refresh=force_refresh,
                    hide_promos=hide_promo_arts,
                    hide_foreign=hide_foreign_arts,
                    hide_list=hide_list_arts,
                    defer_images=SELECT_FIRST_MODE,
                )
            except Exception as exc:
                update_job(job_id, status=f"Skipping {entry.name}: {exc}")
                update_job(job_id, done=index)
                continue
            options = core.filter_and_sort_art_options(
                options,
                sort_order=str(ordering.get("sort_order") or "oldest"),
                preference_categories=ordering.get("preference_categories"),
            )
            current_index, missing = apply_default_selection(entry, options, preferences)
            slots.append(core.CardSlot(entry=entry, options=options, current_index=current_index, requested_printing_missing=missing))
            update_job(job_id, done=index)

        total_options = sum(len(slot.options) for slot in slots)
        with JOBS_LOCK:
            job = JOBS[job_id]
            image_paths = {
                option.preference_id: str(option.cache_path)
                for slot in slots
                for option in slot.options
            }
            option_sources = {
                option.preference_id: option_source_to_json(option)
                for slot in slots
                for option in slot.options
            }
            print_image_paths = {
                option.preference_id: str(client.upscaler.cached_path(option.cache_path))
                for slot in slots
                for option in slot.options
            }
            upscaled_ids = {
                preference_id
                for preference_id, print_path in print_image_paths.items()
                if Path(print_path) != Path(image_paths.get(preference_id, print_path))
            }
            job.update(
                {
                    "state": "done",
                    "status": f"Ready: found {total_options} art option(s) across {sum(1 for slot in slots if slot.options)}/{len(slots)} card(s).",
                    "done": len(entries),
                    "total_options": total_options,
                    "log": job.get("log", [])[-8:]
                    + [
                        f"Finished. Found {total_options} art option(s) across {sum(1 for slot in slots if slot.options)}/{len(slots)} card(s)."
                    ],
                    "image_paths": image_paths,
                    "print_image_paths": print_image_paths,
                    "option_sources": option_sources,
                    "slots": [slot_to_json(slot, job_id, upscaled_ids) for slot in slots],
                }
            )
    except Exception as exc:
        update_job(job_id, state="error", status=str(exc))


def update_job(job_id: str, **updates) -> None:
    with JOBS_LOCK:
        if job_id in JOBS:
            job = JOBS[job_id]
            status = updates.get("status")
            if status:
                log = list(job.get("log", []))
                if not log or log[-1] != status:
                    log.append(str(status))
                updates["log"] = log[-12:]
            job.update(updates)


def create_operation(kind: str, status: str) -> str:
    operation_id = uuid4().hex
    with OPERATIONS_LOCK:
        OPERATIONS[operation_id] = {
            "kind": kind,
            "state": "running",
            "status": status,
            "percent": 0,
        }
    return operation_id


def update_operation(operation_id: str, percent: float, status: str, **updates) -> None:
    percent = max(0.0, min(100.0, float(percent)))
    with OPERATIONS_LOCK:
        operation = OPERATIONS.get(operation_id)
        if operation is None:
            return
        operation.update(
            {
                "percent": round(percent, 1),
                "status": status,
                **updates,
            }
        )


def operation_for_client(operation_id: str) -> dict:
    with OPERATIONS_LOCK:
        operation = dict(OPERATIONS.get(operation_id) or {})
    operation.pop("pdf_data", None)
    return operation


def _bounded_float(payload: dict, key: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(payload.get(key, default))
    except (TypeError, ValueError):
        value = default
    if not minimum <= value <= maximum:
        raise ValueError(f"{key.replace('_', ' ').title()} must be between {minimum} and {maximum}.")
    return value


def prepare_selected(payload: dict, progress_callback=None) -> dict:
    job_id = str(payload.get("job_id") or "")
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        job_snapshot = dict(job or {})
    if not job_snapshot:
        raise ValueError("Find arts before preparing selected images.")

    requested_ids = []
    seen_ids: set[str] = set()
    for item in payload.get("print_items") or []:
        preference_id = str(item.get("preference_id") or "")
        if preference_id and preference_id not in seen_ids:
            requested_ids.append(preference_id)
            seen_ids.add(preference_id)
    if not requested_ids:
        raise ValueError("There are no selected images to prepare.")

    source_records = dict(job_snapshot.get("option_sources") or {})
    missing_ids = [preference_id for preference_id in requested_ids if preference_id not in source_records]
    if missing_ids:
        raise ValueError("One or more selected arts no longer belong to this fetch. Find arts again.")

    client = core.ScryfallClient(Path(str(job_snapshot.get("cache_dir") or core.DEFAULT_CACHE_DIR)).expanduser())
    options = [option_from_source(source_records[preference_id]) for preference_id in requested_ids]
    prepared_sources: list[Path] = []
    if progress_callback:
        progress_callback(2, f"Checking {len(options)} selected art(s)...")
    for index, option in enumerate(options, start=1):
        update_job(
            job_id,
            status=f"Preparing selected art {index}/{len(options)}: {option.display_name}",
        )
        if not option.cache_path.exists():
            if not option.png_url:
                raise ValueError(f"The selected custom art is missing: {option.display_name}")
            client._download_png(option, lambda text: update_job(job_id, status=text))
        if not option.cache_path.exists():
            raise ValueError(f"Could not download the selected art: {option.display_name}")
        prepared_sources.append(option.cache_path)
        if progress_callback:
            progress_callback(
                5 + (20 * index / max(1, len(options))),
                f"Downloaded or found {index}/{len(options)} selected art(s)",
            )

    warning = ""
    try:
        pending_upscales = sum(
            1 for source in prepared_sources if client.upscaler.cached_path(source) == source
        )

        def upscale_progress(done: int, total: int) -> None:
            if not progress_callback:
                return
            if total:
                progress_callback(
                    25 + (68 * done / total),
                    f"AI-upscaling selected arts: {done}/{total}",
                )
            else:
                progress_callback(93, "All selected arts already have cached AI upscales")

        if progress_callback and pending_upscales:
            progress_callback(25, f"Starting AI upscale for {pending_upscales} art(s)...")
        client.upscaler.ensure_batch(
            prepared_sources,
            status_callback=lambda text: update_job(job_id, status=text),
            progress_callback=upscale_progress,
        )
    except (OSError, core.UpscaleError) as exc:
        warning = f"AI upscaling was unavailable; original selected images will be used. {exc}"

    prepared = {}
    image_paths = dict(job_snapshot.get("image_paths") or {})
    print_image_paths = dict(job_snapshot.get("print_image_paths") or {})
    upscaled_count = 0
    if progress_callback:
        progress_callback(95, "Finalizing prepared images...")
    for option in options:
        image_paths[option.preference_id] = str(option.cache_path)
        print_path = client.upscaler.cached_path(option.cache_path)
        print_image_paths[option.preference_id] = str(print_path)
        is_upscaled = print_path != option.cache_path
        if is_upscaled:
            upscaled_count += 1
        prepared[option.preference_id] = {
            "image_url": f"/api/image?job={urllib.parse.quote(job_id)}&id={urllib.parse.quote(option.preference_id)}",
            "print_upscaled": is_upscaled,
        }

    with JOBS_LOCK:
        current_job = JOBS.get(job_id)
        if current_job is not None:
            current_job["image_paths"] = image_paths
            current_job["print_image_paths"] = print_image_paths
            current_job["status"] = (
                f"Prepared {len(options)} selected art(s); {upscaled_count} AI-upscaled."
            )

    return {
        "prepared": prepared,
        "prepared_count": len(options),
        "upscaled_count": upscaled_count,
        "warning": warning,
    }


def start_prepare_operation(payload: dict) -> str:
    operation_id = create_operation("prepare", "Starting selected-art preparation...")

    def worker() -> None:
        try:
            result = prepare_selected(
                payload,
                progress_callback=lambda percent, status: update_operation(
                    operation_id, percent, status
                ),
            )
            update_operation(
                operation_id,
                100,
                f"Prepared {result['prepared_count']} selected art(s).",
                state="done",
                result=result,
            )
        except Exception as exc:
            update_operation(operation_id, 100, str(exc), state="error", error=str(exc))

    threading.Thread(target=worker, daemon=True).start()
    return operation_id


def selected_image_paths(payload: dict, job: dict) -> list[Path]:
    image_paths = dict(job.get("image_paths") or {})
    print_image_paths = dict(job.get("print_image_paths") or image_paths)
    selected: list[Path] = []
    if "print_items" in payload:
        for item in payload.get("print_items") or []:
            source = Path(str(print_image_paths.get(str(item.get("preference_id") or ""), ""))).expanduser()
            if source.exists():
                selected.append(source)
        return selected
    for slot in payload.get("slots") or []:
        options = slot.get("options") or []
        try:
            current_index = int(slot.get("current_index") or 0)
        except (TypeError, ValueError):
            current_index = 0
        current = options[current_index] if 0 <= current_index < len(options) else None
        if not current:
            continue
        source = Path(str(print_image_paths.get(str(current.get("preference_id") or ""), ""))).expanduser()
        if not source.exists():
            continue
        try:
            quantity = max(1, int(slot.get("quantity") or 1))
        except (TypeError, ValueError):
            quantity = 1
        selected.extend([source] * quantity)
    return selected


@lru_cache(maxsize=128)
def build_print_image(
    source_path: str,
    source_mtime_ns: int,
    card_width_mm: float,
    card_height_mm: float,
    bleed_mm: float,
    output_format: str = "PNG",
) -> bytes:
    """Normalize a card to trim size and extend its artwork through the bleed."""
    from PIL import Image, ImageDraw, ImageOps

    def js_round(value: float) -> int:
        """JavaScript Math.round for the non-negative values used by devprint."""
        return int(value + 0.5)

    del source_mtime_ns  # Included in the cache key so refreshed art invalidates the result.
    with Image.open(source_path) as opened:
        source = opened.convert("RGBA")

    # Match devprint's calculateDpi/addBleedEdge sizing exactly: calculate an
    # average DPI from both source dimensions, round it, then cover-crop onto the
    # physical card dimensions derived from that DPI.
    dpi_width = source.width / (card_width_mm / 25.4)
    dpi_height = source.height / (card_height_mm / 25.4)
    dpi = js_round((dpi_width + dpi_height) / 2)
    pixels_per_mm = dpi / 25.4
    card_width_px = max(1, js_round(card_width_mm * pixels_per_mm))
    card_height_px = max(1, js_round(card_height_mm * pixels_per_mm))
    card = ImageOps.fit(
        source,
        (card_width_px, card_height_px),
        method=Image.Resampling.BICUBIC,
        centering=(0.5, 0.5),
    )
    bleed_px = max(0, js_round(bleed_mm * pixels_per_mm))

    if bleed_px:
        # Direct translation of devprint's addBleedEdge implementation.
        dpi_scale = dpi / 300
        corner_size = max(1, min(js_round(30 * dpi_scale), card_width_px, card_height_px))
        sample_inset = js_round(10 * dpi_scale)
        corner_radius = max(1, js_round(2.5 * pixels_per_mm))
        corner_overlap = max(1, js_round(0.4 * pixels_per_mm))
        cut_radius = max(1, corner_radius - corner_overlap)

        corner_configs = (
            (0, 0, False, False),
            (card_width_px - corner_size, 0, True, False),
            (0, card_height_px - corner_size, False, True),
            (card_width_px - corner_size, card_height_px - corner_size, True, True),
        )
        for rect_x, rect_y, from_right, from_bottom in corner_configs:
            corner = card.crop((rect_x, rect_y, rect_x + corner_size, rect_y + corner_size))
            pixels = list(corner.getdata())
            needs_corner_fill = any(
                a == 0 or (r > 200 and g > 200 and b > 200) for r, g, b, a in pixels
            )
            if not needs_corner_fill:
                continue

            # devprint samples a fixed 10x10 square at its DPI-scaled inset and
            # fills with that square's average color; it does not copy a texture.
            sample_x = card_width_px - sample_inset - 10 if from_right else sample_inset
            sample_y = card_height_px - sample_inset - 10 if from_bottom else sample_inset
            sample = card.crop((sample_x, sample_y, sample_x + 10, sample_y + 10))
            sample_pixels = [pixel for pixel in sample.getdata() if pixel[3] != 0]
            if sample_pixels:
                fill_color = tuple(
                    js_round(sum(pixel[channel] for pixel in sample_pixels) / len(sample_pixels))
                    for channel in range(3)
                ) + (255,)
            else:
                fill_color = (0, 0, 0, 255)
            fill = Image.new("RGBA", (corner_size, corner_size), fill_color)
            mask = Image.new("L", (corner_size, corner_size), 255)
            mask_draw = ImageDraw.Draw(mask)
            center_x = corner_size - corner_radius if from_right else corner_radius
            center_y = corner_size - corner_radius if from_bottom else corner_radius
            mask_draw.ellipse(
                (center_x - cut_radius, center_y - cut_radius, center_x + cut_radius, center_y + cut_radius),
                fill=0,
            )
            card.paste(fill, (rect_x, rect_y), mask)

        # devprint blackens near-black pixels only inside these fixed pixel border
        # bands before deciding whether to stretch or mirror the edge.
        pixels = card.load()
        for y in range(card_height_px):
            for x in range(card_width_px):
                if not (y < 96 or y >= card_height_px - 400 or x < 48 or x >= card_width_px - 48):
                    continue
                r, g, b, a = pixels[x, y]
                if r < 30 and g < 30 and b < 30:
                    pixels[x, y] = (0, 0, 0, a)

        # Mirrored corner patches are sampled a few pixels in from the card and
        # can therefore pick up artwork that does not match the true corner.
        # Preserve black corners explicitly in the disposable bleed area.
        black_bleed_corners = (
            pixels[0, 0][:3] == (0, 0, 0),
            pixels[card_width_px - 1, 0][:3] == (0, 0, 0),
            pixels[0, card_height_px - 1][:3] == (0, 0, 0),
            pixels[card_width_px - 1, card_height_px - 1][:3] == (0, 0, 0),
        )

        final_width = card_width_px + 2 * bleed_px
        final_height = card_height_px + 2 * bleed_px
        extended = Image.new("RGBA", (final_width, final_height))
        extended.paste(card, (bleed_px, bleed_px))

        black_threshold = 30
        left_edge = card.crop((0, 0, 1, card_height_px)).convert("RGB")
        black_pixels = sum(
            1 for r, g, b in left_edge.getdata()
            if r < black_threshold and g < black_threshold and b < black_threshold
        )
        mostly_black_border = black_pixels / card_height_px > 0.7

        if mostly_black_border:
            # devprint uses an unscaled, fixed eight-pixel slice.
            edge_slice = min(8, card_width_px, card_height_px)
            boxes = (
                ((0, 0, edge_slice, card_height_px), (0, bleed_px), (bleed_px, card_height_px)),
                ((card_width_px - edge_slice, 0, card_width_px, card_height_px), (bleed_px + card_width_px, bleed_px), (bleed_px, card_height_px)),
                ((0, 0, card_width_px, edge_slice), (bleed_px, 0), (card_width_px, bleed_px)),
                ((0, card_height_px - edge_slice, card_width_px, card_height_px), (bleed_px, bleed_px + card_height_px), (card_width_px, bleed_px)),
                ((0, 0, edge_slice, edge_slice), (0, 0), (bleed_px, bleed_px)),
                ((card_width_px - edge_slice, 0, card_width_px, edge_slice), (bleed_px + card_width_px, 0), (bleed_px, bleed_px)),
                ((0, card_height_px - edge_slice, edge_slice, card_height_px), (0, bleed_px + card_height_px), (bleed_px, bleed_px)),
                ((card_width_px - edge_slice, card_height_px - edge_slice, card_width_px, card_height_px), (bleed_px + card_width_px, bleed_px + card_height_px), (bleed_px, bleed_px)),
            )
            for source_box, destination, size in boxes:
                extended.paste(card.crop(source_box).resize(size, Image.Resampling.BICUBIC), destination)
        else:
            # devprint uses different source offsets for sides and corners. That
            # distinction is essential: the corner sources reach the actual card
            # corner, while the side sources overscan four pixels farther inward.
            overscan = 4
            span = bleed_px + overscan
            side_x = overscan
            side_y = overscan
            right_edge_x = card_width_px - bleed_px - overscan * 2
            bottom_edge_y = card_height_px - bleed_px - overscan * 2
            right_corner_x = card_width_px - bleed_px - overscan
            bottom_corner_y = card_height_px - bleed_px - overscan
            paste_x_right = bleed_px + card_width_px - overscan
            paste_y_bottom = bleed_px + card_height_px - overscan

            left = ImageOps.mirror(card.crop((side_x, 0, side_x + span, card_height_px)))
            right = ImageOps.mirror(card.crop((right_edge_x, 0, right_edge_x + span, card_height_px)))
            top = ImageOps.flip(card.crop((0, side_y, card_width_px, side_y + span)))
            bottom = ImageOps.flip(card.crop((0, bottom_edge_y, card_width_px, bottom_edge_y + span)))
            extended.paste(left, (0, bleed_px))
            extended.paste(right, (paste_x_right, bleed_px))
            extended.paste(top, (bleed_px, 0))
            extended.paste(bottom, (bleed_px, paste_y_bottom))

            corners = (
                (ImageOps.flip(ImageOps.mirror(card.crop((side_x, side_y, side_x + span, side_y + span)))), (0, 0)),
                (ImageOps.flip(ImageOps.mirror(card.crop((right_corner_x, side_y, right_corner_x + span, side_y + span)))), (paste_x_right, 0)),
                (ImageOps.flip(ImageOps.mirror(card.crop((side_x, bottom_corner_y, side_x + span, bottom_corner_y + span)))), (0, paste_y_bottom)),
                (ImageOps.flip(ImageOps.mirror(card.crop((right_corner_x, bottom_corner_y, right_corner_x + span, bottom_corner_y + span)))), (paste_x_right, paste_y_bottom)),
            )
            for corner, destination in corners:
                extended.paste(corner, destination)

        black_corner = Image.new("RGBA", (bleed_px, bleed_px), (0, 0, 0, 255))
        bleed_corner_positions = (
            (0, 0),
            (bleed_px + card_width_px, 0),
            (0, bleed_px + card_height_px),
            (bleed_px + card_width_px, bleed_px + card_height_px),
        )
        for is_black, position in zip(black_bleed_corners, bleed_corner_positions):
            if is_black:
                extended.paste(black_corner, position)
        card = extended

    output = io.BytesIO()
    if output_format.upper() == "JPEG":
        card.convert("RGB").save(output, format="JPEG", quality=92, subsampling=0)
    else:
        card.convert("RGB").save(output, format="PNG", compress_level=3)
    return output.getvalue()


def create_print_pdf(payload: dict, progress_callback=None) -> tuple[bytes, str, int, int]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from reportlab.lib.utils import ImageReader
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    job_id = str(payload.get("job_id") or "")
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        raise ValueError("Fetch arts before creating a PDF.")

    images = selected_image_paths(payload, job)
    if not images:
        raise ValueError("There are no selected images to print.")
    if progress_callback:
        progress_callback(2, f"Checking {len(images)} card(s)...")

    page_sizes_mm = {"letter": (215.9, 279.4), "a4": (210.0, 297.0)}
    page_size_name = str(payload.get("page_size") or "letter").casefold()
    if page_size_name not in page_sizes_mm:
        raise ValueError("Page size must be Letter or A4.")
    page_width_mm, page_height_mm = page_sizes_mm[page_size_name]
    orientation = str(payload.get("orientation") or "portrait").casefold()
    if orientation not in {"portrait", "landscape"}:
        raise ValueError("Orientation must be portrait or landscape.")
    if orientation == "landscape":
        page_width_mm, page_height_mm = page_height_mm, page_width_mm

    card_width_mm = _bounded_float(payload, "card_width_mm", 63.0, 20.0, 200.0)
    card_height_mm = _bounded_float(payload, "card_height_mm", 88.0, 20.0, 200.0)
    bleed_mm = _bounded_float(payload, "bleed_mm", 1.5, 0.0, 10.0)
    guide_width_mm = _bounded_float(payload, "guide_width_mm", 0.3, 0.05, 2.0)
    cut_lines = bool(payload.get("cut_lines", True))
    cell_width_mm = card_width_mm + 2 * bleed_mm
    cell_height_mm = card_height_mm + 2 * bleed_mm
    columns = int(page_width_mm // cell_width_mm)
    rows = int(page_height_mm // cell_height_mm)
    if columns < 1 or rows < 1:
        raise ValueError("The card dimensions do not fit on the selected page.")
    per_page = columns * rows
    pages = (len(images) + per_page - 1) // per_page

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(page_width_mm * mm, page_height_mm * mm), pageCompression=1)
    grid_width_mm = columns * cell_width_mm
    grid_height_mm = rows * cell_height_mm
    origin_x_mm = (page_width_mm - grid_width_mm) / 2
    origin_y_mm = (page_height_mm - grid_height_mm) / 2
    unique_sources = list(dict.fromkeys(images))

    def prepare_source(source: Path) -> tuple[Path, bytes]:
        return (
            source,
            build_print_image(
                str(source),
                source.stat().st_mtime_ns,
                card_width_mm,
                card_height_mm,
                bleed_mm,
                "JPEG",
            ),
        )

    worker_count = min(4, max(1, len(unique_sources)))
    prepared_images = {}
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {executor.submit(prepare_source, source): source for source in unique_sources}
        for completed_count, future in enumerate(as_completed(futures), start=1):
            source, prepared = future.result()
            prepared_images[source] = prepared
            if progress_callback:
                progress_callback(
                    5 + (65 * completed_count / max(1, len(unique_sources))),
                    f"Preparing card images: {completed_count}/{len(unique_sources)} unique art(s)",
                )

    for page_index in range(pages):
        for local_index, source in enumerate(images[page_index * per_page : (page_index + 1) * per_page]):
            column = local_index % columns
            row_from_top = local_index // columns
            x_mm = origin_x_mm + column * cell_width_mm
            y_mm = page_height_mm - origin_y_mm - (row_from_top + 1) * cell_height_mm
            image_reader = ImageReader(io.BytesIO(prepared_images[source]))
            pdf.drawImage(
                image_reader,
                x_mm * mm,
                y_mm * mm,
                width=cell_width_mm * mm,
                height=cell_height_mm * mm,
                preserveAspectRatio=False,
                mask="auto",
            )
            if cut_lines and bleed_mm > 0:
                trim_x = (x_mm + bleed_mm) * mm
                trim_y = (y_mm + bleed_mm) * mm
                trim_right = trim_x + card_width_mm * mm
                trim_top = trim_y + card_height_mm * mm
                bleed = bleed_mm * mm
                segments = (
                    (trim_x - bleed, trim_y, trim_x, trim_y),
                    (trim_x, trim_y - bleed, trim_x, trim_y),
                    (trim_right, trim_y, trim_right + bleed, trim_y),
                    (trim_right, trim_y - bleed, trim_right, trim_y),
                    (trim_x - bleed, trim_top, trim_x, trim_top),
                    (trim_x, trim_top, trim_x, trim_top + bleed),
                    (trim_right, trim_top, trim_right + bleed, trim_top),
                    (trim_right, trim_top, trim_right, trim_top + bleed),
                )
                pdf.saveState()
                pdf.setStrokeColorRGB(173 / 255, 1, 47 / 255)
                pdf.setLineWidth(guide_width_mm * mm)
                pdf.setDash()
                for segment in segments:
                    pdf.line(*segment)
                pdf.restoreState()
        if cut_lines:
            pdf.saveState()
            pdf.setStrokeColorRGB(173 / 255, 1, 47 / 255)
            pdf.setLineWidth(guide_width_mm * mm)
            pdf.setDash()
            top_trim = page_height_mm - origin_y_mm - bleed_mm
            bottom_trim = origin_y_mm + bleed_mm
            for column in range(columns):
                trim_left = origin_x_mm + column * cell_width_mm + bleed_mm
                trim_right = trim_left + card_width_mm
                for x_position in (trim_left, trim_right):
                    pdf.line(x_position * mm, 0, x_position * mm, bottom_trim * mm)
                    pdf.line(x_position * mm, top_trim * mm, x_position * mm, page_height_mm * mm)
            left_trim = origin_x_mm + bleed_mm
            right_trim = page_width_mm - origin_x_mm - bleed_mm
            for row_from_bottom in range(rows):
                trim_bottom = origin_y_mm + row_from_bottom * cell_height_mm + bleed_mm
                trim_top = trim_bottom + card_height_mm
                for y_position in (trim_bottom, trim_top):
                    pdf.line(0, y_position * mm, left_trim * mm, y_position * mm)
                    pdf.line(right_trim * mm, y_position * mm, page_width_mm * mm, y_position * mm)
            pdf.restoreState()
        pdf.showPage()
        if progress_callback:
            progress_callback(
                70 + (28 * (page_index + 1) / max(1, pages)),
                f"Building PDF pages: {page_index + 1}/{pages}",
            )
    pdf.save()
    if progress_callback:
        progress_callback(99, "Finalizing PDF download...")

    filename = core.safe_filename(str(payload.get("filename") or "cards")) or "cards"
    if not filename.casefold().endswith(".pdf"):
        filename += ".pdf"
    return buffer.getvalue(), filename, len(images), pages


def start_pdf_operation(payload: dict) -> str:
    operation_id = create_operation("pdf", "Starting PDF preparation...")

    def worker() -> None:
        try:
            pdf_data, filename, card_count, page_count = create_print_pdf(
                payload,
                progress_callback=lambda percent, status: update_operation(
                    operation_id, percent, status
                ),
            )
            update_operation(
                operation_id,
                100,
                f"PDF ready: {card_count} card(s) on {page_count} page(s).",
                state="done",
                filename=filename,
                card_count=card_count,
                page_count=page_count,
                pdf_data=pdf_data,
            )
        except Exception as exc:
            update_operation(operation_id, 100, str(exc), state="error", error=str(exc))

    threading.Thread(target=worker, daemon=True).start()
    return operation_id


def export_selected(payload: dict) -> dict:
    job_id = str(payload.get("job_id", ""))
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        raise ValueError("Fetch arts before exporting.")

    export_parent = Path(str(payload.get("export_parent") or core.DEFAULT_EXPORT_PARENT)).expanduser()
    export_parent.mkdir(parents=True, exist_ok=True)
    base_name = core.safe_folder_name(str(payload.get("export_name") or "selected-print-arts"))
    output_folder = export_parent / base_name
    counter = 2
    while output_folder.exists():
        output_folder = export_parent / f"{base_name}-{counter}"
        counter += 1
    output_folder.mkdir(parents=True)

    image_paths = dict(job.get("image_paths") or {})
    copied = 0
    skipped = 0
    lines = []
    for slot_index, slot in enumerate(payload.get("slots") or [], start=1):
        options = slot.get("options", [])
        current_index = int(slot.get("current_index") or 0)
        current_option = options[current_index] if 0 <= current_index < len(options) else None
        if not current_option:
            skipped += 1
            lines.append(f"{slot.get('quantity', 1)}x {slot.get('name', '')}\n  - no selected art")
            continue
        quantity = max(1, int(slot.get("quantity") or 1))
        lines.append(f"{quantity}x {slot.get('name', '')}")
        for copy_number in range(1, quantity + 1):
            source = Path(str(image_paths.get(str(current_option.get("preference_id") or ""), ""))).expanduser()
            if not source.exists():
                continue
            base = core.safe_filename(
                f"{slot_index:03d} {slot.get('name', '')} {current_option.get('set_code', '').upper()} {current_option.get('collector_number', '')}"
            )
            if quantity > 1:
                base = f"{base} copy {copy_number}"
            destination = unique_destination(output_folder, f"{base}.png")
            shutil.copy2(source, destination)
            copied += 1
        lines.append(f"  - {current_option.get('display_name', '')} | {current_option.get('label', '')}")

    (output_folder / "_selected_arts.txt").write_text(
        f"Copied {copied} deck image file(s).\nCards with no selected art: {skipped}\n\n" + "\n".join(lines),
        encoding="utf-8",
    )
    return {"copied": copied, "skipped": skipped, "output_folder": str(output_folder)}


def unique_destination(folder: Path, filename: str) -> Path:
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


def preference_profile_state(settings: dict[str, str] | None = None) -> dict:
    settings = settings or core.load_settings()
    profiles = core.load_preference_profiles()
    active = core.resolve_preference_profile_name(
        settings.get(core.PREFERENCE_PROFILE_SETTING),
        profiles,
    )
    return {
        "preference_profiles": list(profiles),
        "preference_profile": active,
        "use_saved_preferences": core.coerce_setting_bool(
            settings.get(core.USE_SAVED_PREFERENCES_SETTING),
            core.DEFAULT_USE_SAVED_PREFERENCES,
        ),
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self.send_text(HTML, "text/html; charset=utf-8")
        elif parsed.path == "/app.css":
            self.send_text(CSS, "text/css; charset=utf-8")
        elif parsed.path == "/app.js":
            self.send_text(JS, "application/javascript; charset=utf-8")
        elif parsed.path == "/api/settings":
            settings = core.load_settings()
            self.send_json(
                {
                    "ok": True,
                    "settings": {
                        "cache_dir": settings.get("cache_dir", str(core.DEFAULT_CACHE_DIR)),
                        "export_parent": settings.get("export_parent", str(core.DEFAULT_EXPORT_PARENT)),
                        "use_saved_preferences": core.coerce_setting_bool(
                            settings.get(core.USE_SAVED_PREFERENCES_SETTING),
                            core.DEFAULT_USE_SAVED_PREFERENCES,
                        ),
                        "preference_profiles": core.preference_profile_names(),
                        "preference_profile": core.resolve_preference_profile_name(
                            settings.get(core.PREFERENCE_PROFILE_SETTING),
                        ),
                        "update_scryfall_images": core.coerce_setting_bool(
                            settings.get(core.UPDATE_SCRYFALL_IMAGES_SETTING),
                            core.DEFAULT_UPDATE_SCRYFALL_IMAGES,
                        ),
                        "art_preference_categories": core.normalize_art_preference_categories(
                            settings.get(core.ART_PREFERENCE_CATEGORIES_SETTING),
                            settings,
                        ),
                        "ignore_basics": core.coerce_setting_bool(
                            settings.get(core.IGNORE_BASICS_SETTING),
                            core.DEFAULT_IGNORE_BASICS,
                        ),
                        "export_name": "",
                    },
                }
            )
        elif parsed.path == "/api/job":
            query = urllib.parse.parse_qs(parsed.query)
            job_id = query.get("id", [""])[0]
            with JOBS_LOCK:
                job = dict(JOBS.get(job_id) or {})
            if not job:
                self.send_json({"ok": False, "error": "Unknown job."}, HTTPStatus.NOT_FOUND)
            else:
                self.send_json({"ok": True, **job})
        elif parsed.path == "/api/operation":
            query = urllib.parse.parse_qs(parsed.query)
            operation_id = query.get("id", [""])[0]
            operation = operation_for_client(operation_id)
            if not operation:
                self.send_json({"ok": False, "error": "Unknown operation."}, HTTPStatus.NOT_FOUND)
            else:
                self.send_json({"ok": True, **operation})
        elif parsed.path == "/api/pdf-download":
            query = urllib.parse.parse_qs(parsed.query)
            operation_id = query.get("id", [""])[0]
            with OPERATIONS_LOCK:
                operation = OPERATIONS.get(operation_id)
                pdf_data = operation.get("pdf_data") if operation else None
                filename = str(operation.get("filename") or "cards.pdf") if operation else "cards.pdf"
            if not pdf_data:
                self.send_json({"ok": False, "error": "The PDF is not ready."}, HTTPStatus.NOT_FOUND)
            else:
                self.send_bytes(
                    pdf_data,
                    "application/pdf",
                    {"Content-Disposition": f'attachment; filename="{filename}"'},
                )
                with OPERATIONS_LOCK:
                    if operation_id in OPERATIONS:
                        OPERATIONS[operation_id]["pdf_data"] = None
        elif parsed.path == "/api/image":
            self.send_image(parsed)
        elif parsed.path == "/api/print-image":
            self.send_print_image(parsed)
        else:
            self.send_json({"ok": False, "error": "Not found."}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        try:
            payload = self.read_json()
            if self.path == "/api/fetch":
                job_id = start_fetch_job(payload)
                self.send_json({"ok": True, "job_id": job_id})
            elif self.path == "/api/prepare-selected":
                operation_id = start_prepare_operation(payload)
                self.send_json({"ok": True, "operation_id": operation_id})
            elif self.path == "/api/start-pdf":
                operation_id = start_pdf_operation(payload)
                self.send_json({"ok": True, "operation_id": operation_id})
            elif self.path == "/api/export":
                self.send_json({"ok": True, **export_selected(payload)})
            elif self.path == "/api/print-pdf":
                pdf_data, filename, card_count, page_count = create_print_pdf(payload)
                self.send_bytes(
                    pdf_data,
                    "application/pdf",
                    {"Content-Disposition": f'attachment; filename="{filename}"', "X-Card-Count": str(card_count), "X-Page-Count": str(page_count)},
                )
            elif self.path == "/api/preferences":
                profile = core.resolve_preference_profile_name(payload.get("profile"))
                preferences = core.load_preferences(profile)
                saved = 0
                for choice in payload.get("choices") or []:
                    name = str(choice.get("name") or "")
                    preference_id = str(choice.get("preference_id") or "")
                    if name and preference_id:
                        preferences[core.normalize_name(name)] = preference_id
                        saved += 1
                core.save_preferences(preferences, profile)
                settings = core.load_settings()
                settings[core.PREFERENCE_PROFILE_SETTING] = profile
                core.save_settings(settings)
                self.send_json({"ok": True, "saved": saved, "profile": profile})
            elif self.path == "/api/preference-profiles":
                settings = core.load_settings()
                active = core.resolve_preference_profile_name(
                    payload.get("profile") or settings.get(core.PREFERENCE_PROFILE_SETTING)
                )
                action = str(payload.get("action") or "configure").casefold()
                if action == "create":
                    active = core.create_preference_profile(payload.get("name"))
                elif action == "rename":
                    active = core.rename_preference_profile(active, payload.get("name"))
                elif action == "delete":
                    active = core.delete_preference_profile(active)
                elif action not in {"select", "configure"}:
                    raise ValueError("Unknown preference-profile action.")
                enabled = core.coerce_setting_bool(
                    payload.get("enabled"),
                    core.coerce_setting_bool(
                        settings.get(core.USE_SAVED_PREFERENCES_SETTING),
                        core.DEFAULT_USE_SAVED_PREFERENCES,
                    ),
                )
                settings[core.PREFERENCE_PROFILE_SETTING] = active
                settings[core.USE_SAVED_PREFERENCES_SETTING] = "true" if enabled else "false"
                core.save_settings(settings)
                self.send_json({"ok": True, **preference_profile_state(settings)})
            elif self.path == "/api/category-preferences":
                settings = core.load_settings()
                categories = core.normalize_art_preference_categories(
                    payload.get("art_preference_categories"),
                    settings,
                )
                settings[core.ART_PREFERENCE_CATEGORIES_SETTING] = json.dumps(categories)
                core.save_settings(settings)
                self.send_json({"ok": True, "art_preference_categories": categories})
            else:
                self.send_json({"ok": False, "error": "Not found."}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def send_image(self, parsed) -> None:
        query = urllib.parse.parse_qs(parsed.query)
        job_id = query.get("job", [""])[0]
        image_id = query.get("id", [""])[0]
        with JOBS_LOCK:
            job = JOBS.get(job_id)
        if not job or not image_id:
            self.send_error(404)
            return
        path = Path(str((job.get("image_paths") or {}).get(image_id, ""))).expanduser()
        if not path.exists():
            self.send_error(404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "private, max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    def send_print_image(self, parsed) -> None:
        query = urllib.parse.parse_qs(parsed.query)
        job_id = query.get("job", [""])[0]
        image_id = query.get("id", [""])[0]
        with JOBS_LOCK:
            job = JOBS.get(job_id)
        if not job or not image_id:
            self.send_error(404)
            return
        source = Path(str((job.get("print_image_paths") or job.get("image_paths") or {}).get(image_id, ""))).expanduser()
        if not source.exists():
            self.send_error(404)
            return
        values = {
            "card_width_mm": query.get("card_width_mm", [63.0])[0],
            "card_height_mm": query.get("card_height_mm", [88.0])[0],
            "bleed_mm": query.get("bleed_mm", [1.5])[0],
        }
        try:
            card_width_mm = _bounded_float(values, "card_width_mm", 63.0, 20.0, 200.0)
            card_height_mm = _bounded_float(values, "card_height_mm", 88.0, 20.0, 200.0)
            bleed_mm = _bounded_float(values, "bleed_mm", 1.5, 0.0, 10.0)
            data = build_print_image(
                str(source), source.stat().st_mtime_ns, card_width_mm, card_height_mm, bleed_mm
            )
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        self.send_bytes(data, "image/png", {"Cache-Control": "private, max-age=86400"})

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        return json.loads(raw)

    def send_text(self, text: str, content_type: str) -> None:
        data = text.encode("utf-8")
        self.send_bytes(data, content_type, {"Cache-Control": "no-store"})

    def send_bytes(self, data: bytes, content_type: str, headers: dict[str, str] | None = None) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, data: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        raw = json.dumps(data).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format: str, *args) -> None:
        return


def main() -> int:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}/"
    if "--no-browser" not in sys.argv:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    mode_name = "Select First" if SELECT_FIRST_MODE else "Classic"
    print(f"MTG Scryfall Art Picker Web ({mode_name}) is running at {url}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


