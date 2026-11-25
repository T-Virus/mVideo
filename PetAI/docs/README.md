# Pet AI Content Factory V1.1（豆包 + 本地版）

基于 **MoneyPrinterTurbo v1.3.10** 的低成本宠物内容工厂改造。面向猫咪/狗狗内容创作者（兽医学习者），支持中英文双语科普 + 萌宠娱乐短视频生产。

**全链路「豆包 + 本地」**：不需要 API Key，不产生 API 费用。

## 核心原则（零 API 费用）

| 项目 | 默认 | 说明 |
|---|---|---|
| 文本生成（脚本/选题/知识整理） | ✅ 豆包桌面版 | **本机免费豆包客户端生成**，不调用 DeepSeek |
| DeepSeek API | ❌ 关闭 | 付费 API，默认关闭；仅手动改配置才会启用 |
| AI 图片生成 | ❌ 关闭 | 需用户主动开启，系统绝不自动调用 |
| AI 视频生成 | ❌ 关闭 | 同上 |
| 在线 TTS | ❌ 关闭 | 绝不自动切换 |
| 本地 TTS (SAPI) | ✅ 开启 | **Windows 系统自带离线语音（免费）**，中文 Huihui / 英文 Zira |
| 本地 TTS (Piper) | 可选 | 需自行安装 Piper + 模型；也可上传自己的配音 |
| 本地素材 / 豆包生成素材 | ✅ 开启 | 图片素材在合成前由本地 FFmpeg 自动转视频 |
| 网络素材下载 | ❌ 关闭 | 需授权信息 + 用户主动开启 |

> 默认配置下文本走豆包、素材走豆包/本地上传、配音走 SAPI、字幕/合成/发布包全部本地，**零 API 费用**。
> 所有付费调用前都有强制闸门；关闭的服务绝不会被偷偷调用，失败时也**禁止自动回退**（豆包失败会直接报错，不会偷偷改用 DeepSeek）。
> 如果你确实要启用 DeepSeek：把 `petai.toml` 的 `[services].deepseek` 改为 `true`、`[script].engine` 改为 `deepseek`，再填 `DEEPSEEK_API_KEY`。费用按实际 Token 计，以官方账单为准。

### 为什么配音不用豆包？
实测豆包客户端的朗读按钮**只播放声音、不导出音频文件**，也没有本地音频缓存，系统回环录音也捕获不到。
因此配音改用 Windows 系统自带的 SAPI5 离线语音（零费用、无需联网、无需下载模型）。
若你更偏好豆包音色，可在豆包生成后手动下载音频，再用「上传配音」入口登记。

## 环境要求

- Windows 11 64 位 / Python ≥ 3.11
- 已安装并跑通原版 MoneyPrinterTurbo v1.3.10（PetAI 复用其虚拟环境与依赖）
- NVIDIA RTX 3070 Ti / i7-12700H / 32GB RAM（GPU 非必需，无 CUDA 也能跑基本流程）
- FFmpeg：原项目已通过 `imageio-ffmpeg` 内置二进制提供，无需单独安装

「豆包 + 本地」需要 3 个小依赖（已装入 `.venv`）：`pyautogui`、`pyperclip`（操作豆包客户端）、`pywin32`（SAPI 配音）。详见 `PetAI/requirements.txt`。

## 快速开始

### 1. 安装依赖（如原项目已跑通则跳过）
```bat
uv sync --frozen
```

### 2. 启动豆包客户端（不需要 API Key）
打开并登录本机豆包桌面客户端，保持窗口可被自动化操作。
> 默认方案**不读取任何 API Key**。`.env` 只在你要手动启用 DeepSeek 时才需要。

### 3. 一键初始化
双击项目根目录 `petai.bat`，或在命令行：
```bat
petai.cmd init
petai.cmd status
```
`init` 会创建 `PetAI/config/petai.toml` 与数据库，**幂等、不覆盖已有配置**。
`status` 可查看本地引擎与付费闸门状态（默认应全部关闭）。

### 4. 启动图形界面（推荐，最直观）

双击项目根目录 **`petai-webui.bat`**，浏览器打开 `http://127.0.0.1:8601`。

> 这是 PetAI 专属界面，与原版 WebUI 互不干扰：原版用 8501 端口、PetAI 用 8601 端口。
> 想换端口：设置环境变量 `PETAI_WEBUI_PORT`（如 `set PETAI_WEBUI_PORT=8602`）。
> 想用原版通用界面：双击 `webui.bat`（完整保留）。

界面结构：

