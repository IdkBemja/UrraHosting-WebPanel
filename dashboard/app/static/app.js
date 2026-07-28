const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

function authHeaders(extra) {
  return Object.assign({ "X-CSRFToken": csrfToken }, extra || {});
}

async function apiFetch(path, options) {
  const opts = options || {};
  opts.headers = authHeaders(opts.headers);
  const response = await fetch(path, opts);
  let data = null;
  try {
    data = await response.json();
  } catch (err) {
    data = null;
  }
  return { ok: response.ok, status: response.status, data };
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value === null || value === undefined || value === "" ? "-" : String(value);
}

function formatBytes(bytes) {
  if (bytes === null || bytes === undefined) return "-";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

function formatDuration(totalSeconds) {
  if (totalSeconds === null || Number.isNaN(totalSeconds)) return "--:--:--";
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const hours = String(Math.floor(seconds / 3600)).padStart(2, "0");
  const minutes = String(Math.floor((seconds % 3600) / 60)).padStart(2, "0");
  const secs = String(seconds % 60).padStart(2, "0");
  return `${hours}:${minutes}:${secs}`;
}

/* ---------------------------------------------------------------------- */
/* Tabs                                                                    */
/* ---------------------------------------------------------------------- */

const tabButtons = Array.from(document.querySelectorAll(".tab-btn"));
const tabPanels = new Map(Array.from(document.querySelectorAll(".tab-panel")).map((panel) => [panel.id, panel]));
const tabLoaders = {};
const loadedTabs = new Set();
const ALWAYS_RELOAD_TABS = new Set(["activity", "software", "database", "repository"]);

function activateTab(name, { focus = false } = {}) {
  for (const btn of tabButtons) {
    const isActive = btn.dataset.tab === name;
    btn.setAttribute("aria-selected", String(isActive));
    btn.tabIndex = isActive ? 0 : -1;
    if (isActive && focus) btn.focus();
  }
  for (const [id, panel] of tabPanels) {
    const isActive = id === `tab-${name}`;
    panel.hidden = !isActive;
    panel.classList.toggle("is-hidden", !isActive);
  }
  if (tabLoaders[name] && (!loadedTabs.has(name) || ALWAYS_RELOAD_TABS.has(name))) {
    loadedTabs.add(name);
    tabLoaders[name]();
  }
}

for (const btn of tabButtons) {
  btn.addEventListener("click", () => activateTab(btn.dataset.tab));
}

document.querySelector(".tab-bar")?.addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  const currentIndex = tabButtons.findIndex((b) => b.getAttribute("aria-selected") === "true");
  let nextIndex = currentIndex;
  if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + tabButtons.length) % tabButtons.length;
  if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % tabButtons.length;
  if (event.key === "Home") nextIndex = 0;
  if (event.key === "End") nextIndex = tabButtons.length - 1;
  event.preventDefault();
  activateTab(tabButtons[nextIndex].dataset.tab, { focus: true });
});

/* ---------------------------------------------------------------------- */
/* Confirm dialog                                                          */
/* ---------------------------------------------------------------------- */

const confirmDialog = document.getElementById("confirmDialog");
const confirmDialogTitle = document.getElementById("confirmDialogTitle");
const confirmDialogBody = document.getElementById("confirmDialogBody");
const confirmDialogAccept = document.getElementById("confirmDialogAccept");
const confirmDialogCancel = document.getElementById("confirmDialogCancel");
let confirmResolver = null;
let confirmTrigger = null;

function askConfirm(title, message) {
  confirmDialogTitle.textContent = title;
  confirmDialogBody.textContent = message;
  confirmDialog.classList.remove("is-hidden");
  confirmTrigger = document.activeElement;
  confirmDialogAccept.focus();
  return new Promise((resolve) => {
    confirmResolver = resolve;
  });
}

function closeConfirm(result) {
  confirmDialog.classList.add("is-hidden");
  if (confirmResolver) confirmResolver(result);
  confirmResolver = null;
  if (confirmTrigger && typeof confirmTrigger.focus === "function") confirmTrigger.focus();
}

confirmDialogAccept.addEventListener("click", () => closeConfirm(true));
confirmDialogCancel.addEventListener("click", () => closeConfirm(false));
confirmDialog.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeConfirm(false);
});

/* ---------------------------------------------------------------------- */
/* Overview / status                                                       */
/* ---------------------------------------------------------------------- */

const statusBadge = document.getElementById("statusBadge");
const containerStatus = document.getElementById("containerStatus");
const healthStatus = document.getElementById("healthStatus");
const uptimeValue = document.getElementById("uptimeValue");
const actionFeedback = document.getElementById("actionFeedback");

