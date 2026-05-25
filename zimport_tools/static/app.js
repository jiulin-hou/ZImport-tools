const CHUNK = 10 * 1024 * 1024; // 10MB
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

// All fetches go through this helper so the CSRF header is consistent.
async function apiFetch(path, opts = {}) {
  const headers = new Headers(opts.headers || {});
  headers.set("X-Zimport-CSRF", "1");
  const r = await fetch(path, { ...opts, headers });
  return r;
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
}

async function loadFolders() {
  const ta = $("targetAccount").value.trim();
  const url = "api/folders" + (ta ? "?account=" + encodeURIComponent(ta) : "");
  const r = await apiFetch(url);
  const sel = $("folder");
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
}

$("targetAccount").addEventListener("change", loadFolders);
$("retryBtn").onclick = probeSession;
$("refreshBtn").onclick = refreshTasks;

async function uploadFile(uploadId, fileIndex, file) {
  const total = Math.ceil(file.size / CHUNK);
  for (let i = 0; i < total; i++) {
    const blob = file.slice(i * CHUNK, (i + 1) * CHUNK);
    const fd = new FormData();
    fd.append("upload_id", uploadId);
    fd.append("file_index", fileIndex);
    fd.append("chunk_index", i);
    fd.append("blob", blob);
    const r = await apiFetch("api/upload/chunk", { method: "POST", body: fd });
    if (!r.ok) throw new Error("上传分片失败: " + r.status);
    $("uploadProgress").textContent =
      `上传 ${file.name}: ${i + 1}/${total} 片`;
  }
  return total;
}

$("startBtn").onclick = async () => {
  const files = $("files").files;
  if (!files.length) { alert("请先选择文件"); return; }
  try {
    const init = await (await apiFetch("api/upload/init", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    })).json();
    const uploadId = init.upload_id;
    const meta = [];
    for (let idx = 0; idx < files.length; idx++) {
      const chunks = await uploadFile(uploadId, idx, files[idx]);
      meta.push({ index: idx, name: files[idx].name, chunks });
    }
    const body = {
      upload_id: uploadId,
      files: meta,
      folder: $("folder").value || "Inbox",
    };
    const ta = $("targetAccount").value.trim();
    if (ta) body.account = ta;
    const r = await apiFetch("api/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    if (!r.ok) { alert(data.error || "导入失败"); return; }
    $("uploadProgress").textContent = "上传完成,任务已进入队列: " + data.task_id;
    refreshTasks();
  } catch (e) {
    alert(e.message || "上传失败");
  }
};

async function refreshTasks() {
  const r = await apiFetch("api/tasks");
  if (!r.ok) return;
  const tasks = await r.json();
  const tbody = $("tasks").querySelector("tbody");
  tbody.innerHTML = "";
  let anyActive = false;
  for (const t of tasks) {
    if (t.status === "queued" || t.status === "running") anyActive = true;
    const pct = t.total ? Math.round(100 * t.done / t.total) : 0;
    const skipped = t.skipped || 0;
    const failed = t.failed || 0;
    const hasDetails = (skipped + failed) > 0;
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td>${esc(t.id.slice(0, 8))}</td><td>${esc(t.account)}</td>` +
      `<td>${esc(statusText(t.status))}</td>` +
      `<td><div class="bar"><div style="width:${pct}%"></div></div>${esc(t.done)}/${esc(t.total)}</td>` +
      `<td>${esc(skipped)}</td>` +
      `<td>${esc(failed)}</td>` +
      `<td>${hasDetails ? `<a href="#" data-tid="${esc(t.id)}" class="detail-link">详情</a>` : ""}</td>`;
    tbody.appendChild(tr);
  }
  tbody.querySelectorAll(".detail-link").forEach(a => {
    a.onclick = (e) => { e.preventDefault(); showTaskDetail(a.dataset.tid); };
  });
  if (anyActive && !pollTimer) {
    pollTimer = setInterval(refreshTasks, 3000);
  } else if (!anyActive && pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function reasonText(r) {
  return r === "duplicate (same batch)" ? "重复(本批内同 Message-ID)" :
         r === "duplicate (already in mailbox)" ? "重复(邮箱内已存在)" :
         r;
}

async function showTaskDetail(tid) {
  const r = await apiFetch("api/tasks/" + encodeURIComponent(tid));
  if (!r.ok) { alert("无法加载详情"); return; }
  const t = await r.json();
  let failures = t.failures;
  if (typeof failures === "string") {
    try { failures = JSON.parse(failures); } catch (e) { failures = []; }
  }
  failures = failures || [];
  const skipped = failures.filter(f => /duplicate/.test(f.reason));
  const failed = failures.filter(f => !/duplicate/.test(f.reason));
  const box = $("taskDetail");
  box.classList.remove("hidden");
  box.innerHTML =
    `<h3>任务 ${esc(t.id.slice(0, 8))} 详情 ` +
    `<button id="closeDetail">关闭</button></h3>` +
    (skipped.length
      ? `<p><b>跳过 ${esc(skipped.length)} 个(已存在的重复邮件)</b></p>` +
        `<ul>${skipped.map(f =>
          `<li>${esc(f.name)} — ${esc(reasonText(f.reason))}</li>`).join("")}</ul>`
      : "") +
    (failed.length
      ? `<p class="err"><b>失败 ${esc(failed.length)} 个</b></p>` +
        `<ul>${failed.map(f =>
          `<li>${esc(f.name)} — ${esc(f.reason)}</li>`).join("")}</ul>`
      : "") +
    (!skipped.length && !failed.length ? "<p>无详情</p>" : "");
  $("closeDetail").onclick = () => box.classList.add("hidden");
}

function statusText(s) {
  return { queued: "排队中", running: "进行中", done: "完成",
           failed: "失败", interrupted: "中断" }[s] || s;
}

async function loadVersion() {
  try {
    const r = await fetch("api/version");
    if (r.ok) $("version").textContent = "v" + (await r.json()).version;
  } catch (e) { /* ignore — footer just shows placeholder */ }
}

probeSession();
loadVersion();
