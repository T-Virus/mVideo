"""用桌面免费豆包客户端生成中英双语脚本（GUI 自动化，零 API 费用）。

设计边界（必读）：
1. 不调用 DeepSeek 或任何在线付费 API；只操作用户本机已登录的豆包客户端。
2. 复用 ScriptService 的存储/审核/事实来源约束：本服务只负责"取回 JSON"，
   写库与 C 级知识闸门仍由 ScriptService.generate 统一管理。
3. 取回方式：发送 prompt → 等待 → 点击 AI 回复的"复制"图标 → 从剪贴板读 JSON。
   实测该图标在 1920x1080 下位于 (631, 848)；坐标集中在 [doubao].coords，
   客户端改版后改配置即可。
4. 生成重试：豆包偶发返回非 JSON 或空内容时自动重试一次；仍失败则明确报错，
   绝不静默改用 DeepSeek（避免用户以为省钱实际在计费）。
5. 依赖 pyautogui / pyperclip 可选；未安装时明确报错，不影响其他功能。
"""
from __future__ import annotations

import json
import time
from contextlib import nullcontext
from typing import Any

from PetAI.services.doubao_desktop_service import DoubaoNotAvailable, DoubaoDesktopService
from PetAI.utils.safe_log import get_logger

logger = get_logger("doubao_script")

# 业务字段：用于在整页文本的多个 JSON 里挑出真正的结果，而不是提示词模板。
_BUSINESS_KEYS = {
    "topics", "plans", "script", "storyboard", "title", "content",
    "data_source", "asset_keywords", "hashtags",
}


def looks_like_result(data: dict[str, Any]) -> bool:
    """判断解析结果像不像真正的业务结果，而不是提示词里的 JSON 模板。"""
    topics = data.get("topics") or data.get("plans")
    if isinstance(topics, list):
        for item in topics:
            if isinstance(item, dict) and str(item.get("title") or "").strip():
                return True
    if str(data.get("script") or data.get("content") or "").strip():
        return True
    storyboard = data.get("storyboard")
    return isinstance(storyboard, list) and bool(storyboard)


def extract_json(text: str) -> dict[str, Any]:
    """从整页文本/回复里挑出最像业务结果的 JSON 对象。

    改为「全选复制」后，剪贴板会带上用户消息、侧栏标题等噪音，
    因此解析所有候选对象，优先业务字段多、体积大的那个。
    """
    if not text:
        raise ValueError("豆包回复为空")
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    index = 0
    while True:
        start = text.find("{", index)
        if start < 0:
            break
        try:
            obj, end = decoder.raw_decode(text[start:])
        except ValueError:
            index = start + 1
            continue
        if isinstance(obj, dict):
            candidates.append(obj)
            index = start + end
        else:
            index = start + 1
    if not candidates:
        raise ValueError("豆包回复中未找到有效 JSON 对象")

    def score(obj: dict[str, Any]) -> tuple[int, int, int]:
        return (
            len(set(obj) & _BUSINESS_KEYS),
            len(obj),
            len(json.dumps(obj, ensure_ascii=False)),
        )

    return max(candidates, key=score)