let uptimeSeconds = null;

function renderUptime() {
  if (uptimeValue) uptimeValue.textContent = formatDuration(uptimeSeconds);
}

setInterval(() => {
  if (uptimeSeconds !== null) {
    uptimeSeconds += 1;
    renderUptime();
  }
}, 1000);

async function fetchStatus() {
  const { ok, data } = await apiFetch("/api/status");
  if (!ok || !data) return;

  if (statusBadge) {
    statusBadge.textContent = data.running ? "En linea" : "Desconectado";
    statusBadge.classList.toggle("online", Boolean(data.running));
    statusBadge.classList.toggle("offline", !data.running);
  }
  setText("containerStatus", data.container_status || "unknown");
  setText("healthStatus", data.health || (data.running ? "sin healthcheck" : "-"));
  uptimeSeconds = data.running ? data.uptime_seconds : null;
  renderUptime();
}

async function loadOverview() {
  const { ok, data } = await apiFetch("/api/overview");
  if (!ok || !data) return;

  if (data.connection) {
    setText("ovConnectAddress", `https://${data.connection.address}`);
  }

  const current = data.deployment && data.deployment.current;
  setText("ovSha", current ? current.sha : "sin desplegar aun");
  setText("ovProfile", current ? current.profile : "-");
  setText("ovSourceKind", current ? current.source_kind : "-");
  setText("ovDeployedAt", current ? current.deployed_at : "-");
  document.getElementById("rollbackWrap")?.classList.toggle("is-hidden", !(data.deployment && data.deployment.previous_available));

  setText("ovCpuLimit", data.resources ? `${data.resources.app_cpu_limit} vCPU` : "-");
  setText("ovMemLimit", data.resources ? data.resources.app_memory_limit : "-");
  if (data.storage) {
    const used = formatBytes(data.storage.usage_bytes);
    const quota = data.storage.quota_bytes ? formatBytes(data.storage.quota_bytes) : "sin limite";
    setText("ovStorage", `${used} / ${quota}`);
  }

  const db = data.database;
  setText("ovDbStatus", db && db.provisioned ? `${db.engine} - ${db.running ? "activa" : "detenida"}` : "Sin base de datos");
}

async function performAction(action) {
  if (actionFeedback) {
    actionFeedback.textContent = `Ejecutando accion: ${action}...`;
    actionFeedback.classList.remove("error", "ok");
  }

  if (action === "stop" || action === "restart") {
    const confirmed = await askConfirm(
      action === "stop" ? "Detener aplicacion" : "Reiniciar aplicacion",
      action === "stop"
        ? "El contenedor de la aplicacion se detendra."
        : "El contenedor de la aplicacion se detendra y volvera a iniciar."
    );
    if (!confirmed) {
      if (actionFeedback) actionFeedback.textContent = "";
      return;
    }
  }

  const { ok, data } = await apiFetch("/api/container", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  });

  if (actionFeedback) {
    actionFeedback.textContent = ok ? "Accion completada" : (data && data.error) || "Error al ejecutar la accion";
    actionFeedback.classList.toggle("ok", ok);
    actionFeedback.classList.toggle("error", !ok);
  }
  await fetchStatus();
  await loadOverview();
}

for (const button of document.querySelectorAll("[data-action]")) {
  button.addEventListener("click", () => performAction(button.dataset.action));
}

document.getElementById("rollbackBtn")?.addEventListener("click", async () => {
  const confirmed = await askConfirm("Revertir despliegue", "Se activara nuevamente la version anterior. La version actual quedara disponible en el historial.");
  if (!confirmed) return;
  const { ok, data } = await apiFetch("/api/deployment/rollback", { method: "POST" });
  window.alert(ok ? "Revertido correctamente." : (data && data.error) || "No se pudo revertir");
  loadOverview();
});

/* ---------------------------------------------------------------------- */
/* Console (logs only - no RCON/shell)                                     */
/* ---------------------------------------------------------------------- */

const logsBox = document.getElementById("logsBox");
const clearLogs = document.getElementById("clearLogs");
const copyLogsBtn = document.getElementById("copyLogs");
const logsSearch = document.getElementById("logsSearch");
const autoscrollToggle = document.getElementById("autoscrollToggle");

