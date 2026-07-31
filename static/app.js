const canvas = document.querySelector("#items");
const emptyState = document.querySelector("#emptyState");
const fileInput = document.querySelector("#fileInput");
const folderInput = document.querySelector("#folderInput");
const dropOverlay = document.querySelector("#dropOverlay");
const textDialog = document.querySelector("#textDialog");
const textForm = document.querySelector("#textForm");
const textContent = document.querySelector("#textContent");
const textTitle = document.querySelector("#textTitle");
const toastEl = document.querySelector("#toast");
const languageToggle = document.querySelector("#languageToggle");
const deleteDialog = document.querySelector("#deleteDialog");
const deleteMessage = document.querySelector("#deleteMessage");
const confirmDelete = document.querySelector("#confirmDelete");
const autoDeleteToggle = document.querySelector("#autoDeleteToggle");
const autoDeleteLabel = document.querySelector("#autoDeleteLabel");
let dragDepth = 0;
let toastTimer;
let gesture = null;
let highestZ = 1;
let expiryRefreshPending = false;
let pendingDelete = null;
let itemsFingerprint = "";
let currentItems = [];
let language = localStorage.getItem("cache-language") || (navigator.language.startsWith("zh") ? "zh" : "en");
let autoDeleteEnabled = true;

const copy = {
  zh: {
    upload: "上传", addText: "新建文字", addFolder: "上传文件夹", empty: "拖入文件，或按 Ctrl+V 粘贴",
    textTitle: "标题", textContent: "写点什么…", save: "保存", chars: "字", loading: "读取中…",
    previewFailed: "无法预览", remaining: "剩余", expired: "已到期", autoDeleteOn: "自动删除：开", autoDeleteOff: "自动删除：关", autoDeletePaused: "自动删除已关闭", download: "下载", delete: "删除",
    deletePrompt: (name) => `删除“${name}”？`, cancel: "取消", deleted: "已删除", deleteFailed: "删除失败",
    readFailed: "无法读取内容", layoutFailed: "位置保存失败", uploading: (n) => `上传中 ${n}%`, uploaded: "上传完成",
    tooLarge: "文件太大", uploadFailed: "上传失败", pasted: "文字已粘贴", pasteFailed: "粘贴失败", saved: "已保存", saveFailed: "保存失败", settingsFailed: "设置保存失败"
  },
  en: {
    upload: "Upload", addText: "New text", addFolder: "Upload folder", empty: "Drop files, or press Ctrl+V to paste",
    textTitle: "Title", textContent: "Write something…", save: "Save", chars: "chars", loading: "Loading…",
    previewFailed: "Preview unavailable", remaining: "Left", expired: "Expired", autoDeleteOn: "Auto-delete: On", autoDeleteOff: "Auto-delete: Off", autoDeletePaused: "Auto-delete off", download: "Download", delete: "Delete",
    deletePrompt: (name) => `Delete “${name}”?`, cancel: "Cancel", deleted: "Deleted", deleteFailed: "Could not delete",
    readFailed: "Could not load content", layoutFailed: "Could not save position", uploading: (n) => `Uploading ${n}%`, uploaded: "Upload complete",
    tooLarge: "File is too large", uploadFailed: "Upload failed", pasted: "Text pasted", pasteFailed: "Paste failed", saved: "Saved", saveFailed: "Could not save", settingsFailed: "Could not save settings"
  }
};

const t = (key, ...args) => {
  const value = copy[language][key];
  return typeof value === "function" ? value(...args) : value;
};

const fingerprintItems = (items) => JSON.stringify(items.map((item) => ({ path: item.path, modified: item.modified, expiresAt: item.expiresAt, layout: item.layout })));

function applyLanguage() {
  document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
  document.querySelector("#uploadLabel").textContent = t("upload");
  document.querySelector("#addText").title = document.querySelector("#addText").ariaLabel = t("addText");
  document.querySelector("#addFolder").title = document.querySelector("#addFolder").ariaLabel = t("addFolder");
  document.querySelector("#emptyDrop span:last-child").textContent = t("empty");
  textTitle.placeholder = t("textTitle");
  textContent.placeholder = t("textContent");
  document.querySelector(".save-button").textContent = t("save");
  const cancelButton = document.querySelector(".cancel-button");
  if (cancelButton) cancelButton.textContent = t("cancel");
  if (confirmDelete) confirmDelete.textContent = t("delete");
  languageToggle.textContent = language === "zh" ? "EN" : "中";
  applyAutoDeleteState(autoDeleteEnabled);
  if (canvas.childElementCount) loadItems();
}

