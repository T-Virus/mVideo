# 测试报告（真实执行结果）

执行日期：2026-11-25　环境：Windows 11 / Python 3.11.15 / MoneyPrinterTurbo v1.3.10

## 汇总

| 测试集 | 结果 | 说明 |
|---|---|---|
| PetAI 全量 | **107 passed**（57.9s） | 含一键生产编排、分镜链路、零成本链路、图片转视频 |
| 原项目核心抽验 | **81 passed / 5 skipped / 31 subtests** | `test_task.py` + `test_state.py` |
| DeepSeek 真实连接 | ✅ 成功（历史） | deepseek-flash 返回 "OK"，9 tokens；**现已默认关闭** |
| 真实视频合成端到端 | ✅ 成功 | 产出 H.264 / 1080×1920 / 30fps MP4 |
| 豆包图片 → 本地视频 | ✅ 成功 | 5.00s / 1080×1920 / H.264 |
| AppTest 页面渲染 | **9/9 通过** | 含更名为「运行状态」的页面 |
| 原项目核心文件改动 | **0** | diff 对比备份确认 |

## 任务书 8 项必测逐项对照

### 测试 1：DeepSeek 连接 ✅（历史记录；现已默认关闭）
- 早期实测：model=deepseek-flash，message="OK"，tokens=9，estimated=False
- `list_models()` 返回官方当前模型：`deepseek-flash` / `deepseek-v4-pro`
- 退役模型 `deepseek-chat` 已从前文/配置中清除；API 失败路径有 mock 测试验证报错
- **v1.1 起默认 `[services].deepseek = false`**：即使填了 Key，闸门也会阻断，界面不再提供连接测试按钮

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
- ~~WebUI 独立宠物页面未开发~~ **已补做（2026-11-25）**：新增独立界面 `PetAI/webui.py` + 启动器 `petai-webui.bat`（8601 端口），原 WebUI（8501）完整保留。验证见下方"图形界面验证"。
- **真实账单对账未做**：费用为按官方价格表的估算，峰谷/缓存命中以官方账单为准。
- 费用账本中最早一笔 `deepseek-chat` 记录是价格校正前用占位价算的（$0.000024），仅为历史痕迹，不作对账依据。

---

## 图形界面验证（2026-11-25 补做）

用户反馈"看到界面不知道怎么操作"，按"独立界面 + 首页向导 + 侧栏菜单"方案补做。

### 交付
- `PetAI/webui.py`：独立 Streamlit 界面，9 个菜单（首页向导 + 8 个管理页）
- `petai-webui.bat`：一键启动器，独立端口 8601（不与原 WebUI 的 8501 冲突）

### 测试结果
- **语法/导入**：`py_compile` 通过；模块可导入（9 菜单 + 6 向导步骤）
- **逐页面渲染（Streamlit AppTest）**：**9/9 PASS**
  首页向导、选题中心、知识库与审核、脚本工厂、素材管理、视频工厂、导出中心、成本控制、设置
- **交互级验证（AppTest 模拟点击）**：
  - 首页成本条正确显示模型 deepseek-flash 与"仅 DeepSeek"付费边界 ✅
  - 模拟填入标题并点"创建选题"按钮 → 真实调用服务成功、无异常 ✅（测试数据已清理）
  - 成本控制页渲染 3 个 JSON 区块（服务状态/本月/累计）✅
- **真实启动验证**：`streamlit run PetAI/webui.py --server.port=8601` → Uvicorn 启动成功，健康检查 `HTTP 200`，测试后已停止进程
- **原项目零改动**：diff 复核确认核心源码未动；`.git`/`service.bat`/`service.ps1` 为用户自行新增，非 PetAI 代码产生

### 修复的真实缺陷
- 设置页调用 `build_video_params()` 时动态导入 `app.models.schema` 在 AppTest/非标准启动上下文失败 → 改为直接从 PetAI 自有配置读取画幅参数，界面零脆弱依赖

