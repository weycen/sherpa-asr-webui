const elements = {
    dropzone: document.getElementById("dropzone"),
    fileInput: document.getElementById("fileInput"),
    fileUploadRow: document.getElementById("fileUploadRow"),
    fileInfo: document.getElementById("fileInfo"),
    fileName: document.getElementById("fileName"),
    fileMeta: document.getElementById("fileMeta"),
    fileState: document.getElementById("fileState"),
    uploadProgressBar: document.getElementById("uploadProgressBar"),
    uploadProgressText: document.getElementById("uploadProgressText"),
    audioPlayerWrap: document.getElementById("audioPlayerWrap"),
    audioPlayer: document.getElementById("audioPlayer"),
    transcribeBtn: document.getElementById("transcribeBtn"),
    modelSelect: document.getElementById("modelSelect"),
    modelStatus: document.getElementById("modelStatus"),
    punctSelect: document.getElementById("punctSelect"),
    resultCard: document.getElementById("resultCard"),
    resultText: document.getElementById("resultText"),
    resultTitle: document.querySelector(".result-title"),
    textViewBtn: document.getElementById("textViewBtn"),
    segmentsViewBtn: document.getElementById("segmentsViewBtn"),
    segmentsBox: document.getElementById("segmentsBox"),
    timeCost: document.getElementById("timeCost"),
    progressTag: document.getElementById("progressTag"),
    statTag: document.getElementById("statTag"),
    copyBtn: document.getElementById("copyBtn"),
    exportBtn: document.getElementById("exportBtn"),
    exportSrtBtn: document.getElementById("exportSrtBtn"),
    exportVttBtn: document.getElementById("exportVttBtn"),
    toast: document.getElementById("toast"),
    tabUpload: document.getElementById("tabUpload"),
    tabUrl: document.getElementById("tabUrl"),
    panelUpload: document.getElementById("panelUpload"),
    panelUrl: document.getElementById("panelUrl"),
    urlInput: document.getElementById("urlInput"),
    urlFetchBtn: document.getElementById("urlFetchBtn"),
    urlDownloadBox: document.getElementById("urlDownloadBox"),
    urlDownloadTitle: document.getElementById("urlDownloadTitle"),
    urlCancelBtn: document.getElementById("urlCancelBtn"),
    urlProgressBarFill: document.getElementById("urlProgressBarFill"),
    urlDownloadPercent: document.getElementById("urlDownloadPercent"),
    urlDownloadSpeed: document.getElementById("urlDownloadSpeed"),
    urlDownloadEta: document.getElementById("urlDownloadEta"),
};

// 允许上传的音频扩展名白名单（与 app/uploads.py 的 SUPPORTED_AUDIO_EXTENSIONS 保持一致）。
// 前后端统一按扩展名校验：没有已知扩展名的文件一律不接受。
const SUPPORTED_EXTENSIONS = new Set([
    "mp3", "wav", "m4a", "aac", "flac", "ogg", "opus",
    "wma", "amr", "aif", "aiff", "webm",
]);

const state = {
    models: [],
    uploadId: null,
    uploadDuration: null,
    uploading: false,
    busy: false,
    cancelRequested: false,
    cancelling: false,
    cancelController: null,
    timer: null,
    progressTimer: null,
    exportBaseName: "transcript",
    toastTimer: null,
    segments: [],
    activeView: "text",
    activeSourceTab: "upload",
    urlTaskId: null,
    urlDownloading: false,
    urlPollingTimer: null,
};

function formatSize(bytes) {
    return (bytes / (1024 * 1024)).toFixed(2) + " MB";
}

