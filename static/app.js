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
  const deleteReelButton = document.getElementById("delete-one-reel");
  const deleteTitle = document.getElementById("delete-dialog-title");
  const deleteWarning = document.getElementById("delete-warning");
  const deleteHelp = document.getElementById("delete-help");
  const deleteManualText = document.getElementById("delete-manual-text");
  const deleteModes = Object.freeze({
    page_post: Object.freeze({route:"/api/posts/delete", label:"Post", idLabel:"Page Post"}),
    reel_video: Object.freeze({route:"/api/reels/delete", label:"Reel", idLabel:"video Reel"})
  });
  const deleteDialog = document.getElementById("delete-dialog");
  const deleteDetails = document.getElementById("delete-details");
  const deleteManual = document.getElementById("delete-manual-check");
  const deleteCancel = document.getElementById("delete-cancel");
  const deleteConfirm = document.getElementById("delete-confirm");
  const deleteStatus = document.getElementById("delete-status");
  const deleteDiagnosis = document.getElementById("delete-diagnosis");
  const readLastButton = document.getElementById("delete-read-last");
  const bulkButton = document.getElementById("delete-selected");
  const bulkDialog = document.getElementById("bulk-dialog");
  const bulkDetails = document.getElementById("bulk-details");
  const bulkTechnical = document.getElementById("bulk-technical");
  const bulkTechnicalContent = document.getElementById("bulk-technical-content");
  const bulkManual = document.getElementById("bulk-manual-check");
  const bulkCancel = document.getElementById("bulk-cancel");
  const bulkConfirm = document.getElementById("bulk-confirm");
  const bulkStatus = document.getElementById("bulk-status");
  const bulkResults = document.getElementById("bulk-results");
  const bulkRead = document.getElementById("bulk-read");
  const bulkPreviewPagination = document.getElementById("bulk-preview-pagination");
  const bulkPagination = document.getElementById("bulk-pagination");
  let bulkPreviewPage = 0, bulkOffset = 0, bulkRevision = 0, bulkPageGeneration = 0;
  const accessPostButton = document.getElementById("access-post");
  const accessPostResult = document.getElementById("access-post-result");
  let accessChecking = false, accessGeneration = 0;
  let bulkPrepared = null, bulkPreparing = false, bulkSubmitting = false, bulkJob = null;
  let bulkPreviewGeneration = 0, bulkPollGeneration = 0;
  let bulkGeneration = 0, bulkTimer = null, bulkExpiry = null, bulkReading = false, bulkStopping = false;
  const bulkActive = () => bulkSubmitting || Boolean(bulkJob && !bulkJob.terminal);
  const bulkBusy = () => bulkActive() || bulkPreparing || Boolean(bulkPrepared);
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
    const deleting = deletePreparing || deleteExecuting || Boolean(deletion) || bulkBusy();
    connectButton.disabled = !ready || busy || postsLoading || dryLoading || deleting || Date.now() < cooldownUntil;
    disconnectButton.disabled = !ready || busy || !connected;
    tokenInput.disabled = busy;
    fanpageSelect.disabled = busy || !hasPages;
    connectButton.textContent = busy ? "Sedang memproses…" : "Hubungkan";
    status.setAttribute("aria-busy", String(busy));
    loadButton.disabled = !ready || busy || postsLoading || dryLoading || deleting || starting || stopping || !connected || !fanpageSelect.value || Date.now() < cooldownUntil;
    dryButton.disabled = !ready || busy || postsLoading || dryLoading || deleting || !connected || selected.size === 0;
    deleteButton.disabled = !ready || busy || postsLoading || dryLoading || deleting || !connected || Date.now() < cooldownUntil || !onePost();
    deleteReelButton.disabled = deleteButton.disabled;
    deleteConfirm.disabled = !deletion || !deleteManual.checked || deleteExecuting || Date.now() < cooldownUntil;
    deleteCancel.disabled = deleteExecuting;
    deleteManual.disabled = deleteExecuting;
    readLastButton.disabled = !ready || busy || !connected || readingLast;
    bulkButton.disabled = !ready || busy || postsLoading || dryLoading || deleting || !connected || !selected.size || Date.now() < cooldownUntil;
    const auditPost = onePost();
    const hasPostSource = auditPost?.sources?.some(source => source.source === "published_posts");
    accessPostButton.disabled = !ready || busy || postsLoading || dryLoading || deleting || accessChecking || !connected || Date.now() < cooldownUntil || !hasPostSource;
    accessPostButton.title = accessPostButton.disabled ? accessChecking ? "Pemeriksaan GET sedang berjalan." : "Pilih tepat satu konten dengan ID Page Post dari Load Posts sesi saat ini." : "GET saja; memeriksa sesi saat ini tanpa DELETE atau konfirmasi penghapusan.";
    bulkConfirm.disabled = !bulkPrepared || !bulkPrepared.supported || !bulkManual.checked || bulkSubmitting || Date.now() < cooldownUntil;
    bulkCancel.disabled = bulkSubmitting;
    bulkManual.disabled = bulkSubmitting;
    bulkRead.disabled = !ready || !connected || busy || bulkReading || bulkSubmitting || bulkPreparing || Boolean(bulkPrepared) || postsLoading || dryLoading || deleteExecuting;
    stopButton.disabled = (!postsLoading && !dryLoading && !bulkActive()) || stopping || bulkStopping || bulkSubmitting;
    stopButton.hidden = !postsLoading && !dryLoading && !bulkActive();
    stopButton.textContent = bulkActive() ? "Berhenti penghapusan" : dryLoading ? "Berhenti dry run" : postsLoading ? "Berhenti memuat" : "Berhenti";
    const deleteHint = !connected ? "Hubungkan Facebook." : deleting || postsLoading || dryLoading || busy ? "Tunggu proses selesai." : Date.now() < cooldownUntil ? "Tunggu jeda API." : !selected.size ? "Pilih konten dahulu." : "";
    bulkButton.title = bulkButton.disabled ? deleteHint : "";
    deleteButton.title = deleteButton.disabled ? deleteHint || "Pilih tepat satu Page Post." : "";
    deleteReelButton.title = deleteReelButton.disabled ? deleteHint || "Pilih tepat satu konten; backend memeriksa ID video Reel asli." : "Periksa satu Reel melalui alat diagnosis.";
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
    accessGeneration += 1;
    accessPostResult.replaceChildren();
    deleteReportGeneration += 1;
    deleteDiagnosis.replaceChildren();
    closeDelete();
    closeBulk();
    if (!connected) clearBulk();
    deleteSelectionHint = null;
    deleteStatus.textContent = "Pilih konten, lalu klik Hapus Terpilih, Hapus 1 Post, atau Uji Hapus 1 Reel (eksperimental).";
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
      partial: "Pengambilan tidak lengkap; hasil parsial.", cancelled: "Pemuatan dihentikan; hasil yang diterima tetap ditampilkan.",
      empty: "Tidak ada konten eligible yang dikembalikan sumber API.", limit_reached: "Batas tampilan tercapai pada kandidat yang dimuat.",
      source_exhausted: "Sumber API yang dipakai sudah habis sebelum batas tercapai."}[data.state] || "Hasil belum dapat dipastikan.";
    const errorSource = {published_posts: "Postingan publik", video_reels: "Reels", stories: "Pemeriksaan Story"}[data.error?.source];
    const counts = Number.isInteger(data.request_count) ? ` Diterima ${data.scanned} item; ${data.request_count} request termasuk retry. ${data.dated_count} bertanggal; ${data.undated_count} tanpa tanggal.` : "";
    loadStatus.textContent = `Ditemukan ${data.found} kandidat unik; ditampilkan ${data.displayed}.${counts} ${stateText}${data.error ? ` ${errorSource ? `${errorSource}: ` : ""}${data.error.message}` : ""}`;
    auditWarning.textContent = "Story teridentifikasi ditolak. Periksa setiap target melalui link sebelum konfirmasi penghapusan.";
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
    selectedCount.textContent = `Terpilih ${selected.size} dari ${selectable.length} konten.`;
    selectAll.disabled = !loadedAudit?.bulk_selectable || selectable.length === 0;
    selectAll.checked = selectable.length > 0 && selected.size === selectable.length;
    selectAll.indeterminate = selected.size > 0 && selected.size < selectable.length;
    const hint = JSON.stringify([fanpageSelect.value, ...selected]);
    if (hint !== deleteSelectionHint && !deleteExecuting && !deletePreparing && !deletion) {
      deleteSelectionHint = hint;
      deleteStatus.textContent = selected.size === 1
        ? "Klik Hapus 1 Post, Hapus Terpilih, atau Uji Hapus 1 Reel (eksperimental) untuk melanjutkan."
        : "Pilih konten untuk Hapus Terpilih; pilih tepat satu untuk Hapus 1 Post atau Uji Hapus 1 Reel.";
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
      const deleteState = {failed:"Gagal",uncertain:"Belum pasti",queued:"Menunggu",processing:"Memproses",blocked:"Dihentikan",cancelled:"Dihentikan"}[item.delete_status];
      if (deleteState) {
        const result = document.createElement("span"); result.className = "help";
        result.textContent = `Penghapusan: ${deleteState}`; row.appendChild(result);
      }
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
    const outcome = result.outcome;
    deleteStatus.textContent = `Proses selesai. Berhasil ${outcome === "succeeded" ? 1 : 0}, gagal ${outcome === "failed" ? 1 : 0}, belum pasti ${outcome === "uncertain" ? 1 : 0}, dihentikan ${outcome === "cancelled" || outcome === "blocked" ? 1 : 0}, tidak didukung ${outcome === "unsupported" ? 1 : 0}.`;
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
      `Fanpage: ${target.page_name || "tidak tersedia"} (Page ${target.page_id || "tidak tersedia"}); target ${target.operation_scope === "reel_video" || result.operation_scope === "reel_video" ? "video Reel asli (eksperimental)" : "Page Post"}: ${target.target_id || "tidak tersedia"}.`,
      `Hasil: ${result.outcome || "tidak tersedia"}; alasan: ${reason}. ${descriptions[reason] || "Lihat status lokal backend; tidak ada retry otomatis."}`,
      `HTTP: ${number(diagnostic.http_status)}; kode Meta: ${number(diagnostic.error_code)}; subkode: ${number(diagnostic.error_subcode)}; durasi: ${number(diagnostic.duration_ms)} ms.`,
      `Content-type: ${diagnostic.content_type || "tidak tersedia"}; JSON terparse: ${flag(diagnostic.json_parsed)}; tipe root JSON: ${diagnostic.json_type || "tidak tersedia"}; tipe success: ${diagnostic.success_type || "tidak tersedia"}; field error ada: ${flag(diagnostic.has_error_field)}.`,
      `Tahap: ${diagnostic.phase || "tidak tersedia"}; masalah respons: ${diagnostic.response_issue || "tidak tersedia"}; kode internal: ${diagnostic.internal_code || "tidak tersedia"}.`
    ];
    for (const key of ["error_message", "error_user_msg"]) {
      if (typeof diagnostic[key] === "string" && diagnostic[key]) lines.push(`${key}: ${diagnostic[key]}`);
    }
    if (result.cleanup_warning || diagnostic.cleanup_warning) lines.push("Peringatan cleanup: cache_cleanup_failed. Respons sukses Meta tetap sukses; tampilan target telah diinvalidasi.");
    for (const text of lines) {
      const line = document.createElement("p");
      line.textContent = text;
      deleteDiagnosis.appendChild(line);
    }
    if (outcome === "failed" && (target.operation_scope || "page_post") === "page_post") {
      const generation = deleteGeneration, reportGeneration = deleteReportGeneration;
      addFailureProbe(deleteDiagnosis, {last_result:true}, () => connected && generation === deleteGeneration && reportGeneration === deleteReportGeneration);
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

  async function prepareDelete(operationScope) {
    const mode = deleteModes[operationScope];
    const item = onePost();
    if (!ready || !connected || busy || postsLoading || dryLoading || deletePreparing || deleteExecuting || deletion || bulkBusy() || Date.now() < cooldownUntil || !item) return;
    const pageId = fanpageSelect.value;
    const generation = ++deleteGeneration;
    deletePreparing = true;
    controls();
    deleteStatus.textContent = `Menyiapkan konfirmasi satu ${mode.label} dari metadata audit lokal; belum mengirim DELETE.`;
    try {
      const data = await api(`${mode.route}/prepare`, {method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({page_id:pageId, keys:[item.key]})});
      if (generation !== deleteGeneration || !connected || pageId !== fanpageSelect.value || onePost()?.key !== item.key) return;
      const targetScope = data.target?.operation_scope || "page_post";
      if (targetScope !== operationScope || data.target?.page_id !== pageId || data.target?.key !== item.key || !data.target?.target_id || !safeAuditURL(data.target.permalink)) throw new Error("Snapshot konfirmasi tidak sesuai target pilihan atau cakupan operasi.");
      deletion = Object.freeze({...data, operationScope, target:Object.freeze({...data.target})});
      deleteTitle.textContent = `Konfirmasi ${operationScope === "reel_video" ? "Uji Hapus" : "Hapus"} 1 ${mode.label}`;
      deleteWarning.textContent = operationScope === "reel_video"
        ? "Satu penghapusan nyata dengan ID video Reel asli dari hasil Load Posts. Keberhasilan target sebelumnya tidak menjamin semua Reel dapat dihapus. Penghapusan tidak dapat dibatalkan."
        : "Post ini akan dihapus dari Fanpage. Penghapusan tidak dapat dibatalkan.";
      deleteHelp.textContent = operationScope === "reel_video"
        ? "Buka link Facebook dan pastikan ID video asli serta Page cocok dengan Reel yang ingin dihapus. Periksa bahwa target benar-benar Reel, bukan Story. Tidak ada retry otomatis atau percobaan ID alternatif."
        : "Buka link Facebook dan periksa bahwa target adalah Page Post yang memang ingin dihapus, bukan Story. Ini konfirmasi manual Anda; aplikasi belum memverifikasi seluruh cakupan Story secara otomatis.";
      deleteManualText.textContent = `Saya sudah memeriksa link, ID asli dan Page, memastikan target adalah ${operationScope === "reel_video" ? "Reel" : "Page Post"} yang dimaksud, bukan Story, serta ingin menghapusnya secara permanen.`;
      deleteManual.checked = false;
      deleteDetails.replaceChildren();
      for (const text of [`Fanpage: ${data.target.page_name} (Page ${data.target.page_id})`, `Jenis: ${data.target.kind}`, `ID ${mode.idLabel} asli: ${data.target.target_id}`,
        `Tanggal: ${data.target.published_wib}`, `Teks: ${data.target.text || "Tanpa teks."}`, `Konfirmasi berlaku ${data.expires_in_seconds} detik.`]) {
        const line = document.createElement("p");
        line.textContent = text;
        deleteDetails.appendChild(line);
      }
      const thumbnail = safeAuditURL(data.target.thumbnail, true);
      if (thumbnail) {
        const image = document.createElement("img");
        image.src = thumbnail; image.alt = `Pratinjau ${mode.label}`; image.className = "delete-preview-thumbnail"; image.loading = "lazy"; image.referrerPolicy = "no-referrer";
        deleteDetails.appendChild(image);
      }
      const link = document.createElement("a");
      link.textContent = `Buka ${mode.label} di Facebook untuk pemeriksaan manual`;
      link.href = safeAuditURL(data.target.permalink);
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      deleteDetails.appendChild(link);
      const scope = document.createElement("p");
      scope.textContent = operationScope === "reel_video" ? "Target hanya satu objek video Reel asli." : "Operasi menargetkan Page Post, bukan jaminan menghapus objek foto/video/Reel secara terpisah.";
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
  }
  deleteButton.addEventListener("click", () => prepareDelete("page_post"));
  deleteReelButton.addEventListener("click", () => prepareDelete("reel_video"));

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
    const mode = deleteModes[snapshot.operationScope];
    if (!mode || (snapshot.target.operation_scope || "page_post") !== snapshot.operationScope || snapshot.target.page_id !== fanpageSelect.value) return;
    const generation = deleteGeneration;
    const pageId = fanpageSelect.value;
    deleteExecuting = true;
    deleteReportGeneration += 1;
    clearOldDryReport();
    deleteDiagnosis.replaceChildren();
    clearTimeout(deleteTimer);
    controls();
    deleteStatus.textContent = `Mengirim satu percobaan penghapusan ${mode.label}. Tidak ada retry otomatis.`;
    const current = () => generation === deleteGeneration && connected && pageId === fanpageSelect.value;
    try {
      // Never use jobMutation (which retries). Execution receives only confirmation ID.
      let result = await api(`${mode.route}/execute`, {method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({confirmation_id:snapshot.confirmation_id, manual_target_confirmed:true})});
      if (!current()) return;
      applyCooldown(result);
      const resultScope = result.target?.operation_scope || result.operation_scope || "page_post";
      const targetMatches = resultScope === snapshot.operationScope && result.target?.key === snapshot.target.key && result.target?.target_id === snapshot.target.target_id && (!result.target?.page_id || result.target.page_id === pageId);
      if (result.outcome === "succeeded" && !targetMatches) result = {...result, outcome:"uncertain", reason:"response_unverified", target:snapshot.target};
      if (result.outcome === "succeeded" && targetMatches) {
        const videos = new Set(snapshot.target.related_video_ids || []);
        if (snapshot.operationScope === "reel_video") videos.add(snapshot.target.target_id);
        const postId = snapshot.operationScope === "page_post" ? snapshot.target.target_id : snapshot.target.linked_post_id;
        const removed = item => item.key === snapshot.target.key || item.sources?.some(source =>
          (source.source === "video_reels" && videos.has(source.object_id)) ||
          (postId && source.source === "published_posts" && source.object_id === postId));
        const removedCount = loadedAudit.items.filter(removed).length;
        for (const item of loadedAudit.items.filter(removed)) selected.delete(item.key);
        loadedAudit.items = loadedAudit.items.filter(item => !removed(item));
        loadedAudit.displayed = loadedAudit.items.length;
        loadedAudit.found = Math.max(loadedAudit.displayed, loadedAudit.found - removedCount);
        loadedAudit.dated_count = loadedAudit.items.filter(item => item.published_at).length;
        loadedAudit.undated_count = loadedAudit.displayed - loadedAudit.dated_count;
        selected.delete(snapshot.target.key);
        displayPage = Math.min(displayPage, Math.max(0, Math.ceil(loadedAudit.items.length / DISPLAY_SIZE)-1));
        loadStatus.textContent = `Ditampilkan ${loadedAudit.displayed} konten setelah satu ${mode.label} berhasil dihapus.`;
        renderAuditPage();
      }
      deleteReportGeneration += 1;
      renderDeleteResult(result);
    } catch (error) {
      if (!current()) return;
      deleteStatus.textContent = error.code ? error.message : `Hasil penghapusan belum dapat dipastikan karena respons backend tidak diterima. ${mode.label} tetap ditampilkan; periksa Facebook manual. Tidak ada retry otomatis.`;
    } finally {
      deleteExecuting = false;
      if (current()) closeDelete();
      controls();
    }
  });
  function closeBulk() {
    bulkPreviewGeneration += 1;
    clearTimeout(bulkExpiry);
    bulkPrepared = null;
    bulkPreparing = false;
    bulkDialog.close();
    bulkManual.checked = false;
    bulkDetails.replaceChildren();
    bulkPreviewPagination.replaceChildren();
  }
  function clearBulk() {
    bulkGeneration += 1; clearTimeout(bulkTimer); closeBulk();
    bulkJob = null; bulkSubmitting = false;
    bulkOffset = 0; bulkRevision = 0; bulkPageGeneration += 1;
    bulkPagination.replaceChildren();
    bulkResults.replaceChildren(); bulkStatus.textContent = "";
    if (bulkTechnical) { bulkTechnical.hidden = true; bulkTechnical.open = false; }
    bulkTechnicalContent?.replaceChildren();
  }
  function bulkLine(parent, text) {
    const line = document.createElement("p"); line.textContent = text; parent.appendChild(line);
  }
  function addFailureProbe(parent, payload, currentCheck, existingButton = null, existingResult = null, onState = null) {
    const button = existingButton || document.createElement("button");
    button.type = "button"; button.className = "secondary";
    if (!existingButton) button.textContent = "Periksa Penyebab Gagal";
    button.title = "GET saja, maksimal lima request termasuk retry. Tidak mencoba penghapusan ulang.";
    const result = existingResult || document.createElement("div"); result.setAttribute("role", "status");
    let checking = false;
    button.addEventListener("click", async () => {
      if (checking || (existingButton && button.disabled)) return;
      const context = typeof payload === "function" ? payload() : {payload,current:currentCheck};
      if (!context || !context.current()) return;
      const current = context.current;
      checking = true; button.disabled = true; result.replaceChildren();
      if (onState) onState(true);
      bulkLine(result, "Memeriksa izin dan metadata melalui GET; tidak mengirim DELETE.");
      try {
        const data = await api("/api/delete-diagnosis", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(context.payload)});
        if (!current()) return;
        applyCooldown(data); result.replaceChildren();
        if (data.target) bulkLine(result, `Fanpage: ${data.target.page_name || data.target.page_id || ""}; ID Page Post: ${data.target.target_id || "Tidak diketahui"}.`);
        bulkLine(result, `${data.request_count}/${data.request_budget} request aktual. ${data.message || "GET tidak membuktikan izin DELETE atau keadaan token lama."}`);
        const labels = {granted:"Diberikan",declined:"Ditolak",expired:"Kedaluwarsa",unknown:"Tidak diketahui"};
        for (const [permission, state] of Object.entries(data.permissions || {})) bulkLine(result, `${permission}: ${labels[state] || "Tidak diketahui"}.`);
        bulkLine(result, `Tasks Page: ${Array.isArray(data.tasks) ? data.tasks.join(", ") || "Daftar kosong" : "Tidak diketahui"}.`);
        bulkLine(result, `Pemilik dari field from: ${data.owner ? `${data.owner.id} (${data.owner.name || "Nama tidak tersedia"}); ${data.owner.matches_page ? "sesuai Page" : "berbeda dari Page"}` : "Tidak diketahui"}. Field from bukan bukti izin atau kepemilikan aplikasi.`);
        bulkLine(result, `ID Post terbaca: ${data.post_id || "Tidak diketahui"}; status_type: ${data.status_type || "Tidak diketahui"}; application: ${data.application?.id || "Tidak diketahui"}; object_id media: ${data.media_object_id || "Tidak diketahui"}. Application tidak dibandingkan dengan app penerbit token karena informasi itu tidak tersedia.`);
        bulkLine(result, `ID foto dari relasi API: ${(data.photo_ids || []).join(", ") || "Tidak tersedia"}. ID tidak diambil dari URL atau akhiran Post.`);
        for (const relation of data.relations || []) bulkLine(result, `Relasi attachment ${relation.type || "Jenis tidak diketahui"}: ${relation.id}.`);
        const details = document.createElement("details"); const summary = document.createElement("summary"); summary.textContent = "Rincian pemeriksaan GET"; details.appendChild(summary);
        for (const check of data.checks || []) {
          bulkLine(details, `${check.source}: ${check.status}; ${check.reason || ""}; fields: ${check.requested_fields || ""}${check.truncated ? "; hanya satu halaman, data berikutnya tidak diperiksa" : ""}.`);
          for (const field of ["http_status","error_code","error_subcode","error_message","error_user_msg","failed_fields","unavailable_fields"])
            if (check[field] !== undefined && check[field] !== null) bulkLine(details, `${field}: ${Array.isArray(check[field]) ? check[field].join(", ") : check[field]}`);
        }
        result.appendChild(details);
      } catch (error) {
        if (current()) { result.replaceChildren(); bulkLine(result, error.message || "Diagnosis tidak tersedia. Jangan ulang penghapusan untuk diagnosis."); }
      } finally { checking = false; if (onState) onState(false); else if (current()) button.disabled = false; }
    });
    if (parent) { parent.appendChild(button); parent.appendChild(result); }
  }
  addFailureProbe(null, () => {
    const item = onePost(), pageId = fanpageSelect.value, generation = accessGeneration;
    if (!item || !connected) return null;
    return {payload:{page_id:pageId,keys:[item.key]},current:() => connected && generation === accessGeneration && pageId === fanpageSelect.value && onePost()?.key === item.key};
  }, null, accessPostButton, accessPostResult, active => { accessChecking = active; controls(); });
  function bulkTarget(parent, item) {
    const details = item.details || item;
    bulkLine(parent, `Fanpage: ${details.page_name || item.page_id || ""}; target ${details.operation_scope === "reel_video" ? "video/Reel" : "Page Post"}: ${details.target_id || item.delete_target || "tidak tersedia"}; jenis: ${details.kind || ""}; teks: ${details.text || "Tanpa teks"}.`);
    const url = safeAuditURL(details.permalink);
    if (url) {
      const link = document.createElement("a"); link.textContent = "Periksa target di Facebook";
      link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer"; parent.appendChild(link);
    }
  }
  function pageButtons(parent, page, totalPages, change) {
    parent.replaceChildren();
    if (totalPages <= 1) return;
    for (const [label, target] of [["Sebelumnya", page - 1], ["Berikutnya", page + 1]]) {
      const button = document.createElement("button"); button.type = "button"; button.className = "secondary";
      button.textContent = label; button.disabled = target < 0 || target >= totalPages;
      button.addEventListener("click", () => { if (!button.disabled) return change(target); });
      parent.appendChild(button);
      if (target < page) bulkLine(parent, `Halaman ${page + 1} dari ${totalPages}; 50 target per halaman.`);
    }
  }
  function renderBulkPreview() {
    const data = bulkPrepared;
    if (!data) return;
    bulkDetails.replaceChildren();
    bulkLine(bulkDetails, `Dipilih ${data.selected}; didukung ${data.supported}; ditolak ${data.unsupported}; duplikat ${data.duplicates}. Maksimal ${data.max_targets || 5000} target. Konfirmasi berlaku ${data.expires_in_seconds} detik.`);
    const items = data.items || [];
    for (const item of items.slice(bulkPreviewPage * DISPLAY_SIZE, (bulkPreviewPage + 1) * DISPLAY_SIZE)) {
      const row = document.createElement("details"); row.open = true;
      const summary = document.createElement("summary"); summary.textContent = `${item.supported ? "Didukung" : "DITOLAK"}: ${item.key}`; row.appendChild(summary);
      bulkTarget(row, item); bulkLine(row, item.message || item.reason || "Target Page Post"); bulkDetails.appendChild(row);
    }
    pageButtons(bulkPreviewPagination, bulkPreviewPage, Math.ceil(items.length / DISPLAY_SIZE), page => { bulkPreviewPage = page; renderBulkPreview(); });
  }
  function renderBulkPagination(data) {
    const total = data.result_total ?? data.total ?? 0;
    pageButtons(bulkPagination, Math.floor(bulkOffset / DISPLAY_SIZE), Math.ceil(total / DISPLAY_SIZE), async page => {
      const generation = bulkGeneration, id = bulkJob?.job_id, requestGeneration = ++bulkPageGeneration;
      bulkPollGeneration += 1; clearTimeout(bulkTimer);
      bulkOffset = page * DISPLAY_SIZE;
      try {
        const result = await api(`/api/bulk-delete/jobs/${encodeURIComponent(id)}?offset=${bulkOffset}&since=${bulkRevision}`);
        if (generation === bulkGeneration && requestGeneration === bulkPageGeneration && connected && bulkJob?.job_id === id) { renderBulk(result); pollBulk(); }
      } catch (error) { if (generation === bulkGeneration && requestGeneration === bulkPageGeneration) { bulkStatus.textContent = error.message; pollBulk(); } }
    });
  }
  function renderBulkTiming(data) {
    const cooldown = data.cooldown;
    const pacing = data.pacing;
    const seconds = Number.isFinite(data.cooldown_seconds) ? data.cooldown_seconds : cooldown?.remaining_seconds;
    const active = Number.isFinite(seconds) && seconds > 0;
    const reasons = {
      local_usage_saturated: "Penggunaan API tinggi (jeda lokal)",
      retry_after: "Meta meminta menunggu", meta_http_429: "Meta meminta menunggu", meta_rate_limit: "Meta meminta menunggu",
      local_policy: "Jeda kebijakan lokal", local_scope_capacity: "Menunggu kapasitas lokal",
    };
    if (active) {
      if (reasons[cooldown?.reason]) bulkStatus.textContent += ` ${reasons[cooldown.reason]}.`;
      bulkStatus.textContent += ` Jeda API ${Math.ceil(seconds)} detik.`;
    } else if (!data.terminal && pacing?.usage_known && Number.isFinite(pacing.last_usage?.percent) && pacing.last_usage.percent >= 90 && Number.isFinite(pacing.interval_seconds)) {
      bulkStatus.textContent += ` Penggunaan API tinggi; interval request ${pacing.interval_seconds} detik.`;
    }
    if (!bulkTechnical || !bulkTechnicalContent) return;
    bulkTechnicalContent.replaceChildren();
    const technicalCooldown = cooldown || data.last_cooldown;
    bulkTechnical.hidden = !technicalCooldown && !pacing;
    // Display only known metadata fields, never raw headers or token identifiers.
    const sources = new Set(["x-app-usage", "x-business-use-case-usage", "retry-after", "http_status", "error_code", "local_policy"]);
    if (technicalCooldown) {
      const detail = technicalCooldown;
      if (!active) bulkLine(bulkTechnicalContent, "Jeda terakhir (sudah berakhir).");
      bulkLine(bulkTechnicalContent, `Alasan: ${reasons[detail.reason] ? detail.reason : "tidak tersedia"}; sumber: ${sources.has(detail.source) ? detail.source : "tidak tersedia"}; scope: ${["token", "global"].includes(detail.scope) ? detail.scope : "tidak tersedia"}; kebijakan: ${["local", "meta"].includes(detail.policy) ? detail.policy : "tidak tersedia"}.`);
      const end = typeof detail.expires_at === "string" && /^\d{4}-\d{2}-\d{2}T[\d:.]+(?:Z|\+00:00)$/.test(detail.expires_at) ? detail.expires_at : "tidak tersedia";
      bulkLine(bulkTechnicalContent, `Akhir jeda (UTC): ${end}; sisa: ${active ? Math.ceil(seconds) : 0} detik.`);
      if (Number.isFinite(detail.usage)) bulkLine(bulkTechnicalContent, `Penggunaan saat jeda: ${detail.usage}%.`);
      if (Number.isFinite(detail.error_code)) bulkLine(bulkTechnicalContent, `Kode error Meta: ${detail.error_code}.`);
    }
    if (pacing) {
      if (Number.isFinite(pacing.interval_seconds)) bulkLine(bulkTechnicalContent, `Interval request: ${pacing.interval_seconds} detik.`);
      const usage = pacing.last_usage;
      if (!pacing.usage_known || !usage) bulkLine(bulkTechnicalContent, "Usage terbaru tidak tersedia.");
      else {
        bulkLine(bulkTechnicalContent, `Sumber usage: ${sources.has(usage.source) ? usage.source : "tidak tersedia"}; penggunaan: ${Number.isFinite(usage.percent) ? `${usage.percent}%` : "tidak tersedia"}.`);
        for (const metric of ["call_count", "total_time", "total_cputime"])
          if (Number.isFinite(usage.metrics?.[metric])) bulkLine(bulkTechnicalContent, `${metric}: ${usage.metrics[metric]}%.`);
        if (typeof usage.observed_at === "string" && /^\d{4}-\d{2}-\d{2}T[\d:.]+(?:Z|\+00:00)$/.test(usage.observed_at)) bulkLine(bulkTechnicalContent, `Usage diamati (UTC): ${usage.observed_at}.`);
      }
    }
  }
  function renderBulk(data) {
    if (bulkJob?.job_id !== data.job_id) { bulkOffset = 0; bulkRevision = 0; }
    bulkOffset = data.offset ?? bulkOffset;
    bulkRevision = data.reset_required ? data.revision : (data.next_revision ?? data.revision ?? bulkRevision);
    bulkJob = data;
    const counts = data.counts || {};
    const states = {queued:"Menunggu antrean", running:"Penghapusan berjalan", cooldown:"Menunggu jeda API", stopping:"Menghentikan proses", completed:"Proses selesai", cancelled:"Proses dihentikan", failed:"Proses selesai", paused_needs_reconnect:"Hubungkan kembali Facebook"};
    const unsupported = data.unsupported ?? counts.unsupported ?? 0;
    bulkStatus.textContent = `${(data.pages || []).map(page => page.name).join(", ")}: ${states[data.state] || "Status belum tersedia"}; selesai ${data.completed}/${data.total}. Berhasil ${counts.succeeded || 0}, gagal ${counts.failed || 0}, belum pasti ${counts.uncertain || 0}, dihentikan ${(counts.cancelled || 0) + (counts.blocked || 0)}, tidak didukung ${unsupported}.${data.terminal ? "" : ` Menunggu ${counts.queued || 0}, memproses ${counts.processing || 0}.`}`;
    renderBulkTiming(data);
    bulkResults.replaceChildren();
    for (const item of (data.items || []).slice(0, DISPLAY_SIZE)) {
      const row = document.createElement("details");
      const summary = document.createElement("summary"); summary.textContent = `${item.details?.kind || "Konten"} ${item.delete_target || item.key}: ${{succeeded:"Berhasil",failed:"Gagal",uncertain:"Belum pasti",blocked:"Dihentikan",cancelled:"Dihentikan",queued:"Menunggu",processing:"Memproses",unsupported:"Tidak didukung"}[item.status] || "Belum pasti"}`; row.appendChild(summary);
      bulkTarget(row, item);
      bulkLine(row, `Status: ${item.status}; percobaan: ${item.attempts || 0}; alasan: ${(item.reasons || []).join(", ")}; cleanup: ${item.cleanup_warning || "tidak ada"}.`);
      const diag = item.diagnostic || {};
      // Deliberate allowlist: never dump response bodies, headers or tokens.
      for (const key of ["reason","http_status","error_code","error_subcode","duration_ms","content_type","json_parsed","json_type","success_type","has_error_field","phase","response_issue","internal_code","error_message","error_user_msg"])
        if (diag[key] !== undefined && diag[key] !== null) bulkLine(row, `${key}: ${diag[key]}`);
      if (data.terminal && item.status === "failed" && (item.details?.operation_scope || "page_post") === "page_post") {
        const generation = bulkGeneration, jobId = data.job_id;
        addFailureProbe(row, {job_id:jobId,key:item.key}, () => connected && generation === bulkGeneration && bulkJob?.job_id === jobId);
      }
      bulkResults.appendChild(row);
    }
    const succeededKeys = new Set(), succeededPosts = new Set(), succeededVideos = new Set();
    for (const item of data.changes || data.items || []) {
      if (item.status !== "succeeded" || loadedAudit?.page_id !== item.page_id) continue;
      succeededKeys.add(item.key);
      const post = item.details?.operation_scope === "reel_video" ? item.details.linked_post_id : item.delete_target;
      if (post) succeededPosts.add(post);
      for (const video of item.details?.related_video_ids || []) succeededVideos.add(video);
    }
    if (loadedAudit && succeededKeys.size) {
      loadedAudit.items = loadedAudit.items.filter(target => {
        const matches = succeededKeys.has(target.key) || (target.sources || []).some(source =>
          (source.source === "published_posts" && succeededPosts.has(source.object_id)) ||
          (source.source === "video_reels" && succeededVideos.has(source.object_id)));
        if (matches) selected.delete(target.key);
        return !matches;
      });
    }
    if (loadedAudit) {
      const statusKeys = new Map(), statusPosts = new Map(), statusVideos = new Map();
      for (const item of [...(data.items || []), ...(data.changes || [])]) {
        if (item.page_id !== loadedAudit.page_id || item.status === "succeeded") continue;
        statusKeys.set(item.key, item.status);
        const post = item.details?.operation_scope === "reel_video" ? item.details.linked_post_id : item.delete_target;
        if (post) statusPosts.set(post, item.status);
        for (const video of item.details?.related_video_ids || []) statusVideos.set(video, item.status);
      }
      for (const target of loadedAudit.items) {
        let state = statusKeys.get(target.key);
        for (const source of target.sources || []) {
          const aliasState = source.source === "published_posts" ? statusPosts.get(source.object_id) : source.source === "video_reels" ? statusVideos.get(source.object_id) : null;
          if (aliasState) state = aliasState;
        }
        if (state) target.delete_status = state;
      }
    }
    renderBulkPagination(data);
    if (loadedAudit) {
      loadedAudit.displayed = loadedAudit.items.length;
      displayPage = Math.min(displayPage, Math.max(0, Math.ceil(loadedAudit.items.length / DISPLAY_SIZE) - 1));
      renderAuditPage();
    }
    controls();
  }
  function pollBulk() {
    const pollGeneration = ++bulkPollGeneration;
    clearTimeout(bulkTimer);
    if (!connected || !bulkJob || (bulkJob.terminal && !bulkJob.has_more_changes)) return;
    const generation = bulkGeneration, id = bulkJob.job_id;
    bulkTimer = setTimeout(async () => {
      if (generation !== bulkGeneration || pollGeneration !== bulkPollGeneration || !connected) return;
      try {
        const data = await api(`/api/bulk-delete/jobs/${encodeURIComponent(id)}?offset=${bulkOffset}&since=${bulkRevision}`);
        if (generation === bulkGeneration && pollGeneration === bulkPollGeneration && connected && bulkJob?.job_id === id) { renderBulk(data); pollBulk(); }
      } catch (error) {
        if (generation !== bulkGeneration) return;
        if (error.code === "session_expired") {
          await reconcileAfterFailure(error);
          operationStatus.textContent = "Sesi kedaluwarsa. Hubungkan kembali Facebook untuk melanjutkan.";
        } else bulkStatus.textContent = error.message + " Gunakan Baca Antrean Penghapusan; konfirmasi tidak diulang.";
      }
    }, 1000);
  }
  bulkButton.addEventListener("click", async () => {
    if (bulkButton.disabled || bulkBusy() || !connected || !selected.size) return;
    const pageId = fanpageSelect.value, generation = bulkGeneration, previewGeneration = ++bulkPreviewGeneration;
    const keys = [...selected];
    bulkPreparing = true; controls();
    bulkStatus.textContent = "Menyiapkan daftar target lokal. Belum ada DELETE dikirim.";
    try {
      const data = await api("/api/bulk-delete/prepare", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({selections:[{page_id:pageId,keys}]})});
      if (!connected || generation !== bulkGeneration || pageId !== fanpageSelect.value || previewGeneration !== bulkPreviewGeneration || !bulkPreparing) return;
      bulkPrepared = data; bulkManual.checked = false; bulkPreviewPage = 0;
      renderBulkPreview();
      bulkDialog.showModal();
      bulkExpiry = setTimeout(() => { if (!bulkSubmitting && bulkPrepared === data) { closeBulk(); bulkStatus.textContent = "Konfirmasi kedaluwarsa. Periksa daftar kembali."; controls(); } }, Math.max(0,data.expires_in_seconds)*1000);
    } catch (error) { if (generation === bulkGeneration && previewGeneration === bulkPreviewGeneration && connected) bulkStatus.textContent = error.message; }
    finally { if (previewGeneration === bulkPreviewGeneration) bulkPreparing = false; controls(); }
  });
  bulkManual.addEventListener("change", controls);
  bulkCancel.addEventListener("click", () => { if (!bulkSubmitting) { closeBulk(); controls(); } });
  bulkDialog.addEventListener("cancel", event => { event.preventDefault(); if (!bulkSubmitting) { closeBulk(); controls(); } });
  bulkConfirm.addEventListener("click", async () => {
    if (!bulkPrepared || !bulkPrepared.supported || !bulkManual.checked || bulkSubmitting || !connected || Date.now() < cooldownUntil) return;
    const snapshot = bulkPrepared, generation = bulkGeneration;
    bulkSubmitting = true; clearTimeout(bulkExpiry); clearOldDryReport(); controls();
    bulkStatus.textContent = "Mengirim konfirmasi satu kali. Jangan ulang penghapusan jika respons hilang.";
    try {
      const data = await api("/api/bulk-delete/confirm", {method:"POST", headers:{"Content-Type":"application/json"},body:JSON.stringify({confirmation_id:snapshot.confirmation_id,manual_targets_confirmed:true})});
      if (generation !== bulkGeneration || !connected) return;
      closeBulk(); renderBulk(data); pollBulk();
    } catch (error) { if (generation === bulkGeneration && connected) { closeBulk(); bulkStatus.textContent = error.message + " Hasil belum pasti. Gunakan Baca Antrean Penghapusan tanpa mengulang konfirmasi."; } }
    finally { if (generation === bulkGeneration) bulkSubmitting = false; controls(); }
  });
  bulkRead.addEventListener("click", async () => {
    if (bulkRead.disabled || bulkReading || !connected) return;
    const generation = bulkGeneration; bulkReading = true; controls();
    try {
      const data = await api("/api/bulk-delete/jobs/active");
      if (generation !== bulkGeneration || !connected) return;
      const job = data.jobs?.[0] || data.last_job;
      if (job) { renderBulk(job); pollBulk(); }
      else bulkStatus.textContent = "Antrean lokal tidak tersedia; hasil mungkin hilang setelah restart. Jangan ulang DELETE untuk diagnosis.";
    } catch (error) { if (generation === bulkGeneration) bulkStatus.textContent = error.message; }
    finally { bulkReading = false; controls(); }
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
    if (!ready || busy || postsLoading || starting || stopping || bulkBusy() || !connected || !fanpageSelect.value || Date.now() < cooldownUntil) return;
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
        operationStatus.textContent = "Daftar siap. Pilih konten untuk dihapus.";
      } else if (!activeJob) postsLoading = false;
      controls();
    }
  });

  stopButton.addEventListener("click", async () => {
    if (bulkActive()) {
      if (!bulkJob || bulkSubmitting || bulkStopping) return;
      const generation = bulkGeneration;
      bulkStopping = true; bulkPollGeneration += 1; clearTimeout(bulkTimer); controls();
      try {
        const data = await api(`/api/bulk-delete/jobs/${encodeURIComponent(bulkJob.job_id)}/stop`, {method:"POST"});
        if (generation === bulkGeneration && connected) { renderBulk(data); pollBulk(); }
      } catch (error) { if (generation === bulkGeneration) bulkStatus.textContent = error.message + " Baca antrean lokal; jangan ulang konfirmasi."; }
      finally { bulkStopping = false; controls(); }
      return;
    }
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
    if (!ready || busy || postsLoading || dryLoading || bulkBusy() || !connected || selected.size === 0) return;
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
