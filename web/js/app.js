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
    transcribeBtn: document.getElementById("transcribeBtn"),
    modelSelect: document.getElementById("modelSelect"),
    modelStatus: document.getElementById("modelStatus"),
    punctSelect: document.getElementById("punctSelect"),
    resultCard: document.getElementById("resultCard"),
    resultText: document.getElementById("resultText"),
    resultTitle: document.querySelector(".result-title"),
    timeCost: document.getElementById("timeCost"),
    progressTag: document.getElementById("progressTag"),
    statTag: document.getElementById("statTag"),
    copyBtn: document.getElementById("copyBtn"),
    exportBtn: document.getElementById("exportBtn"),
    toast: document.getElementById("toast"),
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

function setError(message) {
    elements.resultTitle.textContent = "处理失败";
    elements.resultTitle.classList.add("error-text");
    elements.resultText.value = message;
    hideTranscriptStats();
    elements.exportBtn.disabled = true;
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
    elements.fileName.textContent = file.name;
    elements.fileMeta.textContent = ""; // 上传完成前不显示大小
    elements.fileState.style.display = "none";
    elements.fileUploadRow.classList.remove("is-hidden");
    setUploadProgress(0, "", false);
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
    elements.exportBtn.disabled = true;
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

    elements.timeCost.textContent = "00:00";
    hideTranscriptStats();
    elements.exportBtn.disabled = true;
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
            elements.progressTag.textContent =
                data.total > 0 ? `分段 ${data.done}/${data.total}` : "分段检测中...";
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
        const elapsed = ((performance.now() - start) / 1000).toFixed(2);
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
        elements.timeCost.textContent =
            `耗时 ${elapsed}s / 推理 ${data.inference_time}s`;
        showTranscriptStats(data.char_count || 0);
        elements.exportBtn.disabled = !hasText;
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
    if (!elements.resultText.value) return;
    const oldText = elements.copyBtn.textContent;
    try {
        await navigator.clipboard.writeText(elements.resultText.value);
    } catch {
        // HTTP 非安全上下文下 clipboard API 不可用，退回选中文本让用户手动复制。
        elements.resultText.focus();
        elements.resultText.select();
    }
    elements.copyBtn.textContent = "已复制";
    setTimeout(() => {
        elements.copyBtn.textContent = oldText;
    }, 1500);
});

async function exportTranscript() {
    const text = elements.resultText.value;
    if (!text.trim()) return;

    const filename = `${state.exportBaseName}.txt`;
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });

    // 支持时用系统「另存为」对话框，让用户选位置、改文件名。
    if (window.showSaveFilePicker) {
        try {
            const handle = await window.showSaveFilePicker({
                suggestedName: filename,
                types: [{ description: "文本文件", accept: { "text/plain": [".txt"] } }],
            });
            const writable = await handle.createWritable();
            await writable.write(blob);
            await writable.close();
            return;
        } catch (err) {
            if (err && err.name === "AbortError") return; // 用户取消
            // 其它异常退回普通下载，保证仍能导出。
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

elements.exportBtn.addEventListener("click", exportTranscript);

loadModels();
