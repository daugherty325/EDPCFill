# MTG Art Picker desktop packaging

The Electron shell starts the Python web backend on a private local port and
closes only the backend process it owns. It never starts the bulk cache
migration. If the traditional server is already available on port 8765,
Electron reuses it and leaves it running when the window closes.

Build on Windows:

1. Install Node dependencies with `pnpm install`.
2. Install PyInstaller in the selected build Python environment.
3. Set `MTG_ART_PICKER_BUILD_PYTHON` if `python` is not on PATH.
4. Run `pnpm run build:backend`.
5. Run `pnpm run dist`.

The installer is written to `release`. The multi-gigabyte art cache is not
embedded. Installed settings and new cache files live in the user's app-data
folder. This locally built installer seeds the current settings on first launch
when that cache path exists, so it safely reuses the ongoing cache and its GPU
lock. On another computer it falls back to a fresh user cache.