function formatDuration(seconds) {
    if (!Number.isFinite(seconds) || seconds <= 0) return "";
    const total = Math.round(seconds);
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    const pad = (n) => String(n).padStart(2, "0");
    return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

function formatClock(seconds) {
    const pad = (n) => String(n).padStart(2, "0");
    return `${pad(Math.floor(seconds / 60))}:${pad(Math.floor(seconds % 60))}`;
}

function updateButton() {
    if (state.busy) {
        elements.transcribeBtn.classList.add("btn-danger");
        elements.transcribeBtn.disabled = state.cancelling;
        elements.transcribeBtn.textContent = state.cancelling ? "正在取消..." : "取消转写";
        return;
    }
    elements.transcribeBtn.classList.remove("btn-danger");
    const ready = state.models.length > 0 && !!state.uploadId && !state.uploading;
    elements.transcribeBtn.disabled = !ready;
    elements.transcribeBtn.textContent = "开始转写";
}

function setUploadProgress(percent, text, failed) {
    elements.uploadProgressBar.style.width = `${Math.max(0, Math.min(100, percent))}%`;
    elements.uploadProgressBar.style.background = failed ? "#fecaca" : "#dbeafe";
    elements.uploadProgressText.classList.toggle("is-error", !!failed);
    if (text) {
        elements.uploadProgressText.textContent = text;
        elements.uploadProgressText.classList.remove("is-hidden");
    } else {
        elements.uploadProgressText.textContent = "";
        elements.uploadProgressText.classList.add("is-hidden");
    }
}

function setUploadDone(ok) {
    // 成功：绿色对勾；失败/超时：红色感叹提示，均不出现文字。
    elements.fileState.textContent = ok ? "✓" : "!";
    elements.fileState.style.color = ok ? "#16a34a" : "#dc2626";
    elements.fileState.style.display = "inline-block";
}

function disableActionButtons() {
    elements.copyBtn.disabled = true;
    elements.copyBtn.classList.remove("copied");
    elements.exportBtn.disabled = true;
    elements.exportSrtBtn.disabled = true;
    elements.exportVttBtn.disabled = true;
}

function setError(message) {
    elements.resultTitle.textContent = "处理失败";
    elements.resultTitle.classList.add("error-text");
    elements.resultText.value = message;
    hideTranscriptStats();
    elements.timeCost.textContent = "";
    elements.timeCost.classList.add("is-hidden");
    disableActionButtons();
    elements.resultCard.classList.remove("is-hidden");
}

function setSuccess() {
    elements.resultTitle.textContent = "识别结果";
    elements.resultTitle.classList.remove("error-text");
}

function baseName(name) {
    const idx = name.lastIndexOf(".");
    return idx > 0 ? name.slice(0, idx) : name;
}

function showTranscriptStats(charCount) {
    elements.statTag.textContent = charCount > 0 ? `${charCount} 字` : "";
    elements.statTag.classList.toggle("is-hidden", charCount <= 0);
}

function hideTranscriptStats() {
    elements.statTag.textContent = "";
    elements.statTag.classList.add("is-hidden");
}

function showToast(message) {
    elements.toast.textContent = message;
    elements.toast.classList.remove("is-hidden");
    if (state.toastTimer) clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => {
        elements.toast.classList.add("is-hidden");
        state.toastTimer = null;
    }, 3000);
}

async function readError(res) {
    try {
        const body = await res.json();
        return typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
        return `HTTP ${res.status}`;
    }
}

