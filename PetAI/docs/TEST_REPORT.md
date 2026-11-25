# 测试报告（真实执行结果）

执行日期：2026-11-25　环境：Windows 11 / Python 3.11.15 / MoneyPrinterTurbo v1.3.10

## 汇总

| 测试集 | 结果 | 说明 |
|---|---|---|
| PetAI 全量 | **56 passed**（17.82s） | 阶段 2~4 全部新增测试 |
| 原项目核心抽验 | **81 passed / 5 skipped / 31 subtests** | `test_task.py` + `test_state.py` |
| DeepSeek 真实连接 | ✅ 成功 | deepseek-flash 返回 "OK"，9 tokens，非估算 |
| 真实视频合成端到端 | ✅ 成功 | 产出 H.264 / 1080×1920 / 30fps MP4 |
| 原项目核心文件改动 | **0** | diff 对比备份确认 |

## 任务书 8 项必测逐项对照

### 测试 1：DeepSeek 连接 ✅
- `petai.cmd test-deepseek`：model=deepseek-flash，message="OK"，tokens=9，estimated=False
- `list_models()` 返回官方当前模型：`deepseek-flash` / `deepseek-v4-pro`
- 退役模型 `deepseek-chat` 已从前文/配置中清除；API 失败路径有 mock 测试验证报错

### 测试 2：成本控制 ✅
- test_cost_guard：关闭 AI 图片/视频/在线 TTS 后调用闸门 → `ServiceDisabledError`，不发包
- 无静默回退：DeepSeek 失败 → `assert_no_fallback()` 明确拒绝切换其他 provider（测试覆盖）
- 缺 Key → `MissingCredentialError`，不切换别家
- 日志脱敏：`test_key_not_in_logs` 用 mock 验证 Key 不出现在日志
- 价格表可配置（测试 `test_cost_uses_config_price_not_hardcoded`），峰谷倍率、缓存命中独立计价
- 费用单位 USD、估算标记、月/累计统计齐全

### 测试 3：素材管理 ✅
- 上传猫/狗图片：`test_upload_does_not_overwrite` 等验证同名不覆盖、原件不改
- 分类、标签、搜索：`test_list_and_search`
- 预览：素材索引含 path/kind 可供 UI 预览（本版以 API/CLI 为主，无独立 WebUI 页面）
- 本地优先选取：`select_local_first`

### 测试 4：双语脚本 ✅
- `test_bilingual_script`：中文脚本、英文脚本、分镜、标题简介标签均生成
- 英文基于事实本地化改写（非逐字翻译）：`test_localize_to_english`
- C 级未批准知识不得写入脚本：`test_high_risk_knowledge_blocked_in_script`

### 测试 5：本地配音 ✅
- `test_voice_file_bypasses_any_tts_engine`：上传配音直接使用，不调用任何 TTS 引擎
- Piper 不可用 → 明确报 `LocalTTSNotAvailable`，**绝不切在线 TTS**（`test_piper_not_available_raises_and_never_uses_online`）
- 音量/语速、许可证登记：`test_tts_capabilities_expose_license`
- 注：环境未装 Piper，Piper 实际合成未做端到端（需用户自行安装模型）；上传配音链路已完整验证

### 测试 6：视频生产 ✅
- 单元测试（注入伪合成函数）：9:16 参数、中英双版本、重新合成、空素材拦截等 6 项
- **真实端到端（非 mock）**：3 张本地图片 + ffmpeg 静音音频 5s + 本地 SRT → 真实 MPT 引擎：
  - 产物：H.264 High / yuv420p / **1080×1920** / 30fps / 5.00s / 444547 bytes
  - 字幕：本地生成 SRT 并校验通过
  - 中英独立导出：`produce_bilingual` 单测验证两版 output_path 不同、两条独立记录

### 测试 7：知识审核 ✅
- 高风险（C 级）内容 → `review_status=pending`，`can_publish()` 返回不可发布
- 未审核不得批准、不得从 C 下调到 A：`test_cannot_downgrade_risk`
- 禁止编造来源：来源只接受用户录入，AI 整理后原样保留
- 急症自动追加"联系当地兽医"声明；`answer_comment()` 拒绝个体诊断
- 作者身份强制 learner

### 测试 8：原项目兼容性 ✅
- 原 WebUI 依赖（`app.asgi`、配置加载）导入正常
- 原任务管理测试 81 passed / 5 skipped / 31 subtests
- 原 `config.toml` 未被 PetAI 代码写入（grep 确认 PetAI 无 config.toml 写入）
- diff 对比备份：核心文件零差异，`webui.bat` 等启动脚本未改动

## 过程中发现并修复的真实缺陷（非预设）

1. 闸门错误被 retry 循环误当网络错误重试 → 改为策略拒绝立即透传
2. 配置默认值浅拷贝导致实例间串扰 → 改深拷贝
3. `references` 是 SQL 保留字导致建表失败 → 列名加双引号
4. 本地化脚本重复编码 JSON → 修正
5. `deepseek-chat` 是退役模型名 → 改 `deepseek-flash`
6. 阶段 2 价格表写成人民币占位值 → 校正为官方 USD + 峰谷 + 缓存命中
7. thinking 模式吞掉短回复正文 → 加 `thinking_enabled` 默认关闭
8. 测试用真实 Key 触网计费 → 改 mock
9. `.env.example` 误留真实 Key → 改回占位符
10. `validate_srt` 作用域错误导致导入失败 → 提为模块级函数
11. `list_productions` 无筛选时 params=None 报错 → 改为空元组

## 未验证 / 边界说明（诚实标注）

- **Piper 真实合成未跑**：本机未安装 Piper，按任务书要求走上传配音兜底；Piper adapter 代码已就绪但需用户装好引擎与模型后自行验证。
- **WebUI 独立宠物页面未开发**：本版以 CLI + Python API 为主，原 WebUI 完整保留；任务书阶段 3~4 未强制要求新增 WebUI 页面。
- **真实账单对账未做**：费用为按官方价格表的估算，峰谷/缓存命中以官方账单为准。
- 费用账本中最早一笔 `deepseek-chat` 记录是价格校正前用占位价算的（$0.000024），仅为历史痕迹，不作对账依据。