function applyAutoDeleteState(enabled) {
  autoDeleteEnabled = Boolean(enabled);
  document.body.classList.toggle("auto-delete-off", !autoDeleteEnabled);
  autoDeleteToggle?.classList.toggle("disabled", !autoDeleteEnabled);
  autoDeleteToggle?.setAttribute("aria-pressed", String(autoDeleteEnabled));
  if (autoDeleteLabel) autoDeleteLabel.textContent = t(autoDeleteEnabled ? "autoDeleteOn" : "autoDeleteOff");
  if (autoDeleteToggle) {
    autoDeleteToggle.title = t(autoDeleteEnabled ? "autoDeleteOff" : "autoDeleteOn");
    autoDeleteToggle.setAttribute("aria-label", autoDeleteToggle.title);
  }
}

const escapeHtml = (value) => String(value).replace(/[&<>'"]/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
}[char]));

const encodedPath = (path) => encodeURIComponent(path).replaceAll("%2F", "/");
const layoutMode = () => window.innerWidth < 640 ? "mobile" : "desktop";

function formatSize(bytes) {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / (1024 ** index)).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function extension(name) {
  const parts = name.split(".");
  return parts.length > 1 ? parts.pop() : "file";
}

function fileIcon(item) {
  const palette = ["#e5524a", "#3478d4", "#2f9d68", "#8056c7", "#138f9e", "#e07a2f", "#d04c86", "#5366c9"];
  let hash = 0;
  for (const char of item.name) hash = ((hash * 31) + char.charCodeAt(0)) >>> 0;
  const label = extension(item.name).slice(0, 3).toUpperCase();
  return `<span class="large-file" style="--file-color:${palette[hash % palette.length]}">${escapeHtml(label)}</span>`;
}

function toast(message) {
  clearTimeout(toastTimer);
  toastEl.textContent = message;
  toastEl.classList.add("visible");
  toastTimer = setTimeout(() => toastEl.classList.remove("visible"), 2400);
}

