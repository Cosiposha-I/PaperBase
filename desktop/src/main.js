"use strict";

// Защита от EPIPE: если труба stdout/stderr этого процесса закрыта (например, окно
// консоли/родительский процесс, перенаправлявший вывод, уже завершился), запись в
// неё в Node по умолчанию бросает необработанное исключение — оно валит всё
// Electron-приложение с окном "A JavaScript error occurred in the main process".
// installer.js и server.js пишут прогресс установки в process.stdout/stderr —
// без этой защиты пользователь мог бы увидеть краш вместо обычного лога. Поймано
// вживую при обрыве отладочной сессии, писавшей вывод в трубу.
process.stdout.on("error", () => {});
process.stderr.on("error", () => {});

const { app, BrowserWindow, ipcMain, dialog, Menu } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");
const {
  isSetupDone, installRoot, setInstallRoot, defaultInstallRoot,
  readCorpora, writeCorpora, themeDataDir, isThemeIndexed,
  runtimePython, pyappSourceDir, buildInfo,
} = require("./paths");
const { runFirstTimeSetup } = require("./installer");
const { startServer } = require("./server");

const APP_ID = "com.paperbase.app";
if (process.platform === "win32") app.setAppUserModelId(APP_ID); // для уведомлений Windows

let mainWindow = null;
let pythonChild = null;
let setupEnv = null;      // env, собранный установщиком (PAPERBASE_CONFIG и т.п.)
let currentTheme = null;  // активная тема (см. readCorpora())

function loadSetupEnv() {
  const root = installRoot();
  const configPath = path.join(root, "config.toml");
  return {
    ...process.env,
    PAPERBASE_CONFIG: configPath,
    PAPERBASE_DATA_DIR: path.join(root, "data"),
  };
}

/** env для конкретной темы: тот же общий config.toml, но свои corpus_dir/data_dir. */
function themeEnv(theme) {
  const base = setupEnv || loadSetupEnv();
  return { ...base, PAPERBASE_CORPUS_DIR: theme.corpus, PAPERBASE_DATA_DIR: themeDataDir(theme) };
}

/** Небольшое окно-заглушка на время индексации новой/непроиндексированной темы. */
function showLoadingWindow(text) {
  const win = new BrowserWindow({
    width: 420, height: 200, resizable: false, title: "PaperBase",
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });
  win.setMenuBarVisibility(false);
  const html = `<!doctype html><html><head><meta charset="utf-8"><style>
    body{margin:0;height:100vh;display:flex;flex-direction:column;justify-content:center;
      align-items:center;background:#0f1216;color:#e6e9ee;
      font:15px -apple-system,"Segoe UI",Roboto,Arial,sans-serif;text-align:center;gap:14px}
    .spin{width:34px;height:34px;border:3px solid #2a323c;border-top-color:#5b9bd5;
      border-radius:50%;animation:sp 0.8s linear infinite}
    @keyframes sp{to{transform:rotate(360deg)}}
    p{max-width:340px;color:#9aa4b2}
  </style></head><body><div class="spin"></div><p>${text}</p></body></html>`;
  win.loadURL("data:text/html;charset=utf-8," + encodeURIComponent(html));
  return win;
}

/** Индексация темы (если ещё не построена) — эквивалент menu._launch() на Python. */
function runIngest(theme) {
  return new Promise((resolve, reject) => {
    const child = spawn(runtimePython(), ["-m", "paperbase", "ingest"], {
      cwd: pyappSourceDir(),
      env: themeEnv(theme),
    });
    let lastErr = "";
    child.stdout.on("data", (d) => process.stdout.write(`[ingest] ${d}`));
    child.stderr.on("data", (d) => { lastErr = d.toString().slice(-2000); process.stderr.write(`[ingest] ${d}`); });
    child.on("error", (e) => reject(e));
    child.on("close", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`Индексация завершилась с ошибкой (код ${code}). ${lastErr.slice(-300)}`));
    });
  });
}

/** Переключиться на тему: остановить текущий сервер, при необходимости
 * проиндексировать, поднять сервер для новой темы, показать/обновить окно. */
async function switchToTheme(theme) {
  if (pythonChild) {
    try { pythonChild.kill(); } catch { /* уже завершён */ }
    pythonChild = null;
  }

  let loading = null;
  if (!isThemeIndexed(theme)) {
    loading = showLoadingWindow(`Индексирую «${theme.name}» — это разово, может занять несколько минут…`);
    try {
      await runIngest(theme);
    } catch (e) {
      loading.close();
      dialog.showErrorBox("Не удалось построить базу", e.message);
      return;
    }
  }
  if (!loading) loading = showLoadingWindow(`Открываю «${theme.name}»…`);

  let port;
  try {
    const started = await startServer(themeEnv(theme));
    port = started.port;
    pythonChild = started.child;
  } catch (e) {
    loading.close();
    dialog.showErrorBox("PaperBase не запустился", "Не удалось поднять локальный сервер поиска.\n\n" + e.message);
    return;
  }

  currentTheme = theme;
  if (mainWindow) {
    mainWindow.loadURL(`http://127.0.0.1:${port}/`);
    mainWindow.focus();
  } else {
    mainWindow = new BrowserWindow({
      width: 1280, height: 860, title: "PaperBase",
      webPreferences: { contextIsolation: true, nodeIntegration: false },
    });
    mainWindow.loadURL(`http://127.0.0.1:${port}/`);
    mainWindow.on("closed", () => { mainWindow = null; });
  }
  loading.close();
  buildAppMenu();
}