### 设计约束（界面层安全）
- 界面**不提供**开启 AI 图片/AI 视频/在线 TTS 的入口——关闭的付费服务只能手动改配置开启
- Key 只从 `.env`/环境变量读取，界面只读展示来源，不展示 Key 值、不写日志
- 首页六步向导每步都有明确的"下一步"引导，且前置条件不满足时给出提示（如没选素材不能合成）

---

## 功能正确性核查（2026-11-25 第二轮）

用户要求"核查并验证功能正确性"，对界面做了字段级 + 逻辑级深度核查，发现并修复 4 个真实功能缺陷：

### 发现并修复的缺陷
1. **素材字段错配**：界面读 `name`/`use_count`，服务实际返回 `filename`/`usage_count`
   → 素材列表原本显示为路径、使用次数恒为 0。已修正。
2. **素材分类非法**：界面上传下拉写死 `cats`/`dogs`，服务的合法分类是
   `cat_video`/`dog_video`/`cat_image`/`dog_image`/`educational`/`audio`/`video`，
   原值会被静默 fallback 到 educational 目录。已改为合法枚举 + 中文标签。
3. **审核关卡走过场（重要）**：向导第 3 步让用户审核知识，但第 2 步生成脚本时未传
   `knowledge_ids`，审核过的知识根本没进脚本。已新增"用已批准知识重新生成中英脚本"
   入口，真正把审核结果写进脚本。
4. **字幕时长错位（重要）**：界面把脚本目标时长（如 45s）当 `audio_duration` 传给合成，
   而用户配音可能只有 4s，导致字幕排在 45s 上、视频只有 4s，字幕几乎不可见。
   已改为传 0，让服务按真实音频时长生成字幕时间轴。

### 正确性验证（非"仅无异常"）
- 素材：真实上传 → 界面字段读取显示 `cat.jpg · image · 使用 0 次`；cat/dog 筛选正确
- 审核关卡：C 级未批准 → 脚本引用被拒绝 ✅；批准后可引用且 knowledge_ids 正确记录 ✅；
  双语生成均可引用已批准知识 ✅（5/5）
- 时长修复实证：真实 4s 音频 → 字幕末尾 `00:00:04,000`；错误传 45s → `00:00:45,000`
  （证明修复前确实错位，修复后对齐）
- 全量回归：PetAI 56 passed；AppTest 9/9 页面渲染通过

### 说明
本轮两次"[FAIL]"均为验证脚本自身断言写法问题（AppTest 的 options 返回显示文本而非底层值），
复核底层实现正确，非功能缺陷。

---

## 时长自动对齐（2026-11-25 第三轮）

用户反馈"脚本生成后字幕和视频时长不一致"。根因不止界面传参一处：脚本目标时长、
真实配音时长、MPT 素材拼接时长（含安全余量）三者本来就不相等，只修界面传参无法根治。
新增 `PetAI/services/alignment_service.py`，以**真实配音时长**为唯一时间基准做三层对齐。

### 实现
1. **字幕**：`align_srt_to_duration()` 把任意 SRT 时间轴缩放到音频时长，最后一条精确对齐
   末尾；同时消除重叠、倒序、越界。script 与 whisper 两种模式都经过这一步。
2. **成片**：`align_video_to_audio()` 用 ffprobe/ffmpeg 探测时长；MPT 的安全余量让成片偏长时
   用 `-t` 精确裁剪，偏短时用 `tpad` 克隆末帧补齐；容差 0.3s 内不重编码，避免画质损失。
3. **自检**：`verify_alignment()` 输出配音/字幕/视频三者时长与最大偏差，写入生产记录
   `alignment_report`，界面直接展示；已生成的视频可在「视频工厂」点「校正时长对齐」修复。
4. 配置 `[video] auto_align = true`（默认）、`align_tolerance = 0.3`；关闭后完全不干预。

### 验证（13 项新增测试全部通过）
- SRT 缩放：5s → 12s 末尾精确为 `00:00:12,000`；20s → 6s 尾部裁剪；已对齐的字幕字节不变；
  重叠时间轴被修复
- 成片对齐：偏长触发 FFmpeg `-t 8.000` 裁剪并写回文件；偏短触发 `tpad` 补齐；
  0.1s 偏差在容差内不调用 FFmpeg