function formatRemaining(milliseconds) {
  const seconds = Math.max(0, Math.ceil(milliseconds / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainingSeconds = seconds % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remainingSeconds).padStart(2, "0")}`;
}

function updateCountdowns() {
  const now = Date.now();
  let expired = false;
  for (const element of document.querySelectorAll("[data-expires-at]")) {
    const remaining = new Date(element.dataset.expiresAt).getTime() - now;
    element.hidden = !autoDeleteEnabled;
    element.textContent = !autoDeleteEnabled ? t("autoDeletePaused") : (remaining > 0 ? `${t("remaining")} ${formatRemaining(remaining)}` : t("expired"));
    element.classList.toggle("urgent", autoDeleteEnabled && remaining > 0 && remaining <= 60 * 60 * 1000);
    expired ||= autoDeleteEnabled && remaining <= 0;
  }
  if (expired && !expiryRefreshPending) {
    expiryRefreshPending = true;
    loadItems().finally(() => { expiryRefreshPending = false; });
  }
}

function defaultLayout(index) {
  const mobile = layoutMode() === "mobile";
  const gap = mobile ? 12 : 16;
  const padding = mobile ? 12 : 24;
  const viewportWidth = document.documentElement.clientWidth;
  const width = mobile ? Math.max(180, viewportWidth - padding * 2) : 280;
  const height = mobile ? 170 : 180;
  const columns = mobile ? 1 : Math.max(1, Math.floor((viewportWidth - padding * 2 + gap) / (width + gap)));
  return {
    x: padding + (index % columns) * (width + gap),
    y: 76 + Math.floor(index / columns) * (height + gap),
    w: width,
    h: height,
    z: index + 1
  };
}

function previewMarkup(item, path) {
  if (item.preview === "image") {
    return `<div class="preview media-preview"><img src="/api/content/${path}" alt="" draggable="false" loading="lazy"></div>`;
  }
  if (item.preview === "video") {
    return `<div class="video-titlebar"><span class="video-name">${escapeHtml(item.name)}</span><span class="video-title-actions"><a class="download-button" href="/api/download/${path}" aria-label="${t("download")} ${escapeHtml(item.name)}" title="${t("download")}">↓</a><button class="delete-button" type="button" data-path="${escapeHtml(item.path)}" data-name="${escapeHtml(item.name)}" aria-label="${t("delete")} ${escapeHtml(item.name)}" title="${t("delete")}">×</button></span></div><div class="preview media-preview"><video src="/api/content/${path}" controls preload="metadata" aria-label="${escapeHtml(item.name)}"></video></div>`;
  }
  if (item.preview === "pdf") {
    return `<div class="pdf-titlebar"><span class="pdf-name">${escapeHtml(item.name)}</span><span class="pdf-title-actions"><a class="download-button" href="/api/download/${path}" aria-label="${t("download")} ${escapeHtml(item.name)}" title="${t("download")}">↓</a><button class="delete-button" type="button" data-path="${escapeHtml(item.path)}" data-name="${escapeHtml(item.name)}" aria-label="${t("delete")} ${escapeHtml(item.name)}" title="${t("delete")}">×</button></span></div><div class="preview pdf-preview"><iframe src="/api/content/${path}#view=FitH" title="${escapeHtml(item.name)}"></iframe></div>`;
  }
  if (item.preview === "audio") {
    return `<div class="preview audio-preview"><span class="audio-name">${escapeHtml(item.name)}</span><audio src="/api/content/${path}" controls preload="metadata" aria-label="${escapeHtml(item.name)}"></audio></div>`;
  }
  if (item.preview === "text") {
    return `<div class="preview text-preview" data-text-path="${escapeHtml(item.path)}"><textarea readonly spellcheck="false" aria-label="${t("addText")}">${t("loading")}</textarea></div>`;
  }
  if (item.preview === "folder") {
    return `<div class="preview icon-preview"><span class="large-folder" aria-hidden="true"></span><span class="icon-copy"><span class="icon-name">${escapeHtml(item.name)}</span><span class="icon-meta">${formatSize(item.size)}</span></span></div>`;
  }
  if (item.preview === "archive") {
    return `<div class="preview icon-preview">${fileIcon(item)}<span class="icon-copy"><span class="icon-name">${escapeHtml(item.name)}</span><span class="icon-meta">${formatSize(item.size)}</span></span></div>`;
  }
  return `<div class="preview icon-preview">${fileIcon(item)}<span class="icon-copy"><span class="icon-name">${escapeHtml(item.name)}</span><span class="icon-meta">${formatSize(item.size)}</span></span></div>`;
}

async function hydrateTextPreviews() {
  const previews = [...document.querySelectorAll("[data-text-path]")];
  await Promise.all(previews.map(async (preview) => {
    try {
      const response = await fetch(`/api/preview/${encodedPath(preview.dataset.textPath)}`);
      if (!response.ok) throw new Error();
      const payload = await response.json();
      preview.querySelector("textarea").value = payload.content + (payload.truncated ? "\n…" : "");
    } catch {
      preview.querySelector("textarea").value = t("previewFailed");
    }
  }));
}

function updateCanvasHeight() {
  let bottom = window.innerHeight;
  for (const item of document.querySelectorAll(".item")) {
    bottom = Math.max(bottom, item.offsetTop + item.offsetHeight + 24);
  }
  canvas.style.height = `${bottom}px`;
}

function normalizeDefaultMobileLayouts() {
  if (layoutMode() !== "mobile") return;
  const width = Math.max(180, document.documentElement.clientWidth - 24);
  let index = 0;
  for (const item of document.querySelectorAll('.item[data-saved="false"]')) {
    item.style.left = "12px";
    item.style.top = `${76 + index * 182}px`;
    item.style.width = `${width}px`;
    item.style.height = "170px";
    index += 1;
  }
}

async function loadItems(force = false) {
  try {
    const response = await fetch("/api/items");
    if (!response.ok) throw new Error();
    const items = await response.json();
    const fingerprint = fingerprintItems(items);
    if (!force && fingerprint === itemsFingerprint) {
      updateCountdowns();
      return;
    }
    itemsFingerprint = fingerprint;
    currentItems = items;
    const preservedPdfs = new Map([...canvas.querySelectorAll(".item-pdf")].map((element) => [element.dataset.path, element]));
    emptyState.hidden = items.length > 0;
    canvas.classList.toggle("has-items", items.length > 0);
    const mode = layoutMode();
    highestZ = 1;
    canvas.innerHTML = items.map((item, index) => {
      const path = encodedPath(item.path);
      const saved = item.layout?.[mode];
      const layout = saved || defaultLayout(index);
      highestZ = Math.max(highestZ, layout.z || 1);
      if (item.preview === "pdf" && preservedPdfs.has(item.path)) {
        return `<div class="pdf-placeholder" data-reuse-pdf="${escapeHtml(item.path)}"></div>`;
      }
      if (item.preview === "pdf" && preservedPdfs.has(item.path)) {
        return `<div class="pdf-placeholder" data-reuse-pdf="${escapeHtml(item.path)}"></div>`;
      }
      return `<article class="item item-${item.preview}" data-path="${escapeHtml(item.path)}" data-saved="${Boolean(saved)}" style="left:${layout.x}px;top:${layout.y}px;width:${layout.w}px;height:${layout.h}px;z-index:${layout.z || 1}">
        ${previewMarkup(item, path)}
        <div class="expiry" data-expires-at="${escapeHtml(item.expiresAt)}"></div>
        <div class="item-actions">
          <a class="download-button" href="/api/download/${path}" aria-label="${t("download")} ${escapeHtml(item.name)}" title="${t("download")}">↓</a>
          <button class="delete-button" type="button" data-path="${escapeHtml(item.path)}" data-name="${escapeHtml(item.name)}" aria-label="${t("delete")} ${escapeHtml(item.name)}" title="${t("delete")}">×</button>
        </div>
        <div class="resize-handle" aria-hidden="true"></div>
      </article>`;
    }).join("");
    for (const placeholder of canvas.querySelectorAll("[data-reuse-pdf]")) {
      const item = items.find((entry) => entry.path === placeholder.dataset.reusePdf);
      const pdf = preservedPdfs.get(item.path);
      const saved = item.layout?.[mode];
      const layout = saved || defaultLayout(items.indexOf(item));
      pdf.dataset.saved = Boolean(saved);
      pdf.style.cssText = `left:${layout.x}px;top:${layout.y}px;width:${layout.w}px;height:${layout.h}px;z-index:${layout.z || 1}`;
      pdf.querySelector(".expiry").dataset.expiresAt = item.expiresAt;
      placeholder.replaceWith(pdf);
    }
    for (const placeholder of canvas.querySelectorAll("[data-reuse-pdf]")) {
      const item = items.find((entry) => entry.path === placeholder.dataset.reusePdf);
      const pdf = preservedPdfs.get(item.path);
      const saved = item.layout?.[mode];
      const layout = saved || defaultLayout(items.indexOf(item));
      pdf.dataset.saved = Boolean(saved);
      pdf.style.cssText = `left:${layout.x}px;top:${layout.y}px;width:${layout.w}px;height:${layout.h}px;z-index:${layout.z || 1}`;
      pdf.querySelector(".expiry").dataset.expiresAt = item.expiresAt;
      placeholder.replaceWith(pdf);
    }
    updateCanvasHeight();
    normalizeDefaultMobileLayouts();
    updateCanvasHeight();
    hydrateTextPreviews();
    updateCountdowns();
  } catch {
    toast(t("readFailed"));
  }
}

async function saveItemLayout(item) {
  const payload = {
    mode: layoutMode(),
    x: item.offsetLeft,
    y: item.offsetTop,
    w: item.offsetWidth,
    h: item.offsetHeight,
    z: Number(item.style.zIndex) || 1
  };
  try {
    const response = await fetch(`/api/layout/${encodedPath(item.dataset.path)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (!response.ok) throw new Error();
    const savedItem = currentItems.find((entry) => entry.path === item.dataset.path);
    if (savedItem) {
      savedItem.layout ??= {};
      savedItem.layout[payload.mode] = payload;
      itemsFingerprint = fingerprintItems(currentItems);
    }
  } catch {
    toast(t("layoutFailed"));
  }
}

