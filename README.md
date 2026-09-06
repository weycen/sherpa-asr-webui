# sherpa-asr-webui

基于 [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) + SenseVoice 的本地语音转文字 WebUI，
支持多模型配置、多用户并发访问、前后端分离的项目结构。

## 目录结构

```text
sherpa-asr-webui/
├── server.py            # FastAPI 入口：/api 路由 + 托管 web/ 静态页面
├── app/
│   ├── config.py        # 读取并校验 config/models.json，解析模型文件路径
│   ├── model_manager.py # 模型注册表：按 id 懒加载、缓存、每模型一把推理锁
│   ├── transcriber.py   # ffmpeg 转 16k mono WAV / ffprobe 时长探测
│   ├── vad.py           # silero VAD 长音频切段（语音活动检测）
│   ├── pipeline.py      # VAD 切段 -> 逐段解码 -> 按句群合并输出
│   ├── uploads.py       # 上传文件暂存（临时目录 + TTL 清理）
│   ├── punctuator.py    # 可选：标点恢复（sherpa-onnx OfflinePunctuation）
│   └── postprocess.py   # 转写文本后处理（英文大小写归一化）
├── config/
│   └── models.json      # 模型清单（多模型配置列表）
├── web/
│   ├── index.html       # 前端页面
│   ├── css/style.css
│   └── js/app.js
└── requirements.txt
```

前端与后端通过 `/api/*` 契约通信，页面由 FastAPI 托管在同一端口；需要把前端独立部署时，
将 `web/` 交给任意静态服务器并反向代理 `/api` 到后端即可。

## 运行

```bash
cd /home/weycen/asr-service/sherpa-asr-webui
/home/weycen/asr-service/venv/bin/python server.py
```

浏览器访问 http://127.0.0.1:8000 。依赖 `ffmpeg`，若缺失请先安装：

```bash
sudo apt install ffmpeg
```

## 模型配置

识别模型全部在 `config/models.json` 的 `models` 列表里声明。服务启动不再强依赖某个固定模型目录，
而是在第一次转写时才加载并常驻内存（懒加载 + 缓存）。文件路径相对 `config/models.json` 所在
目录解析（示例中模型与仓库目录平级，故为 `../../`），也支持绝对路径。要新增模型时复制一个条目
改字段即可，Web 界面会自动多出一个可选项。

```json
{
  "default_model": "sense-voice-zh-en-ja-ko-yue-int8",
  "punctuation": {
    "ct_transformer": "../../sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12-int8/model.int8.onnx",
    "num_threads": 2
  },
  "vad": {
    "model": "../../silero_vad.onnx",
    "sample_rate": 16000,
    "threshold": 0.5,
    "min_speech_duration": 0.25,
    "min_silence_duration": 0.5,
    "max_speech_duration": 25.0
  },
  "models": [
    {
      "id": "sense-voice-zh-en-ja-ko-yue-int8",
      "label": "SenseVoice-Small (中/英/日/韩/粤)",
      "description": "SenseVoice-Small 多语种识别，支持自动标点与数字/单位规范化。",
      "type": "sense_voice",
      "config": {
        "model": "../../sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09/model.int8.onnx",
        "tokens": "../../sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09/tokens.txt",
        "num_threads": 4,
        "use_itn": true
      }
    }
  ]
}
```

字段说明：

- `id`：唯一标识，接口和前端用它来选择模型。
- `label` / `description`：下拉框与状态展示用，仅前端文案。
- `type`：指定加载器，当前支持 `sense_voice`、`paraformer`、`whisper`、`transducer`。
- `config`：原样作为对应加载函数的关键字参数（如 `from_sense_voice`），路径类参数自动解析。
- `punctuation`：可选，sherpa-onnx 标点恢复模型（CT-Transformer，中/英）。
- `vad`：可选，silero VAD 长音频分段模型；缺失时按固定 25 秒窗口兜底。

## 长音频处理

音频会先经 silero VAD 切成一个个语音段（单段上限默认 25 秒），逐段独立解码后再合并，因此
上传 20 分钟以上的长录音也不会一次性把所有音频灌进模型导致内存耗尽。合并时按静音停顿和篇幅
切成适合阅读的段落，用空行分隔输出。VAD 模型下载：

```bash
cd /home/weycen/asr-service
wget https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx
```

上传与转写是两步接口：前端先 `POST /api/upload`（带进度）拿 `upload_id`，再
`POST /api/transcribe` 提交 `upload_id` 转写。上传文件暂存在临时目录，自动过期清理。
同时提供 `ASR_MAX_CONCURRENT`（默认 2）限制并发推理，避免小内存机器被同时多个长音频打满。

## 标点恢复与英文大小写

SenseVoice 这类离线模型输出的中文/粤语通常没有断句标点，英文则固定全大写。本服务做了两层后处理：

- 英文大小写归一化：检测到整段英文全大写时，自动转为句子大小写（首字母大写、其余小写）。
- 标点恢复：若 `models.json` 配置了 `punctuation` 字段，会用 sherpa-onnx 官方的
  CT-Transformer 标点模型（中/英）为转写文本补上 `，。！？` 等标点。Web 界面上有一个
  “添加标点（中/英）”开关，默认开启，处理纯日语/韩语等不需要中文标点的音频时可关闭。

标点模型约 75 MB，可下载到与 SenseVoice 同级的模型目录：

```bash
cd /home/weycen/asr-service
curl -SL -O https://github.com/k2-fsa/sherpa-onnx/releases/download/punctuation-models/sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12-int8.tar.bz2
tar xjf sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12-int8.tar.bz2
```

标点模型缺失或加载失败时服务不会报错，只跳过标点这一步，其余功能照常。如果某个模型不希望走
标点恢复，将 `punctuation` 字段从 `models.json` 删掉即可。

## 并发与接口

- 转写接口运行在 FastAPI 线程池中，上传、ffmpeg 转码互不阻塞。
- sherpa-onnx 识别器不做并发解码，因此同一模型的多路请求会排成队串行推理（不会互相拖垮或
  崩溃），不同模型之间互不干扰。模型首次使用时才加载并常驻内存。
- 多用户可同时访问页面并各自上传、排队转写，也可复制结果。

服务端还提供以下环境变量：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `ASR_HOST` | `0.0.0.0` | 监听地址 |
| `ASR_PORT` | `8000` | 监听端口 |
| `ASR_MODELS_FILE` | `config/models.json` | 模型配置文件路径 |
| `ASR_MAX_UPLOAD_MB` | `512` | 单次上传大小上限（MB） |
| `ASR_MAX_AUDIO_SECONDS` | `0` | 音频时长上限（秒），`0` 表示不限 |
| `ASR_MAX_CONCURRENT` | `2` | 同时进行的转写任务数上限 |
| `ASR_UPLOAD_DIR` | 系统临时目录 | 上传文件存放目录 |

## API

- `GET /api/models`：模型列表，含 `default_model`、`punctuation_available` 和模型明细。
- `POST /api/upload`：上传音频（multipart `file`），返回 `upload_id/filename/size/duration`。
- `POST /api/transcribe`：转写已上传文件（multipart `upload_id` + 可选 `model`、
  `use_punctuation`）。成功返回 `{"status", "model", "text", "paragraphs", "segments",
  "inference_time", "audio_seconds"}`；`text` 按空行分段落。