- 生产集成：脚本目标 45s、真实配音 7.5s → 字幕末尾 `00:00:07,500`（证明真实音频优先），
  对齐报告持久化到数据库；关闭 `auto_align` 后对齐服务完全不被调用
- 已有记录校正：人为把字幕末尾改成 20s → 一键校正后回到真实音频 9s，报告同步更新
- **真实 FFmpeg 端到端（非 mock）**：8s 静音配音 + 10s 素材视频 + 5s SRT →
  字幕缩放到 8.00s、成片裁剪 10.00s → 8.00s，自检 `max_delta=0.0 / aligned=True`
- 全量回归：PetAI **69 passed**；AppTest 9/9 页面渲染通过；原项目核心测试无回归

### 已知边界
- 成片尾部的精确裁剪需要重编码（H.264 / crf 20），15~60s 视频开销可接受；
  如需完全避免重编码，可把 `align_tolerance` 调大。
- ffprobe 不在 PATH 时会自动退回 `ffmpeg -i` 解析，再退回 MoviePy；三者都失败则跳过
  视频对齐并保留原成片，不会破坏产物。

---

## 桌面豆包客户端素材生成（2026-11-25 第四轮）

用户要求"素材能不能直接 AI 生成？调用桌面的免费豆包客户端"。豆包无公开 API，
采用 GUI 自动化模拟鼠标键盘，不调用任何付费接口。

### 实现
- 新增 `PetAI/services/doubao_desktop_service.py`：检测客户端/依赖、激活并最大化窗口、
  关闭残留弹窗（Esc）、切换图像/视频模式、剪贴板粘贴中文提示词、点击发送、等待生成、
  打开预览下载、复制入库；所有坐标集中在 `[doubao].coords`（1920×1080 基准，自动缩放）。
- 支持从脚本 `storyboard` / `asset_keywords` 本地生成作图提示词（不额外调用 DeepSeek）。
- 素材记录标记 `source=doubao-desktop-free`，授权字段注明"商用授权需用户自行向平台确认"。
- 素材管理页新增「用桌面豆包生成素材（免费，不调用 API）」区块。

### 验证
- 单元测试 5 项：坐标缩放、分镜提示词去重、缺依赖明确报错、生成全流程 mock、视频超时报错
- **真实端到端（非 mock）**：本机豆包客户端（D:\Tools\Doubao\app\Doubao.exe）输入柯基犬提示词
  → 约 38 秒完成生成、预览下载 3.7MB PNG → 入库 `PetAI/storage/dogs/`；
  人工核对图片内容确为竖屏柯基犬草地奔跑图，带"豆包AI生成"水印，分类/来源/授权字段正确
- AppTest 9/9 页面渲染通过；PetAI 全量 **74 passed**

### 边界
- GUI 自动化依赖界面布局，豆包改版后需更新坐标配置；其他分辨率按比例缩放。
- 视频生成流程已实现但本轮未做真实端到端（耗时长、受豆包免费额度/排队影响）；
  默认等待 180 秒，可按实际调整；超时会明确报错，不影响已有素材。
- 下载文件沿用豆包当前对话标题命名，入库时按原文件名复制、同名自动加后缀不覆盖。

---

## 豆包双语脚本 + 免费本地配音（2026-11-25 第五轮）

用户要求"生成中英双语脚本的步骤也用豆包吧，配音也用可以吗"。

### 可行性实测结论
- **脚本：可行**。豆包对话可以生成严格 JSON，回复下方"复制"图标（1920×1080 下约 `(631, 848)`）
  能把完整 JSON 放入剪贴板。实测一次命中，取回的 6 个字段齐全。
- **配音：不可行**。豆包朗读喇叭只播放不导出；无本地音频缓存；
  用 ffmpeg dshow 虚拟声卡录制是静音（peak=1/rms=0.1）；
  用 soundcard WASAPI 回环录制也是全零（peak=0/rms=0）。
  故改用 **Windows 系统自带 SAPI5 离线语音**（Huihui 中文 / Zira 英文，零费用、无需联网）。

