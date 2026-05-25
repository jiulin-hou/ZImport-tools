const CHUNK = 10 * 1024 * 1024; // 10MB
const STORAGE_KEY = "zimport-tools.pending";
let pollTimer = null;
let identity = null; // {account, is_admin}

function $(id) { return document.getElementById(id); }

// HTML-escape any string before injecting via innerHTML. Used wherever a value
// originates from the user (filenames, Zimbra-returned error text, etc.).
function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function showOnly(id) {
  for (const k of ["loading", "error", "main"]) {
    $(k).classList.toggle("hidden", k !== id);
  }
}

function showError(msg) {
  $("errorMsg").textContent = msg;
  showOnly("error");
}

function toast(msg, kind) {
  const host = $("toastHost");
  if (!host) { alert(msg); return; }
  const el = document.createElement("div");
  el.className = "toast" + (kind ? " " + kind : "");
  el.textContent = msg;
  host.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

// All fetches go through this helper so the CSRF header is consistent.
async function apiFetch(path, opts = {}) {
  const headers = new Headers(opts.headers || {});
  headers.set("X-Zimport-CSRF", "1");
  return fetch(path, { ...opts, headers });
}

async function probeSession() {
  showOnly("loading");
  let r;
  try {
    r = await apiFetch("api/me");
  } catch (e) {
    showError("网络异常,无法连接 ZImport-tools。");
    return;
  }
  if (r.status === 401) {
    showError("请先在 Zimbra Web 登录,然后回到此页签。");
    return;
  }
  if (r.status === 503) {
    showError("Zimbra 暂不可达,请稍后再试。");
    return;
  }
  if (!r.ok) {
    showError("无法识别身份(状态码 " + r.status + ")。");
    return;
  }
  identity = await r.json();
  $("who").textContent = identity.account;
  $("adminBox").classList.toggle("hidden", !identity.is_admin);
  await loadFolders();
  showOnly("main");
  refreshTasks();
  renderResumeBanner();
}

async function loadFolders() {
  const ta = $("targetAccount").value.trim();
  const url = "api/folders" + (ta ? "?account=" + encodeURIComponent(ta) : "");
  const r = await apiFetch(url);
  const sel = $("folder");
  const previous = sel.value;
  sel.innerHTML = "";
  if (!r.ok) {
    const opt = document.createElement("option");
    opt.value = "Inbox";
    opt.textContent = "Inbox";
    sel.appendChild(opt);
    return;
  }
  const data = await r.json();
  const folders = data.folders || [];
  for (const f of folders) {
    const opt = document.createElement("option");
    opt.value = f.path || f;
    opt.textContent = f.path || f;
    sel.appendChild(opt);
  }
  if (previous && folders.includes(previous)) sel.value = previous;
}

$("targetAccount").addEventListener("change", loadFolders);
$("retryBtn").onclick = probeSession;
$("refreshBtn").onclick = refreshTasks;

// Resume edge case: if the user has a pending upload, they need to re-pick
// the *same* file to continue. Browsers won't fire 'change' when the
// selected file is identical to the current input value, so we clear the
// value on click before the file picker opens. We only do this when there's
// a pending upload — otherwise users would lose their already-picked files
// just by clicking the input again.
$("files").addEventListener("click", function () {
  if (loadPending()) this.value = "";
});

$("newFolderBtn").onclick = async () => {
  // Default to "<current folder>/新子文件夹" so a user who has selected
  // Inbox/2024/Q3 and clicks "+ 新建" starts from that prefix rather than
  // from a hard-coded "Inbox/...".
  const current = ($("folder").value || "Inbox").replace(/\/+$/, "");
  const suggested = current + "/新子文件夹";
  const name = prompt(
    "新建文件夹路径(默认在当前选中文件夹下创建子文件夹,可任意改):",
    suggested);
  if (!name) return;
  const account = $("targetAccount").value.trim() || identity.account;
  const r = await apiFetch("api/folders", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: name, account }),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { toast(data.error || "创建失败", "err"); return; }
  await loadFolders();
  const sel = $("folder");
  sel.value = data.path || name.replace(/^\/+/, "");
  toast("已创建:" + (data.path || name), "ok");
};

// ---- upload with resume support ----

function fileFingerprint(file) {
  return file.name + ":" + file.size + ":" + file.lastModified;
}

function loadPending() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "null"); }
  catch (e) { return null; }
}

function savePending(state) {
  if (state) localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  else localStorage.removeItem(STORAGE_KEY);
}