- **全局状态条**：每页顶部都显示文本引擎、豆包客户端、配音、素材、合成方式与本月付费调用，零成本状态一眼可见。
- **一键生产**：只选分类（8 类）→ 点「生成视频（全自动）」→ 等结果。程序自动完成：近 7 天热门选题 → 中英脚本与 6~8 个分镜 → 按分镜备素材 → 本地配音与合成 → 发布包；页面实时显示 6 步进度。
- **首页 · 生产向导**：一条龙六步——① 选题 → ② 中英脚本（含 6~8 个镜头的分镜方案）→ ③ 知识审核 → ④ 按分镜准备素材 → ⑤ 配音 → ⑥ 合成中英双版本。每一步都会告诉你下一步做什么，不会迷路。
- **选题中心 / 知识库与审核 / 脚本工厂 / 素材管理 / 视频工厂 / 导出中心**：各模块的精细管理入口。
  - 选题中心与向导第 1 步都支持「豆包批量生成选题」，免费、不联网计费。
  - 脚本生成会产出 **6~8 个镜头**的分镜方案（景别、时长、画面、素材关键词），不足 4 条时本地自动补足，不会出现整条视频只有一两个画面。
  - 素材管理（以及向导第 4 步）支持三种备料方式：**按分镜自动配本地素材**（关键词匹配 + 轮换）、**按分镜用豆包批量生成**（每个镜头一张，整批只切换一次窗口）、单张生成/本地上传。豆包生成走「开新对话 → 直接发提示词 → Enter」，一次会出 4 张，程序自动取第一张入库。**不走 API、不产生费用**。图片素材合成时会自动转成本地视频片段。
  - 合成按分镜顺序拼接（`sequential`），不会随机打乱镜头；素材少于分镜时界面会提示复用哪几个镜头。
  - 生成结束后会**自动切回原窗口（浏览器）并响一声提示音**，页面保留成功/失败提示，不需要手动切回来。
  - 在向导第 4 步生成时，新素材会**自动加入本步已选素材**，可以直接点「下一步：配音」。
- **运行状态**：自查本地链路是否就绪、付费服务是否全部关闭、本月/累计付费调用次数。
- **设置**：豆包客户端自检、SAPI 音色、视频参数。启用付费 API 的说明折叠在「高级」里，界面不提供一键开启按钮。

界面里 **看不到** "开启 AI 图片/视频/在线配音" 这类按钮——这是刻意的：关闭的付费服务只能手动改配置文件开启，界面不给误触入口。
界面也**不再提供 DeepSeek 连接测试按钮**，避免误触产生费用；需要时用 `petai.cmd test-deepseek`（会先经过闸门）。

### 5. 使用命令行（可选）
```bat
petai.cmd status           :: 本地引擎 + 付费闸门状态（默认应全部关闭）
petai.cmd tts-status       :: 本地 TTS 能力、许可证、模型登记
petai.cmd test-deepseek    :: 仅手动启用 DeepSeek 后使用（一条极短请求，会记 Token 账）
petai.cmd productions      :: 视频生产记录
webui.bat                  :: 启动原版 WebUI（完整保留）
```

## Python API 用法

```python
from PetAI.app.bootstrap import create_app
app = create_app()

# 选题中心：手动新增 / AI 批量生成
topic = app.topics.create("猫为什么爱钻纸箱", animal_type="cat", category="cat_behavior")

# 知识库：自动 A/B/C 分级，高风险强制人工审核
k = app.knowledge.create("猫咪打呼噜的原因", "放松时喉部振动", references=["某权威来源"])
print(k["risk_level"], k["review_status"])   # 高风险 => pending
print(app.knowledge.can_publish(k["knowledge_id"]))  # 未审核 => False

# 双语脚本：英文基于事实本地化改写，不逐字翻译
scripts = app.scripts.generate_bilingual(topic["topic_id"])

# 视频生产：本地素材 + 本地配音 + 本地字幕 -> MPT 合成引擎（9:16）
result = app.video.produce_bilingual(
    topic_id=topic["topic_id"],
    script_zh=scripts["zh"]["content"],
    script_en=scripts["en"]["content"],
    materials_zh=[{"path": "本地猫视频.mp4", "kind": "video"}],
    materials_en=[{"path": "本地猫视频.mp4", "kind": "video"}],
    voice_file_zh="配音_zh.wav",       # Piper 未装时用上传配音
    voice_file_en="配音_en.wav",
)
print(result["zh"]["output_path"], result["en"]["output_path"])

# 用本机桌面豆包客户端生成素材（GUI 自动化，免费，不走 API）
print(app.doubao.capabilities())          # 客户端/依赖/坐标状态
asset = app.doubao.generate_image(
    "竖屏9:16，橘猫钻纸箱，自然光，写实摄影",
    category="cat_image",
)
prompts = app.doubao.prompts_from_script(scripts["zh"], animal="cat")
```

### 时长自动对齐（默认开启）