function appendLogLine(line) {
  if (!logsBox) return;
  const atBottom = logsBox.scrollHeight - logsBox.scrollTop - logsBox.clientHeight < 30;
  const lineEl = document.createElement("div");
  lineEl.textContent = line;
  lineEl.dataset.logLine = "1";
  logsBox.appendChild(lineEl);
  applySearchFilter();
  if (autoscrollToggle?.checked && atBottom) {
    logsBox.scrollTop = logsBox.scrollHeight;
  }
}

function applySearchFilter() {
  const term = logsSearch?.value.trim().toLowerCase() || "";
  for (const lineEl of logsBox.querySelectorAll("[data-log-line]")) {
    const text = lineEl.textContent;
    const matches = !term || text.toLowerCase().includes(term);
    lineEl.style.display = matches ? "" : "none";
  }
}

logsSearch?.addEventListener("input", applySearchFilter);

copyLogsBtn?.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(logsBox.textContent);
    copyLogsBtn.textContent = "Copiado!";
    setTimeout(() => (copyLogsBtn.textContent = "Copiar"), 1500);
  } catch (err) {
    copyLogsBtn.textContent = "No se pudo copiar";
    setTimeout(() => (copyLogsBtn.textContent = "Copiar"), 1500);
  }
});

function initLogsStream() {
  const source = new EventSource("/api/logs/stream");
  source.onmessage = (event) => appendLogLine(event.data);
  source.onerror = () => {
    appendLogLine("[stream] reconectando logs...");
    source.close();
    setTimeout(initLogsStream, 2500);
  };
}

if (clearLogs && logsBox) {
  clearLogs.addEventListener("click", () => {
    logsBox.textContent = "";
  });
}

/* ---------------------------------------------------------------------- */
/* Files (staging: read/write, source: read-only)                          */
/* ---------------------------------------------------------------------- */

const fileCategory = document.getElementById("fileCategory");
const filePathBreadcrumb = document.getElementById("filePathBreadcrumb");
const fileQuota = document.getElementById("fileQuota");
const fileTableBody = document.getElementById("fileTableBody");
const filesEmptyState = document.getElementById("filesEmptyState");
const dropZone = document.getElementById("dropZone");
const fileInput = document.getElementById("fileInput");
const uploadProgressWrap = document.getElementById("uploadProgressWrap");
const uploadProgress = document.getElementById("uploadProgress");
const uploadProgressLabel = document.getElementById("uploadProgressLabel");

let currentFilePath = "";
let currentCategoryWritable = true;

async function loadFiles() {
  const category = fileCategory.value;
  const params = new URLSearchParams({ path: currentFilePath });
  const { ok, data } = await apiFetch(`/api/files/${category}?${params.toString()}`);
  if (!ok || !data) return;

  currentCategoryWritable = Boolean(data.writable);
  dropZone?.classList.toggle("is-hidden", !currentCategoryWritable);

  filePathBreadcrumb.textContent = `/${category}/${currentFilePath}`;
  fileQuota.textContent = data.quota_bytes != null ? `${formatBytes(data.usage_bytes)} / ${formatBytes(data.quota_bytes)}` : "";

  fileTableBody.innerHTML = "";
  filesEmptyState.classList.toggle("is-hidden", data.entries.length > 0);

  if (currentFilePath) {
    const upRow = document.createElement("tr");
    const upCell = document.createElement("td");
    upCell.colSpan = 4;
    const upLink = document.createElement("button");
    upLink.className = "link-like";
    upLink.type = "button";
    upLink.textContent = ".. (subir un nivel)";
    upLink.addEventListener("click", () => {
      const parts = currentFilePath.split("/").filter(Boolean);
      parts.pop();
      currentFilePath = parts.join("/");
      loadFiles();
    });
    upCell.appendChild(upLink);
    upRow.appendChild(upCell);
    fileTableBody.appendChild(upRow);
  }

  for (const entry of data.entries) {
    const row = document.createElement("tr");

    const nameCell = document.createElement("td");
    if (entry.is_dir) {
      const link = document.createElement("button");
      link.className = "link-like";
      link.type = "button";
      link.textContent = `${entry.name}/`;
      link.addEventListener("click", () => {
        currentFilePath = entry.path;
        loadFiles();
      });
      nameCell.appendChild(link);
    } else {
      nameCell.textContent = entry.name;
    }
    row.appendChild(nameCell);

    const sizeCell = document.createElement("td");
    sizeCell.textContent = entry.is_dir ? "-" : formatBytes(entry.size);
    row.appendChild(sizeCell);

    const modifiedCell = document.createElement("td");
    modifiedCell.textContent = new Date(entry.modified_at * 1000).toLocaleString();
    row.appendChild(modifiedCell);

    const actionsCell = document.createElement("td");
    actionsCell.className = "row-actions";
    if (!entry.is_dir) {
      const downloadLink = document.createElement("a");
      downloadLink.className = "btn ghost";
      downloadLink.href = `/api/files/${category}/download?${new URLSearchParams({ path: entry.path })}`;
      downloadLink.textContent = "Descargar";
      actionsCell.appendChild(downloadLink);
    }
    if (currentCategoryWritable) {
      const deleteBtn = document.createElement("button");
      deleteBtn.className = "btn ghost";
      deleteBtn.type = "button";
      deleteBtn.textContent = "Eliminar";
      deleteBtn.addEventListener("click", () => deleteFile(category, entry.path, entry.name));
      actionsCell.appendChild(deleteBtn);
    }
    row.appendChild(actionsCell);

    fileTableBody.appendChild(row);
  }
}