function renderResumeBanner() {
  const box = $("resumeBanner");
  const p = loadPending();
  if (!p || !p.upload_id || !p.files) {
    box.classList.add("hidden"); box.innerHTML = ""; return;
  }
  // Stale (> 24h) — drop and don't show.
  if ((Date.now() - (p.started_at || 0)) / 1000 / 3600 > 24) {
    savePending(null);
    box.classList.add("hidden"); box.innerHTML = ""; return;
  }
  const summary = p.files.map(f => f.name).join("、");
  box.classList.remove("hidden");
  box.innerHTML =
    "<b>上次有未完成的上传:</b>" + esc(summary) +
    '。在下方"选择文件"里选回同样的文件即可自动续传剩余分片。' +
    ' <a href="#" id="resumeDiscard">放弃这次未完成上传</a>';
  $("resumeDiscard").onclick = (e) => {
    e.preventDefault();
    savePending(null);
    renderResumeBanner();
  };
}

async function missingChunks(uploadId, fileIndex, totalChunks) {
  const url = "api/upload/status?upload_id=" + encodeURIComponent(uploadId) +
              "&file_index=" + fileIndex + "&total_chunks=" + totalChunks;
  const r = await apiFetch(url);
  if (!r.ok) return null;  // bail back to "upload everything" on error
  const data = await r.json();
  return Array.isArray(data.missing) ? new Set(data.missing) : null;
}

function fmtBytes(n) {
  if (n >= 1024 * 1024 * 1024) return (n / 1024 / 1024 / 1024).toFixed(2) + " GiB";
  if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + " MiB";
  if (n >= 1024) return (n / 1024).toFixed(0) + " KiB";
  return n + " B";
}

function fmtDuration(seconds) {
  if (!isFinite(seconds) || seconds <= 0) return "—";
  if (seconds < 60) return Math.round(seconds) + " 秒";
  if (seconds < 3600) return Math.round(seconds / 60) + " 分钟";
  return (seconds / 3600).toFixed(1) + " 小时";
}

async function uploadFile(uploadId, fileIndex, file, totals, progressCb) {
  const total = Math.ceil(file.size / CHUNK);
  let missing = null;
  if (totals.alreadyResuming) {
    missing = await missingChunks(uploadId, fileIndex, total);
  }
  let lastTimes = [];  // recent chunk timestamps for ETA
  let lastBytes = 0;
  for (let i = 0; i < total; i++) {
    if (missing && !missing.has(i)) {
      // chunk already on server — skip but still count toward progress
      totals.uploaded += Math.min(CHUNK, file.size - i * CHUNK);
      progressCb({ file, fileIndex, chunkIndex: i, totalChunks: total });
      continue;
    }
    const blob = file.slice(i * CHUNK, (i + 1) * CHUNK);
    const fd = new FormData();
    fd.append("upload_id", uploadId);
    fd.append("file_index", fileIndex);
    fd.append("chunk_index", i);
    fd.append("blob", blob);
    const r = await apiFetch("api/upload/chunk", { method: "POST", body: fd });
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      throw new Error(data.error || ("上传分片失败: " + r.status));
    }
    totals.uploaded += blob.size;
    lastBytes += blob.size;
    const now = Date.now();
    lastTimes.push({ at: now, bytes: lastBytes });
    if (lastTimes.length > 10) lastTimes.shift();
    progressCb({ file, fileIndex, chunkIndex: i, totalChunks: total,
                  recent: lastTimes });
  }
  return total;
}

function renderUploadProgress(totals, current) {
  const pct = totals.size ? Math.round(100 * totals.uploaded / totals.size) : 0;
  $("uploadBar").style.width = pct + "%";
  $("uploadLabel").textContent =
    "上传 " + current.file.name + " " +
    (current.chunkIndex + 1) + "/" + current.totalChunks + " 片 · " +
    fmtBytes(totals.uploaded) + " / " + fmtBytes(totals.size);
  if (current.recent && current.recent.length >= 2) {
    const span = current.recent[current.recent.length - 1].at - current.recent[0].at;
    const bytes = current.recent[current.recent.length - 1].bytes -
                  current.recent[0].bytes;
    if (span > 0 && bytes > 0) {
      const speed = bytes / (span / 1000);  // B/s
      const remain = totals.size - totals.uploaded;
      $("uploadEta").textContent =
        "速率 " + fmtBytes(speed) + "/s · 剩余约 " +
        fmtDuration(remain / speed);
      return;
    }
  }
  $("uploadEta").textContent = "";
}

