# 升级与回滚说明

## 设计前提：原项目零改动

PetAI 采用"只新增、不改原核心"策略：
- 所有新代码在独立的 `PetAI/` 包内。
- 未修改任何 MPT 核心源码（`app/`、`webui/`、`main.py` 等）。
- 仅修改了两个根目录文件：`.gitignore`（新增忽略项）、`.env.example`（配置模板）。
- 不写入原 `config.toml`；PetAI 用自己的 `PetAI/config/petai.toml` 与 `PetAI/storage/petai.db`。

因此 **PetAI 模式与 MoneyPrinterTurbo 原有模式完全并存**，可随时开关或整体移除。

## 升级安装步骤

1. **备份**（务必先做）：
   ```bat
   cd /d D:\Code
   xcopy /E /I /H MoneyPrinterTurbo-main MoneyPrinterTurbo-backup-手动备份
   ```
   （本次开发已自动备份于 `D:\Code\MoneyPrinterTurbo-backup-20261120-172209`）

2. **确认原项目可用**：先按原版方式跑通 `webui.bat`，确保基线正常。

3. **安装 PetAI**：把 `PetAI/` 目录、`petai.bat`、`petai.cmd`、更新后的 `.gitignore` 与 `.env.example` 放入项目根目录。

4. **初始化**：
   ```bat
   petai.bat
   ```
   或 `petai.cmd init`。生成 `petai.toml` 与数据库（幂等，不覆盖已有）。

5. **配置 Key**：在 `.env` 填 `DEEPSEEK_API_KEY`，运行 `petai.cmd test-deepseek`。

6. **验证**：`petai.cmd status`、`petai.cmd tts-status`，以及 `pytest -q PetAI/tests`。

## 回滚步骤（完全移除 PetAI，恢复原版）

因为原核心零改动，回滚极简：

1. 关闭所有运行中的 PetAI / WebUI 进程。
2. 删除新增文件/目录：
   ```bat
   rmdir /S /Q PetAI
   del petai.bat petai.cmd
   ```
3. 还原两个被修改的根文件（从本次备份恢复）：
   ```bat
   copy /Y "D:\Code\MoneyPrinterTurbo-backup-20261120-172209\MoneyPrinterTurbo-main\.gitignore" .
   copy /Y "D:\Code\MoneyPrinterTurbo-backup-20261120-172209\MoneyPrinterTurbo-main\.env.example" .
   ```
   > 注意：`.env` 里是你自己的 Key，回滚时**不要删**；如想彻底清理，手动删除即可。
4. 启动原版 `webui.bat`，确认一切正常。

**回滚后原 MoneyPrinterTurbo v1.3.10 与改造前完全一致。**

## 完全恢复（含用户数据）

若需连同运行数据一起恢复到开发前状态，直接用备份目录整体替换：
```bat
rmdir /S /Q MoneyPrinterTurbo-main
xcopy /E /I /H MoneyPrinterTurbo-backup-20261120-172209\MoneyPrinterTurbo-main MoneyPrinterTurbo-main
```

## 配置兼容性

- 原 `config.toml` 完全不动，原版 WebUI 的所有设置（配音、字幕、素材源等）保持原样。
- `config.toml` 在开发中出现的少量差异是**用户自己运行 WebUI 时 Streamlit 保存的偏好**（如 `config.ui.*`），与 PetAI 无关，PetAI 代码从不写入它。
- 若从阶段 2 老版本 `petai.toml` 升级，数据库有幂等迁移（如 `cost_cny` → `cost_usd`、新增 `currency`/`cache_hit_tokens` 列），不会丢历史记录。

## 版本对应

| 组件 | 版本 |
|---|---|
| MoneyPrinterTurbo | v1.3.10（pyproject.toml / app/__init__.py） |
| Pet AI Content Factory | V1.0（阶段 1~5） |
| Python | ≥ 3.11 |