async function deleteFile(category, path, name) {
  const confirmed = await askConfirm("Eliminar archivo", `Se eliminara "${name}" de forma permanente. ¿Continuar?`);
  if (!confirmed) return;
  const { ok, data } = await apiFetch(`/api/files/${category}/delete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!ok) window.alert((data && data.error) || "No se pudo eliminar el archivo");
  loadFiles();
}

function uploadFile(file) {
  if (!currentCategoryWritable) return;
  const category = fileCategory.value;
  const formData = new FormData();
  formData.append("file", file);
  formData.append("path", currentFilePath);

  const doUpload = (overwrite) => {
    if (overwrite) formData.set("overwrite", "true");
    uploadProgressWrap.classList.remove("is-hidden");
    uploadProgress.value = 0;
    uploadProgressLabel.textContent = `Subiendo ${file.name}...`;

    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/files/${category}/upload`);
    xhr.setRequestHeader("X-CSRFToken", csrfToken);
    xhr.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) uploadProgress.value = Math.round((event.loaded / event.total) * 100);
    });
    xhr.onload = async () => {
      uploadProgressWrap.classList.add("is-hidden");
      let payload = {};
      try {
        payload = JSON.parse(xhr.responseText);
      } catch (err) {
        payload = {};
      }
      if (xhr.status === 400 && /ya existe/i.test(payload.error || "")) {
        const confirmed = await askConfirm("Sobrescribir archivo", `"${file.name}" ya existe. ¿Sobrescribir?`);
        if (confirmed) doUpload(true);
        return;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        loadFiles();
      } else {
        window.alert(payload.error || "No se pudo subir el archivo");
      }
    };
    xhr.onerror = () => {
      uploadProgressWrap.classList.add("is-hidden");
      window.alert("Error de red durante la subida");
    };
    xhr.send(formData);
  };

  doUpload(false);
}

fileCategory?.addEventListener("change", () => {
  currentFilePath = "";
  loadFiles();
});

fileInput?.addEventListener("change", () => {
  if (fileInput.files.length > 0) uploadFile(fileInput.files[0]);
  fileInput.value = "";
});

dropZone?.addEventListener("click", () => fileInput.click());
dropZone?.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    fileInput.click();
  }
});
dropZone?.addEventListener("dragover", (event) => {
  event.preventDefault();
  dropZone.classList.add("is-dragover");
});
dropZone?.addEventListener("dragleave", () => dropZone.classList.remove("is-dragover"));
dropZone?.addEventListener("drop", (event) => {
  event.preventDefault();
  dropZone.classList.remove("is-dragover");
  const file = event.dataTransfer.files[0];
  if (file) uploadFile(file);
});

/* ---------------------------------------------------------------------- */
/* Software / profiles                                                     */
/* ---------------------------------------------------------------------- */

async function loadProfiles() {
  const { ok, data } = await apiFetch("/api/profiles");
  if (!ok || !data) return [];
  return data.profiles;
}

async function loadSoftwareTab() {
  const profiles = await loadProfiles();
  const container = document.getElementById("softwareCards");
  const example = document.getElementById("profileExample");
  if (!container) return;
  container.innerHTML = "";
  for (const profile of profiles) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "software-card";
    card.innerHTML = `<i class="bi bi-box-fill"></i><strong>${profile.label}</strong><small>${profile.description}</small>`;
    card.addEventListener("click", () => {
      for (const c of container.querySelectorAll(".software-card")) c.classList.remove("is-selected");
      card.classList.add("is-selected");
      example.textContent = profile.example_dockerfile;
    });
    container.appendChild(card);
  }
  if (profiles.length) example.textContent = profiles[0].example_dockerfile;
}

