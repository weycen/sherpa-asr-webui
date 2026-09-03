const elements = {
    dropzone: document.getElementById("dropzone"),
    fileInput: document.getElementById("fileInput"),
    fileInfo: document.getElementById("fileInfo"),
    fileName: document.getElementById("fileName"),
    fileSize: document.getElementById("fileSize"),
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
    file: null,
    models: [],
    busy: false,
};

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
    } catch (err) {
        elements.modelSelect.replaceChildren();
        const option = document.createElement("option");
        option.textContent = "模型列表加载失败";
        elements.modelSelect.appendChild(option);
        elements.modelStatus.textContent = err.message;
    }
}

async function readError(res) {
    try {
        const body = await res.json();
        return typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
        return `HTTP ${res.status}`;
    }
}

function formatSize(bytes) {
    return (bytes / (1024 * 1024)).toFixed(2) + " MB";
}

function handleFile(file) {
    state.file = file;
    elements.fileName.textContent = file.name;
    elements.fileSize.textContent = formatSize(file.size);
    elements.fileInfo.classList.remove("is-hidden");
    updateButton();
}

function updateButton() {
    const ready = state.file && state.models.length > 0 && !state.busy;
    elements.transcribeBtn.disabled = !ready;
    elements.transcribeBtn.textContent = state.busy ? "正在转写..." : "开始转写";
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

elements.dropzone.addEventListener("click", () => elements.fileInput.click());
elements.dropzone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        elements.fileInput.click();
    }
});
elements.fileInput.addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (file) handleFile(file);
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
    if (!state.file || state.busy) return;

    state.busy = true;
    updateButton();
    elements.modelSelect.disabled = true;
    elements.modelTag.textContent =
        elements.modelSelect.options[elements.modelSelect.selectedIndex]?.text || "";
    elements.timeCost.textContent = "";
    elements.resultCard.classList.remove("is-hidden");
    elements.resultText.value = "正在上传并解析音频，请稍候...";
    setSuccess();

    const formData = new FormData();
    formData.append("model", elements.modelSelect.value);
    formData.append("file", state.file);

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
        elements.timeCost.textContent = `耗时 ${elapsed}s / 推理 ${data.inference_time}s`;
        elements.modelTag.textContent =
            state.models.find((m) => m.id === data.model)?.label || data.model;
    } catch (err) {
        setError("请求出错，请检查服务端连接: " + err.message);
    } finally {
        state.busy = false;
        elements.modelSelect.disabled = false;
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
