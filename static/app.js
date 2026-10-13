"use strict";

(() => {
  const tokenInput = document.getElementById("facebook-token");
  const connectButton = document.getElementById("connect");
  const disconnectButton = document.getElementById("disconnect");
  const fanpageSelect = document.getElementById("fanpage");
  const fanpageSearch = document.getElementById("fanpage-search");
  const fanpageSearchStatus = document.getElementById("fanpage-search-status");
  const sourceDiagnostics = document.getElementById("source-diagnostics");
  let sessionPages = [];
  let activePageId = "";
  const count = document.getElementById("page-count");
  const status = document.getElementById("availability");
  const operationStatus = document.getElementById("operation-status");
  const loadButton = document.getElementById("load-posts");
  const stopButton = document.getElementById("stop-load");
  const dryButton = document.getElementById("dry-run");
  const dryStatus = document.getElementById("dry-status");
  const dryResults = document.getElementById("dry-results");
  const dryPagination = document.getElementById("dry-pagination");
  const deleteButton = document.getElementById("delete-one");
  const deleteDialog = document.getElementById("delete-dialog");
  const deleteDetails = document.getElementById("delete-details");
  const deleteManual = document.getElementById("delete-manual-check");
  const deleteCancel = document.getElementById("delete-cancel");
  const deleteConfirm = document.getElementById("delete-confirm");
  const deleteStatus = document.getElementById("delete-status");
  const deleteDiagnosis = document.getElementById("delete-diagnosis");
  const readLastButton = document.getElementById("delete-read-last");
  let readingLast = false;
  let deleteReportGeneration = 0;
  let deletion = null;
  let deletePreparing = false;
  let deleteExecuting = false;
  let deleteGeneration = 0;
  let deleteTimer = null;
  let deleteSelectionHint = null;
  let lastDryId = null;
  let dryOffset = 0;
  let dryLoading = false;
  let dryJobId = null;
  let dryGeneration = 0;
  let dryTimer = null;
  let wakeDry = null;
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
  let activeJob = null;
  let continuationTimer = null;
  let wakeContinuation = null;
  let stopping = false;
  let starting = false;
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
    const deleting = deletePreparing || deleteExecuting || Boolean(deletion);
    connectButton.disabled = !ready || busy || postsLoading || dryLoading || deleting || Date.now() < cooldownUntil;
    disconnectButton.disabled = !ready || busy || !connected;
    tokenInput.disabled = busy;
    fanpageSelect.disabled = busy || !hasPages;
    connectButton.textContent = busy ? "Sedang memproses…" : "Hubungkan";
    status.setAttribute("aria-busy", String(busy));
    loadButton.disabled = !ready || busy || postsLoading || dryLoading || deleting || starting || stopping || !connected || !fanpageSelect.value || Date.now() < cooldownUntil;
    dryButton.disabled = !ready || busy || postsLoading || dryLoading || deleting || !connected || selected.size === 0;
    deleteButton.disabled = !ready || busy || postsLoading || dryLoading || deleting || !connected || Date.now() < cooldownUntil || !onePost();
    deleteConfirm.disabled = !deletion || !deleteManual.checked || deleteExecuting || Date.now() < cooldownUntil;
    deleteCancel.disabled = deleteExecuting;
    deleteManual.disabled = deleteExecuting;
    readLastButton.disabled = !ready || busy || !connected || readingLast;
    stopButton.disabled = (!postsLoading && !dryLoading) || stopping;
    stopButton.textContent = dryLoading ? "Berhenti dry run" : postsLoading ? "Berhenti memuat" : "Berhenti";
    maxPosts.disabled = postsLoading || busy;
    loadButton.textContent = postsLoading ? "Sedang memuat…" : "Load Posts";
    loadStatus.setAttribute("aria-busy", String(postsLoading));
  }

  function renderFanpageOptions() {
    const query = fanpageSearch.value.trim().toLowerCase();
    const matches = sessionPages.filter(page => page.name.toLowerCase().includes(query) || page.id.toLowerCase().includes(query));
    const active = sessionPages.find(page => page.id === activePageId);
    const pinned = active && !matches.some(page => page.id === activePageId);
    fanpageSelect.replaceChildren();
    for (const page of pinned ? [active, ...matches] : matches) {
      const option = document.createElement("option");
      option.value = page.id;
      option.textContent = `${page.name} (${page.id})${pinned && page.id === activePageId ? " — aktif, di luar pencarian" : ""}`;
      fanpageSelect.appendChild(option);
    }
    if (!fanpageSelect.options.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = connected ? "Tidak ada Fanpage yang dikembalikan" : "Belum ada Fanpage yang terhubung";
      fanpageSelect.appendChild(option);
    }
    fanpageSelect.value = activePageId;
    fanpageSearchStatus.textContent = `${matches.length} hasil pencarian dari ${sessionPages.length} Fanpage.${matches.length === 0 && query ? " Tidak ditemukan." : ""}${pinned ? " Fanpage aktif tetap dipilih meskipun tidak cocok dengan pencarian." : ""}`;
  }

  function renderSession(data) {
    const previousPage = activePageId;
    connected = data.connected === true;
    hasPages = connected && Boolean(data.pages?.length);
    sessionPages = connected ? data.pages || [] : [];
    activePageId = sessionPages.some(page => page.id === previousPage) ? previousPage : sessionPages[0]?.id || "";
    renderFanpageOptions();
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
      error.sources = data.sources;
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
    deleteReportGeneration += 1;
    deleteDiagnosis.replaceChildren();
    closeDelete();
    deleteSelectionHint = null;
    deleteStatus.textContent = "Pilih tepat satu konten. Backend akan memeriksa ID Page Post sebelum konfirmasi; bulk tetap nonaktif.";
    cancelDry();
    dryResults.replaceChildren();
    dryPagination.replaceChildren();
    lastDryId = null;
    dryOffset = 0;
    dryStatus.textContent = "Dry run belum berjalan. Tidak ada penghapusan nyata.";
    cancelContinuation();
    const oldJob = activeJob;
    activeJob = null;
    if (oldJob && !oldJob.terminal) void stopBackend(oldJob.id);
    postsGeneration += 1;
    loadedAudit = null;
    displayPage = 0;
    selected.clear();
    clearTimeout(cacheTimer);
    contentList.replaceChildren();
    localPagination.replaceChildren();
    sourceDiagnostics.replaceChildren();
    selectAll.disabled = true;
    selectAll.checked = false;
    selectAll.indeterminate = false;
    selectedCount.textContent = "Terpilih 0 konten.";
    loadStatus.textContent = "Belum ada konten yang dimuat untuk Fanpage ini.";
    loadProgress.setAttribute("value", "0");
  }

  function wakeDryWait() {
    clearTimeout(dryTimer);
    if (wakeDry) wakeDry();
    wakeDry = null;
  }

  function cancelDry() {
    dryGeneration += 1;
    wakeDryWait();
    const id = dryJobId;
    dryJobId = null;
    dryLoading = false;
    if (id) void api(`/api/dry-run/jobs/${encodeURIComponent(id)}/stop`, {method:"POST"}).catch(() => {});
  }

  function renderDry(data) {
    dryStatus.hidden = false;
    dryResults.hidden = false;
    dryPagination.hidden = false;
    lastDryId = data.job_id;
    dryOffset = data.offset || 0;
    const names=(data.pages || []).map(page => page.name).join(", ");
    const stateText={queued:"Menunggu antrean",running:"Dry run sedang berjalan",completed:"Pemeriksaan selesai",
      cancelled:"Pemeriksaan dibatalkan",failed:"Pemeriksaan gagal",paused_needs_reconnect:"Pekerjaan dijeda; pemulihan sesi belum tersedia"};
    const progress=data.counts ? ` Antrean ${data.counts.queued}, memeriksa ${data.counts.processing}, gagal ${data.counts.failed}, uncertain ${data.counts.uncertain}, dibatalkan ${data.counts.cancelled}.` : "";
    dryStatus.textContent = `${names}: dipilih ${data.selected}, layak ${data.valid}, belum layak/ambigu ${data.ambiguous}, duplikat ${data.duplicates}; diperiksa ${data.checked}.${progress} ${stateText[data.state] || "Status belum tersedia"}. Tidak ada konten dihapus.`;
    const reasons={story_exclusion_unverified:"Pengecualian Story seluruh kandidat belum terbukti",delete_permissions_unverified:"Izin penghapusan belum terbukti",
      success_semantics_unverified:"Semantik sukses penghapusan belum terverifikasi",reel_delete_semantics_unverified:"Penghapusan Reel belum terverifikasi",
      ambiguous_post_identity:"Relasi identitas Post ambigu",identified_story:"Story teridentifikasi",local_identity_checked:"Identitas lokal diperiksa",
      interrupted_processing:"Pemeriksaan terinterupsi",cancelled_processing:"Dihentikan saat processing",checkpoint_failed:"Checkpoint gagal",
      cooldown:"Menunggu cooldown bersama",rate_limited:"Scope dijeda karena rate limit",session_expired:"Sesi kedaluwarsa",
      session_disconnected:"Sesi diputus",shutdown:"Server berhenti",queue_duration_limit:"Batas durasi antrean",
      executor_error:"Pemeriksaan lokal terinterupsi",local_check_failed:"Pemeriksaan lokal gagal",
      token_invalid:"Token invalid; tidak diulang",permission_denied:"Izin ditolak; tidak diulang",
      timeout_after_send:"Hasil uncertain setelah pengiriman; tidak diulang",timeout_before_send:"Timeout sebelum pengiriman",
      temporary_before_send:"Kegagalan sementara sebelum pengiriman",executor_contract_invalid:"Kontrak executor tidak valid"};
    dryResults.replaceChildren();
    for (const target of data.items || []) {
      const row=document.createElement("p");
      row.textContent=`Page ${target.page_id || ""} · key ${target.key} · object ${target.object_id} · usulan target ${target.delete_target || "belum tersedia"} · ${target.status}: ${target.eligible ? "layak untuk pemeriksaan" : "belum layak dihapus"}; ${target.reasons.map(reason => reasons[reason] || "Hambatan pemeriksaan lokal").join("; ")}.`;
      dryResults.appendChild(row);
    }
    dryPagination.replaceChildren();
    if (data.total > 50) {
      for (const [label,delta] of [["Sebelumnya",-50],["Berikutnya",50]]) {
        const button=document.createElement("button");
        button.type="button";
        button.textContent=label;
        button.disabled=delta<0 ? dryOffset===0 : dryOffset+50>=data.total;
        button.addEventListener("click",async()=>{
          const generation=dryGeneration;
          try {
            const page=await api(`/api/dry-run/jobs/${encodeURIComponent(lastDryId)}?offset=${dryOffset+delta}`);
            if (generation===dryGeneration) renderDry(page);
          } catch (error) { if (generation===dryGeneration) dryStatus.textContent=error.message; }
        });
        dryPagination.appendChild(button);
      }
    }
  }

  function cancelContinuation() {
    clearTimeout(continuationTimer);
    if (wakeContinuation) wakeContinuation();
    wakeContinuation = null;
  }

  function waitLocally(seconds) {
    return new Promise(resolve => {
      wakeContinuation = resolve;
      continuationTimer = setTimeout(() => { wakeContinuation = null; resolve(); }, Math.min(seconds * 1000 + 50, 2147483647));
    });
  }

  async function jobMutation(path, body, allowed = () => true) {
    // A lost response repeats exactly the same revision, never a fresh start.
    for (let attempt = 0; attempt < 3; attempt += 1) {
      if (!allowed()) {
        const error = new Error("Pemuatan dibatalkan.");
        error.code = "load_cancelled";
        throw error;
      }
      try {
        return await api(path, {method: "POST", headers: {"Content-Type": "application/json"}, ...(body ? {body: JSON.stringify(body)} : {})});
      } catch (error) {
        if (error.code || attempt === 2) throw error;
      }
    }
  }

  async function stopBackend(id) {
    stopping = true;
    controls();
    try { return await jobMutation(`/api/posts/jobs/${encodeURIComponent(id)}/stop`); }
    catch { return null; } // Expiry/disconnect also cancels on the backend.
    finally { stopping = false; controls(); }
  }

  function showAudit(data) {
    loadedAudit = data;
    const keys = new Set(data.items.filter(item => item.selectable).map(item => item.key));
    for (const key of selected) if (!keys.has(key)) selected.delete(key);
    displayPage = Math.min(displayPage, Math.max(0, Math.ceil(data.items.length / DISPLAY_SIZE) - 1));
    applyCooldown(data.error);
    for (const error of data.errors || []) applyCooldown(error);
    renderSourceDiagnostics(data.sources);
    const stateText = {running: "Pengambilan bertahap sedang berjalan.", cooldown: "Menunggu cooldown lokal; tidak meminta Meta selama menunggu.",
      partial: "Pengambilan tidak lengkap; hasil parsial. Pilihan hanya untuk audit.", cancelled: "Pemuatan dihentikan; hasil diterima hanya untuk audit.",
      empty: "Tidak ada konten eligible yang dikembalikan sumber API.", limit_reached: "Batas tampilan tercapai pada kandidat yang dimuat.",
      source_exhausted: "Sumber API yang dipakai sudah habis sebelum batas tercapai."}[data.state] || "Hasil belum dapat dipastikan.";
    const errorSource = {published_posts: "Postingan publik", video_reels: "Reels", stories: "Pemeriksaan Story"}[data.error?.source];
    const counts = Number.isInteger(data.request_count) ? ` Diterima ${data.scanned} item; ${data.request_count} request termasuk retry. ${data.dated_count} bertanggal; ${data.undated_count} tanpa tanggal.` : "";
    loadStatus.textContent = `Ditemukan ${data.found} kandidat unik; ditampilkan ${data.displayed}.${counts} ${stateText}${data.error ? ` ${errorSource ? `${errorSource}: ` : ""}${data.error.message}` : ""}`;
    auditWarning.textContent = data.warning;
    loadProgress.setAttribute("value", String(Math.min(100, data.displayed / (data.limit || 1) * 100)));
    renderAuditPage();
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
    if (deletion && (selected.size !== 1 || !selected.has(deletion.target.key))) closeDelete();
    const selectable = loadedAudit?.items.filter(item => item.selectable) || [];
    selectedCount.textContent = `Terpilih ${selected.size} dari ${selectable.length} konten yang dapat diaudit.`;
    selectAll.disabled = !loadedAudit?.bulk_selectable || selectable.length === 0;
    selectAll.checked = selectable.length > 0 && selected.size === selectable.length;
    selectAll.indeterminate = selected.size > 0 && selected.size < selectable.length;
    const hint = JSON.stringify([fanpageSelect.value, ...selected]);
    if (hint !== deleteSelectionHint && !deleteExecuting && !deletePreparing && !deletion) {
      deleteSelectionHint = hint;
      deleteStatus.textContent = selected.size === 1
        ? "Klik Hapus 1 Post untuk memeriksa kelayakan ID Page Post di backend. Belum ada DELETE dikirim."
        : "Pilih tepat satu konten untuk Hapus 1 Post.";
    }
    controls();
  }

  function renderSourceDiagnostics(sources = []) {
    sourceDiagnostics.replaceChildren();
    const names = {published_posts: "Postingan publik", video_reels: "Reels", stories: "Pemeriksaan Story"};
    const statuses = {running: "Sedang memuat", cooldown: "Menunggu cooldown", limit_reached: "Batas tercapai", source_exhausted: "Sumber habis", failed: "Gagal", cancelled: "Dibatalkan", not_needed: "Tidak diperlukan"};
    const reasons = {unique_limit: "batas konten unik gabungan tercapai", source_exhausted: "tidak ada halaman berikutnya",
      story_verification_budget: "batas pemeriksaan Story satu halaman; histori tidak ditelusuri",
      no_eligible_candidates: "tidak ada kandidat yang perlu diperiksa", page_limit: "batas halaman lokal",
      pagination_repeated: "cursor atau halaman berulang", invalid_pagination: "indikator pagination tidak valid",
      invalid_response: "respons sumber invalid", upstream_error: "respons atau kegagalan layanan sumber tidak dapat digunakan", permission_denied: "akses atau izin ditolak",
      token_invalid: "token tidak valid atau kedaluwarsa", timeout: "timeout", cooldown: "cooldown",
      rate_limited: "rate limit", network_error: "gangguan jaringan", session_expired: "sesi kedaluwarsa",
      load_cancelled: "koneksi diputus", user_stopped: "pengguna menghentikan pemuatan", step_timeout: "batas waktu langkah; cursor tersimpan",
      request_retry_limit: "batas tiga percobaan halaman lintas langkah",
      meta_request_timeout: "request Meta timeout setelah retry terbatas", step_timeout_limit: "batas pengulangan timeout langkah",
      step_request_limit: "batas request langkah", job_request_limit: "batas request total pekerjaan", job_step_limit: "batas langkah total",
      job_timeout: "batas waktu total pekerjaan", audit_capacity: "batas memori kandidat", internal_error: "kegagalan internal lokal"};
    const countText = value => Number.isInteger(value) && value >= 0 ? String(value) : "tidak tersedia";
    for (const source of sources) {
      const reason = source.stop_reason?.startsWith("stopped_after_")
        ? `pekerjaan dihentikan setelah ${reasons[source.stop_reason.slice(14)] || "kegagalan sumber lain"}`
        : reasons[source.stop_reason] || "alasan belum tersedia";
      const row = document.createElement("p");
      row.textContent = `${names[source.source] || "Sumber"}: ${countText(source.requests)} request (termasuk retry), ${countText(source.pages)} halaman, ${countText(source.received)} item diterima. ${statuses[source.status] || "Status belum tersedia"} — ${reason}.`;
      sourceDiagnostics.appendChild(row);
    }
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

  function onePost() {
    if (selected.size !== 1) return null;
    const item = loadedAudit?.items.find(item => selected.has(item.key));
    // Selection only. Backend prepare owns provenance/identity/Story decisions.
    return item?.selectable ? item : null;
  }

  function closeDelete() {
    deleteGeneration += 1;
    clearTimeout(deleteTimer);
    deletion = null;
    deletePreparing = false;
    // An already-sent request is not retried; generation protects new Page/session UI.
    deleteDialog.close();
    deleteManual.checked = false;
    deleteDetails.replaceChildren();
  }

  function clearOldDryReport() {
    cancelDry();
    lastDryId = null;
    dryOffset = 0;
    dryStatus.textContent = "";
    dryStatus.hidden = true;
    dryResults.replaceChildren();
    dryResults.hidden = true;
    dryPagination.replaceChildren();
    dryPagination.hidden = true;
  }

  function renderDeleteResult(result) {
    deleteStatus.textContent = result.message;
    deleteDiagnosis.replaceChildren();
    const diagnostic = result.diagnostic || {};
    const reason = result.reason || diagnostic.reason || "not_available";
    const descriptions = {
      timeout_after_send: "Batas waktu request habis setelah percobaan dimulai; operasi Meta mungkin sudah terjadi.",
      network_after_send: "Gangguan transport/jaringan setelah percobaan dimulai; hasil operasi Meta belum terkonfirmasi.",
      response_unverified: "Respons diterima tetapi tidak memenuhi kontrak sukses yang ketat.",
      interrupted: "Percobaan terinterupsi; hasil tidak boleh diasumsikan berhasil atau diulang otomatis.",
      internal_error: "Kegagalan internal aplikasi; lihat kode dan tahap diagnosis aman.",
      success_boolean_true: "Meta mengonfirmasi sukses dengan kontrak respons yang benar.",
      request_in_progress: "Percobaan masih berjalan di backend; pembacaan ini tidak mengirim request Meta.",
      token_invalid: "Token ditolak; tidak diulang otomatis.", permission_denied: "Izin ditolak; tidak diulang otomatis.",
      rate_limited: "Meta membatasi penggunaan; cooldown bersama tetap berlaku.", cooldown: "Cooldown sebelum dispatch.",
      timeout: "Batas waktu lokal sebelum dispatch.", not_available: "Alasan percobaan belum tersimpan atau tidak tersedia."
    };
    const number = value => Number.isSafeInteger(value) && value >= 0 ? String(value) : "tidak tersedia";
    const flag = value => value === true ? "ya" : value === false ? "tidak" : "tidak tersedia";
    const target = result.target || {};
    const lines = [
      `Fanpage: ${target.page_name || "tidak tersedia"} (Page ${target.page_id || "tidak tersedia"}); target Page Post: ${target.target_id || "tidak tersedia"}.`,
      `Hasil: ${result.outcome || "tidak tersedia"}; alasan: ${reason}. ${descriptions[reason] || "Lihat status lokal backend; tidak ada retry otomatis."}`,
      `HTTP: ${number(diagnostic.http_status)}; kode Meta: ${number(diagnostic.error_code)}; subkode: ${number(diagnostic.error_subcode)}; durasi: ${number(diagnostic.duration_ms)} ms.`,
      `Content-type: ${diagnostic.content_type || "tidak tersedia"}; JSON terparse: ${flag(diagnostic.json_parsed)}; tipe root JSON: ${diagnostic.json_type || "tidak tersedia"}; tipe success: ${diagnostic.success_type || "tidak tersedia"}; field error ada: ${flag(diagnostic.has_error_field)}.`,
      `Tahap: ${diagnostic.phase || "tidak tersedia"}; masalah respons: ${diagnostic.response_issue || "tidak tersedia"}; kode internal: ${diagnostic.internal_code || "tidak tersedia"}.`
    ];
    for (const text of lines) {
      const line = document.createElement("p");
      line.textContent = text;
      deleteDiagnosis.appendChild(line);
    }
  }

  readLastButton.addEventListener("click", async () => {
    if (!ready || !connected || busy || readingLast) return;
    const generation = deleteGeneration;
    const reportGeneration = ++deleteReportGeneration;
    readingLast = true;
    controls();
    try {
      const data = await api("/api/posts/delete/last");
      if (generation !== deleteGeneration || reportGeneration !== deleteReportGeneration || !connected) return;
      if (data.result) renderDeleteResult(data.result);
      else { deleteDiagnosis.replaceChildren(); deleteStatus.textContent = data.message; }
    } catch (error) {
      if (generation === deleteGeneration && reportGeneration === deleteReportGeneration) deleteStatus.textContent = error.message;
    } finally { readingLast = false; controls(); }
  });

  deleteButton.addEventListener("click", async () => {
    const item = onePost();
    if (!ready || !connected || busy || postsLoading || dryLoading || deletePreparing || deleteExecuting || deletion || Date.now() < cooldownUntil || !item) return;
    const pageId = fanpageSelect.value;
    const generation = ++deleteGeneration;
    deletePreparing = true;
    controls();
    deleteStatus.textContent = "Menyiapkan konfirmasi satu Post dari metadata audit lokal; belum mengirim DELETE.";
    try {
      const data = await api("/api/posts/delete/prepare", {method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({page_id:pageId, keys:[item.key]})});
      if (generation !== deleteGeneration || !connected || pageId !== fanpageSelect.value || onePost()?.key !== item.key) return;
      if (data.target?.page_id !== pageId || data.target?.key !== item.key || !safeAuditURL(data.target.permalink)) throw new Error("Snapshot konfirmasi tidak sesuai target pilihan.");
      deletion = data;
      deleteManual.checked = false;
      deleteDetails.replaceChildren();
      for (const text of [`Fanpage: ${data.target.page_name}`, `Jenis: ${data.target.kind}`, `ID Page Post asli: ${data.target.target_id}`,
        `Tanggal: ${data.target.published_wib}`, `Teks: ${data.target.text || "Tanpa teks."}`, `Konfirmasi berlaku ${data.expires_in_seconds} detik.`]) {
        const line = document.createElement("p");
        line.textContent = text;
        deleteDetails.appendChild(line);
      }
      const link = document.createElement("a");
      link.textContent = "Buka Post di Facebook untuk pemeriksaan manual";
      link.href = safeAuditURL(data.target.permalink);
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      deleteDetails.appendChild(link);
      const scope = document.createElement("p");
      scope.textContent = "Operasi menargetkan Page Post, bukan jaminan menghapus objek foto/video/Reel secara terpisah.";
      deleteDetails.appendChild(scope);
      deleteDialog.showModal();
      deleteStatus.textContent = "Menunggu pemeriksaan manual dan Konfirmasi Hapus. Belum ada DELETE dikirim.";
      deleteTimer = setTimeout(() => {
        if (generation !== deleteGeneration || deleteExecuting) return;
        closeDelete();
        deleteStatus.textContent = "Konfirmasi kedaluwarsa. Periksa target lagi sebelum menyiapkan konfirmasi baru.";
        controls();
      }, Math.max(0, data.expires_in_seconds) * 1000);
    } catch (error) {
      if (generation === deleteGeneration) deleteStatus.textContent = error.message;
    } finally {
      if (generation === deleteGeneration) deletePreparing = false;
      controls();
    }
  });

  deleteManual.addEventListener("change", controls);
  deleteCancel.addEventListener("click", () => {
    if (deleteExecuting) return;
    closeDelete();
    deleteStatus.textContent = "Konfirmasi dibatalkan; tidak ada DELETE dikirim.";
    controls();
  });
  deleteDialog.addEventListener("cancel", event => {
    event.preventDefault();
    if (!deleteExecuting) { closeDelete(); controls(); }
  });
  deleteConfirm.addEventListener("click", async () => {
    if (!deletion || deleteExecuting || !deleteManual.checked || !connected || Date.now() < cooldownUntil || onePost()?.key !== deletion.target.key) return;
    const snapshot = deletion;
    const generation = deleteGeneration;
    const pageId = fanpageSelect.value;
    deleteExecuting = true;
    deleteReportGeneration += 1;
    clearOldDryReport();
    deleteDiagnosis.replaceChildren();
    clearTimeout(deleteTimer);
    controls();
    deleteStatus.textContent = "Mengirim satu percobaan penghapusan Post. Tidak ada retry otomatis.";
    const current = () => generation === deleteGeneration && connected && pageId === fanpageSelect.value;
    try {
      // Never use jobMutation (which retries). Execution receives only confirmation ID.
      const result = await api("/api/posts/delete/execute", {method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({confirmation_id:snapshot.confirmation_id, manual_target_confirmed:true})});
      if (!current()) return;
      applyCooldown(result);
      if (result.outcome === "succeeded" && result.target?.key === snapshot.target.key && result.target?.target_id === snapshot.target.target_id) {
        loadedAudit.items = loadedAudit.items.filter(item => item.key !== snapshot.target.key);
        loadedAudit.displayed = loadedAudit.items.length;
        loadedAudit.found = Math.max(loadedAudit.displayed, loadedAudit.found - 1);
        loadedAudit.dated_count = loadedAudit.items.filter(item => item.published_at).length;
        loadedAudit.undated_count = loadedAudit.displayed - loadedAudit.dated_count;
        selected.delete(snapshot.target.key);
        displayPage = Math.min(displayPage, Math.max(0, Math.ceil(loadedAudit.items.length / DISPLAY_SIZE)-1));
        loadStatus.textContent = `Ditampilkan ${loadedAudit.displayed} konten setelah satu Post berhasil dihapus.`;
        renderAuditPage();
      }
      deleteReportGeneration += 1;
      renderDeleteResult(result);
    } catch (error) {
      if (!current()) return;
      deleteStatus.textContent = error.code ? error.message : "Hasil penghapusan belum dapat dipastikan karena respons backend tidak diterima. Post tetap ditampilkan; periksa Facebook manual. Tidak ada retry otomatis.";
    } finally {
      deleteExecuting = false;
      if (current()) closeDelete();
      controls();
    }
  });
  fanpageSelect.addEventListener("change", () => {
    if (activePageId !== fanpageSelect.value) {
      activePageId = fanpageSelect.value;
      clearAudit();
    }
    renderFanpageOptions();
    controls();
  });
  fanpageSearch.addEventListener("input", () => {
    renderFanpageOptions();
    controls();
  });

  loadButton.addEventListener("click", async () => {
    if (!ready || busy || postsLoading || starting || stopping || !connected || !fanpageSelect.value || Date.now() < cooldownUntil) return;
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
      const current = () => generation === postsGeneration && connected && pageId === fanpageSelect.value;
      let data;
      starting = true;
      try { data = await jobMutation("/api/posts/jobs", {page_id: pageId, limit}, current); }
      finally { starting = false; controls(); }
      if (!current()) {
        if (data.job_id && !data.terminal) await stopBackend(data.job_id);
        return;
      }
      if (data.job_id) activeJob = {id: data.job_id, terminal: data.terminal};
      while (current() && data.page_id === pageId) {
        if (activeJob) activeJob.terminal = data.terminal;
        showAudit(data);
        if (!data.job_id || data.terminal) break;
        let wait = Math.max(data.resume_after_seconds || 0, (cooldownUntil - Date.now()) / 1000);
        if (Number.isFinite(data.job_remaining_seconds)) wait = Math.min(wait, Math.max(1, data.job_remaining_seconds));
        if (wait > 0) await waitLocally(wait);
        if (!current()) return;
        data = await jobMutation(`/api/posts/jobs/${encodeURIComponent(data.job_id)}/step`, {revision: data.revision}, current);
      }
      if (!current()) return;
      if (Number.isFinite(data.cache_ttl_seconds)) {
        cacheTimer = setTimeout(() => {
          if (generation !== postsGeneration) return;
          clearAudit();
          loadStatus.textContent = "Hasil audit kedaluwarsa. Gunakan Load Posts untuk memuat ulang.";
          deleteStatus.textContent = "Audit kedaluwarsa. Muat ulang konten sebelum Hapus 1 Post.";
        }, Math.max(0, data.cache_ttl_seconds) * 1000);
      }
    } catch (error) {
      if (generation !== postsGeneration || pageId !== fanpageSelect.value) return;
      loadStatus.textContent = error.message;
      renderSourceDiagnostics(error.sources);
      if (["session_expired", "load_cancelled"].includes(error.code)) await reconcileAfterFailure(error);
    } finally {
      if (generation === postsGeneration) {
        postsLoading = false;
        activeJob = null;
        operationStatus.textContent = "Tidak ada penghapusan yang berjalan. Daftar hanya untuk audit.";
      } else if (!activeJob) postsLoading = false;
      controls();
    }
  });

  stopButton.addEventListener("click", async () => {
    if (dryLoading) {
      const id=dryJobId;
      dryGeneration += 1;
      const generation=dryGeneration;
      wakeDryWait();
      dryJobId=null;
      dryLoading=false;
      controls();
      if (id) {
        try {
          const data=await api(`/api/dry-run/jobs/${encodeURIComponent(id)}/stop`,{method:"POST"});
          if (generation===dryGeneration) renderDry(data);
        } catch { if (generation===dryGeneration) dryStatus.textContent="Kelanjutan dry run dihentikan; backend belum mengonfirmasi. Tidak ada penghapusan."; }
      }
      return;
    }
    if (!postsLoading || stopping) return;
    const job = activeJob;
    postsGeneration += 1;
    const generation = postsGeneration;
    activeJob = null;
    cancelContinuation();
    postsLoading = false;
    controls();
    const data = job ? await stopBackend(job.id) : null;
    if (generation !== postsGeneration) return;
    if (data && data.page_id === fanpageSelect.value) showAudit(data);
    else loadStatus.textContent = job ? "Kelanjutan dihentikan di browser; backend belum mengonfirmasi penghentian. Pekerjaan tunduk pada batas waktu." : "Pemuatan dihentikan. Halaman yang diterima hanya untuk audit.";
    operationStatus.textContent = "Pemuatan dihentikan; penghapusan tidak tersedia.";
  });

  dryButton.addEventListener("click", async () => {
    if (!ready || busy || postsLoading || dryLoading || !connected || selected.size === 0) return;
    const pageId=fanpageSelect.value;
    const generation=++dryGeneration;
    dryLoading=true;
    controls();
    try {
      let data=await api("/api/dry-run/jobs",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({selections:[{page_id:pageId,keys:[...selected]}]})});
      const current=()=>generation===dryGeneration && connected && pageId===fanpageSelect.value;
      if (!current()) {
        void api(`/api/dry-run/jobs/${encodeURIComponent(data.job_id)}/stop`,{method:"POST"}).catch(()=>{});
        return;
      }
      dryJobId=data.job_id;
      while (current()) {
        renderDry(data);
        if (data.terminal) break;
        await new Promise(resolve=>{wakeDry=resolve;dryTimer=setTimeout(()=>{wakeDry=null;resolve();},1000);});
        if (!current()) return;
        data=await api(`/api/dry-run/jobs/${encodeURIComponent(dryJobId)}?offset=${dryOffset}`);
      }
    } catch (error) {
      if (generation===dryGeneration) dryStatus.textContent=error.message;
    } finally {
      if (generation===dryGeneration) {dryLoading=false;dryJobId=null;controls();}
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
