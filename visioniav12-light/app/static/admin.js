"use strict";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const state = {
  token: localStorage.getItem("vision_admin_token") || "",
  me: null,
  overview: null,
  cameras: [],
  catalog: {},
  rules: [],
  users: [],
  logs: [],
  canvasImage: null,
  canvasObjectUrl: null,
  geometry: { polygon: [], L1: [], L2: [] },
  drawMode: "polygon",
};

const titles = {
  overview: "Visão geral",
  cameras: "Câmeras",
  rules: "Regras e ROI",
  users: "Usuários",
  logs: "Logs",
  system: "Sistema",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatDate(value) {
  if (!value) return "—";
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" });
}

function formatAge(value) {
  if (value === null || value === undefined) return "sem frame";
  if (value < 60) return `${value.toFixed(1)} s`;
  if (value < 3600) return `${(value / 60).toFixed(1)} min`;
  return `${(value / 3600).toFixed(1)} h`;
}

function statusBadge(value) {
  const raw = String(value || "UNKNOWN");
  const cls = raw.toLowerCase().replaceAll("_", "-");
  return `<span class="status ${escapeHtml(cls)}">${escapeHtml(raw)}</span>`;
}

function toast(message, kind = "good") {
  const item = document.createElement("div");
  item.className = `toast ${kind}`;
  item.textContent = message;
  $("#toastStack").appendChild(item);
  setTimeout(() => item.remove(), 4800);
}

function logout() {
  localStorage.removeItem("vision_admin_token");
  state.token = "";
  state.me = null;
  $("#appView").classList.add("hidden");
  $("#loginView").classList.remove("hidden");
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.token) headers.set("Authorization", `Bearer ${state.token}`);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401) {
    logout();
    throw new Error("Sessão expirada. Entre novamente.");
  }
  const contentType = response.headers.get("content-type") || "";
  let payload;
  if (contentType.includes("application/json")) payload = await response.json();
  else payload = await response.text();
  if (!response.ok) {
    const detail = typeof payload === "object" ? payload.detail || JSON.stringify(payload) : payload;
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return payload;
}

async function login(event) {
  event.preventDefault();
  $("#loginError").textContent = "";
  try {
    const payload = await api("/api/auth/token", {
      method: "POST",
      body: JSON.stringify({ email: $("#loginEmail").value.trim(), password: $("#loginPassword").value }),
    });
    state.token = payload.access_token;
    localStorage.setItem("vision_admin_token", state.token);
    await bootstrap();
  } catch (error) {
    $("#loginError").textContent = error.message;
  }
}

function showView(name) {
  $$(".view").forEach((element) => element.classList.remove("active"));
  $$("#adminNav button").forEach((element) => element.classList.toggle("active", element.dataset.view === name));
  $(`#view-${name}`).classList.add("active");
  $("#pageTitle").textContent = titles[name] || "Administração";
  if (name === "overview") loadOverview();
  if (name === "cameras") loadCameras();
  if (name === "rules") loadRuleWorkspace();
  if (name === "users") loadUsers();
  if (name === "logs") loadLogs();
  if (name === "system") renderSystem();
}

async function bootstrap() {
  state.me = await api("/api/me");
  if (state.me.role !== "admin") {
    logout();
    throw new Error("A conta não possui perfil administrativo.");
  }
  $("#loginView").classList.add("hidden");
  $("#appView").classList.remove("hidden");
  $("#topUser").textContent = state.me.email;
  $("#sidebarUser").textContent = state.me.email;
  await Promise.all([loadCatalog(), loadOverview(), loadCameras()]);
}

async function loadCatalog() {
  const result = await api("/api/events/catalog");
  state.catalog = result.events || {};
  const select = $("#ruleEvent");
  const current = select.value;
  select.innerHTML = Object.entries(state.catalog)
    .map(([key, item]) => `<option value="${escapeHtml(key)}">${escapeHtml(item.title || key)} · ${escapeHtml(key)}</option>`)
    .join("");
  if (current && state.catalog[current]) select.value = current;
}

