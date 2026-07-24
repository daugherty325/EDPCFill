const { app, BrowserWindow, dialog, session } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const http = require("http");
const net = require("net");
const path = require("path");

let mainWindow = null;
let backendProcess = null;
let backendPort = null;
let ownsBackend = false;

function requestOk(port) {
  return new Promise((resolve) => {
    const request = http.get({ hostname: "127.0.0.1", port, path: "/", timeout: 700 }, (response) => {
      response.resume();
      resolve(response.statusCode >= 200 && response.statusCode < 500);
    });
    request.on("error", () => resolve(false));
    request.on("timeout", () => {
      request.destroy();
      resolve(false);
    });
  });
}

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(() => resolve(address.port));
    });
  });
}

function backendCommand() {
  if (app.isPackaged) {
    return {
      executable: path.join(process.resourcesPath, "backend", "mtg-art-picker-backend.exe"),
      args: ["--no-browser", "--select-first"],
      resourceDir: process.resourcesPath,
    };
  }

  const root = path.resolve(__dirname, "..");
  return {
    executable: process.env.MTG_ART_PICKER_PYTHON || "python",
    args: [path.join(root, "outputs", "mtg_art_picker_web.py"), "--no-browser", "--select-first"],
    resourceDir: path.join(root, "outputs"),
  };
}

async function waitForBackend(port, child) {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`The local backend exited with code ${child.exitCode}.`);
    if (await requestOk(port)) return;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error("The local backend did not become ready within 30 seconds.");
}

async function startBackend() {
  // Always start the select-first backend so a separately running classic
  // browser version on port 8765 cannot silently change this app's behavior.
  backendPort = await freePort();
  const command = backendCommand();
  const dataDir = path.join(app.getPath("userData"), "data");
  fs.mkdirSync(dataDir, { recursive: true });
  seedInitialData(dataDir);
  backendProcess = spawn(command.executable, command.args, {
    cwd: app.isPackaged ? path.dirname(command.executable) : path.resolve(__dirname, ".."),
    env: {
      ...process.env,
      MTG_ART_PICKER_HOST: "127.0.0.1",
      MTG_ART_PICKER_PORT: String(backendPort),
      MTG_ART_PICKER_DATA_DIR: dataDir,
      MTG_ART_PICKER_RESOURCE_DIR: command.resourceDir,
    },
    windowsHide: true,
    stdio: "ignore",
  });
  ownsBackend = true;
  await waitForBackend(backendPort, backendProcess);
}

function seedInitialData(dataDir) {
  if (!app.isPackaged) return;
  const initialDir = path.join(process.resourcesPath, "initial-data");
  const preferencesSource = path.join(initialDir, "mtg_art_picker_preferences.json");
  const preferencesTarget = path.join(dataDir, "mtg_art_picker_preferences.json");
  if (!fs.existsSync(preferencesTarget) && fs.existsSync(preferencesSource)) {
    fs.copyFileSync(preferencesSource, preferencesTarget);
  }

  const settingsSource = path.join(initialDir, "mtg_art_picker_settings.json");
  const settingsTarget = path.join(dataDir, "mtg_art_picker_settings.json");
  if (fs.existsSync(settingsTarget) || !fs.existsSync(settingsSource)) return;
  try {
    const settings = JSON.parse(fs.readFileSync(settingsSource, "utf8"));
    // Only seed machine-specific paths when the cache still exists. Installers
    // copied to a different computer therefore fall back to a fresh user cache.
    if (settings.cache_dir && fs.existsSync(settings.cache_dir)) {
      fs.copyFileSync(settingsSource, settingsTarget);
    }
  } catch (_error) {
    // A malformed optional seed must never prevent the application from opening.
  }
}

function stopOwnedBackend() {
  if (!ownsBackend || !backendProcess || backendProcess.killed) return;
  backendProcess.kill();
  backendProcess = null;
}

function configureDownloads() {
  session.defaultSession.on("will-download", (_event, item) => {
    const chosen = dialog.showSaveDialogSync(mainWindow, {
      title: "Save printable PDF",
      defaultPath: item.getFilename() || "printable-cards.pdf",
      filters: [{ name: "PDF documents", extensions: ["pdf"] }],
    });
    if (chosen) item.setSavePath(chosen);
    else item.cancel();
  });
}

async function createWindow() {
  await startBackend();
  configureDownloads();
  mainWindow = new BrowserWindow({
    width: 1500,
    height: 980,
    minWidth: 980,
    minHeight: 700,
    backgroundColor: "#081018",
    title: "MTG Art Picker — Select First",
    show: false,
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  mainWindow.removeMenu();
  mainWindow.once("ready-to-show", () => mainWindow.show());
  await mainWindow.loadURL(`http://127.0.0.1:${backendPort}/`);
}

app.whenReady().then(createWindow).catch((error) => {
  dialog.showErrorBox("MTG Art Picker could not start", error.stack || String(error));
  stopOwnedBackend();
  app.quit();
});
app.on("window-all-closed", () => app.quit());
app.on("before-quit", stopOwnedBackend);