async function promptNewTheme() {
  const res = await dialog.showOpenDialog({
    title: "Выберите папку со статьями для новой темы",
    properties: ["openDirectory"],
  });
  if (res.canceled || !res.filePaths.length) return;
  const corpus = res.filePaths[0];
  const name = path.basename(corpus);
  const slug = name.replace(/[^a-zA-Zа-яА-Я0-9]+/g, "_").replace(/^_+|_+$/g, "") || "corpus";
  const themes = readCorpora();
  const dataDir = themes.some((t) => t.data === "data") ? `data-${slug}` : "data";
  const theme = { name, corpus: corpus.replace(/\\/g, "/"), data: dataDir, port: 0 };
  writeCorpora([...themes, theme]);
  await switchToTheme(theme);
}

/** Какая именно сборка запущена: по этой строке видно, свежий ли код внутри. */
function showAbout() {
  const b = buildInfo();
  dialog.showMessageBox(mainWindow || undefined, {
    type: "info",
    title: "О программе",
    message: `PaperBase ${b.version}`,
    detail: `Сборка ${b.commit}${b.dirty ? " (с незакоммиченными правками)" : ""}` +
      (b.built ? `\nСобрана: ${b.built}` : "") +
      `\nДанные и окружение: ${installRoot()}`,
    buttons: ["OK"],
  });
}

function buildAppMenu() {
  const themes = readCorpora();
  const template = [
    {
      label: "PaperBase",
      submenu: [
        { label: "О программе…", click: () => showAbout() },
        { type: "separator" },
        { role: "quit", label: "Выход" },
      ],
    },
    {
      label: "Темы",
      submenu: [
        ...themes.map((t) => ({
          label: t.name + (currentTheme && currentTheme.name === t.name ? "  ✓" : ""),
          click: () => switchToTheme(t),
        })),
        { type: "separator" },
        { label: "Добавить тему…", click: () => promptNewTheme() },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

async function launchAppWindow() {
  const themes = readCorpora();
  if (themes.length) {
    await switchToTheme(themes[0]);
    return;
  }
  // Резервный путь (не должен происходить в норме — setup:apply-corpus всегда
  // создаёт первую тему): поднимаем сервер на общем config.toml без явной темы.
  const env = setupEnv || loadSetupEnv();
  let port;
  try {
    const started = await startServer(env);
    port = started.port;
    pythonChild = started.child;
  } catch (e) {
    dialog.showErrorBox("PaperBase не запустился",
      "Не удалось поднять локальный сервер поиска.\n\n" + e.message);
    app.quit();
    return;
  }
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    title: "PaperBase",
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });
  mainWindow.loadURL(`http://127.0.0.1:${port}/`);
  mainWindow.on("closed", () => { mainWindow = null; });
  buildAppMenu();
}

function createSetupWindow() {
  const win = new BrowserWindow({
    width: 640,
    height: 520,
    title: "Установка PaperBase",
    resizable: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(__dirname, "preload.js"),
    },
  });
  win.setMenuBarVisibility(false);
  win.loadFile(path.join(__dirname, "setup.html"));
  return win;
}

// --- IPC: экран первого запуска ---

ipcMain.handle("setup:get-install-location", async () => {
  return { path: installRoot(), isDefault: installRoot() === defaultInstallRoot() };
});

ipcMain.handle("setup:choose-install-location", async () => {
  const res = await dialog.showOpenDialog({
    title: "Куда установить PaperBase (потребуется 4–7 ГБ места)",
    properties: ["openDirectory", "createDirectory"],
  });
  if (res.canceled || !res.filePaths.length) return { canceled: true };
  const chosen = path.join(res.filePaths[0], "PaperBase");
  setInstallRoot(chosen);
  return { canceled: false, path: chosen };
});

ipcMain.handle("setup:start", async (event) => {
  const send = (stage, percent, message) =>
    event.sender.send("setup:progress", { stage, percent, message });
  try {
    const result = await runFirstTimeSetup(send);
    setupEnv = result.env;
    return { ok: true };
  } catch (e) {
    return { ok: false, error: e.message };
  }
});

ipcMain.handle("setup:choose-folder", async () => {
  const res = await dialog.showOpenDialog({
    title: "Выберите папку со статьями (PDF)",
    properties: ["openDirectory"],
  });
  if (res.canceled || !res.filePaths.length) return { canceled: true };
  return { canceled: false, path: res.filePaths[0] };
});

ipcMain.handle("setup:apply-corpus", async (_event, corpusPath) => {
  const root = installRoot();
  const configPath = path.join(root, "config.toml");
  let toml = fs.readFileSync(configPath, "utf8");
  const escaped = corpusPath.replace(/\\/g, "/");
  toml = toml.replace(/corpus_dir = ""/, `corpus_dir = "${escaped}"`);
  fs.writeFileSync(configPath, toml, "utf8");

  // Первая тема — сразу в реестр (используется переключателем тем, см. buildAppMenu()).
  const themes = readCorpora();
  if (!themes.length) {
    writeCorpora([{ name: path.basename(corpusPath) || "Моя тема", corpus: escaped, data: "data", port: 0 }]);
  }
  return { ok: true };
});

ipcMain.handle("setup:finish", async () => {
  const win = BrowserWindow.getFocusedWindow();
  await launchAppWindow();
  if (win) win.close();
  return { ok: true };
});

// --- Жизненный цикл приложения ---

app.whenReady().then(async () => {
  if (isSetupDone()) {
    await launchAppWindow();
  } else {
    createSetupWindow();
  }
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  if (pythonChild) {
    try { pythonChild.kill(); } catch { /* уже завершён */ }
  }
});