async function loadModels() {
    try {
        const res = await fetch("/api/models");
        if (!res.ok) throw new Error(await readError(res));
        const data = await res.json();
        state.models = data.models;

        elements.modelSelect.replaceChildren();
        for (const model of data.models) {
            const option = document.createElement("option");
            option.value = model.id;
            option.textContent = model.label;
            elements.modelSelect.appendChild(option);
        }
        if (data.default_model) {
            elements.modelSelect.value = data.default_model;
        }
        elements.modelSelect.disabled = false;
        const readyCount = state.models.filter((m) => m.ready).length;
        elements.modelStatus.textContent =
            readyCount > 0 ? `${readyCount} 个模型已加载` : `${state.models.length} 个模型可用`;

        elements.punctSelect.replaceChildren();
        const off = document.createElement("option");
        off.value = "none";
        off.textContent = "关闭";
        elements.punctSelect.appendChild(off);
        for (const punct of data.punctuations || []) {
            const option = document.createElement("option");
            option.value = punct.id;
            option.textContent = punct.label;
            elements.punctSelect.appendChild(option);
        }
        elements.punctSelect.value = data.default_punctuation || "none";
        elements.punctSelect.disabled = (data.punctuations || []).length === 0;

        if (data.ytdlp_available === false) {
            elements.tabUrl.title = "服务端未检测到 yt-dlp 工具";
            elements.tabUrl.style.opacity = "0.6";
        }

        updateButton();
    } catch (err) {
        elements.modelSelect.replaceChildren();
        const option = document.createElement("option");
        option.textContent = "模型列表加载失败";
        elements.modelSelect.appendChild(option);
        elements.modelStatus.textContent = err.message;
        elements.punctSelect.replaceChildren();
        const off = document.createElement("option");
        off.value = "none";
        off.textContent = "关闭";
        elements.punctSelect.appendChild(off);
    }
}

function resetFileInfo(file) {
    state.uploadId = null;
    state.uploadDuration = null;
    state.segments = [];
    elements.audioPlayer.pause();
    elements.audioPlayer.removeAttribute("src");
    elements.audioPlayer.load();
    elements.audioPlayerWrap.classList.add("is-hidden");
    elements.fileName.textContent = file.name;
    elements.fileMeta.textContent = ""; // 上传完成前不显示大小
    elements.fileState.style.display = "none";
    elements.fileUploadRow.classList.remove("is-hidden");
    setUploadProgress(0, "", false);

    // 重置结果大框为“等待转写”就绪空态，彻底消除上一音频残留信息
    setSuccess();
    elements.resultText.value = "";
    elements.resultText.placeholder = "新音频已就绪，点击「开始转写」开始识别...";
    elements.timeCost.textContent = "";
    elements.timeCost.classList.add("is-hidden");
    hideTranscriptStats();
    elements.progressTag.textContent = "";
    elements.progressTag.classList.add("is-hidden");
    renderSegments([]);
    switchView("text");
    disableActionButtons();
}

function fileExtension(name) {
    const idx = name.lastIndexOf(".");
    return idx >= 0 ? name.slice(idx + 1).toLowerCase() : "";
}

function isSupportedAudioFile(file) {
    return SUPPORTED_EXTENSIONS.has(fileExtension(file.name));
}

function rejectUnsupportedFile(file) {
    if (state.busy || state.uploading) return;
    // 非法类型不写入文件框，也不清空已有文件（可能保留了上次上传的信息）。
    const ext = fileExtension(file.name);
    showToast(ext ? `不支持 ${ext.toUpperCase()} 格式的文件` : "不支持无扩展名的文件");
}

function handleFile(file) {
    if (state.busy || state.uploading) return;
    if (!isSupportedAudioFile(file)) {
        rejectUnsupportedFile(file);
        return;
    }
    resetFileInfo(file);
    updateButton();
    uploadFile(file);
}

function uploadFile(file) {
    state.uploading = true;
    updateButton();
    setUploadProgress(0, "0%", false);

    const TIMEOUT_MS = 60 * 1000; // 60s 内未完成视为失败，避免无限等待
    const formData = new FormData();
    formData.append("file", file);

    const xhr = new XMLHttpRequest();
    xhr.timeout = TIMEOUT_MS;
    xhr.open("POST", "/api/upload");
    xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && e.total > 0) {
            const percent = Math.round((e.loaded / e.total) * 100);
            setUploadProgress(percent, `${percent}%`, false);
        }
    };
    xhr.onload = () => {
        state.uploading = false;
        let data = null;
        try {
            data = JSON.parse(xhr.responseText);
        } catch {
            data = null;
        }
        if (xhr.status >= 400 || !data || data.status !== "success") {
            const message = data?.detail || `HTTP ${xhr.status}`;
            setUploadProgress(0, "", true);
            setUploadDone(false);
            setError("上传失败: " + message);
        } else {
            state.uploadId = data.upload_id;
            state.uploadDuration = data.duration;
            setUploadProgress(100, "", false);
            setUploadDone(true);
            const dur = formatDuration(data.duration);
            elements.fileMeta.textContent = `${formatSize(data.size)}${dur ? " · " + dur : ""}`;
            elements.audioPlayer.src = `/api/audio/${data.upload_id}`;
            elements.audioPlayerWrap.classList.remove("is-hidden");
        }
        updateButton();
    };
    xhr.onerror = () => {
        state.uploading = false;
        setUploadProgress(0, "", true);
        setUploadDone(false);
        setError("上传失败: 网络错误，请重试");
        updateButton();
    };
    xhr.ontimeout = () => {
        state.uploading = false;
        setUploadProgress(0, "", true);
        setUploadDone(false);
        setError("上传超时：服务端未在 60 秒内响应，请确认服务已启动后重试");
        updateButton();
    };
    xhr.send(formData);
}