async function loadOverview() {
  try {
    state.overview = await api("/api/admin/overview");
    const { cameras, rules, logs, services, public_base_url: publicUrl } = state.overview;
    $("#ovCameras").textContent = cameras.total;
    $("#ovCamerasHint").textContent = `${cameras.enabled} habilitadas · ${cameras.offline} offline`;
    $("#ovOnline").textContent = cameras.online;
    $("#ovDegraded").textContent = cameras.degraded;
    $("#ovRules").textContent = rules.enabled;
    $("#ovRulesHint").textContent = `${rules.total} versões cadastradas`;
    $("#ovLogs").textContent = logs.last_24h;
    $("#ovLogsHint").textContent = `${logs.visible_last_24h} visíveis · último ${formatDate(logs.latest_at)}`;
    $("#publicUrl").textContent = publicUrl || "V12 Light";

    $("#serviceRows").innerHTML = services.length
      ? services.map((service) => `<tr><td>${escapeHtml(service.name)}</td><td>${statusBadge(String(service.state || "RUNNING").toUpperCase())}</td><td>${service.age_s === null ? "—" : `${service.age_s} s`}</td></tr>`).join("")
      : `<tr><td colspan="3" class="empty">Nenhuma telemetria de serviço disponível.</td></tr>`;

    const states = Object.entries(rules.by_state || {});
    $("#ruleStateCards").innerHTML = states.length
      ? states.map(([key, value]) => `<div class="card"><div class="label">${escapeHtml(key)}</div><div class="value">${value}</div></div>`).join("")
      : `<div class="empty">Nenhuma regra cadastrada.</div>`;
  } catch (error) {
    toast(`Visão geral: ${error.message}`, "bad");
  }
}

async function loadCameras() {
  try {
    state.cameras = await api("/api/admin/cameras/status");
    const condos = [...new Set(state.cameras.map((camera) => camera.condo))].sort();
    const currentCondo = $("#cameraCondo").value;
    $("#cameraCondo").innerHTML = `<option value="">Todos os condomínios</option>${condos.map((condo) => `<option>${escapeHtml(condo)}</option>`).join("")}`;
    if (condos.includes(currentCondo)) $("#cameraCondo").value = currentCondo;

    const ruleCameraCurrent = $("#ruleCamera").value;
    $("#ruleCamera").innerHTML = state.cameras.map((camera) => `<option value="${escapeHtml(camera.id)}">${escapeHtml(camera.condo)} · ${escapeHtml(camera.name)} · CH ${camera.channel}</option>`).join("");
    if (state.cameras.some((camera) => camera.id === ruleCameraCurrent)) $("#ruleCamera").value = ruleCameraCurrent;
    renderCameras();
  } catch (error) {
    toast(`Câmeras: ${error.message}`, "bad");
  }
}

function renderCameras() {
  const search = $("#cameraSearch").value.trim().toLowerCase();
  const desiredState = $("#cameraState").value;
  const desiredCondo = $("#cameraCondo").value;
  const rows = state.cameras.filter((camera) => {
    const haystack = `${camera.condo} ${camera.name} ${camera.dvr_id} ${camera.id}`.toLowerCase();
    return (!search || haystack.includes(search)) && (!desiredState || camera.runtime_state === desiredState) && (!desiredCondo || camera.condo === desiredCondo);
  });
  $("#cameraRows").innerHTML = rows.length
    ? rows.map((camera) => `<tr>
      <td>${statusBadge(camera.runtime_state)}</td>
      <td>${escapeHtml(camera.condo)}</td>
      <td><strong>${escapeHtml(camera.name)}</strong><br><span class="muted">${escapeHtml(camera.id)}</span></td>
      <td>${escapeHtml(camera.dvr_id)}</td>
      <td>${camera.channel}</td>
      <td>${formatAge(camera.last_frame_age_s)}<br><span class="muted">${formatDate(camera.last_frame_at)}</span></td>
      <td>${camera.failures || 0}${camera.last_error ? `<br><span class="muted" title="${escapeHtml(camera.last_error)}">${escapeHtml(camera.last_error).slice(0, 46)}</span>` : ""}</td>
      <td><button class="btn btn-sm" data-camera-snapshot="${escapeHtml(camera.id)}">Frame</button> <button class="btn btn-blue btn-sm" data-camera-roi="${escapeHtml(camera.id)}">Editar ROI</button></td>
    </tr>`).join("")
    : `<tr><td colspan="8" class="empty">Nenhuma câmera corresponde aos filtros.</td></tr>`;

  $$('[data-camera-roi]').forEach((button) => button.addEventListener("click", () => {
    showView("rules");
    $("#ruleCamera").value = button.dataset.cameraRoi;
    loadRuleForSelection();
    loadSnapshot();
  }));
  $$('[data-camera-snapshot]').forEach((button) => button.addEventListener("click", () => openLatestSnapshot(button.dataset.cameraSnapshot)));
}

