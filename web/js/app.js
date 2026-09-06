const elements = {
    dropzone: document.getElementById("dropzone"),
    fileInput: document.getElementById("fileInput"),
    fileInfo: document.getElementById("fileInfo"),
    fileName: document.getElementById("fileName"),
    fileMeta: document.getElementById("fileMeta"),
    fileState: document.getElementById("fileState"),
    progressRow: document.getElementById("progressRow"),
    progressBar: document.getElementById("progressBar"),
    progressText: document.getElementById("progressText"),
    transcribeBtn: document.getElementById("transcribeBtn"),
    modelSelect: document.getElementById("modelSelect"),
    modelStatus: document.getElementById("modelStatus"),
    resultCard: document.getElementById("resultCard"),
    resultText: document.getElementById("resultText"),
    resultTitle: document.querySelector(".result-title"),
    modelTag: document.getElementById("modelTag"),
    timeCost: document.getElementById("timeCost"),
    copyBtn: document.getElementById("copyBtn"),
};

const state = {
    models: [],
    uploadId: null,
    uploading: false,
    busy: false,
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

function updateButton() {
    const ready = state.models.length > 0 && !!state.uploadId && !state.uploading && !state.busy;
    elements.transcribeBtn.disabled = !ready;
    elements.transcribeBtn.textContent = state.busy ? "正在转写..." : "开始转写";
}

function setProgress(percent, text) {
    elements.progressBar.style.width = `${percent}%`;
    elements.progressText.textContent = text;
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

function handleFile(file) {
    if (state.busy || state.uploading) return;
    state.uploadId = null;
    elements.fileName.textContent = file.name;
    elements.fileMeta.textContent = formatSize(file.size);
    elements.fileState.classList.add("is-hidden");
    elements.fileInfo.classList.remove("is-hidden");
    updateButton();
    uploadFile(file);
}

function uploadFile(file) {
    state.uploading = true;
    updateButton();
    elements.progressRow.classList.remove("is-hidden");
    setProgress(0, "正在上传 0%");

    const formData = new FormData();
    formData.append("file", file);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/upload");
    xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && e.total > 0) {
            const percent = Math.round((e.loaded / e.total) * 100);
            setProgress(percent, `正在上传 ${percent}%`);
        }
    };
    xhr.onload = () => {
        state.uploading = false;
        elements.progressRow.classList.add("is-hidden");
        let data = null;
        try {
            data = JSON.parse(xhr.responseText);
        } catch {
            data = null;
        }
        if (xhr.status >= 400 || !data || data.status !== "success") {
            setProgress(0, "");
            const message = data?.detail || `上传失败 (HTTP ${xhr.status})`;
            elements.fileState.textContent = "上传失败";
            elements.fileState.classList.remove("is-hidden");
            elements.fileState.style.color = "#dc2626";
            setError("上传失败: " + message);
        } else {
            state.uploadId = data.upload_id;
            elements.fileState.textContent = "✓ 已上传";
            elements.fileState.style.color = "";
            elements.fileState.classList.remove("is-hidden");
            const dur = formatDuration(data.duration);
            elements.fileMeta.textContent = `${formatSize(data.size)}${dur ? " · " + dur : ""}`;
        }
        updateButton();
    };
    xhr.onerror = () => {
        state.uploading = false;
        elements.progressRow.classList.add("is-hidden");
        elements.fileState.textContent = "上传失败";
        elements.fileState.style.color = "#dc2626";
        elements.fileState.classList.remove("is-hidden");
        setError("上传失败: 网络错误，请重试");
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
    elements.modelTag.textContent =
        elements.modelSelect.options[elements.modelSelect.selectedIndex]?.text || "";
    elements.timeCost.textContent = "";
    elements.resultCard.classList.remove("is-hidden");
    elements.resultText.value = "正在转写，长录音会分段处理，请稍候...";
    setSuccess();

    const formData = new FormData();
    formData.append("upload_id", state.uploadId);
    formData.append("model", elements.modelSelect.value);
    formData.append("use_punctuation", true);

    const start = performance.now();
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
        const paraTag =
            data.paragraphs > 1 ? `共 ${data.paragraphs} 段 · ` : "";
        elements.timeCost.textContent =
            `${paraTag}耗时 ${elapsed}s / 推理 ${data.inference_time}s`;
        elements.modelTag.textContent =
            state.models.find((m) => m.id === data.model)?.label || data.model;
    } catch (err) {
        setError("请求出错，请检查服务端连接: " + err.message);
    } finally {
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