elements.dropzone.addEventListener("click", () => {
    if (!state.uploading && !state.busy) elements.fileInput.click();
});
elements.dropzone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        if (!state.uploading && !state.busy) elements.fileInput.click();
    }
});
elements.fileInput.addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (file) handleFile(file);
    elements.fileInput.value = "";
});

["dragenter", "dragover"].forEach((name) => {
    elements.dropzone.addEventListener(name, (e) => {
        e.preventDefault();
        elements.dropzone.classList.add("dragover");
    });
});
["dragleave", "drop"].forEach((name) => {
    elements.dropzone.addEventListener(name, (e) => {
        e.preventDefault();
        elements.dropzone.classList.remove("dragover");
    });
});
elements.dropzone.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
});

function setCancelledMessage() {
    elements.resultTitle.textContent = "已取消转写";
    elements.resultTitle.classList.remove("error-text");
    elements.resultText.value = "本次转写已取消，可重新点击「开始转写」继续。";
    hideTranscriptStats();
    elements.timeCost.textContent = "";
    elements.timeCost.classList.add("is-hidden");
    disableActionButtons();
    elements.resultCard.classList.remove("is-hidden");
}

async function cancelTranscription() {
    state.cancelRequested = true;
    state.cancelling = true;
    updateButton();
    try {
        const body = new URLSearchParams();
        body.append("upload_id", state.uploadId);
        await fetch("/api/transcribe/cancel", { method: "POST", body });
    } catch {
        // 通知失败也照常中止本地请求。
    }
    if (state.cancelController) state.cancelController.abort();
}