async function syncRegistry() {
  try {
    const result = await api("/api/admin/cameras/sync-registry", { method: "POST" });
    toast(`${result.synced} câmeras sincronizadas.`);
    await Promise.all([loadCameras(), loadOverview()]);
  } catch (error) {
    toast(`Sincronização: ${error.message}`, "bad");
  }
}

async function openLatestSnapshot(cameraId) {
  try {
    const response = await fetch(`/api/admin/cameras/${encodeURIComponent(cameraId)}/latest-snapshot`, { headers: { Authorization: `Bearer ${state.token}` } });
    if (!response.ok) throw new Error((await response.json()).detail || `HTTP ${response.status}`);
    const blob = await response.blob();
    openModal("Último frame", `<img alt="Último frame da câmera">`);
    const image = $("#modalBody img");
    image.src = URL.createObjectURL(blob);
  } catch (error) {
    toast(`Frame: ${error.message}`, "bad");
  }
}

async function loadRuleWorkspace() {
  if (!state.cameras.length) await loadCameras();
  if (!Object.keys(state.catalog).length) await loadCatalog();
  await loadRuleForSelection();
  await loadSnapshot();
}

async function loadRuleForSelection() {
  const cameraId = $("#ruleCamera").value;
  if (!cameraId) return;
  try {
    state.rules = await api(`/api/admin/rules/by-camera/${encodeURIComponent(cameraId)}`);
    applySelectedRule();
  } catch (error) {
    toast(`Regras: ${error.message}`, "bad");
  }
}

function currentRule() {
  const eventKey = $("#ruleEvent").value;
  return state.rules.filter((rule) => rule.event_key === eventKey).sort((a, b) => b.version - a.version)[0] || null;
}

function resetGeometry(type = $("#geometryType").value) {
  state.geometry = { polygon: [], L1: [], L2: [] };
  state.drawMode = type === "polygon" ? "polygon" : "L1";
  $("#drawModeLabel").textContent = `Modo: ${state.drawMode}`;
  renderGeometryPreview();
  drawCanvas();
}

function applySelectedRule() {
  const rule = currentRule();
  if (!rule) {
    $("#ruleState").value = "DRAFT";
    $("#ruleEnabled").value = "false";
    $("#ruleDwell").value = 0;
    $("#ruleCooldown").value = 30;
    $("#ruleTimeout").value = 8;
    $("#ruleCertification").value = "false";
    $("#ruleConfigExtra").value = "{}";
    resetGeometry(state.catalog[$("#ruleEvent").value]?.mode === "double_line" ? "double_line" : state.catalog[$("#ruleEvent").value]?.mode === "single_line" ? "single_line" : state.catalog[$("#ruleEvent").value]?.mode === "polygon" ? "polygon" : "none");
    $("#geometryType").value = state.catalog[$("#ruleEvent").value]?.mode === "double_line" ? "double_line" : state.catalog[$("#ruleEvent").value]?.mode === "single_line" ? "single_line" : state.catalog[$("#ruleEvent").value]?.mode === "polygon" ? "polygon" : "none";
    $("#ruleStatus").textContent = "Nenhuma versão cadastrada para esta câmera/evento.";
    return;
  }

  $("#ruleState").value = rule.state;
  $("#ruleEnabled").value = String(rule.enabled);
  const config = { ...(rule.config || {}) };
  $("#ruleDwell").value = config.dwell_seconds ?? 0;
  $("#ruleCooldown").value = config.cooldown_seconds ?? 30;
  $("#ruleTimeout").value = config.timeout_seconds ?? 8;
  $("#ruleCertification").value = String(Boolean(config.certification_required));
  delete config.dwell_seconds;
  delete config.cooldown_seconds;
  delete config.timeout_seconds;
  delete config.certification_required;
  $("#ruleConfigExtra").value = JSON.stringify(config, null, 2);

  const geometry = rule.geometry || {};
  $("#geometryType").value = geometry.type || "none";
  $("#ruleDirection").value = geometry.direction || "any";
  state.geometry = {
    polygon: geometry.type === "polygon" ? [...(geometry.points || [])] : [],
    L1: geometry.type === "single_line" ? [...(geometry.points || [])] : [...(geometry.lines?.L1 || [])],
    L2: [...(geometry.lines?.L2 || [])],
  };
  state.drawMode = geometry.type === "polygon" ? "polygon" : "L1";
  $("#drawModeLabel").textContent = `Modo: ${state.drawMode}`;
  $("#ruleStatus").textContent = `Versão atual: ${rule.version} · ${rule.state}`;
  renderGeometryPreview();
  drawCanvas();
}