### 实现
- 新增 `doubao_script_service.py`：新建对话 → 输入 prompt → 发送 → 等待 → 点复制 → 解析 JSON。
  失败重试 1 次，**绝不回退 DeepSeek**。正文字段兼容 `script/content/voiceover/text`。
- `script_service.py` 增加 `script_engine` 开关（`doubao-desktop` / `deepseek`），默认豆包。
- 新增 `sapi_tts_service.py`，`tts_service.py` 的 engine 默认改为 `sapi`；Piper 仍保留可选。
- 界面：脚本工厂显示当前引擎；配音步骤可直接合成中/英文；设置页显示脚本与配音引擎状态。

### 验证
- **真实豆包脚本端到端（非 mock）**：取回完整 JSON，`script` 字段 169 字，
  标题/分镜/标签/关键词齐全。
- **双语脚本入库**：中文 167 字、英文 566 字，`review_status=pending`，
  C 级知识审核闸门仍由 ScriptService 统一管理，未被豆包引擎绕过。
- **SAPI 配音真实合成**：中文 Huihui / 英文 Zira 均产出有效 WAV；
  连续两次调用无 COM 崩溃（修复了 CoUninitialize 重复释放导致的段错误）。
- **完整链路端到端**：豆包脚本 → SAPI 配音（26.15s）→ MPT 合成 → 字幕对齐
  → 产出 H.264 / 1080×1920 / 26.17s MP4，对齐自检 `max_delta=0.02s / aligned=True`。
- 单元测试 8 项（JSON 提取 3 种格式 + 失败、引擎切换、豆包流程 mock、不回退付费、SAPI 真实合成）。
- PetAI 全量 **82 passed**；AppTest 9/9 页面渲染通过。

### 边界
- 豆包生成偶发取不到回复（本次双语各出现 1 次），重试机制覆盖；若连续失败会明确报错。
- 豆包脚本速度受对话长度影响，`[doubao].script_wait_seconds` 默认 25 秒可调。
- 豆包生成的英文可能超出目标字数（实测 566 字符），属内容质量问题，不影响功能。
- GUI 自动化依赖界面布局，改版后更新 `[doubao].coords`（含 `script_copy`、`new_chat`）。

## v1.1：界面与全链路改为「豆包 + 本地」

用户要求"界面也要调整，全部改为豆包+本地完成"。这次不只是改文案，而是把仍在调用付费 API 的入口一并切断。

### 服务层改动
- `[services].deepseek` 默认改为 `false`；`DEFAULT_CONFIG`、`petai.toml`、`petai.example.toml`、`.env.example` 同步。
- `TopicService.generate_batch` 增加 `doubao_scripts` 注入，选题批量生成改走豆包；
  `KnowledgeService.ai_organize` 同理。两者失败都直接报错，**不回退 DeepSeek**。
- `bootstrap.py` 调整装配顺序，把 `DoubaoScriptService` 注入脚本 / 选题 / 知识三个服务。
- 默认文本引擎统一为 `doubao-desktop`（脚本 / 选题 / 知识整理共用一个开关）。

### 图片素材合成闭环
- 豆包主要产出图片，而 MPT `combine_videos` 只接受视频。`VideoService` 新增
  `_image_to_clip()`：用本地 FFmpeg 把图片转成 9:16 静帧 mp4（默认 5 秒）再合成。
- `recompose()` 兼容历史记录里的原始图片路径。
- 离线测试通过 `image_transcode=False` 关闭真实编码，避免假图片触发 FFmpeg 卡死。

### 界面改动
- 顶部新增全局状态条：文本引擎 / 豆包客户端 / 配音 / 素材 / 合成 / 本月付费 API（默认 0 次 $0）。
- 首页与六步向导文案全部改为豆包 + 本地；第 4 步新增「用桌面豆包现场生成素材」。
- 「素材管理」与向导共用同一个豆包生成面板，顺带修复「填入分镜提示词」的 Streamlit 状态冲突。
- 「成本控制」更名为「运行状态」：展示本地链路、付费闸门是否全关、API 账本审计。
- 「设置」移除 DeepSeek 连接测试 / 模型列表按钮，改为豆包自检 + SAPI 音色 + 视频参数；
  付费 API 说明折叠到「高级」并明确不推荐。

