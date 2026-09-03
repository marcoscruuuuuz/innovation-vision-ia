"use strict";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const state = {
  token: localStorage.getItem("vision_client_token") || "",
  me: null,
  summary: null,
  cameras: [],
  logs: [],
  catalog: {},
};

const titles = { dashboard: "Dashboard", cameras: "Câmeras", events: "Eventos" };

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatDate(value) {
  const date = new Date(value);
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
  const element = document.createElement("div");
  element.className = `toast ${kind}`;
  element.textContent = message;
  $("#toastStack").appendChild(element);
  setTimeout(() => element.remove(), 4500);
}

function logout() {
  localStorage.removeItem("vision_client_token");
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
    throw new Error("Sua sessão expirou. Entre novamente.");
  }
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) throw new Error(typeof payload === "object" ? payload.detail || JSON.stringify(payload) : payload);
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
    localStorage.setItem("vision_client_token", state.token);
    await bootstrap();
  } catch (error) {
    $("#loginError").textContent = error.message;
  }
}

function showView(name) {
  $$(".view").forEach((element) => element.classList.remove("active"));
  $$("#portalNav button").forEach((element) => element.classList.toggle("active", element.dataset.view === name));
  $(`#view-${name}`).classList.add("active");
  $("#pageTitle").textContent = titles[name] || "Portal";
  if (name === "dashboard") loadDashboard();
  if (name === "cameras") loadCameras();
  if (name === "events") loadEvents();
}

async function bootstrap() {
  state.me = await api("/api/me");
  $("#loginView").classList.add("hidden");
  $("#appView").classList.remove("hidden");
  $("#topUser").textContent = state.me.email;
  $("#sidebarUser").textContent = state.me.email;
  const scopeText = state.me.role === "admin" ? "Administrador" : (state.me.condo_scope || []).join(", ") || "Sem condomínio atribuído";
  $("#sidebarScope").textContent = scopeText;
  $("#scopePill").textContent = state.me.role === "admin" ? "Visão administrativa" : `${(state.me.condo_scope || []).length} condomínio(s)`;
  const catalogPayload = await api("/api/events/catalog");
  state.catalog = catalogPayload.events || {};
  await Promise.all([loadDashboard(), loadCameras(), loadEvents()]);
}

async function loadDashboard() {
  try {
    state.summary = await api("/api/client/summary");
    $("#dashTotal").textContent = state.summary.cameras.total;
    $("#dashOnline").textContent = state.summary.cameras.online;
    $("#dashDegraded").textContent = state.summary.cameras.degraded;
    $("#dashOffline").textContent = state.summary.cameras.offline;
    $("#dashLogs").textContent = state.summary.visible_logs;
    renderDashboardDetails();
  } catch (error) {
    toast(`Dashboard: ${error.message}`, "bad");
  }
}

function renderDashboardDetails() {
  const recent = state.logs.slice(0, 8);
  $("#recentEvents").innerHTML = recent.length
    ? recent.map((item) => `<div class="camera-tile" style="margin-bottom:8px"><h4>${escapeHtml(state.catalog[item.event_key]?.title || item.event_key)}</h4><p>${escapeHtml(item.condo)} · ${escapeHtml(item.camera_name)}</p><p>${formatDate(item.occurred_at)} · ${(item.confidence * 100).toFixed(1)}%</p></div>`).join("")
    : `<div class="empty">Nenhum evento visível.</div>`;

  const cameras = state.summary?.cameras || { online: 0, degraded: 0, offline: 0 };
  $("#cameraStateSummary").innerHTML = `
    <div class="grid-3">
      <div class="card"><div class="label">Online</div><div class="value">${cameras.online}</div></div>
      <div class="card"><div class="label">Degradadas</div><div class="value">${cameras.degraded}</div></div>
      <div class="card"><div class="label">Offline</div><div class="value">${cameras.offline}</div></div>
    </div>`;
}

async function loadCameras() {
  try {
    state.cameras = await api("/api/client/cameras");
    renderCameras();
  } catch (error) {
    toast(`Câmeras: ${error.message}`, "bad");
  }
}

function renderCameras() {
  const search = $("#cameraSearch").value.trim().toLowerCase();
  const desiredState = $("#cameraState").value;
  const rows = state.cameras.filter((camera) => {
    const haystack = `${camera.condo} ${camera.name} ${camera.dvr_id}`.toLowerCase();
    return (!search || haystack.includes(search)) && (!desiredState || camera.runtime_state === desiredState);
  });
  $("#cameraGrid").innerHTML = rows.length
    ? rows.map((camera) => `<article class="camera-tile">
      <div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start"><h4>${escapeHtml(camera.name)}</h4>${statusBadge(camera.runtime_state)}</div>
      <p>${escapeHtml(camera.condo)}</p>
      <p>DVR ${escapeHtml(camera.dvr_id)} · Canal ${camera.channel}</p>
      <p>Último frame: ${formatAge(camera.last_frame_age_s)}</p>
      ${camera.last_error ? `<p title="${escapeHtml(camera.last_error)}">Último erro: ${escapeHtml(camera.last_error).slice(0, 70)}</p>` : ""}
    </article>`).join("")
    : `<div class="empty">Nenhuma câmera corresponde aos filtros.</div>`;
}

