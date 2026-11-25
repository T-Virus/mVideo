"""调用桌面免费豆包客户端生成图片/视频素材（GUI 自动化，零 API 费用）。

设计边界（必读）：
1. 不调用任何在线付费 API，不需要 Key；只操作用户本机已登录的豆包客户端。
2. 豆包没有公开接口，本服务用 pyautogui 模拟鼠标键盘，**依赖界面布局**。
   所有关键坐标集中在 [doubao].coords，客户端改版后改配置即可，不改代码。
3. 坐标以 1920×1080 为基准；其他分辨率按比例缩放。
4. 生成内容保存到豆包默认下载目录，再复制进 PetAI 素材库（原件不改动、不覆盖）。
5. 授权信息只记录来源 "doubao-desktop-free"，商用授权由用户自行向平台确认。
6. 依赖 pyautogui / pyperclip 为可选依赖；未安装时明确报错并给出安装命令，
   绝不影响不使用本服务的原有功能。
"""
from __future__ import annotations

import ctypes
import shutil
import time
from contextlib import contextmanager
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from typing import Any

from PetAI.services.local_asset_service import IMAGE_EXTS, VIDEO_EXTS, LocalAssetService
from PetAI.utils.safe_log import get_logger

logger = get_logger("doubao")

BASE_WIDTH = 1920
BASE_HEIGHT = 1080
DEFAULT_COORDS: dict[str, list[int]] = {
    # 以下坐标按 2026-11 版豆包桌面端（1920×1080、最大化）实测校准。
    # 豆包改版后只需改 [doubao.coords]，不用改代码。
    "input": [900, 965],            # 输入框文本区中心
    "send": [1416, 985],            # 发送按钮（优先用 Enter，此坐标仅兜底）
    "image_mode": [969, 965],       # 输入框上方「图像生成」技能标签
    "video_mode": [1164, 965],      # 输入框上方「视频生成」技能标签
    "download": [1865, 104],        # 图片预览窗口右上角下载箭头
    "close_preview": [1896, 59],    # 预览窗口关闭按钮
}


class DoubaoNotAvailable(RuntimeError):
    """豆包客户端或 GUI 自动化依赖不可用。"""


class DoubaoGenerationError(RuntimeError):
    """豆包生成/下载流程失败。"""