### 验证
- PetAI 全量 **88 passed**（新增 5 项零成本链路测试 + 1 项手动启用测试）。
- 新增测试覆盖：默认配置无付费服务、选题/知识整理只调豆包、豆包失败不回退、
  图片素材真实本地转视频。
- 真实图片转视频：`猫咪爱纸箱的原因 (1).png` → 5.00s / 1080×1920 / H.264。
- AppTest **9/9** 页面渲染通过（菜单名已更新为「运行状态」）。
- 原项目核心零改动（`app/`、`config.toml` 无 diff）。

### 边界
- GUI 自动化需要豆包客户端已启动并登录；状态条会显示「未启动 / 依赖缺失」。
- 豆包免费额度与排队由客户端决定，PetAI 不绕过、不付费加速。
- 如需恢复 DeepSeek：手动改 `[services].deepseek = true` + `[script].engine = "deepseek"`
  并配置 Key；界面不提供一键开启，避免误触计费。

## v1.1.1：豆包操作后的窗口恢复与反馈

用户反馈："生成图片素材后就不自动执行了，每次操作完没有反馈，每次都需要手动返回网页。"

### 根因
- `generate_image` / `generate_video` 会把豆包最大化切到前台，但**操作结束从不切回**原窗口。
- 生成成功后 WebUI 立即 `st.rerun()`，普通 `st.success` 被清掉，用户回到页面只看到空表单，误以为没执行。
- 图片默认等待 30 秒，而实测生成约 38 秒，容易在未生成完时就去点下载，导致流程中断。
- Windows 前台锁定会让后台进程的 `SetForegroundWindow` 失败，原实现直接抛错，流程当场中断。

### 修复
- 新增 `DoubaoDesktopService.generation_session()`：操作前记录原前台窗口与豆包窗口位置，
  结束后还原豆包窗口 → 切回原窗口 → 响系统提示音；切回失败时最小化豆包兜底。
- `_activate()` 改为多重回退：最大化 + 短暂置顶 → AttachThreadInput → `SwitchToThisWindow`
  → 点击标题栏（真实鼠标事件），并且 `GetForegroundWindow()=0` 时不再误报失败。
- `DoubaoScriptService.generate_json()` 也套用同一会话：生成选题/脚本后同样自动切回。
- 图片默认等待从 30s 提到 45s（`[doubao].image_wait_seconds`）。
- WebUI 新增 `_flash()`：成功/失败提示跨 `st.rerun()` 保留；向导第 4 步生成后自动把新素材
  加入已选，并提示可直接下一步。

### 验证
- 新增 2 项测试：操作结束的恢复顺序（还原豆包 → 切回原窗口 → 提示音）、失败时也必须恢复。
- PetAI 全量 **90 passed**；AppTest **9/9**；原项目核心零改动。
- 说明：当前执行环境是非交互会话（`GetForegroundWindow()` 可能为 0），
  真实窗口切换需在本机交互桌面验证；逻辑已通过单元测试与调用顺序断言。

## v1.2：多镜头分镜方案与多素材合成

用户反馈："分镜头不应该是多个吗？素材不应该也一样吗？可以让豆包设计视频分镜方案……
现在跟原版的差距很大。"

### 根因
- prompt 没要求分镜数量，豆包常只给 2-4 条，且缺 `duration` / `shot`；
- 分镜生成后**从未被使用**：素材为空时只调 `select_local_first(count=1)`；
- MPT `video_concat_mode` 默认 `random`，即使多素材也会被打乱；
- 界面没有"分镜"概念，用户只能手动勾选零散素材。

### 改造
- prompt 要求 **6-8 个镜头**，每镜头含 `scene/duration/shot/description/asset_keyword/caption`。
- `ScriptService._normalize_storyboard()`：不足 4 条按口播句子补足；时长按权重归一；
  字段兜底；上限 8 条；不额外调用 AI。
- `LocalAssetService.plan_for_storyboard()`：按关键词逐镜配素材；`cat/dog/pet` 等通用词
  不算命中；同分优先未使用/少使用的素材；不足时轮换并标注 `matched=False`。
