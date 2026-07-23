// Пути окружения PaperBase внутри Electron-приложения.
//
// В собранном приложении сюда встраивается портативный Python (Этап 6, ещё не сделан
// в этой итерации — см. README в этой папке). Пока для разработки и проверки всей
// схемы используется системный Python, если портативный не найден.
"use strict";

const { app } = require("electron");
const path = require("path");
const fs = require("fs");

// Явно, а не полагаясь на package.json: при запуске файла напрямую (`electron file.js`,
// как в тестовом харнессе) Electron иначе называет приложение "Electron", и все пути
// данных уезжают в %APPDATA%\Electron вместо %APPDATA%\PaperBase. Ставим здесь, а не
// только в main.js, чтобы сработало для любого входа, использующего этот модуль.
app.setName("PaperBase");

function resourcesRoot() {
  // В dev app.isPackaged === false, ресурсы лежат прямо в desktop/resources
  return app.isPackaged
    ? process.resourcesPath
    : path.join(__dirname, "..", "resources");
}

function portablePythonPath() {
  const root = path.join(resourcesRoot(), "pyapp");
  const exe = process.platform === "win32"
    ? path.join(root, "python.exe")
    : path.join(root, "bin", "python3");
  return fs.existsSync(exe) ? exe : null;
}

// Тяжёлые данные (venv + модель, ~4-7 ГБ) можно поставить не в стандартную папку
// профиля, а туда, где больше места (например, на другой диск). Стандартная папка
// профиля (всегда доступна сразу) хранит лишь маленький файл-указатель на реальное
// место — так мы можем узнать выбор пользователя ещё до какой-либо установки.

function defaultInstallRoot() {
  return app.getPath("userData");
}

function installLocationPointerFile() {
  return path.join(defaultInstallRoot(), "install-location.txt");
}

function installRoot() {
  const pointer = installLocationPointerFile();
  if (fs.existsSync(pointer)) {
    const custom = fs.readFileSync(pointer, "utf8").trim();
    if (custom) return custom;
  }
  return defaultInstallRoot();
}

function setInstallRoot(customPath) {
  fs.mkdirSync(defaultInstallRoot(), { recursive: true });
  fs.writeFileSync(installLocationPointerFile(), customPath, "utf8");
}

function runtimeDir() {
  return path.join(installRoot(), "runtime");
}

function runtimePython() {
  const dir = runtimeDir();
  return process.platform === "win32"
    ? path.join(dir, "Scripts", "python.exe")
    : path.join(dir, "bin", "python3");
}

function pyappSourceDir() {
  // Папка с python-пакетом paperbase + requirements.txt (нужна как cwd для запуска).
  // В сборке это extraResources/app (копия при сборке, см. package.json).
  // В разработке — просто существующая папка проекта на уровень выше desktop/.
  if (app.isPackaged) return path.join(resourcesRoot(), "app");
  return path.join(__dirname, "..", "..");
}

function setupDoneMarker() {
  return path.join(installRoot(), "setup-complete.json");
}

function isSetupDone() {
  return fs.existsSync(setupDoneMarker()) && fs.existsSync(runtimePython());
}

// --- Реестр тем (corpora.json) — тот же формат, что читает paperbase.menu на
// стороне Python ({name, corpus, data, port}), только port здесь не используется:
// Electron сам находит свободный порт при каждом запуске (см. server.js).

function corporaPath() {
  return path.join(installRoot(), "corpora.json");
}

function readCorpora() {
  const p = corporaPath();
  if (!fs.existsSync(p)) return [];
  try {
    const data = JSON.parse(fs.readFileSync(p, "utf8"));
    return Array.isArray(data) ? data : [];
  } catch {
    return [];
  }
}

function writeCorpora(themes) {
  fs.writeFileSync(corporaPath(), JSON.stringify(themes, null, 2), "utf8");
}

function themeDataDir(theme) {
  const d = theme.data || "data";
  return path.isAbsolute(d) ? d : path.join(installRoot(), d);
}

function isThemeIndexed(theme) {
  return fs.existsSync(path.join(themeDataDir(theme), "corpus.db"));
}

module.exports = {
  resourcesRoot, portablePythonPath, runtimeDir, runtimePython,
  pyappSourceDir, setupDoneMarker, isSetupDone,
  defaultInstallRoot, installRoot, setInstallRoot,
  corporaPath, readCorpora, writeCorpora, themeDataDir, isThemeIndexed,
};
