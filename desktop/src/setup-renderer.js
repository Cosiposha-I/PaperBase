"use strict";

const STAGE_LABELS = {
  venv: "Готовлю окружение…",
  torch: "Устанавливаю вычислительный движок…",
  deps: "Устанавливаю компоненты…",
  model: "Скачиваю модель для поиска (самый долгий шаг)…",
  verify: "Проверяю, что всё работает…",
  done: "Готово!",
};

function show(id) {
  document.querySelectorAll(".screen").forEach((el) => el.classList.remove("active"));
  document.getElementById(id).classList.add("active");
}

function appendLog(text) {
  const log = document.getElementById("log");
  log.textContent += text + "\n";
  log.scrollTop = log.scrollHeight;
}

async function startInstall() {
  show("screen-progress");
  document.getElementById("bar-fill").style.width = "0%";
  document.getElementById("log").textContent = "";

  window.paperbaseSetup.onProgress(({ stage, percent, message }) => {
    if (typeof percent === "number") {
      document.getElementById("bar-fill").style.width = `${percent}%`;
    }
    document.getElementById("stage-text").textContent = STAGE_LABELS[stage] || message;
    if (message) appendLog(message);
  });

  const result = await window.paperbaseSetup.start();
  if (result.ok) {
    show("screen-folder");
  } else {
    document.getElementById("error-text").textContent = result.error
      || "Произошла неизвестная ошибка. Проверьте подключение к интернету и попробуйте снова.";
    show("screen-error");
  }
}

async function refreshInstallLocation() {
  const { path } = await window.paperbaseSetup.getInstallLocation();
  document.getElementById("install-path").textContent = path;
}
refreshInstallLocation();

document.getElementById("btn-choose-location").addEventListener("click", async () => {
  const res = await window.paperbaseSetup.chooseInstallLocation();
  if (!res.canceled) document.getElementById("install-path").textContent = res.path;
});

document.getElementById("btn-start").addEventListener("click", startInstall);
document.getElementById("btn-retry").addEventListener("click", startInstall);

document.getElementById("btn-choose-folder").addEventListener("click", async () => {
  const res = await window.paperbaseSetup.chooseFolder();
  if (res.canceled) return;
  await window.paperbaseSetup.applyCorpus(res.path);
  const pathEl = document.getElementById("folder-path");
  pathEl.textContent = res.path;
  pathEl.style.display = "block";
  document.getElementById("btn-choose-folder").textContent = "Выбрать другую папку";
  document.getElementById("btn-continue").style.display = "inline-block";
});

document.getElementById("btn-continue").addEventListener("click", () => {
  window.paperbaseSetup.finish();
});