async function runImport() {
  const files = $("files").files;
  if (!files.length) { toast("请先选择文件", "err"); return; }
  const ta = $("targetAccount").value.trim();
  const label = $("label").value.trim();
  const folder = $("folder").value || "Inbox";

  // Resume support: if there's a pending upload whose file list matches
  // exactly (by fingerprint), reuse its upload_id; else mint a new one.
  let uploadId, alreadyResuming = false;
  const pending = loadPending();
  const fingerprints = Array.from(files).map(fileFingerprint);
  if (pending && pending.fingerprints
      && JSON.stringify(pending.fingerprints) === JSON.stringify(fingerprints)) {
    uploadId = pending.upload_id;
    alreadyResuming = true;
  } else {
    const init = await apiFetch("api/upload/init", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (!init.ok) {
      const data = await init.json().catch(() => ({}));
      toast(data.error || "无法分配上传 ID", "err");
      return;
    }
    uploadId = (await init.json()).upload_id;
    savePending({
      upload_id: uploadId,
      fingerprints,
      files: Array.from(files).map(f => ({ name: f.name, size: f.size })),
      started_at: Date.now(),
    });
  }

  $("uploadStage").classList.remove("hidden");
  $("queueStage").classList.add("hidden");
  const totalSize = Array.from(files).reduce((a, f) => a + f.size, 0);
  const totals = { uploaded: 0, size: totalSize, alreadyResuming };
  $("uploadBar").style.width = "0%";
  $("uploadLabel").textContent = "正在准备上传...";

  const meta = [];
  try {
    for (let idx = 0; idx < files.length; idx++) {
      const chunks = await uploadFile(uploadId, idx, files[idx], totals,
                                      (cur) => renderUploadProgress(totals, cur));
      meta.push({ index: idx, name: files[idx].name, chunks });
    }
  } catch (e) {
    toast(e.message || "上传失败,稍后可重新选同样的文件继续上传", "err");
    return;
  }

  $("uploadLabel").textContent = "上传完成,正在提交任务...";
  $("uploadEta").textContent = "";
  $("uploadBar").style.width = "100%";

  const body = {
    upload_id: uploadId, files: meta, folder,
  };
  if (label) body.label = label;
  if (ta) body.account = ta;

  const r = await apiFetch("api/import", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    toast(data.error || "提交导入失败", "err");
    return;
  }
  savePending(null);
  $("uploadStage").classList.add("hidden");
  $("queueStage").classList.remove("hidden");
  $("queueStage").textContent = "导入任务已入队:" + data.task_id.slice(0, 8);
  renderResumeBanner();
  refreshTasks();
}

$("startBtn").onclick = () => runImport();

// ---- tasks ----

async function refreshTasks() {
  const r = await apiFetch("api/tasks");
  if (!r.ok) return;
  const tasks = await r.json();
  const tbody = $("tasks").querySelector("tbody");
  tbody.innerHTML = "";
  let anyActive = false;
  for (const t of tasks) {
    if (["queued", "running", "cancelling"].includes(t.status)) anyActive = true;
    const pct = t.total ? Math.round(100 * t.done / t.total) : 0;
    const skipped = t.skipped || 0;
    const failed = t.failed || 0;
    const tr = document.createElement("tr");
    tr.className = "task-row";
    tr.dataset.tid = t.id;
    tr.title = "完整任务 ID:" + t.id;
    tr.innerHTML =
      `<td>${esc(t.id.slice(0, 8))}</td>` +
      `<td>${esc(t.label || "")}</td>` +
      `<td>${esc(t.account)}</td>` +
      `<td><span class="status status-${esc(t.status)}">${esc(statusText(t.status))}</span></td>` +
      `<td><div class="bar"><div class="bar-fill" style="width:${pct}%"></div></div>${esc(t.done)}/${esc(t.total)}</td>` +
      `<td>${esc(skipped)}</td>` +
      `<td>${esc(failed)}</td>` +
      `<td>${actionLinks(t)}</td>`;
    tbody.appendChild(tr);
  }
  bindActionLinks(tbody);
  // Restore inline-expanded details that survived this refresh
  for (const tid of [...expandedDetails]) {
    renderTaskDetail(tid);  // fire-and-forget; ignores if row no longer present
  }
  if (anyActive && !pollTimer) {
    pollTimer = setInterval(refreshTasks, 3000);
  } else if (!anyActive && pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function hasDetail(t) {
  const skipped = t.skipped || 0;
  const failed = t.failed || 0;
  return (skipped + failed > 0)
      || ["failed", "interrupted", "cancelled"].includes(t.status)
      || Boolean(t.error);
}

function actionLinks(t) {
  const out = [];
  const skipped = t.skipped || 0;
  const failed = t.failed || 0;
  if (hasDetail(t)) {
    out.push(`<a href="#" data-tid="${esc(t.id)}" class="link-detail">详情</a>`);
  }
  if (t.status === "queued"
      || (t.status === "running" && t.kind !== "zimbra-export")) {
    // tgz 任务运行中不允许取消(Zimbra 一次性处理,没有可中断窗口)
    out.push(`<a href="#" data-tid="${esc(t.id)}" class="link-cancel">取消</a>`);
  }
  if (["failed", "interrupted", "cancelled"].includes(t.status)) {
    out.push(`<a href="#" data-tid="${esc(t.id)}" class="link-retry">重试</a>`);
    if (t.kind !== "zimbra-export" && failed > 0) {
      out.push(`<a href="#" data-tid="${esc(t.id)}" class="link-retry-only-failed">只重试失败</a>`);
    }
  }
  // 终态任务可删除(运行中要先取消)
  if (["done", "failed", "interrupted", "cancelled"].includes(t.status)) {
    out.push(`<a href="#" data-tid="${esc(t.id)}" class="link-delete">删除</a>`);
  }
  return out.join(" · ");
}

function bindActionLinks(root) {
  root.querySelectorAll(".link-detail").forEach(a => {
    a.onclick = (e) => { e.preventDefault(); toggleTaskDetail(a.dataset.tid); };
  });
  root.querySelectorAll(".link-cancel").forEach(a => {
    a.onclick = async (e) => {
      e.preventDefault();
      if (!confirm("取消该任务?已处理的部分不会回滚。")) return;
      const r = await apiFetch("api/tasks/" + encodeURIComponent(a.dataset.tid)
                                + "/cancel", { method: "POST" });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) { toast(data.error || "取消失败", "err"); return; }
      toast("已请求取消", "ok");
      refreshTasks();
    };
  });
  root.querySelectorAll(".link-retry").forEach(a => {
    a.onclick = (e) => { e.preventDefault(); doRetry(a.dataset.tid, false); };
  });
  root.querySelectorAll(".link-retry-only-failed").forEach(a => {
    a.onclick = (e) => { e.preventDefault(); doRetry(a.dataset.tid, true); };
  });
  root.querySelectorAll(".link-delete").forEach(a => {
    a.onclick = async (e) => {
      e.preventDefault();
      if (!confirm("删除该任务?上传过的文件、进度记录会一并清掉,不可恢复。")) return;
      const r = await apiFetch("api/tasks/" + encodeURIComponent(a.dataset.tid)
                                + "/delete", { method: "POST" });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) { toast(data.error || "删除失败", "err"); return; }
      expandedDetails.delete(a.dataset.tid);  // forget any open detail row
      toast("已删除", "ok");
      refreshTasks();
    };
  });
}

async function doRetry(tid, onlyFailed) {
  const r = await apiFetch("api/tasks/" + encodeURIComponent(tid) + "/retry", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ only_failed: onlyFailed }),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { toast(data.error || "重试失败", "err"); return; }
  toast("已入队新任务 " + data.task_id.slice(0, 8), "ok");
  refreshTasks();
}