字幕与成片容易出现时长错位的根因是"脚本目标时长 ≠ 真实配音时长"。系统以**真实配音文件**为唯一时间基准：

- 字幕时间轴按真实音频长度重新排布，最后一条精确结束在音频末尾（脚本里写的 45 秒目标时长不参与时间轴计算）；
- MPT 合成素材时会加一段安全余量，成片通常比音频长，系统自动用 FFmpeg 裁剪到音频末尾；成片偏短则克隆末帧补足；
- 每条生产记录都保存对齐报告（配音/字幕/视频时长、偏差、自动执行动作），界面直接显示；
- 已有视频可在「视频工厂」点「校正时长对齐」一键修复，不需要重新合成；
- 配置：`petai.toml` 里 `[video] auto_align = true`、`align_tolerance = 0.3`（秒）。关闭后完全不干预。

## 模块结构

```
PetAI/
├── app/bootstrap.py        # 依赖装配（所有服务共享配置与 CostGuard）
├── cli.py                  # init / status / tts-status / test-deepseek / productions
├── config/                 # settings.py + petai.toml（本地）+ example 模板
├── database/               # 轻量 SQLite（只用标准库 sqlite3）
├── models/constants.py     # 分类/风险分级/审核/身份约束常量
├── services/
│   ├── cost_service.py         # 成本控制中心（付费闸门唯一入口）
│   ├── deepseek_service.py     # DeepSeek 文案（OpenAI 兼容，记实际 usage）
│   ├── topic_service.py        # 选题中心
│   ├── knowledge_service.py    # 知识库 + 7 条审核红线
│   ├── script_service.py       # 双语脚本工厂（引擎可切：豆包免费 / DeepSeek 付费）
│   ├── publish_service.py      # 双市场发布包（只生成文件，不自动发布）
│   ├── local_asset_service.py  # 本地素材（不覆盖原件）
│   ├── tts_service.py          # 本地配音（SAPI/Piper + 上传兜底，绝不切在线）
│   ├── subtitle_service.py     # 本地 SRT（+ 可选 whisper）
│   ├── alignment_service.py    # 音频/字幕/视频时长对齐与自检
│   ├── doubao_desktop_service.py # 桌面免费豆包客户端素材生成（GUI 自动化）
│   ├── doubao_script_service.py  # 豆包对话生成双语脚本（复制按钮取回 JSON）
│   ├── sapi_tts_service.py       # Windows SAPI5 离线配音（豆包朗读不导出音频的替代）
│   └── video_service.py        # 视频生产（复用 MPT 引擎，中英双版本）
├── tests/                  # 107 个 pytest 测试（含一键生产、分镜链路、零成本链路）
├── docs/                   # 本文档 + API 配置 + 测试报告 + 升级回滚
└── storage/                # 本地素材/音频/字幕/导出（运行时数据）
```

## 桌面豆包素材生成：使用与边界

- 依赖：Python 库 `pyautogui` + `pyperclip`（免费，可 `pip install`）；需先启动并登录豆包桌面客户端。
- 流程：激活窗口 → 关闭残留弹窗 → 输入提示词 → 点击生成 → 等待 → 从预览面板下载 → 复制入库（不改原件）。
- 豆包没有公开接口，本功能靠模拟鼠标键盘，**依赖界面布局**。分辨率以 1920×1080 为基准自动缩放；
  客户端改版后只需改 `petai.toml` 的 `[doubao].coords`，不需要改代码。
- 视频生成的等待时间、免费额度与排队由豆包决定；默认 180 秒，不够就调大 `[doubao].video_wait_seconds`。
- 所有生成素材都标记来源 `doubao-desktop-free`，授权字段明确写"商用授权需用户自行向平台确认"。
- 本功能与"默认关闭付费 AI 图片/视频服务"不冲突：它不调用任何 API、不需要 Key、不产生 API 费用。

## 知识审核红线（不可绕过）

1. AI 不得编造参考文献（来源只接受用户录入）
2. 无可靠来源 → 强制待核查
3. 用药/剂量/中毒/急症 → 强制人工复核，未审核不得批准
4. 急症内容自动追加"立即联系当地兽医或动物急诊"
5. 拒绝给评论区用户个体诊断
6. 不得从高风险下调到低风险绕过审核
7. 发布前保留人工确认；作者身份固定为"兽医学习者"，禁止自动标为执业兽医

## 详细文档

- `docs/API_CONFIG.md` — API / 本地 TTS / ffmpeg 配置说明
- `docs/TEST_REPORT.md` — 测试报告（真实执行结果）
- `docs/UPGRADE_AND_ROLLBACK.md` — 升级与回滚说明
- `requirements.txt` — 依赖澄清（零新增依赖）
