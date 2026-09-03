import os
import time
import shutil
import tempfile
import subprocess
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
import sherpa_onnx
import soundfile as sf

app = FastAPI(title="SenseVoice ASR WebUI")

# 模型路径（确认与你解压的文件夹名一致）
MODEL_DIR = "./sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09"
TOKENS_PATH = os.path.join(MODEL_DIR, "tokens.txt")
MODEL_PATH = os.path.join(MODEL_DIR, "model.int8.onnx")

print("正在载入 SenseVoice-Small 模型...")
recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
    tokens=TOKENS_PATH,
    model=MODEL_PATH,
    num_threads=4,
    use_itn=True,  # 自动标点与规范化
)
print("模型加载完毕！")

# 嵌入前端 Web 页面
HTML_CONTENT = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SenseVoice 本地语音转文字</title>
    <style>
        :root {
            --primary: #2563eb;
            --primary-hover: #1d4ed8;
            --bg: #f8fafc;
            --card-bg: #ffffff;
            --border: #e2e8f0;
            --text-main: #0f172a;
            --text-sub: #64748b;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
        body { background-color: var(--bg); color: var(--text-main); display: flex; justify-content: center; padding: 40px 16px; min-height: 100vh; }
        .container { width: 100%; max-width: 680px; display: flex; flex-direction: column; gap: 20px; }
        .card { background: var(--card-bg); border-radius: 14px; padding: 24px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05), 0 2px 4px -2px rgba(0,0,0,0.05); border: 1px solid var(--border); }
        h1 { font-size: 1.4rem; font-weight: 700; margin-bottom: 6px; }
        p.subtitle { font-size: 0.9rem; color: var(--text-sub); margin-bottom: 20px; }
        
        /* 拖拽上传区域 */
        .dropzone {
            border: 2px dashed #cbd5e1;
            border-radius: 10px;
            padding: 36px 20px;
            text-align: center;
            cursor: pointer;
            transition: all 0.2s ease;
            background: #fdfdfd;
        }
        .dropzone.dragover { border-color: var(--primary); background: #eff6ff; }
        .dropzone-icon { font-size: 2.2rem; margin-bottom: 10px; color: var(--text-sub); }
        .dropzone-text { font-size: 0.95rem; font-weight: 500; color: var(--text-main); margin-bottom: 4px; }
        .dropzone-hint { font-size: 0.8rem; color: var(--text-sub); }
        #fileInput { display: none; }
        
        .file-info {
            display: none;
            margin-top: 14px;
            padding: 10px 14px;
            background: #f1f5f9;
            border-radius: 8px;
            font-size: 0.85rem;
            color: #334155;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        /* 转录操作栏 */
        .btn-group { margin-top: 18px; display: flex; gap: 10px; }
        button {
            flex: 1;
            background: var(--primary);
            color: white;
            border: none;
            padding: 12px;
            border-radius: 8px;
            font-size: 0.95rem;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.15s ease;
        }
        button:hover { background: var(--primary-hover); }
        button:disabled { background: #94a3b8; cursor: not-allowed; }

        /* 结果展示区 */
        .result-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
        .result-title { font-weight: 600; font-size: 1rem; }
        .duration-tag { font-size: 0.75rem; color: var(--text-sub); background: #f1f5f9; padding: 2px 8px; border-radius: 4px; }
        .result-box {
            width: 100%;
            min-height: 140px;
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 14px;
            font-size: 0.95rem;
            line-height: 1.6;
            color: var(--text-main);
            background: #fafafa;
            resize: vertical;
            outline: none;
            white-space: pre-wrap;
            word-break: break-word;
        }
        .copy-btn {
            background: transparent;
            color: var(--primary);
            border: 1px solid var(--primary);
            padding: 6px 12px;
            font-size: 0.8rem;
            border-radius: 6px;
            width: auto;
            flex: none;
        }
        .copy-btn:hover { background: #eff6ff; }
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <h1>SenseVoice 语音转写控制台</h1>
            <p class="subtitle">纯本地 CPU 推理，支持中/英/日/韩/粤语等多种语言</p>

            <div class="dropzone" id="dropzone">
                <div class="dropzone-icon">🎙️</div>
                <div class="dropzone-text">点击选择 或 将音频文件拖曳至此</div>
                <div class="dropzone-hint">支持 MP3, WAV, M4A, AAC, FLAC, OGG 等常见格式</div>
                <input type="file" id="fileInput" accept="audio/*">
            </div>

            <div class="file-info" id="fileInfo" style="display: none;">
                <span id="fileName">已选文件</span>
                <span id="fileSize">0 MB</span>
            </div>

            <div class="btn-group">
                <button id="transcribeBtn" disabled>开始转写</button>
            </div>
        </div>

        <div class="card" id="resultCard" style="display: none;">
            <div class="result-header">
                <span class="result-title">识别结果</span>
                <div>
                    <span class="duration-tag" id="timeCost"></span>
                    <button class="copy-btn" id="copyBtn">复制文本</button>
                </div>
            </div>
            <textarea class="result-box" id="resultText" readonly placeholder="等待处理..."></textarea>
        </div>
    </div>

    <script>
        const dropzone = document.getElementById('dropzone');
        const fileInput = document.getElementById('fileInput');
        const fileInfo = document.getElementById('fileInfo');
        const fileName = document.getElementById('fileName');
        const fileSize = document.getElementById('fileSize');
        const transcribeBtn = document.getElementById('transcribeBtn');
        const resultCard = document.getElementById('resultCard');
        const resultText = document.getElementById('resultText');
        const timeCost = document.getElementById('timeCost');
        const copyBtn = document.getElementById('copyBtn');

        let selectedFile = null;

        // 拖拽事件
        ['dragenter', 'dragover'].forEach(name => {
            dropzone.addEventListener(name, (e) => { e.preventDefault(); dropzone.classList.add('dragover'); });
        });
        ['dragleave', 'drop'].forEach(name => {
            dropzone.addEventListener(name, (e) => { e.preventDefault(); dropzone.classList.remove('dragover'); });
        });
        dropzone.addEventListener('drop', (e) => {
            const files = e.dataTransfer.files;
            if (files.length > 0) handleFile(files[0]);
        });

        dropzone.addEventListener('click', () => fileInput.click());
        fileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) handleFile(e.target.files[0]);
        });

        function handleFile(file) {
            selectedFile = file;
            fileName.textContent = file.name;
            fileSize.textContent = (file.size / (1024 * 1024)).toFixed(2) + ' MB';
            fileInfo.style.display = 'flex';
            transcribeBtn.disabled = false;
        }

        // 转写请求
        transcribeBtn.addEventListener('click', async () => {
            if (!selectedFile) return;

            transcribeBtn.disabled = true;
            transcribeBtn.textContent = '转写中...';
            resultCard.style.display = 'block';
            resultText.value = '正在解析音频并推理，请稍候...';
            timeCost.textContent = '';

            const formData = new FormData();
            formData.append('file', selectedFile);

            const startTime = performance.now();
            try {
                const response = await fetch('/api/transcribe', {
                    method: 'POST',
                    body: formData
                });
                const res = await response.json();
                const elapsed = ((performance.now() - startTime) / 1000).toFixed(2);

                if (res.status === 'success') {
                    resultText.value = res.text || '(未识别到有效语音内容)';
                    timeCost.textContent = `耗时 ${elapsed} 秒 (引擎推理: ${res.inference_time}s)`;
                } else {
                    resultText.value = '转写失败: ' + res.message;
                }
            } catch (err) {
                resultText.value = '请求出错，请检查服务端连接: ' + err.message;
            } finally {
                transcribeBtn.disabled = false;
                transcribeBtn.textContent = '开始转写';
            }
        });

        // 复制功能
        copyBtn.addEventListener('click', () => {
            if (!resultText.value) return;
            navigator.clipboard.writeText(resultText.value).then(() => {
                const oldText = copyBtn.textContent;
                copyBtn.textContent = '已复制！';
                setTimeout(() => copyBtn.textContent = oldText, 1500);
            });
        });
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_CONTENT

@app.post("/api/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    suffix = os.path.splitext(file.filename)[-1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        input_path = tmp.name

    # 转为标准 16kHz mono WAV，确保兼容 mp3/m4a/aac 等任何音频格式
    wav_path = input_path + ".converted.wav"
    try:
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            wav_path
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        audio_data, sample_rate = sf.read(wav_path, dtype="float32")

        t0 = time.time()
        stream = recognizer.create_stream()
        stream.accept_waveform(sample_rate, audio_data)
        recognizer.decode_stream(stream)
        text = stream.result.text.strip()
        inf_time = round(time.time() - t0, 3)

        return {"status": "success", "text": text, "inference_time": inf_time}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        for p in [input_path, wav_path]:
            if os.path.exists(p):
                os.remove(p)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)