function geometryPayload() {
  const type = $("#geometryType").value;
  if (type === "none") return {};
  if (type === "polygon") return { type, points: state.geometry.polygon };
  if (type === "single_line") return { type, points: state.geometry.L1, direction: $("#ruleDirection").value };
  const eventKey = $("#ruleEvent").value;
  const sequence = eventKey.includes("saida") ? ["L2", "L1"] : ["L1", "L2"];
  return { type, lines: { L1: state.geometry.L1, L2: state.geometry.L2 }, sequence, direction: $("#ruleDirection").value };
}

function validateGeometry(geometry) {
  if (!geometry.type) return;
  if (geometry.type === "polygon" && geometry.points.length < 3) throw new Error("O polígono precisa de pelo menos três pontos.");
  if (geometry.type === "single_line" && geometry.points.length !== 2) throw new Error("A linha precisa de dois pontos.");
  if (geometry.type === "double_line" && (geometry.lines.L1.length !== 2 || geometry.lines.L2.length !== 2)) throw new Error("A dupla linha precisa de dois pontos em L1 e dois pontos em L2.");
}

async function saveRule() {
  try {
    const geometry = geometryPayload();
    validateGeometry(geometry);
    let extra;
    try { extra = JSON.parse($("#ruleConfigExtra").value || "{}"); }
    catch { throw new Error("A configuração adicional não é um JSON válido."); }
    const config = {
      ...extra,
      dwell_seconds: Number($("#ruleDwell").value || 0),
      cooldown_seconds: Number($("#ruleCooldown").value || 0),
      timeout_seconds: Number($("#ruleTimeout").value || 8),
      certification_required: $("#ruleCertification").value === "true",
    };
    const payload = {
      camera_id: $("#ruleCamera").value,
      event_key: $("#ruleEvent").value,
      enabled: $("#ruleEnabled").value === "true",
      state: $("#ruleState").value,
      geometry,
      config,
    };
    const result = await api("/api/admin/rules", { method: "POST", body: JSON.stringify(payload) });
    toast(`Regra salva na versão ${result.version}.`);
    await loadRuleForSelection();
    await loadOverview();
  } catch (error) {
    $("#ruleStatus").textContent = error.message;
    toast(`Regra: ${error.message}`, "bad");
  }
}

async function loadSnapshot() {
  const cameraId = $("#ruleCamera").value;
  if (!cameraId) return;
  if (state.canvasObjectUrl) URL.revokeObjectURL(state.canvasObjectUrl);
  state.canvasObjectUrl = null;
  state.canvasImage = null;
  try {
    const response = await fetch(`/api/admin/cameras/${encodeURIComponent(cameraId)}/latest-snapshot`, { headers: { Authorization: `Bearer ${state.token}` } });
    if (!response.ok) throw new Error("Frame atual ainda não disponível");
    const blob = await response.blob();
    const image = new Image();
    state.canvasObjectUrl = URL.createObjectURL(blob);
    image.onload = () => {
      state.canvasImage = image;
      const canvas = $("#roiCanvas");
      canvas.width = image.naturalWidth || 960;
      canvas.height = image.naturalHeight || 540;
      drawCanvas();
    };
    image.src = state.canvasObjectUrl;
  } catch (error) {
    drawCanvas();
    toast(error.message, "bad");
  }
}

