// Скачивает портативный Python (python-build-standalone) для текущей платформы
// в desktop/resources/pyapp/ — его встраивает electron-builder (extraResources).
// Запускать перед сборкой: `npm run fetch-python` (или автоматически в build:win/build:mac).
//
// Использует системные curl и tar — они предустановлены и на windows-latest,
// и на macos-latest раннерах GitHub Actions, поэтому лишних npm-зависимостей не нужно.
"use strict";

const { execFileSync } = require("child_process");
const fs = require("fs");
const path = require("path");
const os = require("os");

// Зафиксированный релиз (не "latest") — чтобы сборки были воспроизводимы и не ломались
// от смены пути релиза апстримом. Проверено вручную 2026-07-20: содержит
// python/python.exe (Windows) и python/bin/python3 (macOS) — см. desktop/README.md.
const RELEASE_TAG = "20260718";
const PY_VERSION = "3.12.13";

const ASSETS = {
  "win32-x64": `cpython-${PY_VERSION}+${RELEASE_TAG}-x86_64-pc-windows-msvc-install_only.tar.gz`,
  "darwin-arm64": `cpython-${PY_VERSION}+${RELEASE_TAG}-aarch64-apple-darwin-install_only.tar.gz`,
  "darwin-x64": `cpython-${PY_VERSION}+${RELEASE_TAG}-x86_64-apple-darwin-install_only.tar.gz`,
};

function main() {
  const key = `${process.platform}-${process.arch}`;
  const asset = ASSETS[key];
  if (!asset) {
    console.error(`Нет портативного Python для платформы "${key}". `
      + `Поддерживаются: ${Object.keys(ASSETS).join(", ")}`);
    process.exit(1);
  }

  const url = `https://github.com/astral-sh/python-build-standalone/releases/download/${RELEASE_TAG}/${asset}`;
  const destDir = path.join(__dirname, "..", "resources", "pyapp");
  const tmpFile = path.join(os.tmpdir(), asset);

  if (fs.existsSync(destDir)) {
    console.log(`Уже скачано: ${destDir} (удалите папку, чтобы скачать заново)`);
    return;
  }
  fs.mkdirSync(destDir, { recursive: true });

  console.log(`Скачиваю портативный Python (${key}): ${asset}`);
  execFileSync("curl", ["-sL", "--fail", "-o", tmpFile, url], { stdio: "inherit" });

  console.log("Распаковываю…");
  // --strip-components=1: убираем общий верхний каталог "python/" из архива —
  // тогда бинарник лежит прямо в pyapp/ (pyapp/python.exe или pyapp/bin/python3),
  // как этого ожидает paths.js.
  execFileSync("tar", ["-xzf", tmpFile, "--strip-components=1", "-C", destDir], { stdio: "inherit" });

  fs.rmSync(tmpFile, { force: true });
  console.log(`Готово: ${destDir}`);
}

main();
