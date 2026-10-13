"use strict";

(() => {
  const tokenInput = document.getElementById("facebook-token");
  const connectButton = document.getElementById("connect");
  const disconnectButton = document.getElementById("disconnect");
  const fanpageSelect = document.getElementById("fanpage");
  const count = document.getElementById("page-count");
  const status = document.getElementById("availability");
  const operationStatus = document.getElementById("operation-status");
  const loadButton = document.getElementById("load-posts");
  const maxPosts = document.getElementById("max-posts");
  const contentList = document.getElementById("content-list");
  const selectAll = document.getElementById("select-all");
  const selectedCount = document.getElementById("selected-count");
  const loadStatus = document.getElementById("load-status");
  const auditWarning = document.getElementById("audit-warning");
  const localPagination = document.getElementById("local-pagination");
  const loadProgress = document.getElementById("load-progress");
  let postsLoading = false;
  let postsGeneration = 0;
  let loadedAudit = null;
  let displayPage = 0;
  let cacheTimer = null;
  let sessionTimer = null;
  const selected = new Set();
  const DISPLAY_SIZE = 50;
  let busy = false;
  let ready = false;
  let connected = false;
  let hasPages = false;
  let cooldownUntil = 0;
  let cooldownTimer = null;

  function message(text, kind = "info") {
    status.textContent = text;
    status.dataset.kind = kind;
  }

  function controls() {
    connectButton.disabled = !ready || busy || postsLoading || Date.now() < cooldownUntil;
    disconnectButton.disabled = !ready || busy || !connected;
    tokenInput.disabled = busy;
    fanpageSelect.disabled = busy || !hasPages;
    connectButton.textContent = busy ? "Sedang memproses…" : "Hubungkan";
    status.setAttribute("aria-busy", String(busy));
    loadButton.disabled = !ready || busy || postsLoading || !connected || !fanpageSelect.value || Date.now() < cooldownUntil;
    maxPosts.disabled = postsLoading || busy;
    loadButton.textContent = postsLoading ? "Sedang memuat…" : "Load Posts";
    loadStatus.setAttribute("aria-busy", String(postsLoading));
  }

  function renderSession(data) {
    const previousPage = fanpageSelect.value;
    connected = data.connected === true;
    hasPages = connected && Boolean(data.pages?.length);
    fanpageSelect.replaceChildren();
    for (const page of data.pages || []) {
      const option = document.createElement("option");
      option.value = page.id;
      option.textContent = `${page.name} (${page.id})`;
      fanpageSelect.appendChild(option);
    }
    if (!fanpageSelect.options.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = connected ? "Tidak ada Fanpage yang dikembalikan" : "Belum ada Fanpage yang terhubung";
      fanpageSelect.appendChild(option);
    }
    count.textContent = connected ? `${data.count} Fanpage dimuat dalam sesi ini.` : "Belum ada sesi aktif.";
    if (!connected || previousPage !== fanpageSelect.value) clearAudit();
    message(data.message, connected && data.pages?.length ? "success" : "info");
    clearTimeout(sessionTimer);
    if (connected && Number.isFinite(data.session_remaining_seconds)) {
      sessionTimer = setTimeout(() => {
        renderSession({connected: false, pages: [], count: 0, message: "Sesi kedaluwarsa. Hubungkan kembali."});
      }, Math.max(0, data.session_remaining_seconds) * 1000);
    }
    controls();
  }

  async function api(path, options = {}) {
    let response;
    try {
      response = await fetch(path, {
        ...options,
        credentials: "same-origin",
        cache: "no-store",
        redirect: "error",
        headers: {"X-Fanpage-Client": "1", ...options.headers},
      });
    } catch {
      throw new Error("Gangguan jaringan ke backend lokal. Muat ulang atau coba lagi nanti.");
    }
    let data;
    try {
      data = await response.json();
    } catch {
      throw new Error("Respons backend lokal tidak dapat dibaca.");
    }
    if (!response.ok) {
      applyCooldown(data.error);
      const error = new Error(data.error?.message || "Request tidak dapat diselesaikan.");
      error.code = data.error?.code;
      throw error;
    }
    return data;
  }

  function applyCooldown(error) {
    const seconds = error?.retry_after;
    if (Number.isFinite(seconds) && seconds > 0) {
      cooldownUntil = Math.max(cooldownUntil, Date.now() + seconds * 1000);
      clearTimeout(cooldownTimer);
      // UI timer only: no Meta request or automatic retry.
      cooldownTimer = setTimeout(controls, Math.min(cooldownUntil - Date.now() + 50, 2147483647));
    }
  }

  async function reconcileAfterFailure(error) {
    const failureMessage = error.message || "Gangguan jaringan ke backend lokal.";
    // Mutations may have completed even when their response was lost.
    // Reconcile from local memory only, never by repeating a Graph request.
    try { renderSession(await api("/api/session")); } catch { ready = false; }
    message(failureMessage, "error");
  }

  function clearAudit() {
    postsGeneration += 1;
    loadedAudit = null;
    displayPage = 0;
    selected.clear();
    clearTimeout(cacheTimer);
    contentList.replaceChildren();
    localPagination.replaceChildren();
    selectAll.disabled = true;
    selectAll.checked = false;
    selectAll.indeterminate = false;
    selectedCount.textContent = "Terpilih 0 konten.";
    loadStatus.textContent = "Belum ada konten yang dimuat untuk Fanpage ini.";
    loadProgress.setAttribute("value", "0");
  }

  function safeAuditURL(value, thumbnail = false) {
    if (typeof value !== "string" || value.length > 2048 || /[\s\\]/.test(value)) return null;
    try {
      const url = new URL(value);
      const domains = thumbnail ? ["fbcdn.net", "fbsbx.com"] : ["facebook.com", "fb.watch"];
      if (url.protocol !== "https:" || url.username || url.password || (url.port && url.port !== "443")) return null;
      if (!domains.some(domain => url.hostname === domain || url.hostname.endsWith(`.${domain}`))) return null;
      if ([...url.searchParams.keys()].some(key => /token|secret|authorization/i.test(key))) return null;
      return url.href;
    } catch { return null; }
  }

  function updateSelected() {
    const selectable = loadedAudit?.items.filter(item => item.selectable) || [];
    selectedCount.textContent = `Terpilih ${selected.size} dari ${selectable.length} konten yang dapat diaudit.`;
    selectAll.disabled = !loadedAudit?.bulk_selectable || selectable.length === 0;
    selectAll.checked = selectable.length > 0 && selected.size === selectable.length;
    selectAll.indeterminate = selected.size > 0 && selected.size < selectable.length;
  }

  function renderAuditPage() {
    contentList.replaceChildren();
    localPagination.replaceChildren();
    if (!loadedAudit) return;
    const offset = displayPage * DISPLAY_SIZE;
    let unknownHeading = false;
    for (const [localIndex, item] of loadedAudit.items.slice(offset, offset + DISPLAY_SIZE).entries()) {
      if (!item.published_at && !unknownHeading) {
        const heading = document.createElement("h3");
        heading.className = "date-unknown-heading";
        heading.textContent = "Tanggal publikasi tidak tersedia";
        contentList.appendChild(heading);
        unknownHeading = true;
      }
      const row = document.createElement("article");
      row.className = "audit-row";
      const number = document.createElement("span");
      number.textContent = String(offset + localIndex + 1);
      row.appendChild(number);
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = selected.has(item.key);
      checkbox.disabled = !item.selectable;
      checkbox.setAttribute("aria-label", `Pilih konten nomor ${offset + localIndex + 1}`);
      checkbox.addEventListener("change", () => {
        if (!item.selectable) return;
        if (checkbox.checked) selected.add(item.key); else selected.delete(item.key);
        updateSelected();
      });
      row.appendChild(checkbox);
      const kind = document.createElement("span");
      kind.textContent = item.kind;
      row.appendChild(kind);
      const date = document.createElement("time");
      date.textContent = item.published_wib;
      if (item.published_at) date.dateTime = item.published_at;
      row.appendChild(date);
      const text = document.createElement("p");
      text.textContent = item.text || "Tanpa teks.";
      row.appendChild(text);
      const thumbnailURL = safeAuditURL(item.thumbnail, true);
      if (thumbnailURL) {
        const thumbnail = document.createElement("img");
        thumbnail.src = thumbnailURL;
        thumbnail.alt = `Thumbnail ${item.kind}`;
        thumbnail.loading = "lazy";
        thumbnail.referrerPolicy = "no-referrer";
        row.appendChild(thumbnail);
      }
      const permalink = safeAuditURL(item.permalink);
      if (permalink) {
        const link = document.createElement("a");
        link.href = permalink;
        link.textContent = "Buka di Facebook";
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        row.appendChild(link);
      }
      contentList.appendChild(row);
    }
    const totalPages = Math.max(1, Math.ceil(loadedAudit.items.length / DISPLAY_SIZE));
    for (const [text, direction] of [["Sebelumnya", -1], ["Berikutnya", 1]]) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "secondary";
      button.textContent = text;
      button.disabled = direction < 0 ? displayPage === 0 : displayPage + 1 >= totalPages;
      button.addEventListener("click", () => {
        const target = displayPage + direction;
        if (target >= 0 && target < totalPages) { displayPage = target; renderAuditPage(); }
      });
      localPagination.appendChild(button);
    }
    const pageLabel = document.createElement("span");
    pageLabel.textContent = `Halaman ${displayPage + 1} dari ${totalPages} · ${DISPLAY_SIZE} konten per halaman · dari memori lokal`;
    localPagination.appendChild(pageLabel);
    updateSelected();
  }

  selectAll.addEventListener("change", () => {
    if (!loadedAudit?.bulk_selectable) return;
    selected.clear();
    if (selectAll.checked) for (const item of loadedAudit.items) if (item.selectable) selected.add(item.key);
    renderAuditPage();
  });
  fanpageSelect.addEventListener("change", () => {
    clearAudit();
    controls();
  });

  loadButton.addEventListener("click", async () => {
    if (!ready || busy || postsLoading || !connected || !fanpageSelect.value || Date.now() < cooldownUntil) return;
    const rawLimit = maxPosts.value;
    const limit = Number(rawLimit);
    if (!/^[0-9]+$/.test(rawLimit) || !Number.isSafeInteger(limit) || limit < 1 || limit > 5000) {
      loadStatus.textContent = "Maksimal postingan harus bilangan bulat antara 1 dan 5000.";
      maxPosts.focus();
      return;
    }
    const pageId = fanpageSelect.value;
    clearAudit();
    const generation = postsGeneration;
    postsLoading = true;
    controls();
    loadProgress.removeAttribute("value");
    loadStatus.textContent = "Sedang memuat kandidat postingan, foto, video dan Reels serta memeriksa ID Story…";
    operationStatus.textContent = "Load Posts sedang berjalan untuk Fanpage yang dipilih.";
    try {
      const data = await api("/api/posts/load", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({page_id: pageId, limit})});
      if (generation !== postsGeneration || !connected || pageId !== fanpageSelect.value || data.page_id !== pageId) return;
      loadedAudit = data;
      applyCooldown(data.error);
      const stateText = {partial: "Pengambilan tidak lengkap; hasil parsial. Pilihan massal dinonaktifkan.",
        empty: "Tidak ada konten eligible yang dikembalikan sumber API.",
        limit_reached: "Batas tampilan tercapai pada kandidat yang dimuat.",
        source_exhausted: "Sumber API yang dipakai sudah habis sebelum batas tercapai."}[data.state] || "Hasil belum dapat dipastikan.";
      loadStatus.textContent = `Ditemukan ${data.found} kandidat unik; ditampilkan ${data.displayed}. ${stateText}${data.error ? ` ${data.error.message}` : ""}`;
      auditWarning.textContent = data.warning;
      renderAuditPage();
      if (Number.isFinite(data.cache_ttl_seconds)) {
        cacheTimer = setTimeout(() => {
          if (generation !== postsGeneration) return;
          clearAudit();
          loadStatus.textContent = "Hasil audit kedaluwarsa. Gunakan Load Posts untuk memuat ulang.";
        }, Math.max(0, data.cache_ttl_seconds) * 1000);
      }
    } catch (error) {
      if (generation !== postsGeneration || pageId !== fanpageSelect.value) return;
      loadStatus.textContent = error.message;
      if (["session_expired", "load_cancelled"].includes(error.code)) await reconcileAfterFailure(error);
    } finally {
      postsLoading = false;
      if (generation === postsGeneration) loadProgress.setAttribute("value", "0");
      operationStatus.textContent = "Tidak ada penghapusan yang berjalan. Daftar hanya untuk audit.";
      controls();
    }
  });

  connectButton.addEventListener("click", async () => {
    if (!ready || busy || Date.now() < cooldownUntil) return;
    let token = tokenInput.value;
    if (!token.trim()) {
      message("Token Facebook kosong. Masukkan user access token.", "error");
      tokenInput.focus();
      return;
    }
    busy = true;
    clearAudit();
    controls();
    operationStatus.textContent = "Menghubungkan Facebook dan memuat daftar Fanpage…";
    message("Memuat seluruh daftar Fanpage. Mohon tunggu.");
    try {
      const request = api("/api/connect", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({token})});
      token = "";
      // Clear even on failure so the field does not retain a secret unnecessarily.
      tokenInput.value = "";
      renderSession(await request);
    } catch (error) {
      await reconcileAfterFailure(error);
    } finally {
      token = "";
      tokenInput.value = "";
      busy = false;
      operationStatus.textContent = "Belum ada operasi konten yang berjalan.";
      controls();
    }
  });

  disconnectButton.addEventListener("click", async () => {
    if (!ready || busy || !connected) return;
    busy = true;
    clearAudit();
    controls();
    try {
      renderSession(await api("/api/disconnect", {method: "POST"}));
      tokenInput.value = "";
      message("Koneksi diputuskan. Sesi backend dan cookie sesi telah dihapus.");
    } catch (error) {
      await reconcileAfterFailure(error);
    } finally {
      busy = false;
      controls();
    }
  });

  async function restore() {
    try {
      renderSession(await api("/api/session"));
      ready = true;
    } catch {
      message("Status sesi lokal tidak dapat dimuat. Muat ulang halaman untuk mencoba lagi.", "error");
    }
    controls();
  }
  restore();
})();