class DoubaoDesktopService:
    def __init__(
        self,
        config: Any,
        assets: LocalAssetService,
        storage_root: str | Path | None = None,
        restore_ui: bool = True,
        notify: bool = True,
    ) -> None:
        self.config = config
        self.assets = assets
        self.storage_root = Path(storage_root) if storage_root else None
        # 操作结束后自动切回原窗口并响提示音；离线测试可关闭，避免移动真实窗口。
        self.restore_ui = bool(restore_ui)
        self.notify = bool(notify)
        self.download_root = Path(
            str(config.get("doubao", "download_dir", "") or "").strip()
            or (Path.home() / "Downloads")
        )
        self.image_wait = float(config.get("doubao", "image_wait_seconds", 30) or 30)
        self.video_wait = float(config.get("doubao", "video_wait_seconds", 180) or 180)
        coords = dict(DEFAULT_COORDS)
        configured = config.get("doubao", "coords", {}) or {}
        if isinstance(configured, dict):
            coords.update({k: list(v) for k, v in configured.items() if isinstance(v, (list, tuple))})
        self.coords = coords
        self._pyautogui: Any = None
        self._pyperclip: Any = None

    # ---------- 能力检测 ----------
    def capabilities(self) -> dict[str, Any]:
        deps = self._load_deps()
        return {
            "client_installed": self.find_executable() is not None,
            "client_running": bool(self._find_windows()),
            "gui_dependencies": deps,
            "download_dir": str(self.download_root),
            "coords": dict(self.coords),
            "note": (
                "调用本机已登录的豆包客户端，不走 API、不产生 API 费用。"
                "界面改版后需更新 petai.toml 的 [doubao].coords。"
                "生成内容的商用授权请自行向平台确认。"
            ),
        }

    @staticmethod
    def find_executable() -> Path | None:
        for candidate in (
            Path(r"D:\Tools\Doubao\app\Doubao.exe"),
            Path.home() / "AppData/Local/Doubao/Application/Doubao.exe",
        ):
            if candidate.is_file():
                return candidate
        from shutil import which

        found = which("Doubao") or which("doubao")
        return Path(found) if found else None

    def _load_deps(self) -> dict[str, bool]:
        if self._pyautogui is None:
            try:
                import pyautogui
                import pyperclip

                self._pyautogui = pyautogui
                self._pyperclip = pyperclip
                pyautogui.FAILSAFE = True
                pyautogui.PAUSE = 0.08
            except ImportError:
                return {"pyautogui": False, "pyperclip": False}
        return {"pyautogui": True, "pyperclip": True}

    def _require_deps(self) -> None:
        if not all(self._load_deps().values()):
            raise DoubaoNotAvailable(
                "缺少 GUI 自动化依赖。请在项目虚拟环境执行："
                ".venv\\Scripts\\python.exe -m pip install pyautogui pyperclip"
            )
        if not self._find_windows():
            raise DoubaoNotAvailable(
                "未检测到正在运行的豆包客户端。请先启动并登录豆包桌面版，再重试。"
            )

    # ---------- Win32 前台控制 ----------
    @staticmethod
    def _find_windows() -> list[int]:
        user32 = ctypes.windll.user32
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        handles: list[int] = []

        def collect(hwnd: int, _: Any) -> bool:
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if "豆包" in buf.value or "Doubao" in buf.value:
                    rect = wintypes.RECT()
                    user32.GetWindowRect(hwnd, ctypes.byref(rect))
                    if rect.right - rect.left > 300:
                        handles.append(hwnd)
            return True

        user32.EnumWindows(callback_type(collect), 0)
        return handles

    def _activate(self) -> None:
        """把豆包切到前台并最大化（多重回退，绕过 Windows 前台锁定）。"""
        handles = self._find_windows()
        if not handles:
            raise DoubaoNotAvailable("豆包窗口不存在，请先启动客户端。")
        hwnd = handles[0]
        user32 = ctypes.windll.user32
        # 1) 最大化并短暂置顶：不需要前台权限，先保证窗口可见在最前，
        #    否则后续鼠标点击可能落到遮挡窗口上，造成误操作。
        try:
            user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
            flags = 0x0001 | 0x0002 | 0x0040  # NOSIZE | NOMOVE | SHOWWINDOW
            user32.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(-1), 0, 0, 0, 0, flags)
            user32.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(-2), 0, 0, 0, 0, flags)
        except Exception:
            pass
        # 2) 常规切换；失败则点击标题栏（真实鼠标事件必然激活窗口）
        if not self._bring_to_front(hwnd, maximize=True):
            self._click_title_bar(hwnd)
        time.sleep(0.5)
        # fg=0 表示当前会话没有可判定的前台窗口（如远程/非交互桌面），
        # 此时不要误报失败——后续真实鼠标点击仍会激活窗口。
        foreground = user32.GetForegroundWindow()
        if foreground not in (0, hwnd):
            raise DoubaoNotAvailable("豆包窗口无法获得焦点，请手动点开客户端后重试。")
        # 关闭上一轮可能残留的图片预览/文件对话框，否则后续坐标全部错位。
        if self._pyautogui is not None:
            self._pyautogui.press("esc")
            time.sleep(0.4)

    # ---------- 操作结束后恢复用户窗口 ----------
    @staticmethod
    def window_title(hwnd: int) -> str:
        """读取窗口标题；新建对话后标题会从「会话名 - 豆包」变回「豆包」。"""
        try:
            user32 = ctypes.windll.user32
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            return buf.value
        except Exception:
            return ""

    @staticmethod
    def _foreground() -> int:
        try:
            return int(ctypes.windll.user32.GetForegroundWindow())
        except Exception:
            return 0

    @staticmethod
    def _placement(hwnd: int) -> tuple[int, int, int, int, bool] | None:
        """记录窗口位置/大小/是否最大化，用于操作后还原豆包窗口。"""
        if not hwnd:
            return None
        try:
            user32 = ctypes.windll.user32
            rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return None
            return (
                rect.left,
                rect.top,
                rect.right - rect.left,
                rect.bottom - rect.top,
                bool(user32.IsZoomed(hwnd)),
            )
        except Exception:
            return None

    def _restore_placement(self, hwnd: int, placement: Any) -> None:
        if not hwnd or not placement:
            return
        left, top, width, height, maximized = placement
        try:
            user32 = ctypes.windll.user32
            if not user32.IsWindow(hwnd):
                return
            if maximized:
                user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
            else:
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                if width > 200 and height > 200:
                    user32.MoveWindow(hwnd, left, top, width, height, True)
        except Exception:
            pass

    def _bring_to_front(self, hwnd: int, maximize: bool = False) -> bool:
        """把指定窗口切到前台，多重回退提高成功率。

        maximize=True 用于激活豆包；False 用于操作结束后恢复用户原来的窗口，
        并保持它原本的最大化状态。
        """
        if not hwnd:
            return False
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        try:
            if not user32.IsWindow(hwnd):
                return False
            if user32.GetForegroundWindow() == hwnd:
                return True
        except Exception:
            return False

        for _ in range(3):
            try:
                foreground = user32.GetForegroundWindow()
                fg_thread = user32.GetWindowThreadProcessId(foreground, None)
                target_thread = user32.GetWindowThreadProcessId(hwnd, None)
                current = kernel32.GetCurrentThreadId()
                user32.AttachThreadInput(current, fg_thread, True)
                user32.AttachThreadInput(current, target_thread, True)
                try:
                    if maximize:
                        user32.ShowWindow(hwnd, 3)      # SW_MAXIMIZE
                    elif user32.IsIconic(hwnd):
                        user32.ShowWindow(hwnd, 9)      # 仅最小化时 SW_RESTORE
                    else:
                        user32.ShowWindow(hwnd, 5)      # SW_SHOW，不改变最大化状态
                    user32.BringWindowToTop(hwnd)
                    # 模拟 Alt 键，解除 Windows 前台锁定
                    try:
                        user32.keybd_event(0x12, 0, 0, 0)
                        user32.keybd_event(0x12, 0, 2, 0)
                    except Exception:
                        pass
                    user32.SetForegroundWindow(hwnd)
                finally:
                    user32.AttachThreadInput(current, fg_thread, False)
                    user32.AttachThreadInput(current, target_thread, False)
                time.sleep(0.4)
                if user32.GetForegroundWindow() == hwnd:
                    return True
                # 最后回退：未文档化但通常可用，等价于任务栏点击
                try:
                    user32.SwitchToThisWindow(hwnd, True)
                    time.sleep(0.4)
                    if user32.GetForegroundWindow() == hwnd:
                        return True
                except Exception:
                    pass
                # 非交互会话：前台句柄恒为 0，继续重试没有意义
                if user32.GetForegroundWindow() == 0:
                    break
            except Exception:
                time.sleep(0.3)
        return user32.GetForegroundWindow() == hwnd

    def _click_title_bar(self, hwnd: int) -> bool:
        """点击窗口标题栏空白处激活窗口：真实鼠标事件不受前台锁定限制。"""
        try:
            import pyautogui

            rect = wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
            width = rect.right - rect.left
            x = rect.left + max(120, width // 2)
            y = rect.top + 18
            pyautogui.click(x, y)
            time.sleep(0.6)
            return ctypes.windll.user32.GetForegroundWindow() == hwnd
        except Exception:
            return False

    def notify_done(self, message: str = "已完成") -> None:
        """本地完成提示：系统提示音（不联网、不弹阻塞对话框）。"""
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass
        logger.info("豆包操作结束：%s", message)

    @contextmanager
    def generation_session(self, message: str = "已完成"):
        """包住一次 GUI 操作：结束后还原豆包窗口、切回浏览器并响提示音。

        这样用户点完生成后不需要再手动切回网页——操作结束会自动回到原窗口。
        """
        previous = self._foreground() if self.restore_ui else 0
        doubao_hwnd = 0
        placement = None
        if self.restore_ui:
            handles = self._find_windows()
            doubao_hwnd = handles[0] if handles else 0
            placement = self._placement(doubao_hwnd)
        ok = False
        try:
            yield
            ok = True
        finally:
            if self.restore_ui:
                self._restore_placement(doubao_hwnd, placement)
                restored = self._bring_to_front(previous) if previous else False
                if not restored and doubao_hwnd:
                    # 最后兜底：最小化豆包，让系统把焦点交给其他窗口，
                    # 用户至少不用面对"豆包挡在前面、网页在后面"的局面。
                    try:
                        ctypes.windll.user32.ShowWindow(doubao_hwnd, 6)  # SW_MINIMIZE
                    except Exception:
                        pass
            if self.notify:
                self.notify_done(message if ok else "豆包操作失败，请查看页面提示")

    # ---------- 坐标与输入 ----------
    def _point(self, name: str) -> tuple[int, int]:
        try:
            import pyautogui

            width, height = pyautogui.size()
        except Exception:
            width, height = BASE_WIDTH, BASE_HEIGHT
        x, y = self.coords.get(name, DEFAULT_COORDS[name])
        return (
            int(round(x * width / BASE_WIDTH)),
            int(round(y * height / BASE_HEIGHT)),
        )

    def _point_from_base(self, x: int, y: int) -> tuple[int, int]:
        """把 1920x1080 基准坐标按当前屏幕分辨率缩放。供脚本/配音服务复用。"""
        try:
            import pyautogui

            width, height = pyautogui.size()
        except Exception:
            width, height = BASE_WIDTH, BASE_HEIGHT
        return (
            int(round(x * width / BASE_WIDTH)),
            int(round(y * height / BASE_HEIGHT)),
        )

    def require_ready(self) -> None:
        """检查依赖与窗口可用性（公开入口，供脚本/配音服务调用）。"""
        self._require_deps()

    def activate(self) -> None:
        """激活豆包窗口（公开入口）。"""
        self._activate()

    def _click(self, name: str) -> None:
        x, y = self._point(name)
        self._pyautogui.click(x, y)
        time.sleep(0.4)

    def _new_chat(self) -> None:
        """开新对话：豆包会按自然语言提示词直接出图，不需要点「图像生成」技能。"""
        x, y = self._point_from_base(272, 52)
        self._pyautogui.click(x, y)
        time.sleep(1.6)

    def _input_prompt(self, prompt: str) -> None:
        text = (prompt or "").strip()
        if not text:
            raise ValueError("提示词为空，无法生成素材。")
        self._click("input")
        self._pyautogui.hotkey("ctrl", "a")
        time.sleep(0.2)
        self._pyautogui.press("backspace")
        time.sleep(0.2)
        # 剪贴板粘贴中文最稳；给 WebView 留出处理时间。
        self._pyperclip.copy(text)
        time.sleep(0.5)
        self._pyautogui.hotkey("ctrl", "v")
        time.sleep(0.8)

    def _snapshot(self) -> Any:
        from PIL import ImageGrab

        return ImageGrab.grab(all_screens=True).convert("RGB")

    # ---------- 下载 ----------
    def _new_download(self, before: set[str], media_type: str) -> Path | None:
        exts = IMAGE_EXTS if media_type == "image" else VIDEO_EXTS
        deadline = time.time() + 40
        while time.time() < deadline:
            candidates = [
                p for p in self.download_root.iterdir()
                if p.is_file() and p.suffix.lower() in exts and str(p) not in before
            ]
            if candidates:
                return max(candidates, key=lambda p: p.stat().st_mtime)
            time.sleep(1)
        return None

    def _download_latest(self, media_type: str) -> Path:
        before = {str(p) for p in self.download_root.iterdir() if p.is_file()}
        self._click("download")
        downloaded = self._new_download(before, media_type)
        if not downloaded:
            raise DoubaoGenerationError(
                f"未在 {self.download_root} 检测到新下载文件。"
                "请确认豆包已完成生成、下载按钮位置正确（[doubao].coords.download）。"
            )
        logger.info("豆包生成文件已下载 file=%s size=%d", downloaded.name, downloaded.stat().st_size)
        return downloaded

    # ---------- 生成主流程 ----------
    def generate_image(
        self,
        prompt: str,
        category: str = "cat_image",
        tags: list[str] | None = None,
        manage_session: bool = True,
    ) -> dict[str, Any]:
        """用豆包生成一张图片并入库；返回素材记录。

        manage_session=False 供按分镜批量生成使用：整批只切换/恢复一次窗口。
        """
        self._require_deps()
        if not manage_session:
            return self._generate_image_once(prompt, category, tags)
        with self.generation_session("图片素材已生成并入库"):
            return self._generate_image_once(prompt, category, tags)

    def _generate_image_once(
        self, prompt: str, category: str, tags: list[str] | None
    ) -> dict[str, Any]:
        self._activate()
        # 新对话 + 自然语言提示词直接出图（豆包会一次返回多张 9:16 图），
        # 不点「图像生成」技能，避免技能栏坐标漂移。
        self._new_chat()
        self._input_prompt(f"请生成一张图片：{prompt}")
        before = self._snapshot()
        self._pyautogui.press("enter")
        time.sleep(self.image_wait)
        after = self._snapshot()
        # 点第一张图打开预览；找不到新增图时用新对话 4 图布局的首图位置。
        center = self._find_new_media_center(before, after, fallback=(738, 435))
        self._pyautogui.click(*center)
        time.sleep(1.5)
        downloaded = self._download_latest("image")
        try:
            self._click("close_preview")
        except Exception:
            pass
        return self._import(downloaded, category, tags, "image")

    def generate_video(
        self,
        prompt: str,
        category: str = "cat_video",
        tags: list[str] | None = None,
        manage_session: bool = True,
    ) -> dict[str, Any]:
        """用豆包生成视频并入库；免费额度/排队时间由豆包决定。"""
        self._require_deps()
        if not manage_session:
            return self._generate_video_once(prompt, category, tags)
        with self.generation_session("视频素材已生成并入库"):
            return self._generate_video_once(prompt, category, tags)

    def _generate_video_once(
        self, prompt: str, category: str, tags: list[str] | None
    ) -> dict[str, Any]:
        self._activate()
        # 视频入口在普通对话模式的技能栏；先退出图像模式再选视频。
        self._click("video_mode")
        self._input_prompt(prompt)
        self._pyautogui.press("enter")
        time.sleep(self.video_wait)
        downloaded = self._new_download(
            {str(p) for p in self.download_root.iterdir() if p.is_file()}, "video"
        )
        if not downloaded:
            raise DoubaoGenerationError(
                "未在下载目录发现视频。豆包视频生成耗时较长或受免费额度限制，"
                "可调大 [doubao].video_wait_seconds 后重试；也可先用图片素材+本地合成。"
            )
        return self._import(downloaded, category, tags, "video")

    @staticmethod
    def _find_new_media_center(before: Any, after: Any, fallback: tuple[int, int]) -> tuple[int, int]:
        """对比发送前后截图，在对话区域找新增彩色块中心（生成图片）。"""
        points: list[tuple[int, int]] = []
        try:
            width, height = after.size
            x0, x1 = int(width * 0.31), int(width * 0.60)
            y0, y1 = int(height * 0.30), int(height * 0.88)
            for y in range(y0, y1, 4):
                for x in range(x0, x1, 4):
                    r, g, b = after.getpixel((x, y))[:3]
                    if r > 100 and g > 55 and b < 130 and r > b + 40:
                        old = before.getpixel((x, y))[:3]
                        if max(abs(a - c) for a, c in zip((r, g, b), old)) > 30:
                            points.append((x, y))
        except Exception:
            return fallback
        if len(points) < 40:
            return fallback
        return (
            sum(p[0] for p in points) // len(points),
            sum(p[1] for p in points) // len(points),
        )

    def _import(
        self,
        source: Path,
        category: str,
        tags: list[str] | None,
        media_type: str,
    ) -> dict[str, Any]:
        import_tags = list(tags or []) + [f"doubao-{media_type}"]
        asset = self.assets.upload(
            source,
            category=category,
            tags=import_tags,
            license_info="豆包免费生成（商用授权需用户自行向平台确认）",
            source="doubao-desktop-free",
        )
        logger.info("豆包素材已入库 id=%s kind=%s", asset["asset_id"], asset["kind"])
        return asset

    # ---------- 按分镜批量生成 ----------
    def generate_for_storyboard(
        self,
        storyboard: list[dict[str, Any]],
        animal: str = "cat",
        max_images: int = 6,
        category: str = "",
        progress: Any = None,
    ) -> list[dict[str, Any]]:
        """按分镜逐条生成图片并入库；整批只切换/恢复一次窗口。

        单张约 30-45 秒，默认最多 6 张。progress(index, total, description)
        供界面显示进度，避免用户以为卡住。
        """
        self._require_deps()
        limit = max(1, int(max_images or 6))
        prompts = self.prompts_from_storyboard(storyboard, animal)[:limit]
        if not prompts:
            raise ValueError("当前脚本没有可用分镜，无法按分镜生成素材。")
        category = category or ("cat_image" if animal == "cat" else "dog_image")
        results: list[dict[str, Any]] = []
        with self.generation_session(f"已按分镜生成 {len(prompts)} 张素材"):
            for index, item in enumerate(prompts, 1):
                if callable(progress):
                    progress(index, len(prompts), item["description"])
                asset = self._generate_image_once(
                    item["prompt"],
                    category,
                    tags=[f"scene-{item['scene']}", f"doubao-{animal}"],
                )
                results.append(
                    {
                        "scene": item["scene"],
                        "path": asset.get("path", ""),
                        "kind": asset.get("kind", "image"),
                        "asset_id": asset.get("asset_id", ""),
                        "description": item["description"],
                    }
                )
                time.sleep(0.8)
        return results

    @staticmethod
    def prompts_from_storyboard(
        storyboard: list[dict[str, Any]], animal: str = "cat"
    ) -> list[dict[str, Any]]:
        """把结构化分镜转成豆包作图提示词，保留 scene 对应关系。"""
        subject = "猫咪" if animal == "cat" else "狗狗"
        prompts: list[dict[str, Any]] = []
        for index, scene in enumerate(storyboard or [], 1):
            if not isinstance(scene, dict):
                continue
            description = str(scene.get("description") or "").strip()
            if not description:
                continue
            shot = str(scene.get("shot") or "").strip()
            keyword = str(scene.get("asset_keyword") or "").strip()
            prompt = f"竖屏 9:16 写实摄影，{subject}主题：{description}"
            if shot:
                prompt += f"，{shot}构图"
            if keyword:
                prompt += f"，画面关键词：{keyword}"
            prompt += "；自然光、高清细节、无文字、无水印logo"
            prompts.append(
                {
                    "scene": str(scene.get("scene") or index),
                    "description": description,
                    "prompt": prompt,
                }
            )
        return prompts

    # ---------- 从脚本分镜生成提示词 ----------
    @staticmethod
    def prompts_from_script(script: dict[str, Any], animal: str = "cat") -> list[str]:
        """把脚本的 storyboard/asset_keywords 转成豆包作图提示词（本地，不联网）。"""
        subject = "猫咪" if animal == "cat" else "狗狗"
        prompts: list[str] = []
        for scene in (script.get("storyboard") or [])[:8]:
            description = str(scene.get("description", "")).strip()
            if not description:
                continue
            prompts.append(
                f"竖屏 9:16 写实摄影，{subject}主题：{description}；"
                "自然光、高清细节、无文字、无水印logo"
            )
        for keyword in (script.get("asset_keywords") or [])[:6]:
            keyword = str(keyword).strip()
            if keyword:
                prompts.append(
                    f"竖屏 9:16 写实摄影，{subject}与{keyword}，"
                    "自然光、高清细节、无文字、无水印logo"
                )
        # 去重且保留顺序
        seen: set[str] = set()
        return [p for p in prompts if not (p in seen or seen.add(p))]