function uploadFiles(files) {
  if (!files.length) return;
  const body = new FormData();
  for (const file of files) body.append("files", file, file.relativePath || file.webkitRelativePath || file.name);
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/upload");
  xhr.upload.addEventListener("progress", (event) => {
    if (event.lengthComputable) toast(t("uploading", Math.round((event.loaded / event.total) * 100)));
  });
  xhr.addEventListener("load", () => {
    if (xhr.status >= 200 && xhr.status < 300) { toast(t("uploaded")); loadItems(); }
    else toast(xhr.status === 413 ? t("tooLarge") : t("uploadFailed"));
  });
  xhr.addEventListener("error", () => toast(t("uploadFailed")));
  xhr.send(body);
}

async function createText(title, content) {
  const response = await fetch("/api/text", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, content })
  });
  if (!response.ok) throw new Error();
  await loadItems();
}

async function loadSettings() {
  const response = await fetch("/api/settings");
  if (!response.ok) throw new Error();
  const settings = await response.json();
  applyAutoDeleteState(settings.autoDelete !== false);
}

async function toggleAutoDelete() {
  if (!autoDeleteToggle) return;
  autoDeleteToggle.disabled = true;
  try {
    const response = await fetch("/api/settings", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ autoDelete: !autoDeleteEnabled })
    });
    if (!response.ok) throw new Error();
    const settings = await response.json();
    applyAutoDeleteState(settings.autoDelete !== false);
    await loadItems(true);
  } catch {
    toast(t("settingsFailed"));
  } finally {
    autoDeleteToggle.disabled = false;
  }
}