// Human-readable failure reasons (server may also return Chinese already; this
// is just a fallback for codes the backend didn't pre-translate).
const REASON_BY_CODE = {
  duplicate_batch: "重复(本批内同 Message-ID)",
  duplicate_mailbox: "重复(邮箱内已存在)",
  no_message_id: "缺 Message-ID 头,无法判重已直接导入",
  dedupe_check_failed: "判重查询出错,已直接导入,建议手工确认",
  network: "网络异常,请检查 Zimbra 是否可达",
  transient: "Zimbra 临时错误,已自动重试",
  quota: "目标邮箱配额已满",
  permission: "无权限写入目标邮箱",
  invalid: "邮件被 Zimbra 拒绝",
  unknown: "未知错误",
};

const WARNING_CODES = new Set(["no_message_id", "dedupe_check_failed"]);

function classifyEntry(f) {
  const code = String(f.code || "");
  if (code.startsWith("duplicate")) return "duplicate";
  if (WARNING_CODES.has(code)) return "warning";
  return "failure";
}

// Track which task IDs have an inline-expanded detail row, so periodic
// refreshTasks() can re-attach them after rebuilding the table body.
const expandedDetails = new Set();

const TASKS_TABLE_COLSPAN = 8;

async function toggleTaskDetail(tid) {
  if (expandedDetails.has(tid)) {
    expandedDetails.delete(tid);
    _removeDetailRow(tid);
    return;
  }
  expandedDetails.add(tid);
  await renderTaskDetail(tid);
}