- `VideoService.build_video_params()` 固定 `VideoConcatMode.sequential`；
  `produce` / `produce_bilingual` 新增 `storyboard`，素材为空时按镜头自动配齐。
- `DoubaoDesktopService.generate_for_storyboard()` + `prompts_from_storyboard()`：
  按分镜批量生成图片，整批只切换/恢复一次窗口。
- WebUI：脚本页与向导展示分镜表；第 4 步新增「按分镜自动配本地素材」和
  「按分镜豆包批量生成」；第 6 步展示分镜与素材缺口并传入分镜。

### 验证
- PetAI 全量 **100 passed**（新增 10 项分镜测试）。
- AppTest **9/9** 页面渲染通过。
- **真实多素材合成**：红/绿/蓝三段 6s 素材 + 15s 音频 → 成片 15.0s，
  抽帧 2s 为红 `(250,0,0)`、8s 为绿 `(0,124,0)`、14s 为蓝 `(0,0,250)`，顺序正确。
- MPT 核心零改动。

### 边界
- 每段时长仍由 `[video].clip_duration`（默认 5s）控制，与原版一致，不是逐镜精确时长。
- 本地素材不足时自动配齐会复用素材；建议用豆包按分镜批量生成补足。
- 豆包批量生成每张约 40s，6 张约 4 分钟，期间请勿操作鼠标；整批完成后才切回页面。

## v1.3：一键生产（选分类 → 点一次 → 等结果）

目标：用户打开 `http://127.0.0.1:8602/`，只选分类，点一次，全流程自动跑完。

### 新增
- `HotTopicService`：按分类构造「近 7 天同分类热门」检索提示词，豆包取回后落库为选题，
  并如实标注 `data_source`（拿不到榜单时写「模型趋势判断，非平台实时榜单」）。
- `PipelineService`：串起 6 步——热门选题 → 中英脚本/分镜 → C 级风险拦截 →
  按分镜备素材（豆包失败回退本地）→ 配音合成 → 发布包，带 `progress` 回调。
- WebUI「一键生产」页：选分类 + 素材张数 + 是否英文，一个按钮，6 步进度条，完成后展示成片/发布包。
- `VideoService._diversify_materials`：本地素材少于分镜时，对重复素材做 5 种区域裁剪，
  避免整条视频同一画面（豆包生图不可用时的兜底）。

### 关键修复
- **发送改用 Enter**：不再依赖「发送按钮」坐标。豆包改版后该坐标极易漂移。
- 重新校准 `image_mode`/`video_mode`/`new_chat`，并加「新建对话后校验窗口标题」，
  防止旧会话内容混入取回结果。
- 取回改为「复制按钮优先 + 全选复制回退 + 轮询」：联网检索耗时不固定，
  固定等待会拿到提示词本身。

### 真实验证
- 热门检索：取回 2 条具体选题（如「原来猫咪偷偷观察你，是在打这个主意」），
  `data_source=模型趋势判断，非平台实时榜单`。
- 中英脚本：各 7 个分镜、合计 45 秒，含景别/画面/关键词。
- 缩短版一键生产（2 张素材、仅中文）：成片 41.26s，音/字/画 `max_delta=0.01s`，发布包已生成。
- PetAI 全量 **107 passed**；AppTest **10/10**。

### 后续修复：新对话直接出图（不点技能栏）
- 用户指出：不需要点「图像生成」技能，**开新对话直接发提示词即可**。
- 实测：新对话输入「请生成一张图片：…」→ Enter，豆包一次返回 4 张 9:16 图。
- 下载：点第一张图打开预览窗口 → 右上角下载箭头 `(1865,104)` → 落盘 Downloads。
- `_generate_image_once` 改为「激活 → 开新对话 → Enter → 点首图 → 下载」，
  不再依赖技能栏坐标；发送统一用 Enter。
- 真实结果：单张 2.71MB 入库；按分镜批量两张分别为 3.70MB / 3.38MB；
  完整一键 `material_source=doubao`，3 张豆包图，成片 49.44s，对齐 0.03s。
- 兜底仍保留：豆包失败时回退本地素材 + 5 种区域裁剪，避免画面完全重复。
