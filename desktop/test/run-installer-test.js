// Тестовый харнесс: вызывает реальную логику installer.js напрямую внутри Electron
// (нужен app.getPath), минуя клики по UI — так проверяется весь пайплайн первого
// запуска end-to-end. Не часть приложения, не попадает в сборку.
"use strict";

// См. комментарий в src/main.js — защита от EPIPE при обрыве родительской сессии,
// пишущей наш вывод (актуально именно для этого тестового харнесса).
process.stdout.on("error", () => {});
process.stderr.on("error", () => {});

const { app } = require("electron");
const path = require("path");

// ВАЖНО: require("../src/paths") (через installer/server) должен произойти ДО
// app.whenReady() — именно там вызывается app.setName("PaperBase"), а Electron
// фиксирует путь userData по имени приложения на момент готовности, не позже.
const { runFirstTimeSetup } = require("../src/installer");
const { startServer } = require("../src/server");

app.whenReady().then(async () => {
  console.log("userData:", app.getPath("userData"));
  console.log("--- запускаю runFirstTimeSetup ---");

  const onProgress = (stage, percent, message) => {
    console.log(`[${stage}] ${percent ?? "?"}%  ${message}`);
  };

  try {
    const { env } = await runFirstTimeSetup(onProgress);
    console.log("--- установка завершена, запускаю сервер ---");
    const { port } = await startServer(env);
    console.log("SERVER_OK port=" + port);

    const http = require("http");
    http.get(`http://127.0.0.1:${port}/api/stats`, (res) => {
      let body = "";
      res.on("data", (d) => (body += d));
      res.on("end", () => {
        console.log("API_RESPONSE:", body);
        process.exit(0);
      });
    });
  } catch (e) {
    console.error("INSTALL_FAILED:", e.message);
    process.exit(1);
  }
});