async function populateProfileSelects() {
  const profiles = await loadProfiles();
  for (const id of ["repoDeployProfile", "uploadDeployProfile"]) {
    const select = document.getElementById(id);
    if (!select || select.dataset.filled) continue;
    select.dataset.filled = "1";
    for (const profile of profiles) {
      const option = document.createElement("option");
      option.value = profile.id;
      option.textContent = profile.label;
      select.appendChild(option);
    }
  }
}

/* ---------------------------------------------------------------------- */
/* Repository                                                               */
/* ---------------------------------------------------------------------- */

const repoForm = document.getElementById("repoForm");
const repoFeedback = document.getElementById("repoFeedback");

async function loadRepository() {
  await populateProfileSelects();
  const { ok, data } = await apiFetch("/api/repository");
  if (!ok || !data) return;
  document.getElementById("repoUrl").value = data.url || "";
  document.getElementById("repoRef").value = data.ref || "main";
  document.getElementById("repoSshBlock")?.classList.toggle("is-hidden", !data.is_ssh);
  if (data.deploy_public_key) document.getElementById("deployPublicKey").textContent = data.deploy_public_key;
  if (data.known_host_pending_fingerprints?.length) {
    document.getElementById("hostKeyFingerprints").textContent = data.known_host_pending_fingerprints.join("\n");
    document.getElementById("trustHostKeyBtn")?.classList.toggle("is-hidden", data.known_host_confirmed);
  }
  document.getElementById("hostKeyStatus").textContent = data.known_host_confirmed ? "Huella confirmada." : "";
  setText("repoRemoteSha", data.last_checked_sha ? data.last_checked_sha.slice(0, 12) : "-");
}

repoForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const url = document.getElementById("repoUrl").value.trim();
  const ref = document.getElementById("repoRef").value.trim();
  const { ok, data } = await apiFetch("/api/repository", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, ref }),
  });
  repoFeedback.textContent = ok ? "Repositorio configurado." : (data && data.error) || "No se pudo guardar";
  repoFeedback.className = `feedback ${ok ? "ok" : "error"}`;
  if (ok) loadRepository();
});

document.getElementById("genDeployKeyBtn")?.addEventListener("click", async () => {
  const { ok, data } = await apiFetch("/api/repository/deploy-key", { method: "POST" });
  if (ok) document.getElementById("deployPublicKey").textContent = data.public_key;
  else window.alert((data && data.error) || "No se pudo generar la clave");
});

document.getElementById("scanHostKeyBtn")?.addEventListener("click", async () => {
  const { ok, data } = await apiFetch("/api/repository/host-key/scan", { method: "POST" });
  if (ok) {
    document.getElementById("hostKeyFingerprints").textContent = data.fingerprints.join("\n");
    document.getElementById("trustHostKeyBtn")?.classList.remove("is-hidden");
  } else {
    window.alert((data && data.error) || "No se pudo consultar la huella del host");
  }
});

document.getElementById("trustHostKeyBtn")?.addEventListener("click", async () => {
  const fingerprints = document.getElementById("hostKeyFingerprints").textContent;
  const confirmed = await askConfirm("Confiar en esta huella SSH", `Confirma que estas huellas coinciden con las publicadas por el proveedor:\n${fingerprints}`);
  if (!confirmed) return;
  const { ok, data } = await apiFetch("/api/repository/host-key/trust", { method: "POST" });
  document.getElementById("hostKeyStatus").textContent = ok ? "Huella confirmada." : (data && data.error) || "No se pudo confirmar";
});

document.getElementById("repoCheckBtn")?.addEventListener("click", async () => {
  const feedback = document.getElementById("repoDeployFeedback");
  feedback.textContent = "Consultando...";
  feedback.className = "feedback";
  const { ok, data } = await apiFetch("/api/repository/check", { method: "POST" });
  if (!ok) {
    feedback.textContent = (data && data.error) || "No se pudo consultar el repositorio";
    feedback.classList.add("error");
    return;
  }
  setText("repoRemoteSha", data.remote_sha ? data.remote_sha.slice(0, 12) : "-");
  setText("repoDeployedSha", data.deployed_sha ? data.deployed_sha.slice(0, 12) : "sin desplegar");
  document.getElementById("repoDeployBtn").disabled = !data.changes_available;
  feedback.textContent = data.changes_available ? "Hay cambios disponibles para desplegar." : "Ya estas en la ultima version.";
  feedback.classList.add(data.changes_available ? "ok" : "ok");
});

