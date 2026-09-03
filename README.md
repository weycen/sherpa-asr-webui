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
│   └── transcriber.py   # ffmpeg 转 16k mono WAV + sherpa-onnx 解码
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

模型全部在 `config/models.json` 的 `models` 列表里声明。服务启动不再强依赖某个固定模型目录，
而是在第一次转写时才加载并常驻内存（懒加载 + 缓存）。文件路径相对 `config/models.json` 所在
目录解析（示例中模型与仓库目录平级，故为 `../../`），也支持绝对路径。要新增模型时复制一个条目
改字段即可，Web 界面会自动多出一个可选项。

```json
{
  "default_model": "sense-voice-zh-en-ja-ko-yue-int8",
  "models": [
    {
      "id": "sense-voice-zh-en-ja-ko-yue-int8",
      "label": "SenseVoice (中/英/日/韩/粤) int8",
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

## API

- `GET /api/models`：模型列表，含 `id/label/description/type/ready`。
- `POST /api/transcribe`：multipart 表单，字段 `model`（可选，缺省用默认模型）+ `file`。
  成功返回 `{"status": "success", "model", "text", "inference_time"}`。