elements.transcribeBtn.addEventListener("click", async () => {
    if (state.busy) {
        cancelTranscription();
        return;
    }
    if (!state.uploadId || state.uploading) return;

    state.busy = true;
    state.cancelRequested = false;
    state.cancelling = false;
    state.exportBaseName = baseName(elements.fileName.textContent.trim()) || "transcript";
    updateButton();

    state.segments = [];
    renderSegments([]);
    disableActionButtons();

    elements.timeCost.textContent = "00:00";
    elements.timeCost.classList.remove("is-hidden");
    hideTranscriptStats();
    elements.resultCard.classList.remove("is-hidden");
    elements.resultText.value = "正在转写，长录音会分段处理，请稍候...";
    setSuccess();
    elements.progressTag.textContent = "准备中...";
    elements.progressTag.classList.remove("is-hidden");

    const start = performance.now();
    state.timer = setInterval(() => {
        elements.timeCost.textContent = formatClock((performance.now() - start) / 1000);
    }, 250);

    const controller = new AbortController();
    state.cancelController = controller;

    // 分段解码是串行的，轮询服务端拿已完成段数并实时显示 n/m。
    state.progressTimer = setInterval(async () => {
        if (!state.uploadId || state.cancelRequested) return;
        try {
            const url = `/api/transcribe/progress?upload_id=${encodeURIComponent(state.uploadId)}`;
            const res = await fetch(url);
            if (!res.ok) return;
            const data = await res.json();
            if (!data.active) return;
            if (data.stage === "queued") {
                elements.progressTag.textContent = "排队等待中...";
            } else if (data.stage === "converting") {
                elements.progressTag.textContent = "音频转码中...";
            } else if (data.stage === "segmenting") {
                elements.progressTag.textContent = "语音分段中...";
            } else if (data.stage === "transcribing") {
                elements.progressTag.textContent =
                    data.total > 0 ? `分段识别 ${data.done}/${data.total}` : "正在识别...";
            } else {
                elements.progressTag.textContent =
                    data.total > 0 ? `分段 ${data.done}/${data.total}` : "处理中...";
            }
        } catch {
            // 轮询失败静默，转写主请求会报告真实结果。
        }
    }, 500);

    const formData = new FormData();
    formData.append("upload_id", state.uploadId);
    formData.append("model", elements.modelSelect.value);
    formData.append("punctuation_model", elements.punctSelect.value);

    try {
        const res = await fetch("/api/transcribe", {
            method: "POST",
            body: formData,
            signal: controller.signal,
        });
        const elapsed = Math.round((performance.now() - start) / 1000);
        if (!res.ok) {
            setError("转写失败: " + (await readError(res)));
            return;
        }
        const data = await res.json();
        if (data.status === "cancelled") {
            setCancelledMessage();
            return;
        }
        setSuccess();
        const hasText = !!(data.text && data.text.trim());
        elements.resultText.value = data.text || "(未识别到有效语音内容)";
        state.segments = data.segments || [];
        renderSegments(state.segments);
        const inference = Math.round(Number(data.inference_time) || 0);
        elements.timeCost.textContent =
            `耗时 ${elapsed}s / 推理 ${inference}s`;
        elements.timeCost.classList.remove("is-hidden");
        showTranscriptStats(data.char_count || 0);
        elements.copyBtn.disabled = !hasText;
        elements.exportBtn.disabled = !hasText;
        elements.exportSrtBtn.disabled = !hasText || state.segments.length === 0;
        elements.exportVttBtn.disabled = !hasText || state.segments.length === 0;
    } catch (err) {
        if (state.cancelRequested || err.name === "AbortError") {
            setCancelledMessage();
        } else {
            setError("请求出错，请检查服务端连接: " + err.message);
        }
    } finally {
        clearInterval(state.timer);
        clearInterval(state.progressTimer);
        state.cancelController = null;
        state.busy = false;
        state.cancelling = false;
        elements.progressTag.textContent = "";
        elements.progressTag.classList.add("is-hidden");
        updateButton();
    }
});

elements.copyBtn.addEventListener("click", async () => {
    if (state.busy || elements.copyBtn.disabled || !elements.resultText.value) return;
    const oldText = elements.copyBtn.textContent;
    try {
        await navigator.clipboard.writeText(elements.resultText.value);
    } catch {
        // HTTP 非安全上下文下 clipboard API 不可用，退回选中文本让用户手动复制。
        elements.resultText.focus();
        elements.resultText.select();
    }
    elements.copyBtn.textContent = "已复制";
    elements.copyBtn.classList.add("copied");
    setTimeout(() => {
        elements.copyBtn.textContent = oldText;
        elements.copyBtn.classList.remove("copied");
    }, 1500);
});

function formatSrtTime(seconds) {
    const totalMs = Math.round(seconds * 1000);
    const ms = totalMs % 1000;
    const totalS = Math.floor(totalMs / 1000);
    const s = totalS % 60;
    const m = Math.floor(totalS / 60) % 60;
    const h = Math.floor(totalS / 3600);
    const pad = (n, len = 2) => String(n).padStart(len, "0");
    return `${pad(h)}:${pad(m)}:${pad(s)},${pad(ms, 3)}`;
}