async function loadEvents() {
  try {
    state.logs = await api("/api/client/logs?limit=500");
    const condos = [...new Set(state.logs.map((item) => item.condo))].sort();
    const current = $("#eventCondo").value;
    $("#eventCondo").innerHTML = `<option value="">Todos os condomínios</option>${condos.map((condo) => `<option>${escapeHtml(condo)}</option>`).join("")}`;
    if (condos.includes(current)) $("#eventCondo").value = current;
    renderEvents();
    renderDashboardDetails();
  } catch (error) {
    toast(`Eventos: ${error.message}`, "bad");
  }
}

function renderEvents() {
  const search = $("#eventSearch").value.trim().toLowerCase();
  const condo = $("#eventCondo").value;
  const rows = state.logs.filter((item) => {
    const title = state.catalog[item.event_key]?.title || item.event_key;
    const haystack = `${title} ${item.event_key} ${item.camera_name} ${item.condo}`.toLowerCase();
    return (!search || haystack.includes(search)) && (!condo || item.condo === condo);
  });
  $("#eventRows").innerHTML = rows.length
    ? rows.map((item) => `<tr>
      <td>${formatDate(item.occurred_at)}</td>
      <td>${escapeHtml(item.condo)}</td>
      <td>${escapeHtml(item.camera_name)}</td>
      <td><strong>${escapeHtml(state.catalog[item.event_key]?.title || item.event_key)}</strong><br><span class="muted">${escapeHtml(item.event_key)}</span></td>
      <td>${(item.confidence * 100).toFixed(1)}%</td>
      <td>${item.has_snapshot ? `<button class="btn btn-sm" data-media-id="${item.id}" data-kind="snapshot">Snapshot</button>` : ""} ${item.has_clip ? `<button class="btn btn-blue btn-sm" data-media-id="${item.id}" data-kind="clip">Clipe 15s</button>` : ""}</td>
    </tr>`).join("")
    : `<tr><td colspan="6" class="empty">Nenhum evento encontrado.</td></tr>`;
  $$('[data-media-id]').forEach((button) => button.addEventListener("click", () => openMedia(button.dataset.mediaId, button.dataset.kind)));
}

async function openMedia(logId, kind) {
  try {
    const response = await fetch(`/api/logs/${encodeURIComponent(logId)}/media/${encodeURIComponent(kind)}`, { headers: { Authorization: `Bearer ${state.token}` } });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || `HTTP ${response.status}`);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    $("#modalTitle").textContent = kind === "snapshot" ? "Snapshot do evento" : "Clipe de 15 segundos";
    $("#modalBody").innerHTML = kind === "snapshot" ? `<img alt="Snapshot do evento">` : `<video controls autoplay playsinline></video>`;
    $(kind === "snapshot" ? "#modalBody img" : "#modalBody video").src = url;
    $("#mediaModal").classList.remove("hidden");
  } catch (error) {
    toast(`Evidência: ${error.message}`, "bad");
  }
}

function closeModal() {
  $("#mediaModal").classList.add("hidden");
  $("#modalBody").innerHTML = "";
}

function bindEvents() {
  $("#loginForm").addEventListener("submit", login);
  $("#logoutButton").addEventListener("click", logout);
  $$("#portalNav button").forEach((button) => button.addEventListener("click", () => showView(button.dataset.view)));
  $("#refreshDashboard").addEventListener("click", async () => { await Promise.all([loadDashboard(), loadCameras(), loadEvents()]); });
  $("#refreshCameras").addEventListener("click", loadCameras);
  $("#refreshEvents").addEventListener("click", loadEvents);
  $("#cameraSearch").addEventListener("input", renderCameras);
  $("#cameraState").addEventListener("change", renderCameras);
  $("#eventSearch").addEventListener("input", renderEvents);
  $("#eventCondo").addEventListener("change", renderEvents);
  $("#closeModal").addEventListener("click", closeModal);
  $("#mediaModal").addEventListener("click", (event) => { if (event.target.id === "mediaModal") closeModal(); });
}

bindEvents();
if (state.token) bootstrap().catch((error) => { logout(); $("#loginError").textContent = error.message; });
