# Aliyun TTS → Qwen-TTS Realtime Commit Mode

日期：2026-07-28

## 背景

现有 `livekit-plugins-aliyun` TTS 使用 DashScope CosyVoice Inference WebSocket（`/api-ws/v1/inference`，`run-task` / `continue-task` / `finish-task`，二进制 PCM 帧）。

目标：全面替换为 [Qwen-TTS Realtime](https://help.aliyun.com/zh/model-studio/realtime-tts-user-guide) 的 **commit 模式**，适配对话式 Agent 逐句合成。

## 决策摘要

| 项 | 选择 |
|----|------|
| CosyVoice 兼容 | 不保留，直接替换 |
| 交互模式 | 仅 `commit`（不做 `server_commit`） |
| 分句 | 保留 `TextStreamSentencizer`，每句 `append` + `commit` |
| 会话生命周期 | 单次 `stream()` 共用一条 Realtime WS |
| 指令控制 | 首版不做 `instructions` / instruct 模型 |
| 默认模型 | `qwen3-tts-flash-realtime` |
| 默认音色 | `Cherry` |
| 实现方式 | 纯 `aiohttp` 重写 `tts.py`，不引入 `dashscope` SDK |

## 架构

重写 `livekit-plugins/livekit-plugins-aliyun/livekit/plugins/aliyun/tts.py`。对外 API 仍为 `aliyun.TTS` / `SynthesizeStream`。

| 组件 | 职责 |
|------|------|
| `TTSOptions` | `api_key`、`model`、`voice`、`sample_rate`、`language_type`、`base_url`；组装 WS URL 与 `session.update` payload |
| `TTS` | LiveKit `tts.TTS` 适配；`ConnectionPool` 管理 Realtime WS |
| `SynthesizeStream._run` | 借一条 WS → `session.update(mode=commit)` → 分句循环 → `session.finish` → 归还连接 |

### 端点

- 默认：`wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model={model}`
- 可通过 `base_url` 覆盖（例如新加坡 `wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime`）
- Header：`Authorization: Bearer {api_key}`（或 `bearer`，与现有 DashScope 用法兼容）

### 默认参数

- `model="qwen3-tts-flash-realtime"`
- `voice="Cherry"`
- `sample_rate=24000`
- `language_type="Auto"`
- `response_format="pcm"`（session 内）
- 鉴权：`DASHSCOPE_API_KEY` 或构造参数 `api_key`

### 移除的 CosyVoice 参数

不再支持：`rate`、`pitch`、`volume`、`speech_rate`，以及 Inference 协议字段。

## 数据流

```
LLM tokens
  → TextStreamSentencizer(remove_emoji=True)
  → 完整句子
      → input_text_buffer.append(text)
      → input_text_buffer.commit
      → 等待 response.done
          · response.audio.delta → base64 解码 → emitter.push(pcm)
          · error → 日志并抛 APIStatusError
      → emitter.start_segment / end_segment（每句一段）
  → 输入结束 / Flush
      → 剩余句子同上
      → session.finish，等待 session.finished（或超时关连接）
```

### 并发模型

- 同一条 WS 上 send 与 recv 并行；recv 任务常驻。
- 每句 commit 后用 `asyncio.Future` 等待该句 `response.done`，再处理下一句，避免重叠合成。
- 建连后先发 `session.update`（`mode=commit`、`voice`、`response_format=pcm`、`sample_rate`、`language_type`），再开始 append。
- 音频仅来自 JSON 文本帧的 base64 PCM；不处理 binary 帧。

### 客户端事件（发送）

| 事件 | 用途 |
|------|------|
| `session.update` | 配置 mode/voice/format 等 |
| `input_text_buffer.append` | 追加句子文本 |
| `input_text_buffer.commit` | 触发本句合成 |
| `session.finish` | 结束会话 |

发送时附带 `event_id`（如 `event_{ms}`）。

### 服务端事件（接收）

| 事件 | 处理 |
|------|------|
| `session.created` / `session.updated` | 日志 |
| `response.audio.delta` | 解码并 push PCM |
| `response.done` | 完成本句 Future |
| `session.finished` | 结束会话 |
| `error` | 抛错 |

## 错误处理

- 缺少 API Key：构造时 `ValueError`
- WS 建连失败 / 异常关闭：`APIConnectionError`（交给 LiveKit 重试）
- `type=error`：`APIStatusError`（带服务端 message）
- 等 `response.done` 超时：使用 `conn_options.timeout`；标记连接不可复用并中断 stream
- `session.finish` 后无 `session.finished`：超时关闭 WS，不阻塞归还池
- 出错或未正常 finish 的连接在 close 回调中关闭，避免脏会话复用
- 保留 `max_session_duration` 连接池行为

## 文档与验证

- 更新插件 README 与仓库 `.env.example` 中的 TTS 示例（model/voice）
- 无现成 aliyun TTS 单测；用带 API Key 的手工脚本验证多句 commit 与 PCM 输出

## 非目标（首版）

- `instructions` / `qwen3-tts-instruct-flash-realtime`
- `server_commit` 模式
- CosyVoice Inference 回退路径
- 非流式 `synthesize()`（继续 `NotImplementedError`）

## 破坏性变更

- 默认 model/voice 从 CosyVoice（如 `cosyvoice-v2` / `longcheng`）变为 Qwen Realtime
- 旧 CosyVoice 专用构造参数失效
- 调用方若仍传 CosyVoice model/voice，需自行改为 Qwen Realtime 兼容值