function formatVttTime(seconds) {
    const totalMs = Math.round(seconds * 1000);
    const ms = totalMs % 1000;
    const totalS = Math.floor(totalMs / 1000);
    const s = totalS % 60;
    const m = Math.floor(totalS / 60) % 60;
    const h = Math.floor(totalS / 3600);
    const pad = (n, len = 2) => String(n).padStart(len, "0");
    return `${pad(h)}:${pad(m)}:${pad(s)}.${pad(ms, 3)}`;
}

function generateSrt(segments) {
    return segments.map((seg, idx) => {
        return `${idx + 1}\n${formatSrtTime(seg.start)} --> ${formatSrtTime(seg.end)}\n${seg.text}\n`;
    }).join("\n");
}

function generateVtt(segments) {
    const lines = ["WEBVTT\n"];
    segments.forEach((seg, idx) => {
        lines.push(`${idx + 1}\n${formatVttTime(seg.start)} --> ${formatVttTime(seg.end)}\n${seg.text}\n`);
    });
    return lines.join("\n");
}

function renderSegments(segments) {
    elements.segmentsBox.replaceChildren();
    if (!segments || segments.length === 0) {
        const empty = document.createElement("div");
        empty.className = "status-text";
        empty.style.textAlign = "center";
        empty.style.padding = "24px 0";
        empty.textContent = "(无时间戳分段信息)";
        elements.segmentsBox.appendChild(empty);
        return;
    }
    for (const seg of segments) {
        const row = document.createElement("div");
        row.className = "segment-row";

        const badge = document.createElement("span");
        badge.className = "segment-time-badge";
        badge.textContent = `${formatDuration(seg.start)} ▶`;
        badge.title = "点击跳转播放";
        badge.addEventListener("click", () => {
            if (elements.audioPlayer.src) {
                elements.audioPlayer.currentTime = seg.start;
                elements.audioPlayer.play();
            }
        });

        const textSpan = document.createElement("span");
        textSpan.className = "segment-text";
        textSpan.textContent = seg.text;

        row.appendChild(badge);
        row.appendChild(textSpan);
        elements.segmentsBox.appendChild(row);
    }
}

function switchView(view) {
    state.activeView = view;
    if (view === "segments") {
        elements.textViewBtn.classList.remove("active");
        elements.segmentsViewBtn.classList.add("active");
        elements.resultText.classList.add("is-hidden");
        elements.segmentsBox.classList.remove("is-hidden");
    } else {
        elements.segmentsViewBtn.classList.remove("active");
        elements.textViewBtn.classList.add("active");
        elements.segmentsBox.classList.add("is-hidden");
        elements.resultText.classList.remove("is-hidden");
    }
}

elements.textViewBtn.addEventListener("click", () => switchView("text"));
elements.segmentsViewBtn.addEventListener("click", () => switchView("segments"));