document.getElementById("repoDeployBtn")?.addEventListener("click", async () => {
  const confirmed = await askConfirm("Actualizar y desplegar", "Se clonara la ultima version, se construira la imagen y se activara solo si pasa el chequeo de salud.");
  if (!confirmed) return;
  const feedback = document.getElementById("repoDeployFeedback");
  const logEl = document.getElementById("repoDeployLog");
  feedback.textContent = "Desplegando, esto puede tardar varios minutos...";
  feedback.className = "feedback";
  const profile = document.getElementById("repoDeployProfile").value;
  const { ok, data } = await apiFetch("/api/repository/deploy", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ profile }),
  });
  feedback.textContent = ok ? "Despliegue activado correctamente." : (data && data.error) || "El despliegue fallo";
  feedback.classList.add(ok ? "ok" : "error");
  if (data && data.log_tail && data.log_tail.length) {
    logEl.textContent = data.log_tail.join("\n");
    logEl.classList.remove("is-hidden");
  }
  loadOverview();
});

document.getElementById("uploadDeployBtn")?.addEventListener("click", async () => {
  const fileEl = document.getElementById("uploadDeployFile");
  const feedback = document.getElementById("uploadDeployFeedback");
  if (!fileEl.files.length) {
    feedback.textContent = "Selecciona un archivo primero.";
    feedback.className = "feedback error";
    return;
  }
  const confirmed = await askConfirm("Subir y desplegar", "Se validara, construira y desplegara el codigo subido.");
  if (!confirmed) return;
  const formData = new FormData();
  formData.append("file", fileEl.files[0]);
  formData.append("profile", document.getElementById("uploadDeployProfile").value);
  feedback.textContent = "Subiendo y desplegando...";
  feedback.className = "feedback";
  const response = await fetch("/api/deployment/upload", { method: "POST", headers: authHeaders(), body: formData });
  let data = null;
  try {
    data = await response.json();
  } catch (err) {
    data = null;
  }
  feedback.textContent = response.ok ? "Despliegue activado correctamente." : (data && data.error) || "El despliegue fallo";
  feedback.classList.add(response.ok ? "ok" : "error");
  loadOverview();
});

/* ---------------------------------------------------------------------- */
/* Environment                                                             */
/* ---------------------------------------------------------------------- */

let currentEnvVars = {};

async function loadEnvironment() {
  const { ok, data } = await apiFetch("/api/environment");
  if (!ok || !data) return;
  currentEnvVars = data.variables || {};
  renderEnvList();
}

function renderEnvList() {
  const list = document.getElementById("envList");
  list.innerHTML = "";
  for (const [name, redactedValue] of Object.entries(currentEnvVars)) {
    const row = document.createElement("div");
    row.className = "user-row";
    const info = document.createElement("div");
    info.className = "user-row-info";
    const nameEl = document.createElement("strong");
    nameEl.textContent = name;
    const valueEl = document.createElement("small");
    valueEl.textContent = redactedValue;
    info.append(nameEl, valueEl);
    const removeBtn = document.createElement("button");
    removeBtn.className = "btn ghost";
    removeBtn.type = "button";
    removeBtn.textContent = "Quitar";
    removeBtn.addEventListener("click", () => {
      delete currentEnvVars[name];
      renderEnvList();
    });
    row.append(info, removeBtn);
    list.appendChild(row);
  }
}

document.getElementById("envAddForm")?.addEventListener("submit", (event) => {
  event.preventDefault();
  const nameInput = document.getElementById("envName");
  const valueInput = document.getElementById("envValue");
  const name = nameInput.value.trim();
  if (!name) return;
  currentEnvVars[name] = valueInput.value;
  nameInput.value = "";
  valueInput.value = "";
  renderEnvList();
});

document.getElementById("envSaveBtn")?.addEventListener("click", async () => {
  const feedback = document.getElementById("envFeedback");
  const { ok, data } = await apiFetch("/api/environment", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ variables: currentEnvVars }),
  });
  feedback.textContent = ok ? "Guardado. Los cambios se aplicaran en el proximo despliegue." : (data && data.error) || "No se pudo guardar";
  feedback.className = `feedback ${ok ? "ok" : "error"}`;
  if (ok) loadEnvironment();
});

/* ---------------------------------------------------------------------- */
/* Database                                                                 */
/* ---------------------------------------------------------------------- */

