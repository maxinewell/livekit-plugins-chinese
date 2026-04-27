# livekit-plugins-tencent

[![PyPI version](https://badge.fury.io/py/livekit-plugins-tencent.svg)](https://pypi.org/project/livekit-plugins-tencent/)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/License-Apache%202.0-green.svg)](https://opensource.org/licenses/Apache-2.0)

腾讯云服务专用的 [LiveKit Agents](https://github.com/livekit/agents) 插件，当前同时提供 STT 与 TTS 能力：

- `STT`：实时语音识别（WebSocket）+ Flash 识别（HTTP）
- `TTS`：流式文本语音合成（WebSocket `TextToStreamAudioWSv2`）

## 📋 能力概览

- STT 文档：[腾讯云语音识别](https://cloud.tencent.com/document/product/1093/48982)
- TTS 文档：[腾讯云流式文本语音合成](https://cloud.tencent.com/document/api/1073/108595)

## 🛠️ 安装

```bash

pip install -e . 
```

## ✅ 版本要求

- Python >= 3.10
- livekit-agents>=1.2.9

## ⚙️ 配置

### 环境变量

在使用插件前，请配置以下环境变量：

| 环境变量 | 描述 | 获取方式 |
|----------|------|----------|
| `TENCENT_STT_APP_ID` | 腾讯云应用 ID | [腾讯云控制台](https://console.cloud.tencent.com/) |
| `TENCENT_STT_SECRET_KEY` | 腾讯云 STT Secret Key | [腾讯云控制台](https://console.cloud.tencent.com/) |
| `TENCENT_STT_SECRET_ID` | 腾讯云 STT Secret ID | [腾讯云控制台](https://console.cloud.tencent.com/) |
| `TENCENT_TTS_APP_ID` | 腾讯云应用 ID | [腾讯云控制台](https://console.cloud.tencent.com/) |
| `TENCENT_TTS_SECRET_KEY` | 腾讯云 TTS Secret Key | [腾讯云控制台](https://console.cloud.tencent.com/) |
| `TENCENT_TTS_SECRET_ID` | 腾讯云 TTS Secret ID | [腾讯云控制台](https://console.cloud.tencent.com/) |

### .env 文件示例

```bash
# STT
TENCENT_STT_APP_ID=your_app_id
TENCENT_STT_SECRET_KEY=your_secret_key
TENCENT_STT_SECRET_ID=your_secret_id

# TTS
TENCENT_TTS_APP_ID=your_app_id
TENCENT_TTS_SECRET_KEY=your_secret_key
TENCENT_TTS_SECRET_ID=your_secret_id
```

## 📖 快速开始

### STT（实时识别）

```python
from livekit.plugins import tencent

stt = tencent.STT(
    app_id=1234567890,
    secret_id="your_secret_id",
    secret_key="your_secret_key",
    streaming=True,  # WebSocket 实时识别
)
```

### STT（Flash/非流式识别）

```python
from livekit.plugins import tencent

stt = tencent.STT(
    app_id=1234567890,
    secret_id="your_secret_id",
    secret_key="your_secret_key",
    streaming=False,  # recognize() 走 Flash HTTP 接口
)
```

### TTS（流式文本语音合成）

```python
from livekit.plugins import tencent

tts = tencent.TTS(
    app_id=1234567890,
    secret_id="your_secret_id",
    secret_key="your_secret_key",
    voice_type=101001,
    sample_rate=16000,
    codec="pcm",  # "pcm" or "mp3"
)
```

## 🔧 参数参考（按当前实现）

### STT

- `app_id` / `secret_id` / `secret_key`
- `streaming`：默认 `True`，`False` 时使用 Flash HTTP
- `interim_results`：默认 `True`
- `noise_threshold`：默认 `0.5`，范围 `[-1, 1]`
- `vad_silence_time`：默认 `500`，范围约 `240-2000ms`

说明：当前实现默认使用 `engine_model_type="16k_zh"`，流式模式通过 WebSocket 输出 interim/final 事件。

### TTS

`TTS` 通过 WebSocket 接口 `TextToStreamAudioWSv2` 工作，常用参数：

- `voice_type`：音色 ID，默认 `101001`
- `codec`：`pcm` 或 `mp3`（默认 `pcm`）
- `sample_rate`：`16000` 或 `8000`
- `speed`：语速，范围 `[-2, 6]`
- `volume`：音量，范围 `[-10, 10]`
- `enable_subtitle`：是否开启时间戳字幕
- `emotion_category` / `emotion_intensity`：多情感音色参数
- `segment_rate`：断句敏感阈值（0/1/2）

## 🔄 TTS 协议流程（官方文档对齐）

插件实现遵循腾讯云官方流式文本语音合成接口流程：

1. 握手成功后等待 `ready=1`
2. 连续发送 `ACTION_SYNTHESIS`
3. 文本发送结束后发送 `ACTION_COMPLETE`
4. 收到 `final=1` 后结束会话

详细协议与参数限制请参考腾讯云官方文档：
[流式文本语音合成（TextToStreamAudioWSv2）](https://cloud.tencent.com/document/api/1073/108595)

## 🔌 导出对象

- `tencent.STT`
- `tencent.TTS`

## ❓ 常见问题

### Q: 如何获取腾讯云的认证信息？

A: 请访问 [腾讯云控制台](https://console.cloud.tencent.com/) 创建对应服务并获取：
- App ID
- Secret ID
- Secret Key

### Q: STT 和 TTS 的环境变量能混用吗？

A: 建议分开配置。`STT` 读取 `TENCENT_STT_*`，`TTS` 读取 `TENCENT_TTS_*`，互不替代。

### Q: TTS 为什么会“等一会儿”才返回音频？

A: 流式 TTS 会按句子进行缓存和合成。建议输入包含句末标点，并在文本结束时发送完成信号（插件内部会在结束时发送 `ACTION_COMPLETE`）。

## 📝 更新日志

### v1.3.0
- 支持腾讯云 STT（流式 + Flash）

### Unreleased
- 新增腾讯云流式 TTS（`TextToStreamAudioWSv2`）支持
- README 调整为 STT/TTS 并列覆盖并与当前实现对齐
