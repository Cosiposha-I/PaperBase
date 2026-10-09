// Метка сборки: версия, коммит и дата — кладётся в ресурсы приложения.
//
// Зачем: собранное приложение носит с собой КОПИЮ кода paperbase, сделанную в момент
// сборки. Без метки нельзя понять, свежая это копия или устаревшая — так установщик
// от июля месяцами раздавал старый последовательный OCR, и снаружи этого не было видно.
// Метка показывается в меню «О программе» и сверяется в scripts/verify-package.js.
"use strict";

const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");

function git(args) {
  try {
    return execSync("git " + args, { cwd: path.join(__dirname, ".."), stdio: ["ignore", "pipe", "ignore"] })
      .toString().trim();
  } catch {
    return "";
  }
}

const pkg = require("../package.json");
const commit = (process.env.GITHUB_SHA || git("rev-parse HEAD") || "unknown").slice(0, 7);
// «грязная» сборка — собрана из незакоммиченного кода; в CI такого не бывает
const dirty = !process.env.GITHUB_SHA && git("status --porcelain -- ../paperbase ../requirements.txt src") !== "";

const info = {
  version: pkg.version,
  commit,
  dirty,
  built: new Date().toISOString().slice(0, 16).replace("T", " ") + " UTC",
};

const out = path.join(__dirname, "..", "resources", "build-info.json");
fs.mkdirSync(path.dirname(out), { recursive: true });
fs.writeFileSync(out, JSON.stringify(info, null, 2) + "\n", "utf8");
console.log("build-info:", JSON.stringify(info));
