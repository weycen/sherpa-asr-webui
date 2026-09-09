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
    resultCard: document.getElementById("resultCard"),
    resultText: document.getElementById("resultText"),
    resultTitle: document.querySelector(".result-title"),
    timeCost: document.getElementById("timeCost"),
    copyBtn: document.getElementById("copyBtn"),
};

const state = {
    models: [],
    uploadId: null,
    uploadDuration: null,
    uploading: false,
    busy: false,
    timer: null,
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
    const ready = state.models.length > 0 && !!state.uploadId && !state.uploading && !state.busy;
    elements.transcribeBtn.disabled = !ready;
    elements.transcribeBtn.textContent = state.busy ? "转写中..." : "开始转写";
}

function setUploadProgress(percent, text, failed) {
    elements.uploadProgressBar.style.width = `${Math.max(0, Math.min(100, percent))}%`;
    elements.uploadProgressBar.style.background = failed ? "#fecaca" : "#dbeafe";
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
}

function setSuccess() {
    elements.resultTitle.textContent = "识别结果";
    elements.resultTitle.classList.remove("error-text");
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
        updateButton();
    } catch (err) {
        elements.modelSelect.replaceChildren();
        const option = document.createElement("option");
        option.textContent = "模型列表加载失败";
        elements.modelSelect.appendChild(option);
        elements.modelStatus.textContent = err.message;
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

function handleFile(file) {
    if (state.busy || state.uploading) return;
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

elements.transcribeBtn.addEventListener("click", async () => {
    if (!state.uploadId || state.busy || state.uploading) return;

    state.busy = true;
    updateButton();
    elements.timeCost.textContent = "00:00";
    elements.resultCard.classList.remove("is-hidden");
    elements.resultText.value = "正在转写，长录音会分段处理，请稍候...";
    setSuccess();

    const start = performance.now();
    state.timer = setInterval(() => {
        elements.timeCost.textContent = formatClock((performance.now() - start) / 1000);
    }, 250);

    const formData = new FormData();
    formData.append("upload_id", state.uploadId);
    formData.append("model", elements.modelSelect.value);
    formData.append("use_punctuation", true);

    try {
        const res = await fetch("/api/transcribe", { method: "POST", body: formData });
        const elapsed = ((performance.now() - start) / 1000).toFixed(2);
        if (!res.ok) {
            setError("转写失败: " + (await readError(res)));
            return;
        }
        const data = await res.json();
        setSuccess();
        elements.resultText.value = data.text || "(未识别到有效语音内容)";
        const paraTag = data.paragraphs > 1 ? `共 ${data.paragraphs} 段 · ` : "";
        elements.timeCost.textContent =
            `${paraTag}耗时 ${elapsed}s / 推理 ${data.inference_time}s`;
    } catch (err) {
        setError("请求出错，请检查服务端连接: " + err.message);
    } finally {
        clearInterval(state.timer);
        state.busy = false;
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

loadModels();