class DoubaoScriptService:
    def __init__(self, config: Any, desktop: DoubaoDesktopService) -> None:
        self.config = config
        self.desktop = desktop
        self.wait_seconds = float(config.get("doubao", "script_wait_seconds", 25) or 25)
        self.poll_seconds = 6.0  # 轮询取回间隔；测试可调小
        self.copy_x, self.copy_y = self._copy_coords(config, desktop)
        self.reply_x, self.reply_y = self._reply_coords(config)
        self.new_chat_x, self.new_chat_y = self._newchat_coords(config, desktop)

    @staticmethod
    def _reply_coords(config: Any) -> tuple[int, int]:
        """点击回复正文的坐标；用于「点正文 → Ctrl+A → Ctrl+C」取回。

        不依赖复制图标坐标：回复越长，图标位置越会往下漂。
        """
        coords = config.get("doubao", "coords", {}) or {}
        value = coords.get("script_reply") if isinstance(coords, dict) else None
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return int(value[0]), int(value[1])
        return 980, 500

    @staticmethod
    def _copy_coords(config: Any, desktop: DoubaoDesktopService) -> tuple[int, int]:
        coords = config.get("doubao", "coords", {}) or {}
        value = coords.get("script_copy") if isinstance(coords, dict) else None
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return int(value[0]), int(value[1])
        # 默认取素材服务的 input 坐标做缩放，再叠加复制按钮相对偏移。
        # 1920x1080 基准：复制图标 (631, 848)。
        return 631, 848

    @staticmethod
    def _newchat_coords(config: Any, desktop: DoubaoDesktopService) -> tuple[int, int]:
        coords = config.get("doubao", "coords", {}) or {}
        value = coords.get("new_chat") if isinstance(coords, dict) else None
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return int(value[0]), int(value[1])
        # 2026-11 版豆包：窗口左上角笔形「新建对话」图标
        return 272, 52

    def _grab_reply(
        self, pyautogui: Any, pyperclip: Any, sentinel: str
    ) -> dict[str, Any] | None:
        """尝试取回本次回复：复制按钮优先，全选复制回退。"""
        # 1) 回复下方的「复制」图标：短/中等回复位置稳定
        try:
            pyperclip.copy(sentinel)
            cx, cy = self.desktop._point_from_base(self.copy_x, self.copy_y)
            pyautogui.click(cx, cy)
            time.sleep(0.6)
            raw = pyperclip.paste()
            if raw.strip() and raw.strip() != sentinel:
                data = extract_json(raw)
                if looks_like_result(data):
                    return data
        except Exception:
            pass
        # 2) 回退：点回复正文 → Ctrl+A → Ctrl+C（长回复图标会漂移）
        try:
            pyperclip.copy(sentinel)
            rx, ry = self.desktop._point_from_base(self.reply_x, self.reply_y)
            pyautogui.click(rx, ry)
            time.sleep(0.3)
            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.2)
            pyautogui.hotkey("ctrl", "c")
            time.sleep(0.4)
            raw = pyperclip.paste()
            if raw.strip() and raw.strip() != sentinel:
                data = extract_json(raw)
                if looks_like_result(data):
                    return data
        except Exception:
            pass
        return None

    def _start_new_chat(self, pyautogui: Any) -> None:
        """点击新建对话并校验窗口标题，避免旧会话内容混入取回结果。"""
        px, py = self.desktop._point_from_base(self.new_chat_x, self.new_chat_y)
        pyautogui.click(px, py)
        time.sleep(1.4)
        handles = self.desktop._find_windows()
        if not handles:
            return
        title = self.desktop.window_title(handles[0])
        if " - " not in title:
            return
        # 再点一次：首次点击可能被弹窗/加载状态吃掉
        pyautogui.click(px, py)
        time.sleep(1.6)
        title = self.desktop.window_title(handles[0])
        if " - " in title:
            raise RuntimeError(
                f"豆包新建对话失败（当前窗口：{title}）。旧内容会污染取回结果，已中止。"
                "请手动新建对话后重试，或更新 [doubao].coords.new_chat。"
            )

    def capabilities(self) -> dict[str, Any]:
        return {
            "available": self.desktop.capabilities()["gui_dependencies"],
            "wait_seconds": self.wait_seconds,
            "copy_coords": [self.copy_x, self.copy_y],
            "note": (
                "脚本由本机豆包客户端生成，不调用 DeepSeek，不产生 API 费用。"
                "写库仍走 ScriptService 的审核与事实约束。"
            ),
        }

    # ---------- 对外主接口 ----------
    def generate_json(
        self,
        prompt: str,
        task_id: str = "",
        wait_seconds: float | None = None,
    ) -> dict[str, Any]:
        """向豆包发送 prompt 并取回 JSON；失败抛错，绝不回退到付费服务。

        wait_seconds 可按任务覆盖：联网检索热门需要更久（建议 60s），
        普通短脚本用配置里的默认值。
        """
        if not prompt or not prompt.strip():
            raise ValueError("豆包脚本 prompt 为空")
        wait = float(wait_seconds) if wait_seconds is not None else self.wait_seconds
        self.desktop.require_ready()
        # 结束后自动切回浏览器并响提示音；测试用的替身没有该方法时退化为普通上下文。
        session_factory = getattr(self.desktop, "generation_session", None)
        session = (
            session_factory("脚本已生成并取回")
            if callable(session_factory)
            else nullcontext()
        )
        with session:
            self.desktop.activate()

            last_error: Exception | None = None
            for attempt in range(1, 3):  # 最多 2 次
                try:
                    pyautogui = self.desktop._pyautogui
                    pyperclip = self.desktop._pyperclip
                    # 新建对话，避免历史内容混入取回结果
                    self._start_new_chat(pyautogui)

                    ix, iy = self.desktop._point("input")
                    pyautogui.click(ix, iy)
                    time.sleep(0.4)
                    pyautogui.hotkey("ctrl", "a")
                    time.sleep(0.2)
                    pyautogui.press("backspace")
                    time.sleep(0.2)
                    pyperclip.copy(prompt.strip())
                    time.sleep(0.5)
                    pyautogui.hotkey("ctrl", "v")
                    time.sleep(0.8)

                    # 直接 Enter 发送：不点发送按钮，避免豆包改版后坐标漂移。
                    # 输入框支持 Enter 发送、Shift+Enter 换行。
                    pyautogui.press("enter")

                    # 取回方式：轮询「复制按钮 / 点正文全选复制」。
                    # 不用固定 sleep，也不点「复制」图标：
                    # - 联网检索/长回复生成时间不确定，固定等待会拿到提示词本身；
                    # - 回复一长，复制图标位置会往下漂。
                    # 每轮只接受「像真正结果」的 JSON，避免把提示词模板当结果。
                    sentinel = f"__PETAI_WAIT_{attempt}_{int(time.time())}__"
                    deadline = time.time() + max(wait, 1.0)
                    data: dict[str, Any] | None = None
                    while time.time() < deadline:
                        time.sleep(self.poll_seconds)
                        data = self._grab_reply(pyautogui, pyperclip, sentinel)
                        if data is not None:
                            break
                    if data is None:
                        raise RuntimeError(
                            "未取到有效结果：可能生成尚未完成，或回复被折叠遮挡。"
                            "请调大 [doubao].script_wait_seconds（联网检索建议 120），"
                            "或调整 [doubao].coords.script_copy / script_reply。"
                        )
                    logger.info("豆包脚本 JSON 取回成功 task=%s attempt=%d", task_id, attempt)
                    return data
                except Exception as exc:  # noqa: BLE001 - 明确记录后重试
                    last_error = exc
                    logger.warning("豆包脚本生成第 %d 次失败 task=%s: %s", attempt, task_id, exc)
                    time.sleep(1.0)

            raise RuntimeError(
                f"豆包脚本生成失败（已重试 1 次）：{last_error}。"
                "本系统不会自动切换到 DeepSeek，请检查豆包界面/坐标配置后重试。"
            )
