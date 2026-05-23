const CHUNK = 10 * 1024 * 1024; // 10MB
let pollTimer = null;
let session = null; // {account, is_admin}

function $(id) { return document.getElementById(id); }

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
    r = await apiFetch("/api/me");
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
  session = await r.json();
  $("who").textContent = session.account;
  $("adminBox").classList.toggle("hidden", !session.is_admin);
  await loadFolders();
  showOnly("main");
  refreshTasks();
}

async function loadFolders() {
  const ta = $("targetAccount").value.trim();
  const url = "/api/folders" + (ta ? "?account=" + encodeURIComponent(ta) : "");
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
    const r = await apiFetch("/api/upload/chunk", { method: "POST", body: fd });
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
    const init = await (await apiFetch("/api/upload/init", {
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
    const r = await apiFetch("/api/import", {
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
  const r = await apiFetch("/api/tasks");
  if (!r.ok) return;
  const tasks = await r.json();
  const tbody = $("tasks").querySelector("tbody");
  tbody.innerHTML = "";
  let anyActive = false;
  for (const t of tasks) {
    if (t.status === "queued" || t.status === "running") anyActive = true;
    const pct = t.total ? Math.round(100 * t.done / t.total) : 0;
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td>${t.id.slice(0, 8)}</td><td>${t.account}</td>` +
      `<td>${statusText(t.status)}</td>` +
      `<td><div class="bar"><div style="width:${pct}%"></div></div>${t.done}/${t.total}</td>` +
      `<td>${t.failed}</td>`;
    tbody.appendChild(tr);
  }
  if (anyActive && !pollTimer) {
    pollTimer = setInterval(refreshTasks, 3000);
  } else if (!anyActive && pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function statusText(s) {
  return { queued: "排队中", running: "进行中", done: "完成",
           failed: "失败", interrupted: "中断" }[s] || s;
}

probeSession();