function _removeDetailRow(tid) {
  const row = document.querySelector(
    `#tasks tbody tr.task-detail[data-tid="${cssEscape(tid)}"]`);
  if (row) row.remove();
}

function cssEscape(s) {
  // Minimal: tids are hex/uuid-like, but be defensive.
  return String(s).replace(/(["\\])/g, "\\$1");
}

async function renderTaskDetail(tid) {
  const taskRow = document.querySelector(
    `#tasks tbody tr.task-row[data-tid="${cssEscape(tid)}"]`);
  if (!taskRow) return;  // row no longer in table (task purged, etc.)

  const r = await apiFetch("api/tasks/" + encodeURIComponent(tid));
  if (!r.ok) {
    expandedDetails.delete(tid);
    toast("无法加载详情", "err");
    return;
  }
  const t = await r.json();

  let failures = t.failures;
  if (typeof failures === "string") {
    try { failures = JSON.parse(failures); } catch (e) { failures = []; }
  }
  failures = failures || [];

  const dupes = failures.filter(f => classifyEntry(f) === "duplicate");
  const warns = failures.filter(f => classifyEntry(f) === "warning");
  const fails = failures.filter(f => classifyEntry(f) === "failure");

  const groupByCode = (list) => {
    const out = {};
    for (const f of list) {
      const code = f.code || "unknown";
      (out[code] = out[code] || []).push(f);
    }
    return out;
  };

  const renderRow = (f) =>
    `<li>${esc(f.name)} — ${esc(f.reason || REASON_BY_CODE[f.code] || "未知")}</li>`;

  const renderSection = (klass, byCode) => {
    const parts = [];
    for (const code of Object.keys(byCode)) {
      const list = byCode[code];
      const head = REASON_BY_CODE[code] || code;
      parts.push(`<p class="${klass}"><b>${esc(head)} — ${list.length} 个</b></p>`);
      parts.push(`<ul>${list.map(renderRow).join("")}</ul>`);
    }
    return parts.join("");
  };

  const sections = [];
  if (t.label) sections.push(`<p class="muted">备注:${esc(t.label)}</p>`);
  // Surface task-level error (set when worker aborts before / between per-eml
  // injection: archive normalize crash, delegate_token failure, disk full…).
  if (t.error) {
    sections.push(
      `<p class="err"><b>任务整体失败</b></p>` +
      `<pre class="task-error">${esc(t.error)}</pre>`
    );
  }
  if (dupes.length) {
    sections.push(`<p><b>跳过 ${dupes.length} 个</b>(已存在的重复邮件)</p>`);
    sections.push(`<ul>${dupes.map(renderRow).join("")}</ul>`);
  }
  if (warns.length) {
    sections.push(`<p class="warn"><b>提醒 ${warns.length} 个</b>` +
                  `(已导入但需注意,详情见下)</p>`);
    sections.push(renderSection("warn", groupByCode(warns)));
  }
  if (fails.length) {
    sections.push(`<p class="err"><b>失败 ${fails.length} 个</b></p>`);
    sections.push(renderSection("err", groupByCode(fails)));
  }
  if (!sections.length) {
    sections.push("<p>无详情</p>");
  }

  // Remove any previous detail row for this tid (in case of refresh + restore).
  _removeDetailRow(tid);

  const detailTr = document.createElement("tr");
  detailTr.className = "task-detail";
  detailTr.dataset.tid = tid;
  detailTr.innerHTML =
    `<td colspan="${TASKS_TABLE_COLSPAN}">` +
    `<div class="task-detail-body">${sections.join("")}</div>` +
    `</td>`;
  taskRow.parentNode.insertBefore(detailTr, taskRow.nextSibling);
}

function statusText(s) {
  return {
    queued: "排队中", running: "进行中", done: "完成",
    failed: "失败", interrupted: "中断",
    cancelling: "取消中", cancelled: "已取消",
  }[s] || s;
}

async function loadVersion() {
  try {
    const r = await fetch("api/version");
    if (r.ok) $("version").textContent = "v" + (await r.json()).version;
  } catch (e) { /* ignore */ }
}

probeSession();
loadVersion();