function canvasPoint(event) {
  const canvas = $("#roiCanvas");
  const rect = canvas.getBoundingClientRect();
  return [
    Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
    Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
  ];
}

function addPoint(event) {
  const type = $("#geometryType").value;
  if (type === "none") return;
  const point = canvasPoint(event);
  if (type === "polygon") state.geometry.polygon.push(point);
  else if (state.drawMode === "L2") {
    if (state.geometry.L2.length >= 2) state.geometry.L2 = [];
    state.geometry.L2.push(point);
  } else {
    if (state.geometry.L1.length >= 2) state.geometry.L1 = [];
    state.geometry.L1.push(point);
  }
  renderGeometryPreview();
  drawCanvas();
}

function undoPoint() {
  const type = $("#geometryType").value;
  if (type === "polygon") state.geometry.polygon.pop();
  else state.geometry[state.drawMode === "L2" ? "L2" : "L1"].pop();
  renderGeometryPreview();
  drawCanvas();
}

function drawPolyline(ctx, points, color, close = false) {
  if (!points.length) return;
  const canvas = ctx.canvas;
  ctx.beginPath();
  points.forEach(([x, y], index) => {
    const px = x * canvas.width;
    const py = y * canvas.height;
    if (index === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
  });
  if (close && points.length >= 3) ctx.closePath();
  ctx.lineWidth = Math.max(2, canvas.width / 420);
  ctx.strokeStyle = color;
  ctx.stroke();
  if (close && points.length >= 3) {
    ctx.fillStyle = `${color}26`;
    ctx.fill();
  }
  for (const [x, y] of points) {
    ctx.beginPath();
    ctx.arc(x * canvas.width, y * canvas.height, Math.max(4, canvas.width / 180), 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
  }
}

function drawCanvas() {
  const canvas = $("#roiCanvas");
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (state.canvasImage) ctx.drawImage(state.canvasImage, 0, 0, canvas.width, canvas.height);
  else {
    ctx.fillStyle = "#050b12";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#91a9c5";
    ctx.font = `${Math.max(16, canvas.width / 42)}px system-ui`;
    ctx.textAlign = "center";
    ctx.fillText("Aguardando frame recente da câmera", canvas.width / 2, canvas.height / 2);
  }
  drawPolyline(ctx, state.geometry.polygon, "#37d8cb", true);
  drawPolyline(ctx, state.geometry.L1, "#38a3ff");
  drawPolyline(ctx, state.geometry.L2, "#ffca5b");
  ctx.textAlign = "left";
  ctx.font = `${Math.max(13, canvas.width / 65)}px system-ui`;
  if (state.geometry.L1.length) { ctx.fillStyle = "#38a3ff"; ctx.fillText("L1", state.geometry.L1[0][0] * canvas.width + 8, state.geometry.L1[0][1] * canvas.height - 8); }
  if (state.geometry.L2.length) { ctx.fillStyle = "#ffca5b"; ctx.fillText("L2", state.geometry.L2[0][0] * canvas.width + 8, state.geometry.L2[0][1] * canvas.height - 8); }
}

function renderGeometryPreview() {
  $("#geometryPreview").textContent = JSON.stringify(geometryPayload(), null, 2);
}

async function loadUsers() {
  try {
    state.users = await api("/api/admin/users");
    $("#userRows").innerHTML = state.users.length
      ? state.users.map((user) => `<tr><td>${escapeHtml(user.email)}</td><td>${escapeHtml(user.role)}</td><td>${escapeHtml((user.condo_scope || []).join(", ") || "Todos (admin)")}</td><td>${statusBadge(user.active ? "ONLINE" : "DISABLED")}</td><td><button class="btn btn-sm ${user.active ? "btn-danger" : "btn-primary"}" data-toggle-user="${escapeHtml(user.id)}" data-active="${user.active}">${user.active ? "Desativar" : "Ativar"}</button></td></tr>`).join("")
      : `<tr><td colspan="5" class="empty">Nenhum usuário.</td></tr>`;
    $$('[data-toggle-user]').forEach((button) => button.addEventListener("click", () => toggleUser(button.dataset.toggleUser, button.dataset.active !== "true")));
  } catch (error) {
    toast(`Usuários: ${error.message}`, "bad");
  }
}

function generatedPassword() {
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%*+-_";
  const values = crypto.getRandomValues(new Uint32Array(24));
  return [...values].map((value) => alphabet[value % alphabet.length]).join("");
}

async function createUser(event) {
  event.preventDefault();
  const payload = {
    email: $("#newUserEmail").value.trim(),
    password: $("#newUserPassword").value,
    role: $("#newUserRole").value,
    condo_scope: $("#newUserScope").value.split(",").map((item) => item.trim()).filter(Boolean),
  };
  try {
    const result = await api("/api/admin/users", { method: "POST", body: JSON.stringify(payload) });
    if ($("#newUserActive").value === "false") await api(`/api/admin/users/${encodeURIComponent(result.id)}`, { method: "PATCH", body: JSON.stringify({ active: false }) });
    $("#createdCredential").textContent = `E-mail: ${payload.email}\nSenha inicial: ${payload.password}\nPerfil: ${payload.role}\nEscopo: ${payload.condo_scope.join(", ") || "administrativo"}\n\nRegistre em cofre seguro; a senha não será recuperada depois.`;
    $("#createUserForm").reset();
    $("#newUserPassword").value = generatedPassword();
    toast("Credencial criada com sucesso.");
    await loadUsers();
  } catch (error) {
    toast(`Criar usuário: ${error.message}`, "bad");
  }
}

async function toggleUser(userId, active) {
  try {
    await api(`/api/admin/users/${encodeURIComponent(userId)}`, { method: "PATCH", body: JSON.stringify({ active }) });
    toast(active ? "Usuário ativado." : "Usuário desativado.");
    await loadUsers();
  } catch (error) {
    toast(`Usuário: ${error.message}`, "bad");
  }
}

async function loadLogs() {
  try {
    const params = new URLSearchParams({ limit: "500" });
    if ($("#logCondoFilter").value.trim()) params.set("condo", $("#logCondoFilter").value.trim());
    if ($("#logEventFilter").value.trim()) params.set("event_key", $("#logEventFilter").value.trim());
    state.logs = await api(`/api/admin/logs?${params}`);
    renderLogs();
  } catch (error) {
    toast(`Logs: ${error.message}`, "bad");
  }
}

function renderLogs() {
  const search = $("#logSearch").value.trim().toLowerCase();
  const rows = state.logs.filter((item) => !search || `${item.event_key} ${item.camera_name} ${item.condo} ${item.dvr_id}`.toLowerCase().includes(search));
  $("#logRows").innerHTML = rows.length
    ? rows.map((item) => `<tr><td>${formatDate(item.occurred_at)}</td><td>${escapeHtml(item.condo)}</td><td>${escapeHtml(item.camera_name)} · CH ${item.channel}</td><td>${escapeHtml(item.event_key)}</td><td>${statusBadge(item.decision)}</td><td>${item.client_visible ? statusBadge("ONLINE") : statusBadge("DISABLED")}</td><td>${item.has_snapshot ? `<button class="btn btn-sm" data-media-id="${item.id}" data-media-kind="snapshot">Snapshot</button>` : "—"} ${item.has_clip ? `<button class="btn btn-sm" data-media-id="${item.id}" data-media-kind="clip">Clipe</button>` : ""}</td></tr>`).join("")
    : `<tr><td colspan="7" class="empty">Nenhum log encontrado.</td></tr>`;
  $$('[data-media-id]').forEach((button) => button.addEventListener("click", () => openMedia(button.dataset.mediaId, button.dataset.mediaKind)));
}

async function openMedia(logId, kind) {
  try {
    const response = await fetch(`/api/logs/${encodeURIComponent(logId)}/media/${encodeURIComponent(kind)}`, { headers: { Authorization: `Bearer ${state.token}` } });
    if (!response.ok) throw new Error((await response.json()).detail || `HTTP ${response.status}`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    openModal(kind === "snapshot" ? "Snapshot" : "Clipe de 15 segundos", kind === "snapshot" ? `<img alt="Snapshot">` : `<video controls autoplay playsinline></video>`);
    $(kind === "snapshot" ? "#modalBody img" : "#modalBody video").src = url;
  } catch (error) {
    toast(`Mídia: ${error.message}`, "bad");
  }
}

function openModal(title, content) {
  $("#modalTitle").textContent = title;
  $("#modalBody").innerHTML = content;
  $("#mediaModal").classList.remove("hidden");
}

function closeModal() {
  $("#mediaModal").classList.add("hidden");
  $("#modalBody").innerHTML = "";
}

function renderSystem() {
  if (!state.overview) return;
  $("#systemPublication").textContent = JSON.stringify({
    public_base_url: state.overview.public_base_url,
    admin: `${state.overview.public_base_url}/admin`,
    portal: `${state.overview.public_base_url}/portal`,
    api: `${state.overview.public_base_url}/api`,
  }, null, 2);
  $("#systemServices").innerHTML = (state.overview.services || []).map((service) => `<div class="camera-tile"><h4>${escapeHtml(service.name)}</h4><p>${statusBadge(String(service.state || "RUNNING").toUpperCase())}</p><p>Atualização: ${service.age_s === null ? "—" : `${service.age_s} s`}</p></div>`).join("") || `<div class="empty">Sem telemetria.</div>`;
}

function bindEvents() {
  $("#loginForm").addEventListener("submit", login);
  $("#logoutButton").addEventListener("click", logout);
  $$("#adminNav button").forEach((button) => button.addEventListener("click", () => showView(button.dataset.view)));
  $("#refreshOverview").addEventListener("click", loadOverview);
  $("#refreshCameras").addEventListener("click", loadCameras);
  $("#syncRegistryButton").addEventListener("click", syncRegistry);
  ["#cameraSearch", "#cameraState", "#cameraCondo"].forEach((selector) => $(selector).addEventListener(selector === "#cameraSearch" ? "input" : "change", renderCameras));
  $("#ruleCamera").addEventListener("change", async () => { await loadRuleForSelection(); await loadSnapshot(); });
  $("#ruleEvent").addEventListener("change", applySelectedRule);
  $("#loadRuleButton").addEventListener("click", applySelectedRule);
  $("#saveRuleButton").addEventListener("click", saveRule);
  $("#reloadRuleData").addEventListener("click", loadRuleWorkspace);
  $("#reloadSnapshot").addEventListener("click", loadSnapshot);
  $("#geometryType").addEventListener("change", () => resetGeometry());
  $("#ruleDirection").addEventListener("change", renderGeometryPreview);
  $$("[data-draw]").forEach((button) => button.addEventListener("click", () => { state.drawMode = button.dataset.draw; $("#drawModeLabel").textContent = `Modo: ${state.drawMode}`; }));
  $("#undoPoint").addEventListener("click", undoPoint);
  $("#clearGeometry").addEventListener("click", () => resetGeometry());
  $("#roiCanvas").addEventListener("click", addPoint);
  $("#refreshUsers").addEventListener("click", loadUsers);
  $("#createUserForm").addEventListener("submit", createUser);
  $("#generatePassword").addEventListener("click", () => { $("#newUserPassword").value = generatedPassword(); });
  $("#refreshLogs").addEventListener("click", loadLogs);
  $("#logSearch").addEventListener("input", renderLogs);
  $("#logEventFilter").addEventListener("change", loadLogs);
  $("#logCondoFilter").addEventListener("change", loadLogs);
  $("#closeModal").addEventListener("click", closeModal);
  $("#mediaModal").addEventListener("click", (event) => { if (event.target.id === "mediaModal") closeModal(); });
}

bindEvents();
$("#newUserPassword").value = generatedPassword();
if (state.token) bootstrap().catch((error) => { logout(); $("#loginError").textContent = error.message; });
