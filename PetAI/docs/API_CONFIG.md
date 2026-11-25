# API 与本地服务配置说明

> 默认方案是 **「桌面豆包 + 本地」**：文本用豆包、配音用 SAPI、字幕/合成/发布包本地完成。
> **不需要任何 API Key，也不会产生 API 费用。** 本文件中 DeepSeek 与 Piper 均为可选进阶配置。

## 1. DeepSeek（默认关闭，可选）

### 启用方式（确认要付费才做）
1. 编辑 `PetAI/config/petai.toml`：`[services].deepseek = true`，`[script].engine = "deepseek"`。
2. 在项目根目录 `.env` 填入 Key：
```
DEEPSEEK_API_KEY=sk-你的真实密钥
```
不启用时 PetAI 不会读取 Key，也不会发起任何请求。
- Key 只从环境变量或 `.env` 读取；`.env` 已被 gitignore；日志有脱敏过滤。
- `DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL` 可选，实际以 `PetAI/config/petai.toml` 的 `[deepseek]` 段为准。

### 模型名（实测核对，2026-11-25）
用 `app.deepseek.list_models()` 查官方当前可用模型，**不要写死退役模型名**：

| 模型 | 状态 | 用途 |
|---|---|---|
| `deepseek-flash` | ✅ 当前可用（默认） | 日常文案，最便宜 |
| `deepseek-v4-pro` | ✅ 当前可用 | 需要更强推理时手动切换 |
| `deepseek-chat` / `deepseek-reasoner` | ❌ 已退役 | 仅兼容路由，勿用 |

### 价格表（官方 USD / 1M tokens，非高峰 cache-miss 为基准）
来源：`https://api-docs.deepseek.com/quick_start/pricing`

| 模型 | 输入 | 缓存命中输入 | 输出 |
|---|---|---|---|
| deepseek-flash | $0.15 | $0.003 | $0.60 |
| deepseek-v4-pro | $0.66 | $0.022 | $1.98 |

高峰时段为官方 2 倍价（UTC 周一~周五 01-04、06-10，中国节假日除外，无法自动识别）。价格写在 `petai.toml [pricing.*]`，不写死在代码里；缺失价格条目的调用费用记 0 并标记"估算"。

### thinking 模式
`[deepseek] thinking_enabled = false`（默认）。flash 默认开启 thinking 会吞掉短回复正文且更贵，关闭后正文直接可用。需要深度推理时改为 `true`。

### 连接测试
```bat
petai.cmd test-deepseek
```
会发一条极短真实请求（记一笔 Token 账），返回模型名、回复、Token、费用、是否估算。

## 2. 本地 TTS（默认 SAPI，Piper 可选）

### 状态
默认引擎是 **Windows 系统自带 SAPI5**（中文 Huihui / 英文 Zira），完全离线、零费用、无需下载模型。
Piper 未安装，作为可选进阶引擎保留；两者都不可用时才走"上传配音兜底"，绝不调用在线/付费 TTS。

### 启用 Piper（可选，需切换引擎）
1. 安装 Piper（rhasspy/piper），得到可执行文件（如 `piper.exe`）。
2. 下载合规许可的声音模型（`.onnx` + `.json`），例如：
   - 中文：`zh_CN-huayan-medium`
   - 英文：`en_US-lessac-medium`
   来源：`https://huggingface.co/rhasspy/piper-voices`
3. 编辑 `PetAI/config/petai.toml`：
```toml
[tts]
piper_executable = "D:/tools/piper/piper.exe"
piper_model_zh = "D:/models/zh_CN-huayan-medium.onnx"
piper_model_en = "D:/models/en_US-lessac-medium.onnx"
volume = 1.0
rate = 1.0
allow_upload_fallback = true
```
4. 验证：`petai.cmd tts-status` 应显示 `piper_available: true`。

### 许可证登记
- Piper 引擎：MIT（`https://github.com/rhasspy/piper`）
- 声音模型：多为 CC-BY 4.0，**以实际下载模型页为准**（`tts-status` 输出含登记链接）

### 上传配音兜底
Piper 不可用时，用 `app.tts.upload_voice()` 或 `video.produce(voice_file=...)` 提供自己的配音文件。`synthesize()` 检测到 `voice_file` 时直接使用，不触碰任何 TTS 引擎。上传不覆盖原件。

## 3. FFmpeg

- 不需要单独安装：原项目 `imageio-ffmpeg==0.6.0` 提供内置二进制。
- PetAI 通过复用 `app.utils.utils.get_ffmpeg_binary()` 解析（PATH → 内置二进制）。
- 用途：视频合成、时长裁剪/补齐，以及把豆包生成的**图片素材转成 9:16 静帧视频**（`clip_duration` 默认 5 秒）。
- 如需指定：`set IMAGEIO_FFMPEG_EXE=D:/path/to/ffmpeg.exe`。

## 4. 字幕

- **默认**：`script_to_srt()`，按标点切句、按字数比例分配音频时长生成本地 SRT，零外部依赖。
- **可选**：`whisper_from_audio()`，用 `faster-whisper` 本地模型转写；需主动调用，不自动下载模型、不上传音频。

## 5. 付费服务闸门（重要）

默认配置下 `[services]` 全部为 `false`（含 DeepSeek）。所有付费服务调用前必须过 `CostGuard.require()`：
- 服务关闭 → 抛 `ServiceDisabledError`，不调用。
- 已开启但缺 Key → 抛 `MissingCredentialError`，不切换别家。
- 调用失败 → `assert_no_fallback()` 留痕并明确拒绝回退。
- 日志/配置/前端均不出现 API Key。

要启用任何付费 AI 图片/视频服务，必须由用户手动改 `petai.toml [services]` 并填对应 Key；系统不会因 DeepSeek 失败而自动切到它们。
