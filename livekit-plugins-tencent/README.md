# livekit-plugins-tencent

[LiveKit Agents](https://github.com/livekit/agents) 插件：使用腾讯云「流式文本语音合成」WebSocket 接口（`TextToStreamAudioWSv2`）。

## 环境变量

- `TENCENT_TTS_APP_ID`：腾讯云账号 AppId（整数）
- `TENCENT_TTS_SECRET_ID` / `TENCENT_TTS_SECRET_KEY`：API 密钥

也可在代码里传入 `app_id`、`secret_id`、`secret_key`。

## 用法示例

```python
from livekit.plugins.tencent import TTS

tts = TTS(voice_type=101001, sample_rate=16000, codec="pcm")
# agent 中使用 tts.stream() 与 volcengine 等插件一致
```

官方文档：[https://cloud.tencent.com/document/api/1073/108595](https://cloud.tencent.com/document/api/1073/108595)