async function saveFile(content, filename, mimeType, description) {
    if (!content.trim()) return;
    const blob = new Blob([content], { type: `${mimeType};charset=utf-8` });

    if (window.showSaveFilePicker) {
        try {
            const ext = filename.slice(filename.lastIndexOf("."));
            const handle = await window.showSaveFilePicker({
                suggestedName: filename,
                types: [{ description: description, accept: { [mimeType]: [ext] } }],
            });
            const writable = await handle.createWritable();
            await writable.write(blob);
            await writable.close();
            return;
        } catch (err) {
            if (err && err.name === "AbortError") return;
        }
    }

    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

elements.exportBtn.addEventListener("click", () => {
    if (state.busy || elements.exportBtn.disabled) return;
    saveFile(elements.resultText.value, `${state.exportBaseName}.txt`, "text/plain", "文本文件");
});

elements.exportSrtBtn.addEventListener("click", () => {
    if (state.busy || elements.exportSrtBtn.disabled || !state.segments.length) return;
    saveFile(generateSrt(state.segments), `${state.exportBaseName}.srt`, "text/plain", "SRT 字幕");
});

elements.exportVttBtn.addEventListener("click", () => {
    if (state.busy || elements.exportVttBtn.disabled || !state.segments.length) return;
    saveFile(generateVtt(state.segments), `${state.exportBaseName}.vtt`, "text/vtt", "VTT 字幕");
});

function switchSourceTab(tab) {
    if (state.busy || state.uploading || state.urlDownloading) {
        showToast("当前有任务正在进行中，请等待完成或取消");
        return;
    }
    state.activeSourceTab = tab;
    if (tab === "url") {
        elements.tabUpload.classList.remove("active");
        elements.tabUpload.setAttribute("aria-selected", "false");
        elements.tabUrl.classList.add("active");
        elements.tabUrl.setAttribute("aria-selected", "true");
        elements.panelUpload.classList.add("is-hidden");
        elements.panelUrl.classList.remove("is-hidden");
        elements.urlInput.focus();
    } else {
        elements.tabUrl.classList.remove("active");
        elements.tabUrl.setAttribute("aria-selected", "false");
        elements.tabUpload.classList.add("active");
        elements.tabUpload.setAttribute("aria-selected", "true");
        elements.panelUrl.classList.add("is-hidden");
        elements.panelUpload.classList.remove("is-hidden");
    }
}

function resetUrlDownloadUI() {
    elements.urlDownloadTitle.textContent = "准备就绪";
    elements.urlDownloadTitle.classList.remove("is-error");
    elements.urlProgressBarFill.style.width = "0%";
    elements.urlDownloadPercent.textContent = "0%";
    elements.urlDownloadSpeed.textContent = "";
    elements.urlDownloadEta.textContent = "";
    elements.urlCancelBtn.disabled = false;
    elements.urlCancelBtn.textContent = "取消";
}

function stopUrlPolling() {
    if (state.urlPollingTimer) {
        clearInterval(state.urlPollingTimer);
        state.urlPollingTimer = null;
    }
}

async function startUrlDownload() {
    if (state.busy || state.uploading || state.urlDownloading) return;
    const url = elements.urlInput.value.trim();
    if (!url) {
        showToast("请输入有效的视频或音频链接");
        elements.urlInput.focus();
        return;
    }
    if (!/^https?:\/\//i.test(url)) {
        showToast("链接必须以 http:// 或 https:// 开头");
        return;
    }

    state.urlDownloading = true;
    elements.urlFetchBtn.disabled = true;
    elements.urlInput.disabled = true;
    resetUrlDownloadUI();
    elements.urlDownloadBox.classList.remove("is-hidden");
    elements.urlDownloadTitle.textContent = "正在连接并解析视频信息...";

    try {
        const res = await fetch("/api/ytdlp/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url }),
        });
        if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            throw new Error(errData.detail || `请求失败 (${res.status})`);
        }
        const data = await res.json();
        state.urlTaskId = data.task_id;
        pollUrlProgress(data.task_id);
    } catch (err) {
        state.urlDownloading = false;
        elements.urlFetchBtn.disabled = false;
        elements.urlInput.disabled = false;
        elements.urlDownloadTitle.textContent = err.message || "发起下载失败";
        elements.urlDownloadTitle.classList.add("is-error");
        showToast(err.message || "下载失败");
    }
}