async function loadDatabaseTab() {
  const { ok, data } = await apiFetch("/api/database");
  if (!ok || !data) return;

  const provisioned = Boolean(data.provisioned);
  document.getElementById("dbProvisionForm")?.classList.toggle("is-hidden", provisioned);
  document.getElementById("dbManageActions")?.classList.toggle("is-hidden", !provisioned);

  const statusEl = document.getElementById("dbStatus");
  if (statusEl) {
    statusEl.textContent = provisioned ? `${data.engine} (${data.db_name}) - ${data.running ? "activa" : "detenida"}` : "Sin base de datos";
  }

  if (!document.getElementById("dbEngine").dataset.filled) {
    const { ok: cfgOk, data: cfg } = await apiFetch("/api/config");
    if (cfgOk && cfg) {
      const select = document.getElementById("dbEngine");
      select.dataset.filled = "1";
      for (const engine of cfg.allowed_db_engines) {
        const option = document.createElement("option");
        option.value = engine;
        option.textContent = engine === "mysql" ? "MySQL" : "PostgreSQL";
        select.appendChild(option);
      }
    }
  }

  await loadBackups();
}

document.getElementById("dbProvisionBtn")?.addEventListener("click", async () => {
  const engine = document.getElementById("dbEngine").value;
  const confirmed = await askConfirm("Aprovisionar base de datos", `Se creara una base de datos ${engine} administrada para esta instancia.`);
  if (!confirmed) return;
  const feedback = document.getElementById("dbFeedback");
  const { ok, data } = await apiFetch("/api/database/provision", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ engine }),
  });
  feedback.textContent = ok ? "Base de datos aprovisionada." : (data && data.error) || "No se pudo aprovisionar";
  feedback.className = `feedback ${ok ? "ok" : "error"}`;
  loadDatabaseTab();
});

document.getElementById("dbRotateBtn")?.addEventListener("click", async () => {
  const confirmed = await askConfirm("Rotar credenciales", "Se generara una nueva contrasena y se actualizara en la base de datos. Redeploya la aplicacion para que tome el nuevo valor.");
  if (!confirmed) return;
  const feedback = document.getElementById("dbFeedback");
  const { ok, data } = await apiFetch("/api/database/rotate", { method: "POST" });
  feedback.textContent = ok ? "Credenciales rotadas." : (data && data.error) || "No se pudo rotar";
  feedback.className = `feedback ${ok ? "ok" : "error"}`;
});

document.getElementById("dbBackupBtn")?.addEventListener("click", async () => {
  const feedback = document.getElementById("dbFeedback");
  feedback.textContent = "Generando respaldo...";
  feedback.className = "feedback";
  const { ok, data } = await apiFetch("/api/database/backup", { method: "POST" });
  feedback.textContent = ok ? `Respaldo creado: ${data.filename}` : (data && data.error) || "No se pudo respaldar";
  feedback.className = `feedback ${ok ? "ok" : "error"}`;
  loadBackups();
});

document.getElementById("dbDeprovisionBtn")?.addEventListener("click", async () => {
  const confirmed = await askConfirm("Eliminar base de datos", "Se eliminara la base de datos y todos sus datos de forma permanente. Esta accion no se puede deshacer.");
  if (!confirmed) return;
  const feedback = document.getElementById("dbFeedback");
  const { ok, data } = await apiFetch("/api/database/deprovision", { method: "POST" });
  feedback.textContent = ok ? "Base de datos eliminada." : (data && data.error) || "No se pudo eliminar";
  feedback.className = `feedback ${ok ? "ok" : "error"}`;
  loadDatabaseTab();
});

async function loadBackups() {
  const { ok, data } = await apiFetch("/api/database/backups");
  if (!ok || !data) return;
  const list = document.getElementById("backupsList");
  const emptyState = document.getElementById("backupsEmptyState");
  list.innerHTML = "";
  emptyState.classList.toggle("is-hidden", data.backups.length > 0);
  for (const backup of data.backups) {
    const row = document.createElement("div");
    row.className = "user-row";
    const info = document.createElement("div");
    info.className = "user-row-info";
    const name = document.createElement("strong");
    name.textContent = backup.name;
    const meta = document.createElement("small");
    meta.textContent = `${formatBytes(backup.size)} - ${new Date(backup.modified_at * 1000).toLocaleString()}`;
    info.append(name, meta);
    const downloadLink = document.createElement("a");
    downloadLink.className = "btn ghost";
    downloadLink.href = `/api/database/backups/${encodeURIComponent(backup.name)}/download`;
    downloadLink.textContent = "Descargar";
    const restoreBtn = document.createElement("button");
    restoreBtn.className = "btn ghost";
    restoreBtn.type = "button";
    restoreBtn.textContent = "Restaurar";
    restoreBtn.addEventListener("click", async () => {
      const confirmed = await askConfirm("Restaurar respaldo", `Se sobrescribira la base de datos actual con "${backup.name}".`);
      if (!confirmed) return;
      const { ok: restoreOk, data: restoreData } = await apiFetch("/api/database/restore", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: backup.name }),
      });
      window.alert(restoreOk ? "Restaurado correctamente." : (restoreData && restoreData.error) || "No se pudo restaurar");
    });
    row.append(info, downloadLink, restoreBtn);
    list.appendChild(row);
  }
}