async function filesFromEntry(entry, prefix = "") {
  if (entry.isFile) {
    const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
    file.relativePath = `${prefix}${file.name}`;
    return [file];
  }
  const reader = entry.createReader();
  const children = [];
  while (true) {
    const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
    if (!batch.length) break;
    children.push(...batch);
  }
  const nested = await Promise.all(children.map((child) => filesFromEntry(child, `${prefix}${entry.name}/`)));
  return nested.flat();
}

document.querySelector("#addFiles").addEventListener("click", () => fileInput.click());
document.querySelector("#emptyDrop").addEventListener("click", () => fileInput.click());
document.querySelector("#addFolder").addEventListener("click", () => folderInput.click());
fileInput.addEventListener("change", () => { uploadFiles([...fileInput.files]); fileInput.value = ""; });
folderInput.addEventListener("change", () => { uploadFiles([...folderInput.files]); folderInput.value = ""; });

document.addEventListener("dragenter", (event) => { event.preventDefault(); dragDepth += 1; dropOverlay.classList.add("visible"); });
document.addEventListener("dragover", (event) => event.preventDefault());
document.addEventListener("dragleave", () => {
  dragDepth -= 1;
  if (dragDepth <= 0) { dragDepth = 0; dropOverlay.classList.remove("visible"); }
});
document.addEventListener("drop", async (event) => {
  event.preventDefault();
  dragDepth = 0;
  dropOverlay.classList.remove("visible");
  const entries = [...event.dataTransfer.items].map((item) => item.webkitGetAsEntry?.()).filter(Boolean);
  if (entries.length) {
    const groups = await Promise.all(entries.map((entry) => filesFromEntry(entry)));
    uploadFiles(groups.flat());
  } else uploadFiles([...event.dataTransfer.files]);
});

canvas.addEventListener("pointerdown", (event) => {
  const item = event.target.closest(".item");
  if (!item || event.button !== 0 || event.target.closest(".item-actions, .pdf-title-actions, .video-title-actions, video, audio, iframe")) return;
  if (event.target.closest(".text-preview textarea")) {
    highestZ += 1;
    item.style.zIndex = highestZ;
    saveItemLayout(item);
    return;
  }
  event.preventDefault();
  highestZ += 1;
  item.style.zIndex = highestZ;
  item.setPointerCapture(event.pointerId);
  const resizing = Boolean(event.target.closest(".resize-handle"));
  gesture = {
    item,
    pointerId: event.pointerId,
    resizing,
    startX: event.clientX,
    startY: event.clientY,
    x: item.offsetLeft,
    y: item.offsetTop,
    w: item.offsetWidth,
    h: item.offsetHeight
  };
  item.classList.add(resizing ? "resizing" : "moving");
});

