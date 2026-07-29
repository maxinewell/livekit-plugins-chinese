# livekit-plugins-aliyun

[![PyPI version](https://badge.fury.io/py/livekit-plugins-aliyun.svg)](https://pypi.org/project/livekit-plugins-aliyun/)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/License-Apache%202.0-green.svg)](https://opensource.org/licenses/Apache-2.0)

阿里云服务专用的 [LiveKit Agents](https://github.com/livekit/agents) 插件，提供完整的语音和语言模型集成解决方案。

## ✨ 特性

- 🎤 **语音识别 (STT)** - 支持阿里云Paraformer语音识别服务
- 🗣️ **语音合成 (TTS)** - 支持阿里云 Qwen-TTS Realtime（`commit` 模式）文本转语音服务
- 🤖 **大语言模型 (LLM)** - 支持阿里云Qwen系列大模型
- 🔧 **热词功能** - 支持STT热词识别增强
- 📦 **开箱即用** - 完整的 Python 包支持

## 📋 支持的服务

| 服务 | 描述 | 文档链接 |
|------|------|----------|
| TTS | 文本转语音 | [阿里云TTS](https://bailian.console.aliyun.com/model-market?capabilities=%5B%22TTS%22%5D) |
| STT | 语音识别 | [阿里云ASR](https://bailian.console.aliyun.com/model-market?capabilities=%5B%22ASR%22%5D) |
| LLM | 大语言模型 | [阿里云LLM](https://bailian.console.aliyun.com/model-market) |

## 🛠️ 安装

### 使用 pip 安装

```bash
pip install livekit-plugins-aliyun
```

### 从源码安装

```bash
git clone https://github.com/your-repo/livekit-plugins-volcengine.git
cd livekit-plugins-volcengine
pip install -e ./livekit-plugins/livekit-plugins-aliyun
```

### 系统要求

- Python >= 3.9
- LiveKit Agents >= 1.2.9

## ⚙️ 配置

### 环境变量

在使用插件前，请配置以下环境变量：

| 环境变量 | 描述 | 获取方式 |
|----------|------|----------|
| `DASHSCOPE_API_KEY` | DashScope API 密钥 | [阿里云控制台](https://bailian.console.aliyun.com/) |

### .env 文件示例

```bash
# .env
DASHSCOPE_API_KEY=your_dashscope_api_key_here
```

## 📖 使用指南

### 基础使用

```python
from livekit.agents import Agent, AgentSession, JobContext, cli, WorkerOptions
from livekit.plugins import aliyun
from dotenv import load_dotenv

async def entry_point(ctx: JobContext):
    agent = Agent(instructions="You are a helpful assistant.")

    session = AgentSession(
        # 语音识别
        stt=aliyun.STT(model="paraformer-realtime-v2"),
        # 语音合成
        tts=aliyun.TTS(model="qwen3-tts-flash-realtime", voice="Cherry"),
        # 大语言模型
        llm=aliyun.LLM(model="qwen-plus")
    )

    await session.start(agent=agent, room=ctx.room)
    await ctx.connect()

if __name__ == "__main__":
    load_dotenv()
    cli.run_app(WorkerOptions(entrypoint_fnc=entry_point))
```

### STT热词功能

阿里云STT支持热词功能，可以提高特定词汇的识别准确率：

```python
from livekit.agents import Agent, AgentSession, JobContext, cli, WorkerOptions
from livekit.plugins import aliyun
from dotenv import load_dotenv

async def entry_point(ctx: JobContext):
    agent = Agent(instructions="You are a helpful assistant.")

    session = AgentSession(
        # 配置热词功能的STT
        stt=aliyun.STT(
            model="paraformer-realtime-v2",
            vocabulary_id="your_vocabulary_id"  # 热词表ID
        ),
        tts=aliyun.TTS(model="qwen3-tts-flash-realtime", voice="Cherry"),
        llm=aliyun.LLM(model="qwen-plus")
    )

    await session.start(agent=agent, room=ctx.room)
    await ctx.connect()

if __name__ == "__main__":
    load_dotenv()
    cli.run_app(WorkerOptions(entrypoint_fnc=entry_point))
```

### 高级配置

```python
from livekit.plugins import aliyun

# 自定义TTS配置
tts = aliyun.TTS(
    model="qwen3-tts-flash-realtime",  # 模型名称
    voice="Cherry",           # 语音类型
    sample_rate=24000,        # 采样率
    language_type="Auto",     # 语言类型
)

# 自定义LLM配置
llm = aliyun.LLM(
    model="qwen-max",     # 模型名称
    temperature=0.7,      # 温度
    max_tokens=2000       # 最大token数
)

# 自定义STT配置
stt = aliyun.STT(
    model="paraformer-realtime-v2",
    vocabulary_id="your_vocabulary_id",  # 热词表ID
    format="wav",         # 音频格式
    sample_rate=16000     # 采样率
)
```

## 🔧 API 参考

### TTS (文本转语音)

基于 Qwen-TTS Realtime WebSocket 接口的 `commit` 模式实现：每个句子独立 append + commit，
并等待该句 `response.done` 后再处理下一句。

```python
aliyun.TTS(
    model: str = "qwen3-tts-flash-realtime",
    voice: str = "Cherry",
    sample_rate: int = 24000,
    language_type: str = "Auto",
    base_url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",  # 默认北京 Realtime 端点
)
```

### STT (语音识别)

```python
aliyun.STT(
    model: str = "paraformer-realtime-v2",  # 模型名称
    vocabulary_id: str = None,        # 热词表ID
    format: str = "wav",             # 音频格式
    sample_rate: int = 16000         # 采样率
)
```

### LLM (大语言模型)

```python
aliyun.LLM(
    model: str = "qwen-plus",        # 模型名称
    temperature: float = 0.7,        # 温度
    max_tokens: int = 2000           # 最大token数
)
```

## ❓ 常见问题

### Q: 如何获取 DashScope API 密钥？

A: 请访问[阿里云控制台](https://bailian.console.aliyun.com/)，在DashScope服务页面创建API密钥。

### Q: 支持哪些语音合成模型？

A: 本插件使用阿里云 Qwen-TTS Realtime 的 `commit` 模式，默认模型为 `qwen3-tts-flash-realtime`（默认音色 `Cherry`）。

### Q: 如何创建和管理热词表？

A: 在阿里云控制台的语音识别服务中，可以创建热词表来提高特定词汇的识别准确率。创建后会获得 `vocabulary_id`，在STT配置中使用。

### Q: 支持哪些大语言模型？

A: 支持阿里云Qwen系列模型，包括：
- `qwen-plus` - Qwen Plus 模型
- `qwen-max` - Qwen Max 模型
- `qwen-turbo` - Qwen Turbo 模型
- 其他Qwen系列模型

## 📝 更新日志

### v1.2.9
- 支持阿里云TTS、STT、LLM服务
- 支持STT热词功能
- 完善的API文档和使用示例

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

1. Fork 本项目
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 打开 Pull Request

## 📄 许可证

本项目采用 Apache 2.0 许可证 - 查看 [LICENSE](../LICENSE) 文件了解详情。

## 📞 联系我们

- 项目主页: [GitHub](https://github.com/your-repo/livekit-plugins-volcengine)
- 问题反馈: [Issues](https://github.com/your-repo/livekit-plugins-volcengine/issues)
- 邮箱: 790990241@qq.com

## 🙏 致谢

- [LiveKit](https://github.com/livekit/agents) - 优秀的实时通信框架
- [阿里云](https://bailian.console.aliyun.com/) - 强大的AI服务提供商
