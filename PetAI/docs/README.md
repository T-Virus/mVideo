# Pet AI Content Factory V1.0

基于 **MoneyPrinterTurbo v1.3.10** 的低成本宠物内容工厂改造。面向猫咪/狗狗内容创作者（兽医学习者），支持中英文双语科普 + 萌宠娱乐短视频生产。

## 核心原则（成本可控）

| 项目 | 默认 | 说明 |
|---|---|---|
| DeepSeek API | ✅ 开启 | **唯一默认启用的付费 AI 服务**，按官方价格计费 |
| AI 图片生成 | ❌ 关闭 | 需用户主动开启，系统绝不自动调用 |
| AI 视频生成 | ❌ 关闭 | 同上 |
| 在线 TTS | ❌ 关闭 | 绝不自动切换；Piper 不可用时走上传配音 |
| 本地 TTS (Piper) | ✅ 开启 | 需自行安装 Piper + 模型；未装则用上传配音 |
| 本地素材优先 | ✅ 开启 | 不自动下载/抓取网络素材 |
| 网络素材下载 | ❌ 关闭 | 需授权信息 + 用户主动开启 |

> 不是"绝对零成本"：DeepSeek 按实际 Token 计费；费用账本单位 USD，峰谷与缓存命中无法 100% 自动识别，以官方账单为准。所有付费调用前都有强制闸门，关闭的服务绝不会被偷偷调用，失败时也**禁止自动回退到其他付费服务**。

## 环境要求

- Windows 11 64 位 / Python ≥ 3.11
- 已安装并跑通原版 MoneyPrinterTurbo v1.3.10（PetAI 复用其虚拟环境与依赖）
- NVIDIA RTX 3070 Ti / i7-12700H / 32GB RAM（GPU 非必需，无 CUDA 也能跑基本流程）
- FFmpeg：原项目已通过 `imageio-ffmpeg` 内置二进制提供，无需单独安装

PetAI **不引入任何新第三方依赖**，详见 `PetAI/requirements.txt`。

## 快速开始

### 1. 安装依赖（如原项目已跑通则跳过）
```bat
uv sync --frozen
```

### 2. 配置 API Key
编辑项目根目录 `.env`（由 `.env.example` 复制）：
```
DEEPSEEK_API_KEY=你的真实密钥
```
> `.env` 已在 `.gitignore` 中，不会提交；Key 只从环境变量/.env 读取，绝不写入前端或日志。

### 3. 一键初始化
双击项目根目录 `petai.bat`，或在命令行：
```bat
petai.cmd init
petai.cmd test-deepseek
```
`init` 会创建 `PetAI/config/petai.toml` 与数据库，**幂等、不覆盖已有配置**。

### 4. 使用 CLI
```bat
petai.cmd status           :: 成本控制中心（服务开关 + 本月/累计费用）
petai.cmd tts-status       :: 本地 TTS 能力、许可证、模型登记
petai.cmd test-deepseek    :: 连接测试（一条极短请求，会记一笔 Token 账）
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
```

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
│   ├── script_service.py       # 双语脚本工厂
│   ├── publish_service.py      # 双市场发布包（只生成文件，不自动发布）
│   ├── local_asset_service.py  # 本地素材（不覆盖原件）
│   ├── tts_service.py          # 本地配音（Piper + 上传兜底，绝不切在线）
│   ├── subtitle_service.py     # 本地 SRT（+ 可选 whisper）
│   └── video_service.py        # 视频生产（复用 MPT 引擎，中英双版本）
├── tests/                  # 56 个 pytest 测试
├── docs/                   # 本文档 + API 配置 + 测试报告 + 升级回滚
└── storage/                # 本地素材/音频/字幕/导出（运行时数据）
```

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