canvas.addEventListener("pointermove", (event) => {
  if (!gesture || event.pointerId !== gesture.pointerId) return;
  const dx = event.clientX - gesture.startX;
  const dy = event.clientY - gesture.startY;
  if (gesture.resizing) {
    gesture.item.style.width = `${Math.max(120, gesture.w + dx)}px`;
    gesture.item.style.height = `${Math.max(120, gesture.h + dy)}px`;
  } else {
    gesture.item.style.left = `${Math.max(0, gesture.x + dx)}px`;
    gesture.item.style.top = `${Math.max(0, gesture.y + dy)}px`;
  }
  updateCanvasHeight();
});

async function finishGesture(event) {
  if (!gesture || event.pointerId !== gesture.pointerId) return;
  const { item } = gesture;
  item.classList.remove("moving", "resizing");
  gesture = null;
  await saveItemLayout(item);
}
canvas.addEventListener("pointerup", finishGesture);
canvas.addEventListener("pointercancel", finishGesture);

document.addEventListener("paste", async (event) => {
  if (event.target.matches("input, textarea, [contenteditable='true']")) return;
  const files = [...event.clipboardData.files];
  if (files.length) { event.preventDefault(); uploadFiles(files); return; }
  const text = event.clipboardData.getData("text/plain");
  if (!text.trim()) return;
  event.preventDefault();
  const stamp = new Date().toLocaleString("sv-SE").replaceAll(":", "-");
  try { await createText(`${language === "zh" ? "粘贴" : "Pasted"} ${stamp}`, text); toast(t("pasted")); }
  catch { toast(t("pasteFailed")); }
});

document.querySelector("#addText").addEventListener("click", () => { textDialog.showModal(); textTitle.focus(); });
document.querySelector("#closeDialog").addEventListener("click", () => textDialog.close());
textDialog.addEventListener("click", (event) => { if (event.target === textDialog) textDialog.close(); });
textContent.addEventListener("input", () => { document.querySelector("#textCount").textContent = `${textContent.value.length} ${t("chars")}`; });
textForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await createText(textTitle.value, textContent.value);
    textForm.reset();
    document.querySelector("#textCount").textContent = `0 ${t("chars")}`;
    textDialog.close();
    toast(t("saved"));
  } catch { toast(t("saveFailed")); }
});

canvas.addEventListener("click", async (event) => {
  const button = event.target.closest(".delete-button");
  if (!button) return;
  if (!confirm(t("deletePrompt", button.dataset.name))) return;
  const response = await fetch(`/api/items/${encodedPath(button.dataset.path)}`, { method: "DELETE" });
  if (response.ok) { toast(t("deleted")); loadItems(); } else toast(t("deleteFailed"));
});

deleteDialog?.addEventListener("close", async () => {
  if (deleteDialog.returnValue !== "confirm" || !pendingDelete) { pendingDelete = null; return; }
  const { path } = pendingDelete;
  pendingDelete = null;
  const response = await fetch(`/api/items/${encodedPath(path)}`, { method: "DELETE" });
  if (response.ok) { toast(t("deleted")); loadItems(); } else toast(t("deleteFailed"));
});

languageToggle.addEventListener("click", () => {
  language = language === "zh" ? "en" : "zh";
  localStorage.setItem("cache-language", language);
  applyLanguage();
});
autoDeleteToggle?.addEventListener("click", toggleAutoDelete);

window.addEventListener("resize", () => updateCanvasHeight());
setInterval(updateCountdowns, 1000);
setInterval(() => {
  if (!document.hidden && !gesture && !textDialog.open && !deleteDialog?.open) loadItems();
}, 1000);
applyLanguage();
Promise.all([loadSettings(), loadItems(true)]).catch(() => toast(t("readFailed")));