function pollUrlProgress(taskId) {
    stopUrlPolling();
    state.urlPollingTimer = setInterval(async () => {
        try {
            const res = await fetch(`/api/ytdlp/progress/${encodeURIComponent(taskId)}`);
            if (!res.ok) {
                stopUrlPolling();
                state.urlDownloading = false;
                elements.urlFetchBtn.disabled = false;
                elements.urlInput.disabled = false;
                elements.urlDownloadTitle.textContent = "查询进度失败或任务已过期";
                elements.urlDownloadTitle.classList.add("is-error");
                return;
            }
            const job = await res.json();
            if (job.title) {
                elements.urlDownloadTitle.textContent = job.title;
            } else if (job.status === "fetching") {
                elements.urlDownloadTitle.textContent = "正在解析视频音轨信息...";
            }

            if (Number.isFinite(job.percent)) {
                elements.urlProgressBarFill.style.width = `${Math.min(100, Math.max(0, job.percent))}%`;
                elements.urlDownloadPercent.textContent = `${Math.round(job.percent)}%`;
            }
            elements.urlDownloadSpeed.textContent = job.speed || "";
            elements.urlDownloadEta.textContent = job.eta ? `剩余 ${job.eta}` : "";

            if (job.status === "converting") {
                elements.urlDownloadEta.textContent = "正在提取封装音频...";
            }

            if (job.status === "ready") {
                stopUrlPolling();
                state.urlDownloading = false;
                elements.urlFetchBtn.disabled = false;
                elements.urlInput.disabled = false;
                elements.urlDownloadTitle.textContent = `✓ 已完成: ${job.filename}`;
                elements.urlProgressBarFill.style.width = "100%";
                elements.urlDownloadPercent.textContent = "100%";
                elements.urlDownloadEta.textContent = "提取完成";

                // 导入全局转录就绪状态
                state.uploadId = job.upload_id;
                state.uploadDuration = job.duration;
                state.segments = [];
                state.exportBaseName = (job.title || job.filename || "audio").replace(/\.[^.]+$/, "");

                elements.fileName.textContent = job.filename;
                elements.fileMeta.textContent = `${formatSize(job.size)}${job.duration ? ` / ${formatDuration(job.duration)}` : ""}`;
                elements.fileUploadRow.classList.remove("is-hidden");
                elements.uploadProgressBar.style.width = "100%";
                setUploadDone(true);

                elements.audioPlayer.pause();
                elements.audioPlayer.src = `/api/audio/${encodeURIComponent(job.upload_id)}`;
                elements.audioPlayer.load();
                elements.audioPlayerWrap.classList.remove("is-hidden");

                // 重置识别结果框为等待转写状态
                setSuccess();
                elements.resultText.value = "";
                elements.resultText.placeholder = "网络音频已就绪，点击「开始转写」开始识别...";
                elements.timeCost.textContent = "";
                elements.timeCost.classList.add("is-hidden");
                hideTranscriptStats();
                elements.progressTag.textContent = "";
                elements.progressTag.classList.add("is-hidden");
                renderSegments([]);
                switchView("text");
                disableActionButtons();
                updateButton();
            } else if (job.status === "error") {
                stopUrlPolling();
                state.urlDownloading = false;
                elements.urlFetchBtn.disabled = false;
                elements.urlInput.disabled = false;
                elements.urlDownloadTitle.textContent = job.error || "下载失败";
                elements.urlDownloadTitle.classList.add("is-error");
                elements.urlDownloadEta.textContent = "";
                showToast(job.error || "下载失败");
            } else if (job.status === "cancelled") {
                stopUrlPolling();
                state.urlDownloading = false;
                elements.urlFetchBtn.disabled = false;
                elements.urlInput.disabled = false;
                elements.urlDownloadTitle.textContent = "已取消下载";
                elements.urlDownloadTitle.classList.remove("is-error");
                elements.urlDownloadEta.textContent = "";
            }
        } catch (err) {
            // 网络抖动等待下一次轮询
        }
    }, 1000);
}

async function cancelUrlDownload() {
    if (!state.urlTaskId || !state.urlDownloading) return;
    elements.urlCancelBtn.disabled = true;
    elements.urlCancelBtn.textContent = "正在取消...";
    try {
        await fetch(`/api/ytdlp/cancel/${encodeURIComponent(state.urlTaskId)}`, {
            method: "POST",
        });
    } catch (err) {
        // 忽略取消请求异常
    }
}

elements.tabUpload.addEventListener("click", () => switchSourceTab("upload"));
elements.tabUrl.addEventListener("click", () => switchSourceTab("url"));
elements.urlFetchBtn.addEventListener("click", startUrlDownload);
elements.urlCancelBtn.addEventListener("click", cancelUrlDownload);
elements.urlInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
        e.preventDefault();
        startUrlDownload();
    }
});

disableActionButtons();
loadModels();