/* ---------------------------------------------------------------------- */
/* Activity                                                                 */
/* ---------------------------------------------------------------------- */

async function loadActivity() {
  const { ok, data } = await apiFetch("/api/activity");
  if (!ok || !data) return;
  const list = document.getElementById("activityList");
  const emptyState = document.getElementById("activityEmptyState");
  list.innerHTML = "";
  emptyState.classList.toggle("is-hidden", data.entries.length > 0);

  for (const entry of data.entries) {
    const item = document.createElement("li");
    const time = document.createElement("time");
    time.textContent = entry.timestamp;
    const text = document.createElement("span");
    const detailText = entry.detail && Object.keys(entry.detail).length ? ` - ${JSON.stringify(entry.detail)}` : "";
    text.textContent = `${entry.action}${detailText}`;
    item.appendChild(time);
    item.appendChild(text);
    list.appendChild(item);
  }
}

/* ---------------------------------------------------------------------- */
/* Users                                                                    */
/* ---------------------------------------------------------------------- */

const userForm = document.getElementById("userForm");
const usersList = document.getElementById("usersList");
const userFeedback = document.getElementById("userFeedback");

async function loadUsers() {
  if (!usersList) return;
  const { ok, data } = await apiFetch("/api/users");
  if (!ok || !data) return;
  usersList.innerHTML = "";
  for (const user of data.users) {
    const row = document.createElement("div");
    row.className = "user-row";
    const avatar = document.createElement("span");
    avatar.className = "user-avatar";
    avatar.textContent = user.username.slice(0, 1).toUpperCase();
    const info = document.createElement("div");
    info.className = "user-row-info";
    const name = document.createElement("strong");
    name.textContent = user.username;
    const meta = document.createElement("small");
    meta.textContent = user.active ? "Acceso activo" : "Acceso desactivado";
    info.append(name, meta);
    const role = document.createElement("span");
    role.className = "role-pill";
    role.textContent = user.role === "admin" ? "Administrador" : "Operador";
    const active = document.createElement("button");
    active.className = "btn ghost";
    active.type = "button";
    active.textContent = user.active ? "Desactivar" : "Activar";
    active.addEventListener("click", async () => {
      const confirmed = await askConfirm(user.active ? "Desactivar usuario" : "Activar usuario", `${user.active ? "Desactivaras" : "Activaras"} el acceso de ${user.username}.`);
      if (!confirmed) return;
      const response = await apiFetch(`/api/users/${encodeURIComponent(user.username)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ active: !user.active }),
      });
      if (!response.ok) window.alert(response.data?.error || "No se pudo actualizar el usuario");
      loadUsers();
    });
    row.append(avatar, info, role, active);
    usersList.appendChild(row);
  }
}

userForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const username = document.getElementById("newUsername").value.trim();
  const password = document.getElementById("newPassword").value;
  const role = document.getElementById("newUserRole").value;
  const { ok, data } = await apiFetch("/api/users", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password, role }),
  });
  userFeedback.textContent = ok ? "Usuario creado correctamente." : data?.error || "No se pudo crear el usuario.";
  userFeedback.className = `feedback ${ok ? "ok" : "error"}`;
  if (ok) {
    userForm.reset();
    loadUsers();
  }
});

/* ---------------------------------------------------------------------- */
/* Wiring                                                                   */
/* ---------------------------------------------------------------------- */

tabLoaders.overview = loadOverview;
tabLoaders.files = loadFiles;
tabLoaders.repository = loadRepository;
tabLoaders.environment = loadEnvironment;
tabLoaders.software = loadSoftwareTab;
tabLoaders.database = loadDatabaseTab;
tabLoaders.activity = loadActivity;
tabLoaders.users = loadUsers;

fetchStatus();
loadOverview();
populateProfileSelects();
setInterval(fetchStatus, 10000);
setInterval(() => {
  if (document.getElementById("tab-overview") && !document.getElementById("tab-overview").hidden) {
    loadOverview();
  }
}, 30000);
initLogsStream();
