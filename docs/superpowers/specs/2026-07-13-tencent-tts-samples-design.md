# Tencent TTS 音色样例库设计

日期：2026-07-13  
状态：已定稿（待实现）

## 背景

`livekit-plugins-tencent` 的 TTS 通过 `voice_type`（整数音色 ID）选择音色，默认 `101001`。仓库内缺少可试听的音色样例库。需要为官方「实时/流式 TTS」支持的全部固定音色生成样例，便于选型与演示。

## 目标

在仓库根目录独立目录 `tts_samples/` 下，为腾讯云实时/流式 TTS 全部固定音色生成样例库，每条包含：

- 名字（`name`）
- 代码（`code` / `VoiceType`）
- 性别（`gender`）
- 标签（`tags`）
- 中文音频（`zh_audio`）
- 英文音频（`en_audio`，不支持时为 `null`）

音频通过本机 `TENCENT_TTS_*` 凭证调用现有插件批量合成（`codec=mp3`）。`tts_samples/` 整体加入 `.gitignore`，不进入版本库；生成工具与音色清单可入库。

## 非目标

- 不包含一句话复刻占位音色 `200000000`（需 `FastVoiceType`）
- 不爬取官方文档作为运行时依赖
- 不生成 wav/pcm 双格式
- 不把音频提交进 Git / Git LFS

## 目录结构

```
tts_samples/tencent/                    # gitignored
  catalog.json
  errors.json
  voices/
    {name}/
      zh.mp3
      en.mp3
      meta.json

tools/
  generate_tencent_tts_samples.py       # 可入库
  tencent_tts_voices.json               # 可入库：完整音色清单
```

说明：仓库根 `.gitignore` 已忽略整个 `scripts/`，因此生成工具放在 `tools/`，避免无法提交。音色目录名为展示名 `name`（`/` 等不安全字符替换为 `-`）。

## 数据模型

### `tools/tencent_tts_voices.json`

音色清单条目：

```json
{
  "name": "智瑜",
  "code": 101001,
  "gender": "female",
  "tags": ["情感", "精品音色"],
  "supported_languages": ["zh", "en"]
}
```

- `gender`：`male` | `female` | `child`（按官方描述映射）
- `tags`：来自官方「推荐场景」与「音色类型」
- `supported_languages`：官方标注的语言能力；无英文能力时仅 `["zh"]`

### `catalog.json` / `meta.json`

```json
{
  "name": "智瑜",
  "code": 101001,
  "gender": "female",
  "tags": ["情感", "精品音色", "中文"],
  "zh_audio": "voices/智瑜/zh.mp3",
  "en_audio": "voices/智瑜/en.mp3",
  "sample_rate": 16000,
  "supported_languages": ["zh", "en"]
}
```

路径均为相对 `tts_samples/tencent/` 的相对路径。若英文不支持：`en_audio` 为 `null`，并可在 `tags` 中保留 `en_unsupported` 语义（或仅依赖 `supported_languages`）。

## 试听文案

中性文案，不出现厂商名：

- 中文：`今天天气不错，适合出去走走，顺便买杯咖啡。`
- 英文：`The weather is nice today. Let's take a walk and grab a coffee.`

## 合成流程

1. 读取 `tools/tencent_tts_voices.json`
2. 从环境变量 / `.env` 加载 `TENCENT_TTS_APP_ID`、`TENCENT_TTS_SECRET_ID`、`TENCENT_TTS_SECRET_KEY`
3. 对每个音色：
   - 若目标 mp3 已存在且非空 → 跳过
   - 否则使用 `livekit.plugins.tencent.TTS(voice_type=code, codec="mp3", sample_rate=16000)` 的 `synthesize()` 写出
4. 全部结束后写 `catalog.json`；失败项写入 `errors.json`
5. CLI 支持：
   - `--only CODE`：只跑指定音色
   - `--limit N`：限制数量（调试）
   - `--concurrency N`：默认 `2`，避免打满账号并发配额

### 错误与重试

- 单音色失败不中断整批
- `errors.json` 记录 `code` / `name` / `lang` / `error`
- 重复运行同一命令即可幂等补齐缺失文件
- 官方不支持英文的音色：不请求英文合成，`en_audio=null`

## 音色清单来源

- 以腾讯云文档「实时语音合成音色列表」为准（流式 `TextToStreamAudioWSv2` 与该表共用）
- 手工整理进 `tools/tencent_tts_voices.json`，作为可审阅的静态清单
- 文档更新时人工同步清单即可

## Git 策略

在根 `.gitignore` 增加：

```
tts_samples/
```

可提交：`tools/generate_tencent_tts_samples.py`、`tools/tencent_tts_voices.json`、本设计文档。

## 验收标准

1. `tts_samples/` 已被 `.gitignore`
2. `python tools/generate_tencent_tts_samples.py` 能对清单内音色生成中/英 mp3（不支持英则为 `en_audio: null`）
3. `catalog.json` 每条含名字、代码、性别、标签、中文音频、英文音频（或 null）
4. 重复运行跳过已有文件；失败进入 `errors.json`，重跑可补齐
5. 抽样试听至少 3 个不同性别/类型音色，中英文（若支持）可正常播放

## 实现顺序（概要）

1. 整理并写入完整 `tools/tencent_tts_voices.json`
2. 实现生成脚本（读清单、调插件、写 mp3、catalog/errors）
3. 更新 `.gitignore`
4. 用真实凭证跑全量（或先 `--limit` 验证后再全量）
