"""Pet AI Content Factory — 独立「豆包 + 本地」WebUI。

设计原则：
- 独立入口，不加载原 MPT 的 webui/Main.py，不改动原项目任何文件。
- 全链路零 API 费用：文本用本机桌面豆包，素材用豆包生成/本地上传，
  配音用 Windows SAPI 离线语音，字幕/合成/发布包全部本地完成。
- 付费服务（DeepSeek / AI 图片 / AI 视频 / 在线 TTS）默认全部关闭，
  界面不提供触发入口，也不会在豆包失败时偷偷回退。
- 首页六步向导带用户走完整流程；侧栏菜单提供各模块的精细管理。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

import streamlit as st

# 允许 `streamlit run PetAI/webui.py` 直接运行
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from PetAI.app.bootstrap import create_app  # noqa: E402
from PetAI.config.settings import Service  # noqa: E402
from PetAI.models.constants import (  # noqa: E402
    ANIMAL_LABELS,
    ANIMALS,
    CATEGORIES,
    CATEGORY_LABELS,
    CATEGORY_TO_ANIMAL,
    CONTENT_TYPE_LABELS,
    LANGUAGES,
    REVIEW_APPROVED,
    REVIEW_PENDING,
    REVIEW_REJECTED,
    RISK_LABELS,
    RISK_LEVELS,
    TOPIC_STATUSES,
)

st.set_page_config(
    page_title="Pet AI Content Factory",
    page_icon="🐾",
    layout="wide",
    initial_sidebar_state="expanded",
)

MENU_HOME = "首页 · 生产向导"
MENU_AUTO = "一键生产"
MENU_TOPICS = "选题中心"
MENU_KNOWLEDGE = "知识库与审核"
MENU_SCRIPTS = "脚本工厂"
MENU_ASSETS = "素材管理"
MENU_VIDEO = "视频工厂"
MENU_EXPORT = "导出中心"
MENU_COST = "运行状态"
MENU_SETTINGS = "设置"
MENUS = [
    MENU_HOME,
    MENU_AUTO,
    MENU_TOPICS,
    MENU_KNOWLEDGE,
    MENU_SCRIPTS,
    MENU_ASSETS,
    MENU_VIDEO,
    MENU_EXPORT,
    MENU_COST,
    MENU_SETTINGS,
]

WIZARD_STEPS = [
    "1 选题",
    "2 脚本",
    "3 审核",
    "4 素材",
    "5 配音",
    "6 合成",
]

# 素材分类（与 local_asset_service.CATEGORY_DIRS 的键一致，避免非法分类被静默 fallback）
ASSET_CATEGORIES = [
    "cat_video", "dog_video", "cat_image", "dog_image", "educational", "audio", "video",
]
ASSET_CATEGORY_LABELS = {
    "cat_video": "猫咪视频", "dog_video": "狗狗视频",
    "cat_image": "猫咪图片", "dog_image": "狗狗图片",
    "educational": "科普插图", "audio": "音频/配乐", "video": "其他视频",
}

_CSS = """
<style>
div[data-testid="stHeader"], div[data-testid="stToolbar"],
div[data-testid="stDecoration"], div[data-testid="stStatusWidget"] { display: none !important; }
h1 { padding-top: 0 !important; margin-top: 0 !important; }
.petai-card { border: 0.5px solid var(--color-border-tertiary); border-radius: 12px;
              padding: 0.9rem 1.1rem; margin-bottom: 0.75rem; background: var(--color-background-primary); }
.petai-hint { color: var(--color-text-secondary); font-size: 0.85rem; }
.petai-step { border: 0.5px solid var(--color-border-tertiary); border-radius: 8px;
              padding: 0.5rem; text-align: center; font-size: 0.8rem; }
.petai-step-active { border: 0.5px solid #378ADD; background: #E6F1FB; color: #0C447C; }
</style>
"""
st.markdown(_CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def _get_app():
    return create_app()


def _init_state() -> None:
    defaults = {
        "wizard_step": 0,
        "w_topic_id": "",
        "w_script_zh_id": "",
        "w_script_en_id": "",
        "w_script_zh": "",
        "w_script_en": "",
        "w_knowledge_ids": [],
        "w_materials": [],
        "w_voice_zh": "",
        "w_voice_en": "",
        "w_duration": 5.0,
        "w_output": {},
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _call(fn, *args, **kwargs) -> Any:
    """调用服务并统一展示错误；失败返回 None，按钮逻辑据此停止。"""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - WebUI 必须把任何错误显示给用户
        st.error(f"操作失败：{type(exc).__name__}：{exc}")
        return None


def _flash(kind: str, text: str) -> None:
    """暂存一条提示：st.rerun() 会清掉普通 success，这里保证回到页面仍能看到。"""
    st.session_state.setdefault("_flashes", []).append((kind, text))


def _render_flashes() -> None:
    for kind, text in st.session_state.pop("_flashes", []):
        renderer = getattr(st, kind, None)
        (renderer if callable(renderer) else st.info)(text)


def _money(v: float) -> str:
    return f"${v:.6f}"


def _short(s: str, n: int = 16) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[:n] + "…"


def _doubao_status(app) -> tuple[str, bool]:
    """豆包客户端状态文案 + 是否可用；任何异常都不阻断页面渲染。"""
    try:
        caps = app.doubao.capabilities()
        deps_ok = all(caps.get("gui_dependencies", {}).values())
        if not caps.get("client_installed"):
            return "未找到客户端", False
        if not deps_ok:
            return "自动化依赖缺失", False
        if not caps.get("client_running"):
            return "客户端未启动", False
        return "运行中", True
    except Exception:  # noqa: BLE001 - 状态条不能因探测失败而中断页面
        return "状态未知", False


def _show_local_strip(app) -> None:
    """全页顶部状态条：一眼看清「豆包 + 本地」链路与零 API 费用。"""
    month = app.guard.month_cost()
    calls = int(month.get("calls", 0) or 0)
    cost = float(month.get("cost_usd", 0.0) or 0.0)
    tts = app.tts.capabilities()
    if tts.get("sapi_available"):
        voice = "SAPI 离线"
    elif tts.get("piper_available"):
        voice = "本地 Piper"
    else:
        voice = "上传配音"
    doubao_text, _ = _doubao_status(app)

    cols = st.columns(6)
    cols[0].metric("文本生成", "桌面豆包", help="脚本 / 选题 / 知识整理都由本机豆包客户端完成")
    cols[1].metric("豆包客户端", doubao_text, help="GUI 自动化需要客户端已启动并登录")
    cols[2].metric("配音", voice, help="Windows 自带离线语音或你自己的录音，不联网")
    cols[3].metric("画面素材", "豆包 / 本地", help="豆包生成的图片会在本地自动转成视频片段")
    cols[4].metric("视频合成", "本地 FFmpeg", help="9:16 1080×1920，不调用付费视频服务")
    cols[5].metric(
        "本月付费 API", f"{calls} 次 · ${cost:.2f}",
        help="账本含历史记录（旧版付费模式可能留下）；当前是否零成本，以「运行状态」里的开关全关为准",
    )


def _show_steps(current: int) -> None:
    cols = st.columns(len(WIZARD_STEPS))
    for i, (col, label) in enumerate(zip(cols, WIZARD_STEPS)):
        with col:
            cls = "petai-step petai-step-active" if i == current else "petai-step"
            st.markdown(f'<div class="{cls}">{label}</div>', unsafe_allow_html=True)


def _topic_options(app, limit: int = 200) -> list[dict[str, Any]]:
    return app.topics.list(limit=limit)


def _script_record(app, script_id: str) -> dict[str, Any]:
    return (app.scripts.get(script_id) or {}) if script_id else {}


def _current_storyboard(app, language: str = "zh") -> list[dict[str, Any]]:
    """取向导当前脚本的分镜；没有脚本时返回空列表。"""
    key = "w_script_zh_id" if language == "zh" else "w_script_en_id"
    record = _script_record(app, st.session_state.get(key, ""))
    storyboard = record.get("storyboard") or []
    return storyboard if isinstance(storyboard, list) else []


def _render_storyboard(storyboard: list[dict[str, Any]], title: str = "分镜方案") -> None:
    """以表格展示分镜，便于确认镜头数量、时长与画面。"""
    if not storyboard:
        st.caption("（暂无分镜）")
        return
    total = sum(int(item.get("duration", 0) or 0) for item in storyboard)
    st.markdown(f"**{title}** · {len(storyboard)} 个镜头 · 合计约 {total} 秒")
    st.dataframe(
        [
            {
                "镜头": item.get("scene", ""),
                "时长(s)": item.get("duration", ""),
                "景别": item.get("shot", ""),
                "画面": item.get("description", ""),
                "素材关键词": item.get("asset_keyword", ""),
            }
            for item in storyboard
        ],
        width="stretch",
        hide_index=True,
    )


def _render_material_plan(plan: list[dict[str, Any]]) -> None:
    """展示「镜头 → 素材」对应关系，让用户确认不是同一张图反复用。"""
    if not plan:
        return
    st.dataframe(
        [
            {
                "镜头": item.get("scene", ""),
                "素材": _short(str(item.get("path", "")).split("\\")[-1].split("/")[-1], 36),
                "类型": item.get("kind", ""),
                "关键词命中": "是" if item.get("matched") else "轮换",
            }
            for item in plan
        ],
        width="stretch",
        hide_index=True,
    )


def _label_topic(t: dict[str, Any]) -> str:
    return f"{_short(t.get('title', ''), 40)}  [{ANIMAL_LABELS.get(t.get('animal_type'), '猫狗')}]"


# ---------------------------------------------------------------------------
# 首页向导
# ---------------------------------------------------------------------------
def page_home(app) -> None:
    st.title("今天做一个什么视频？")
    st.caption(
        "跟着下面 6 步走完，就得到中英两条 9:16 竖屏视频。"
        "全程只用桌面豆包 + 本地素材 + 本地配音 + 本地合成，不需要任何 API Key，零 API 费用。"
    )
    _show_steps(st.session_state.wizard_step)

    step = st.session_state.wizard_step

    if step == 0:
        _wizard_topic(app)
    elif step == 1:
        _wizard_script(app)
    elif step == 2:
        _wizard_review(app)
    elif step == 3:
        _wizard_asset(app)
    elif step == 4:
        _wizard_voice(app)
    elif step == 5:
        _wizard_video(app)


def page_auto(app) -> None:
    """一键生产：只选分类，其余全自动。"""
    st.title("一键生产")
    st.caption(
        "选一个分类，点一次，剩下的全自动：近 7 天热门选题 → 中英脚本与分镜 → "
        "按分镜生成素材 → 本地配音 → 本地合成 → 发布包。"
    )
    st.info(
        "全流程约 5-8 分钟。期间程序会接管豆包客户端，请不要移动鼠标；"
        "结束后会自动切回本页并响一声提示。"
    )

    c1, c2 = st.columns(2)
    with c1:
        category = st.selectbox(
            "分类", CATEGORIES,
            format_func=lambda x: CATEGORY_LABELS[x], key="auto_cat",
        )
    with c2:
        image_count = st.slider("按分镜生成素材张数", 3, 8, 6, key="auto_img")
    with_english = st.checkbox("同时生成英文版", value=True, key="auto_en")
    st.caption(
        f"本分类面向：{ANIMAL_LABELS.get(CATEGORY_TO_ANIMAL.get(category, 'both'), '猫狗')}"
        " · 趋势来源由豆包联网检索，仅作参考，不是平台实时榜单。"
    )

    if st.button("生成视频（全自动）", type="primary", key="auto_go"):
        progress = st.progress(0.0)
        status = st.empty()

        def on_progress(step: int, total: int, message: str) -> None:
            progress.progress(step / max(total, 1))
            status.info(f"第 {step}/{total} 步：{message}")

        try:
            with st.spinner("全自动生产中，请勿操作豆包窗口…"):
                result = app.pipeline.run(
                    category=category,
                    image_count=image_count,
                    with_english=with_english,
                    task_id="auto",
                    progress=on_progress,
                )
        except Exception as exc:  # noqa: BLE001 - 失败要把原因带回页面
            _flash("error", f"一键生产失败：{type(exc).__name__}：{exc}")
            st.rerun()
        else:
            st.session_state["auto_result"] = result
            _flash("success", "全自动生产完成，结果在下方。")
            st.rerun()

    result = st.session_state.get("auto_result")
    if not result:
        return

    st.divider()
    st.subheader("本次结果")
    topic = result.get("topic") or {}
    st.markdown(
        f"**{topic.get('title', '')}** · {result.get('category_label', '')}"
    )
    if result.get("data_source"):
        st.caption(f"趋势来源：{result['data_source']}")
    if result.get("risk"):
        st.caption(f"内容风险等级：{RISK_LABELS.get(result['risk'], result['risk'])}")
    st.caption(
        "素材来源："
        + ("豆包按分镜生成" if result.get("material_source") == "doubao" else "本地素材回退")
        + f" · {len(result.get('materials') or [])} 个"
    )

    zh = (result.get("scripts") or {}).get("zh") or {}
    _render_storyboard(zh.get("storyboard") or [], "中文分镜")

    videos = result.get("videos") or {}
    for language, label in (("zh", "中文版"), ("en", "英文版")):
        item = videos.get(language) or {}
        path = item.get("output_path", "")
        if path and Path(path).exists():
            st.markdown(f"##### {label}")
            st.video(path)
            with open(path, "rb") as fh:
                st.download_button(
                    f"下载{label} MP4", fh, file_name=Path(path).name,
                    key=f"auto_dl_{language}",
                )
            verify = (item.get("alignment") or {}).get("verify") or {}
            if verify:
                st.caption(
                    f"时长对齐：配音 {verify.get('audio_duration', 0):.1f}s · "
                    f"字幕 {verify.get('subtitle_end', 0):.1f}s · "
                    f"视频 {verify.get('video_duration', 0):.1f}s · "
                    f"{'已对齐' if verify.get('aligned') else '未完全对齐'}"
                )

    packs = result.get("packs") or {}
    if packs:
        with st.expander("发布包（标题/简介/标签/封面文字）"):
            for language, pack in packs.items():
                st.markdown(f"**{language}**：{pack.get('file_path', '')}")
                st.write(f"标题：{pack.get('title', '')}")
                st.write(f"简介：{pack.get('description', '')}")

    if st.button("再生成一条", key="auto_again"):
        st.session_state.pop("auto_result", None)
        st.rerun()


def _wizard_topic(app) -> None:
    st.subheader("第 1 步 · 选一个选题")
    st.markdown("#### 方式 A：用豆包批量生成选题（免费）")
    st.caption("生成期间会短暂接管豆包窗口，请不要移动鼠标或操作豆包，约 25 秒后自动取回结果。")
    c1, c2, c3 = st.columns([1, 1, 1])
    with c1:
        animal = st.selectbox("动物", ANIMALS, format_func=lambda x: ANIMAL_LABELS[x], key="hz_animal")
    with c2:
        ctype = st.selectbox(
            "方向", [None, "science", "entertainment", "product"],
            format_func=lambda x: "全部" if x is None else CONTENT_TYPE_LABELS[x], key="hz_ctype",
        )
    with c3:
        count = st.slider("生成条数", 1, 10, 3, key="hz_count")
    if st.button("豆包生成选题（免费）", key="hz_gen"):
        with st.spinner("正在让豆包生成选题，请勿操作豆包窗口…"):
            batch = _call(
                app.topics.generate_batch, count=count, animal_type=animal,
                content_type=ctype, task_id="webui-wizard",
            )
        if batch:
            st.success(f"生成 {len(batch)} 条选题，已加入选题中心")
            st.session_state["hz_batch"] = [t["topic_id"] for t in batch]

    st.markdown("#### 方式 B：手动输入主题")
    with st.form("hz_manual"):
        title = st.text_input("主题标题", placeholder="例如：猫咪为什么爱钻纸箱？")
        cat = st.selectbox("分类", CATEGORIES, format_func=lambda x: CATEGORY_LABELS[x], key="hz_cat")
        sub = st.form_submit_button("创建选题")
        if sub and title.strip():
            t = _call(app.topics.create, title.strip(), category=cat, task_id="webui-wizard")
            if t:
                st.success("选题已创建")

    st.markdown("#### 从已有选题中选择")
    topics = _topic_options(app)
    if topics:
        chosen = st.selectbox("选题", topics, format_func=_label_topic, key="hz_pick")
        if st.button("选它，下一步 →", key="hz_next") and chosen:
            st.session_state.w_topic_id = chosen["topic_id"]
            st.session_state.wizard_step = 1
            st.rerun()
    else:
        st.info("还没有选题。用上面任一方式创建，或去「选题中心」看看。")


def _wizard_script(app) -> None:
    st.subheader("第 2 步 · 生成中英双语脚本")
    engine = app.scripts.script_engine
    engine_label = "桌面豆包（免费）" if engine == "doubao-desktop" else "DeepSeek（付费）"
    st.caption(f"当前文本引擎：{engine_label}。英文按当地表达习惯本地化改写，不是逐字翻译。")
    topic = app.topics.get(st.session_state.w_topic_id)
    if not topic:
        st.error("选题不存在，请返回第 1 步重新选择。")
        return
    st.info(f"当前选题：**{topic.get('title', '')}**")
    c1, c2 = st.columns([1, 1])
    with c1:
        stype = st.selectbox(
            "脚本类型",
            [("science", "科普"), ("entertainment", "娱乐")],
            format_func=lambda x: x[1], key="ws_type",
        )[0]
    with c2:
        dur = st.slider("目标时长（秒）", 15, 60, 45, key="ws_dur")
    if st.button("用豆包生成中英脚本（免费）", key="ws_gen"):
        with st.spinner("豆包生成中，请勿操作豆包窗口（中英各约 25 秒）…"):
            result = _call(
                app.scripts.generate_bilingual,
                st.session_state.w_topic_id, script_type=stype, task_id="webui-wizard",
            )
        if result:
            zh, en = result["zh"], result["en"]
            st.session_state.w_script_zh_id = zh["script_id"]
            st.session_state.w_script_en_id = en["script_id"]
            st.session_state.w_script_zh = zh.get("content", "")
            st.session_state.w_script_en = en.get("content", "")
            st.session_state.w_duration = float(dur)
            st.success("中英脚本已生成")
    if st.session_state.w_script_zh:
        with st.expander("中文脚本与分镜", expanded=True):
            st.write(st.session_state.w_script_zh)
            _render_storyboard(_current_storyboard(app, "zh"), "中文分镜")
        with st.expander("英文脚本与分镜"):
            st.write(st.session_state.w_script_en)
            _render_storyboard(_current_storyboard(app, "en"), "英文分镜")
        if st.button("下一步：知识审核 →", key="ws_next"):
            st.session_state.wizard_step = 2
            st.rerun()
    else:
        if st.button("← 返回第 1 步", key="ws_back"):
            st.session_state.wizard_step = 0
            st.rerun()


def _wizard_review(app) -> None:
    st.subheader("第 3 步 · 知识审核（高风险内容必须人工确认）")
    st.caption("用到疾病、用药、急症内容时，必须先在这里录入知识并完成人工审核，脚本才允许使用。")
    pending = app.knowledge.list(review_status=REVIEW_PENDING)
    high_risk = [k for k in pending if k.get("risk_level") in ("B", "C")]
    if high_risk:
        st.warning(f"有 {len(high_risk)} 条待审核知识（B/C 级），发布前需人工批准：")
        for k in high_risk:
            with st.container():
                st.markdown(f"**{k['topic']}** · {RISK_LABELS.get(k['risk_level'], k['risk_level'])}")
                st.caption(_short(k.get("content", ""), 100))
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("批准", key=f"wr_ap_{k['knowledge_id']}"):
                        reviewer = st.session_state.get("wr_reviewer", "") or "本机用户"
                        r = _call(app.knowledge.approve, k["knowledge_id"], reviewer, "人工复核通过")
                        if r:
                            st.success("已批准")
                            st.rerun()
                with c2:
                    if st.button("驳回", key=f"wr_rj_{k['knowledge_id']}"):
                        r = _call(app.knowledge.reject, k["knowledge_id"], "本机用户", "人工驳回")
                        if r:
                            st.info("已驳回")
                            st.rerun()
    else:
        st.success("没有待处理的高风险知识。")

    with st.expander("新增一条知识（录入来源，AI 不得编造参考文献）"):
        with st.form("wr_new"):
            kt = st.text_input("知识主题")
            kc = st.text_area("知识正文")
            refs = st.text_input("参考资料来源（多个用逗号分隔）")
            animal = st.selectbox("适用动物", ANIMALS, format_func=lambda x: ANIMAL_LABELS[x], key="wr_animal")
            if st.form_submit_button("保存知识") and kt.strip() and kc.strip():
                ref_list = [x.strip() for x in refs.split(",") if x.strip()]
                k = _call(
                    app.knowledge.create, kt.strip(), kc.strip(),
                    animal_type=animal, references=ref_list, task_id="webui-wizard",
                )
                if k:
                    lvl = k["risk_level"]
                    msg = "已保存，风险等级 " + RISK_LABELS.get(lvl, lvl)
                    if lvl == "C":
                        msg += "。C 级为高风险，必须人工批准后才能用于脚本。"
                    st.warning(msg) if lvl in ("B", "C") else st.success(msg)

    st.markdown("#### 把已批准的知识写进脚本")
    st.caption("选中知识后重新生成脚本，脚本才会真正引用这些已核查事实；C 级未批准的知识不会被引用。")
    approved = app.knowledge.list(review_status=REVIEW_APPROVED)
    if approved:
        picked_k = st.multiselect(
            "选择要引用的已批准知识", approved,
            format_func=lambda k: f"{_short(k.get('topic',''), 40)} · {RISK_LABELS.get(k.get('risk_level',''),'')}",
            key="wr_refs",
        )
        if st.button("用选中知识重新生成中英脚本", key="wr_regen"):
            ids = [k["knowledge_id"] for k in picked_k]
            with st.spinner("重新生成脚本…"):
                result = _call(
                    app.scripts.generate_bilingual,
                    st.session_state.w_topic_id, script_type="science",
                    knowledge_ids=ids, task_id="webui-wizard",
                )
            if result:
                zh2, en2 = result["zh"], result["en"]
                st.session_state.w_script_zh_id = zh2["script_id"]
                st.session_state.w_script_en_id = en2["script_id"]
                st.session_state.w_script_zh = zh2.get("content", "")
                st.session_state.w_script_en = en2.get("content", "")
                st.session_state.w_knowledge_ids = ids
                st.success(f"脚本已用 {len(ids)} 条已批准知识重新生成")
    else:
        st.caption("目前没有已批准的知识。若脚本只讲普通行为/娱乐内容，可直接进入下一步。")

    b1, b2 = st.columns(2)
    with b1:
        if st.button("← 返回第 2 步", key="wr_back"):
            st.session_state.wizard_step = 1
            st.rerun()
    with b2:
        if st.button("下一步：选素材 →", key="wr_next"):
            st.session_state.wizard_step = 3
            st.rerun()


def _doubao_generate_panel(app, key_prefix: str, auto_select: bool = False) -> None:
    """桌面豆包现场生成素材并入库（向导第 4 步与素材管理页共用）。

    auto_select=True 时，生成成功后自动把新素材加入向导已选素材，省掉一次手动勾选。
    """
    prompt_key = f"{key_prefix}_prompt"
    fill_key = f"{key_prefix}_fill"
    if prompt_key not in st.session_state:
        st.session_state[prompt_key] = ""
    # 在 text_area 实例化前消费「填入提示词」请求，避免 Streamlit 状态冲突。
    pending = st.session_state.pop(fill_key, None)
    if pending:
        st.session_state[prompt_key] = pending

    caps = app.doubao.capabilities()
    deps_ok = all(caps.get("gui_dependencies", {}).values())
    st.caption(
        f"客户端：{'已安装' if caps.get('client_installed') else '未找到'} · "
        f"运行中：{'是' if caps.get('client_running') else '否'} · "
        f"自动化依赖：{'就绪' if deps_ok else '缺失'}"
    )
    st.caption(
        "免费、不调用任何 API。图片约 40 秒、视频可能数分钟；生成时请勿移动鼠标。"
        "完成后会自动切回本页并响一声提示，不需要手动切回来。"
        "图片入库后，合成时会由本地 FFmpeg 自动转成视频片段。"
    )

    c1, c2, c3 = st.columns([3, 1, 1])
    with c1:
        prompt = st.text_area(
            "画面提示词", key=prompt_key, height=90,
            placeholder="竖屏9:16，橘猫钻进纸箱，自然光，写实摄影，高清细节",
        )
    with c2:
        media = st.selectbox(
            "类型", ["image", "video"],
            format_func=lambda x: "图片" if x == "image" else "视频",
            key=f"{key_prefix}_type",
        )
    with c3:
        animal = st.selectbox(
            "动物", ["cat", "dog"],
            format_func=lambda x: ANIMAL_LABELS[x], key=f"{key_prefix}_animal",
        )

    script_rows = app.scripts.list(limit=40)
    if script_rows:
        def _script_label(sid: str) -> str:
            row = next((s for s in script_rows if s.get("script_id") == sid), {})
            return _short(str(row.get("title", sid)), 28)

        chosen = st.selectbox(
            "从已有脚本分镜生成提示词（可选）",
            [s.get("script_id", "") for s in script_rows],
            format_func=_script_label, key=f"{key_prefix}_script",
        )
        if st.button("填入第一条分镜提示词", key=f"{key_prefix}_fromscript"):
            record = app.scripts.get(chosen)
            prompts = app.doubao.prompts_from_script(record or {}, animal)
            if prompts:
                st.session_state[fill_key] = prompts[0]
                st.rerun()
            else:
                st.warning("该脚本没有可用的分镜或素材关键词")

    if st.button("开始生成并入库", key=f"{key_prefix}_go"):
        if not prompt.strip():
            st.error("请先填写提示词")
        elif not deps_ok or not caps.get("client_running"):
            st.error("豆包客户端或自动化依赖未就绪，请先启动并登录豆包桌面版")
        else:
            category = (
                ("cat_video" if animal == "cat" else "dog_video")
                if media == "video"
                else ("cat_image" if animal == "cat" else "dog_image")
            )
            fn = app.doubao.generate_image if media == "image" else app.doubao.generate_video
            with st.spinner("正在操控豆包生成…完成后会自动切回本页并响一声提示"):
                try:
                    result = fn(prompt.strip(), category=category)
                except Exception as exc:  # noqa: BLE001 - 失败也要让用户回到页面看到原因
                    _flash("error", f"生成失败：{type(exc).__name__}：{exc}")
                    st.rerun()
                else:
                    filename = result.get("filename", "")
                    if auto_select:
                        path = result.get("path", "")
                        materials = list(st.session_state.get("w_materials", []))
                        if path and all(m.get("path") != path for m in materials):
                            materials.append(
                                {"path": path, "kind": result.get("kind", "image")}
                            )
                            st.session_state.w_materials = materials
                        _flash(
                            "success",
                            f"已生成并入库：{filename}；已自动加入本步素材，可直接点「下一步：配音」。",
                        )
                    else:
                        _flash("success", f"已生成并入库：{filename}；可在下方列表勾选使用。")
                    st.rerun()


def _doubao_storyboard_panel(
    app,
    storyboard: list[dict[str, Any]],
    key_prefix: str = "sb",
    assign_to_wizard: bool = True,
) -> None:
    """按脚本分镜逐条生成图片：整批只切换一次窗口，结束后自动切回。"""
    if not storyboard:
        st.info("没有分镜可生成；请先在第 2 步生成脚本。")
        return
    caps = app.doubao.capabilities()
    deps_ok = all(caps.get("gui_dependencies", {}).values())
    st.caption(
        f"将按 {len(storyboard)} 个镜头逐条生成，每张约 40 秒；"
        "整批只切换一次豆包窗口，完成后自动切回本页并响一声提示。"
    )
    c1, c2 = st.columns(2)
    with c1:
        animal = st.selectbox(
            "动物", ["cat", "dog"],
            format_func=lambda x: ANIMAL_LABELS[x], key=f"{key_prefix}_animal",
        )
    with c2:
        limit = st.slider(
            "生成张数", 1, min(8, max(1, len(storyboard))),
            min(6, len(storyboard)), key=f"{key_prefix}_limit",
        )
    if st.button("按分镜生成并加入素材", key=f"{key_prefix}_go"):
        if not deps_ok or not caps.get("client_running"):
            st.error("豆包客户端或自动化依赖未就绪，请先启动并登录豆包桌面版")
            return
        progress = st.progress(0.0)
        status = st.empty()

        def on_progress(index: int, total: int, description: str) -> None:
            progress.progress(index / max(total, 1))
            status.caption(f"第 {index}/{total} 张：{_short(description, 28)}")

        try:
            results = app.doubao.generate_for_storyboard(
                storyboard, animal=animal, max_images=limit, progress=on_progress
            )
        except Exception as exc:  # noqa: BLE001 - 失败也要回到页面看到原因
            _flash("error", f"按分镜生成失败：{type(exc).__name__}：{exc}")
            st.rerun()
        else:
            plan = [
                {
                    "path": item.get("path", ""),
                    "kind": item.get("kind", "image"),
                    "scene": item.get("scene", ""),
                    "keyword": "",
                    "matched": True,
                }
                for item in results
            ]
            if assign_to_wizard:
                st.session_state.w_materials = plan
                _flash(
                    "success",
                    f"已生成 {len(plan)} 张素材并按镜头顺序加入，可直接点「下一步：配音」。",
                )
            else:
                _flash("success", f"已生成 {len(plan)} 张素材并入库，可在上方列表查看。")
            st.rerun()


def _wizard_asset(app) -> None:
    st.subheader("第 4 步 · 按分镜准备画面素材")
    st.caption(
        "画面来自豆包生成的图片/视频，或你本地上传的素材；不抓取网络视频、不调用付费 AI 出图接口。"
        "建议每个镜头一个素材，避免整条视频重复同一画面。"
    )
    storyboard = _current_storyboard(app, "zh")
    if storyboard:
        _render_storyboard(storyboard, "本片分镜方案")
        if st.button("按分镜自动配本地素材", key="wa_plan"):
            plan = _call(app.assets.plan_for_storyboard, storyboard)
            if plan:
                st.session_state.w_materials = plan
                st.success(f"已按 {len(plan)} 个镜头配好素材")
                st.rerun()
        if st.session_state.get("w_materials"):
            _render_material_plan(st.session_state.w_materials)
    else:
        st.info("还没有分镜。请先在第 2 步生成脚本，或在下方手动选素材。")

    with st.expander("用桌面豆包按分镜批量生成（免费，推荐）"):
        _doubao_storyboard_panel(app, storyboard, key_prefix="wa_sb", assign_to_wizard=True)
    with st.expander("用桌面豆包单张生成素材"):
        _doubao_generate_panel(app, "wa_ai", auto_select=True)

    assets = app.assets.list_assets()
    if assets:
        opts = list(range(len(assets)))
        picked = st.multiselect(
            "勾选要用的素材（可多选，图片/视频混合）", opts,
            format_func=lambda i: f"{_short(assets[i].get('filename', assets[i].get('path','')), 40)} · {assets[i].get('kind','')}",
            key="wa_pick",
        )
        if picked:
            st.session_state.w_materials = [
                {"path": assets[i]["path"], "kind": assets[i].get("kind", "image")} for i in picked
            ]
            st.success(f"已选 {len(picked)} 个素材")
    else:
        st.info("素材库是空的。上传方式见下方，或到「素材管理」页面上传。")

    with st.expander("上传新素材"):
        up = st.file_uploader("选择图片或视频", type=["png", "jpg", "jpeg", "webp", "mp4", "mov", "avi"], key="wa_upload")
        c1, c2 = st.columns([1, 1])
        with c1:
            category = st.selectbox(
                "素材分类", ASSET_CATEGORIES,
                format_func=lambda x: ASSET_CATEGORY_LABELS[x], key="wa_animal",
            )
        with c2:
            tags = st.text_input("标签（逗号分隔）", key="wa_tags")
        if up and st.button("上传到素材库", key="wa_do"):
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(up.name).suffix) as tmp:
                tmp.write(up.getbuffer())
                tmp_path = tmp.name
            r = _call(
                app.assets.upload, tmp_path, category,
                tags=[x.strip() for x in tags.split(",") if x.strip()],
            )
            if r:
                st.success("上传成功（原件未被修改）")
                st.rerun()

    st.caption(f"当前已选素材：{len(st.session_state.w_materials)} 个")
    b1, b2 = st.columns(2)
    with b1:
        if st.button("← 返回第 3 步", key="wa_back"):
            st.session_state.wizard_step = 2
            st.rerun()
    with b2:
        if st.button("下一步：配音 →", key="wa_next"):
            st.session_state.wizard_step = 4
            st.rerun()


def _wizard_voice(app) -> None:
    st.subheader("第 5 步 · 配音")
    caps = app.tts.capabilities()
    can_synth = caps.get("piper_available") or caps.get("sapi_available")
    if can_synth:
        engine_label = (
            "Windows 系统自带语音（离线免费）" if caps.get("sapi_available")
            else "本地 Piper"
        )
        st.success(f"{engine_label}已就绪，输入文本即可合成配音，无需联网、不产生费用。")
        text = st.text_area("配音文本", value=st.session_state.w_script_zh[:500], height=120, key="wv_text")
        c_syn1, c_syn2 = st.columns(2)
        with c_syn1:
            if st.button("合成中文配音", key="wv_synth"):
                r = _call(app.tts.synthesize, text, "zh", task_id="webui-wizard")
                if r:
                    st.session_state.w_voice_zh = r["audio_path"]
                    st.audio(r["audio_path"])
        with c_syn2:
            if st.button("合成英文配音", key="wv_synth_en"):
                en_text = st.session_state.get("w_script_en", "")[:500] or text
                r = _call(app.tts.synthesize, en_text, "en", task_id="webui-wizard")
                if r:
                    st.session_state.w_voice_en = r["audio_path"]
                    st.audio(r["audio_path"])
    else:
        st.info(
            "未检测到本地语音引擎（Piper / Windows SAPI 均不可用）。"
            "请上传你自己的配音文件（支持 mp3/wav/m4a 等）；系统绝不会自动切换到在线付费配音。"
        )
    st.caption(
        "配音 100% 本地：Windows 自带离线语音，或你自己的录音。"
        "不使用豆包朗读（它只播放、不导出音频），也不调用任何在线 TTS。"
    )

    c1, c2 = st.columns(2)
    with c1:
        fzh = st.file_uploader("中文配音", type=["wav", "mp3", "m4a", "aac"], key="wv_zh")
        if fzh and st.button("登记中文配音", key="wv_zh_do"):
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(fzh.name).suffix) as tmp:
                tmp.write(fzh.getbuffer())
                p = tmp.name
            r = _call(app.tts.upload_voice, p, "zh")
            if r:
                st.session_state.w_voice_zh = r["path"]
                st.success("中文配音已登记")
    with c2:
        fen = st.file_uploader("英文配音", type=["wav", "mp3", "m4a", "aac"], key="wv_en")
        if fen and st.button("登记英文配音", key="wv_en_do"):
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(fen.name).suffix) as tmp:
                tmp.write(fen.getbuffer())
                p = tmp.name
            r = _call(app.tts.upload_voice, p, "en")
            if r:
                st.session_state.w_voice_en = r["path"]
                st.success("英文配音已登记")

    st.caption(f"中文配音：{'已上传' if st.session_state.w_voice_zh else '未上传'} · "
               f"英文配音：{'已上传' if st.session_state.w_voice_en else '未上传'}")
    b1, b2 = st.columns(2)
    with b1:
        if st.button("← 返回第 4 步", key="wv_back"):
            st.session_state.wizard_step = 3
            st.rerun()
    with b2:
        if st.button("下一步：合成视频 →", key="wv_next"):
            st.session_state.wizard_step = 5
            st.rerun()


def _wizard_video(app) -> None:
    st.subheader("第 6 步 · 合成中英两条视频")
    mats = st.session_state.w_materials
    storyboard_zh = _current_storyboard(app, "zh")
    storyboard_en = _current_storyboard(app, "en") or storyboard_zh
    st.caption(
        f"选题：{_short(app.topics.get(st.session_state.w_topic_id, {}).get('title',''), 30)} · "
        f"分镜 {len(storyboard_zh)} 个 · 素材 {len(mats)} 个 · "
        f"中文配音 {'有' if st.session_state.w_voice_zh else '无'} · "
        f"英文配音 {'有' if st.session_state.w_voice_en else '无'}"
    )
    if storyboard_zh:
        _render_storyboard(storyboard_zh, "分镜与素材顺序")
    if not mats:
        if storyboard_zh:
            st.info(f"未手动选素材：合成时会按 {len(storyboard_zh)} 个分镜自动从本地素材库配齐。")
        else:
            st.warning("还没有素材和分镜。请返回第 4 步准备素材。")
    elif storyboard_zh and len(mats) < len(storyboard_zh):
        st.warning(
            f"素材 {len(mats)} 个，少于分镜 {len(storyboard_zh)} 个，部分镜头会复用同一素材。"
            "可返回第 4 步「按分镜配齐」或用豆包批量生成。"
        )
    if st.button("开始合成中英双版本（本地 FFmpeg，零 API 费用）", key="wv_do"):
        if not mats and not storyboard_zh:
            st.error("请先选择素材")
        else:
            with st.spinner("正在合成，通常需要 10~60 秒…"):
                r = _call(
                    app.video.produce_bilingual,
                    topic_id=st.session_state.w_topic_id,
                    script_zh=st.session_state.w_script_zh,
                    script_en=st.session_state.w_script_en,
                    materials_zh=mats,
                    materials_en=mats,
                    voice_file_zh=st.session_state.w_voice_zh or None,
                    voice_file_en=st.session_state.w_voice_en or None,
                    # 传 0：让服务按真实音频时长生成字幕时间轴，
                    # 否则脚本目标时长(如45s)与实际配音(如4s)不符会导致字幕错位。
                    audio_duration=0.0,
                    task_id="webui-wizard",
                    # 传入分镜：素材为空时按镜头自动配齐，并按顺序拼接。
                    storyboard_zh=storyboard_zh,
                    storyboard_en=storyboard_en,
                )
            if r:
                st.session_state.w_output = r
                st.success("合成完成！")
    out = st.session_state.w_output
    if out:
        for lang, label in [("zh", "中文版"), ("en", "英文版")]:
            item = out.get(lang, {})
            p = item.get("output_path", "")
            if p and Path(p).exists():
                st.markdown(f"##### {label}")
                st.video(p)
                with open(p, "rb") as fh:
                    st.download_button(f"下载{label} MP4", fh, file_name=Path(p).name, key=f"wv_dl_{lang}")
                st.caption(f"字幕：{_short(item.get('subtitle_path',''), 50)}")
                alignment = item.get("alignment", {}) or {}
                verify = alignment.get("verify", {}) or {}
                if verify:
                    action = (alignment.get("video", {}) or {}).get("action", "")
                    st.caption(
                        f"时长对齐：配音 {verify.get('audio_duration', 0):.1f}s · "
                        f"字幕 {verify.get('subtitle_end', 0):.1f}s · "
                        f"视频 {verify.get('video_duration', 0):.1f}s · "
                        f"{'已对齐' if verify.get('aligned') else '未完全对齐'}"
                        + (f"（自动{'裁剪' if action == 'trim' else '补齐'}成片）"
                           if action in ("trim", "extend") else "")
                    )
        if st.button("生成中英发布包（本地生成文案文件）", key="wv_pack"):
            if st.session_state.w_script_zh_id and st.session_state.w_script_en_id:
                paths = {
                    "zh_video_path": out.get("zh", {}).get("output_path", ""),
                    "en_video_path": out.get("en", {}).get("output_path", ""),
                }
                packs = _call(
                    app.publish.create_bilingual_packs,
                    st.session_state.w_topic_id,
                    st.session_state.w_script_zh_id,
                    st.session_state.w_script_en_id,
                    task_id="webui-wizard", **paths,
                )
                if packs:
                    st.success("发布包已生成，可到「导出中心」查看和下载")
        if st.button("重新开始一个视频", key="wv_restart"):
            for k in list(st.session_state.keys()):
                if k.startswith("w_"):
                    del st.session_state[k]
            st.rerun()
    b1, b2 = st.columns(2)
    with b1:
        if st.button("← 返回第 5 步", key="wv_back2"):
            st.session_state.wizard_step = 4
            st.rerun()


# ---------------------------------------------------------------------------
# 各管理页面
# ---------------------------------------------------------------------------
def page_topics(app) -> None:
    st.title("选题中心")
    st.caption("管理所有猫狗短视频选题。豆包批量生成免费；手动新增不联网、不产生费用。")
    c1, c2, c3 = st.columns(3)
    with c1:
        animal = st.selectbox("动物", [None, *ANIMALS], format_func=lambda x: "全部" if x is None else ANIMAL_LABELS[x], key="pt_animal")
    with c2:
        ctype = st.selectbox("方向", [None, *CONTENT_TYPE_LABELS], format_func=lambda x: "全部" if x is None else CONTENT_TYPE_LABELS[x], key="pt_ctype")
    with c3:
        fav = st.checkbox("只看收藏", key="pt_fav")
    topics = app.topics.list(animal_type=animal, content_type=ctype, favorite_only=fav)
    st.caption(f"共 {len(topics)} 条")
    for t in topics[:50]:
        with st.container():
            st.markdown(f"**{t.get('title','')}** · {ANIMAL_LABELS.get(t.get('animal_type'),'猫狗')} · {t.get('status','')}"
                        + (" · 已收藏" if t.get("favorite") else ""))
            st.caption(_short(t.get("description", ""), 100))
            c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
            with c1:
                if st.button("收藏/取消", key=f"pt_f_{t['topic_id']}"):
                    _call(app.topics.set_favorite, t["topic_id"], not bool(t.get("favorite")))
                    st.rerun()
            with c2:
                new_status = st.selectbox(
                    "状态", TOPIC_STATUSES, index=TOPIC_STATUSES.index(t.get("status", "idea")),
                    key=f"pt_s_{t['topic_id']}", label_visibility="collapsed",
                )
            with c3:
                if st.button("保存状态", key=f"pt_sv_{t['topic_id']}"):
                    _call(app.topics.set_status, t["topic_id"], new_status)
                    st.rerun()
            with c4:
                if st.button("去生产向导", key=f"pt_w_{t['topic_id']}"):
                    st.session_state.w_topic_id = t["topic_id"]
                    st.session_state.wizard_step = 1
                    st.info("已选为向导选题，请到「首页 · 生产向导」继续")
            if st.button("删除", key=f"pt_d_{t['topic_id']}"):
                _call(app.topics.delete, t["topic_id"])
                st.rerun()

    with st.expander("用豆包批量生成选题（免费）"):
        st.caption("生成期间会短暂接管豆包窗口，请不要操作豆包，约 25 秒后自动取回。")
        c1, c2, c3 = st.columns(3)
        with c1:
            a = st.selectbox("动物", ANIMALS, format_func=lambda x: ANIMAL_LABELS[x], key="pt_ga")
        with c2:
            ct = st.selectbox("方向", [None, *CONTENT_TYPE_LABELS], format_func=lambda x: "全部" if x is None else CONTENT_TYPE_LABELS[x], key="pt_gc")
        with c3:
            n = st.slider("条数", 1, 10, 5, key="pt_gn")
        if st.button("豆包生成", key="pt_gen"):
            with st.spinner("豆包生成中，请勿操作豆包窗口…"):
                _call(app.topics.generate_batch, count=n, animal_type=a, content_type=ct, task_id="webui")
            st.rerun()


def page_knowledge(app) -> None:
    st.title("知识库与审核")
    st.caption(
        "A 级普通知识；B 级营养疾病护理；C 级用药剂量中毒急症——C 级必须人工审核才可发布。"
        "分级与审核是本地规则，不联网、不调用 AI，AI 不能自批。"
    )
    c1, c2 = st.columns(2)
    with c1:
        risk = st.selectbox("风险等级", [None, *RISK_LEVELS], format_func=lambda x: "全部" if x is None else RISK_LABELS[x], key="pk_risk")
    with c2:
        rev = st.selectbox(
            "审核状态", [None, REVIEW_PENDING, REVIEW_APPROVED, REVIEW_REJECTED],
            format_func=lambda x: {None: "全部", REVIEW_PENDING: "待核查", REVIEW_APPROVED: "已批准", REVIEW_REJECTED: "已驳回"}[x],
            key="pk_rev",
        )
    rows = app.knowledge.list(risk_level=risk, review_status=rev)
    st.caption(f"共 {len(rows)} 条")
    for k in rows[:50]:
        with st.container():
            st.markdown(f"**{k['topic']}** · {RISK_LABELS.get(k['risk_level'],'')} · "
                        f"{ {'pending':'待核查','approved':'已批准','rejected':'已驳回'}.get(k['review_status'], k['review_status']) }")
            st.caption(_short(k.get("content", ""), 120))
            can_pub = app.knowledge.can_publish(k["knowledge_id"])
            st.caption("可发布：" + ("是" if can_pub else "否（需人工审核或补齐来源）"))
            c1, c2 = st.columns([1, 1])
            with c1:
                if st.button("批准", key=f"pk_ap_{k['knowledge_id']}"):
                    _call(app.knowledge.approve, k["knowledge_id"], "本机用户", "人工复核通过")
                    st.rerun()
            with c2:
                if st.button("驳回", key=f"pk_rj_{k['knowledge_id']}"):
                    _call(app.knowledge.reject, k["knowledge_id"], "本机用户", "人工驳回")
                    st.rerun()

    st.subheader("评论问答安全测试")
    st.caption("系统拒绝给个体宠物做诊断。输入任意问题验证：")
    q = st.text_input("评论内容", key="pk_q")
    if q and st.button("测试回复", key="pk_answer"):
        st.info(app.knowledge.answer_comment(q))

    with st.expander("新增知识"):
        with st.form("pk_new"):
            t = st.text_input("知识主题")
            c = st.text_area("知识正文")
            refs = st.text_input("参考资料来源（逗号分隔）")
            a = st.selectbox("适用动物", ANIMALS, format_func=lambda x: ANIMAL_LABELS[x], key="pk_a")
            if st.form_submit_button("保存") and t.strip() and c.strip():
                r = _call(
                    app.knowledge.create, t.strip(), c.strip(), animal_type=a,
                    references=[x.strip() for x in refs.split(",") if x.strip()], task_id="webui",
                )
                if r:
                    st.success("已保存，等级：" + RISK_LABELS.get(r["risk_level"], r["risk_level"]))
                    st.rerun()


def page_scripts(app) -> None:
    st.title("脚本工厂")
    engine = app.scripts.script_engine
    st.caption(
        "中英双语脚本：英文按当地习惯本地化改写，不是逐字翻译。"
        f"当前文本引擎：{'桌面豆包（免费）' if engine == 'doubao-desktop' else 'DeepSeek（付费，需手动开启）'}"
    )
    if engine == "doubao-desktop":
        st.info(
            "脚本由本机豆包客户端生成，零 API 费用：点生成后请保持豆包窗口不被操作，"
            "约 25 秒后自动取回。失败会明确报错，不会偷偷改走 DeepSeek。"
        )
    else:
        st.warning("当前使用付费 DeepSeek 引擎；该模式默认关闭，仅在你手动改配置后生效。")
    scripts = app.scripts.list()
    if scripts:
        for s in scripts[:30]:
            with st.expander(f"{_short(s.get('title',''),40)} · {s.get('language','')} · {s.get('script_id','')[:8]}"):
                st.write(s.get("content", ""))
                _render_storyboard(s.get("storyboard") or [], "分镜")
                if st.button("本地化为英文", key=f"ps_loc_{s['script_id']}") and s.get("language") == "zh":
                    r = _call(app.scripts.localize_to_english, s["script_id"], task_id="webui")
                    if r:
                        st.success("英文脚本已生成")
    else:
        st.info("还没有脚本。到「首页 · 生产向导」第 2 步生成，或在下方快捷生成。")
    with st.expander("快捷生成双语脚本"):
        topics = _topic_options(app)
        if topics:
            t = st.selectbox("选题", topics, format_func=_label_topic, key="ps_topic")
            stype = st.selectbox("类型", [("science", "科普"), ("entertainment", "娱乐")], format_func=lambda x: x[1], key="ps_type")[0]
            if st.button("生成双语脚本", key="ps_gen"):
                with st.spinner("豆包生成中，请稍候（不要操作豆包窗口）…" if engine == "doubao-desktop" else "DeepSeek（付费）生成中…"):
                    r = _call(app.scripts.generate_bilingual, t["topic_id"], script_type=stype, task_id="webui")
                if r:
                    st.success("生成完成")
                    st.rerun()
        else:
            st.info("请先创建选题")


def page_assets(app) -> None:
    st.title("素材管理")
    st.caption(
        "画面素材来自本机豆包客户端或你本地上传，零 API 费用。"
        "上传不覆盖原件；图片素材会在合成时自动转成本地视频片段。"
    )
    c1, c2 = st.columns([2, 1])
    with c1:
        kw = st.text_input("搜索文件名/标签", key="pa_kw")
    with c2:
        animal_filter = st.selectbox(
            "按动物筛选", [None, "cat", "dog"],
            format_func=lambda x: "全部" if x is None else ANIMAL_LABELS[x], key="pa_cat",
        )
    assets = app.assets.list_assets(animal=animal_filter, keyword=kw or None)
    st.caption(f"共 {len(assets)} 个素材")
    for a in assets[:60]:
        with st.container():
            st.markdown(f"**{_short(a.get('filename', a.get('path','')), 50)}** · {a.get('kind','')} "
                        f"· 使用 {a.get('usage_count', 0)} 次")
            st.caption(a.get("path", ""))
            p = a.get("path", "")
            if a.get("kind") == "video":
                st.video(p)
            elif a.get("kind") == "image":
                st.image(p, width=240)
    with st.expander("按某个脚本的分镜批量生成（免费，推荐多素材）"):
        script_rows = app.scripts.list(limit=40)
        if not script_rows:
            st.info("还没有脚本。请先在「脚本工厂」生成。")
        else:
            chosen = st.selectbox(
                "选择脚本", script_rows,
                format_func=lambda s: _short(str(s.get("title", s.get("script_id", ""))), 40),
                key="pa_sb_script",
            )
            storyboard = _script_record(app, chosen.get("script_id", "")).get("storyboard") or []
            _doubao_storyboard_panel(
                app, storyboard, key_prefix="pa_sb", assign_to_wizard=False
            )

    with st.expander("用桌面豆包生成素材（免费，不调用 API）"):
        _doubao_generate_panel(app, "pa_ai")

    with st.expander("上传素材"):
        up = st.file_uploader("图片或视频", type=["png", "jpg", "jpeg", "webp", "mp4", "mov", "avi"], key="pa_up")
        c1, c2, c3 = st.columns(3)
        with c1:
            category = st.selectbox(
                "素材分类", ASSET_CATEGORIES,
                format_func=lambda x: ASSET_CATEGORY_LABELS[x], key="pa_c",
            )
        with c2:
            tags = st.text_input("标签", key="pa_t")
        with c3:
            lic = st.text_input("授权来源", value="用户自有/已授权", key="pa_l")
        if up and st.button("上传", key="pa_do"):
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(up.name).suffix) as tmp:
                tmp.write(up.getbuffer())
                p = tmp.name
            r = _call(app.assets.upload, p, category,
                      tags=[x.strip() for x in tags.split(",") if x.strip()], license_info=lic)
            if r:
                st.success("上传成功")
                st.rerun()


def page_video(app) -> None:
    st.title("视频工厂")
    st.caption(
        "所有视频由本地 FFmpeg 合成，固定 9:16 / 1080×1920。"
        "配音来自本地 SAPI 或你的录音，字幕本地生成，时长自动对齐，零 API 费用。"
    )
    rows = app.video.list_productions()
    st.caption(f"共 {len(rows)} 条生产记录")
    for r in rows[:30]:
        with st.container():
            st.markdown(f"**{r.get('production_id','')[:10]}** · {r.get('language','')} · "
                        f"{r.get('status','')} · 配音来源 {r.get('tts_source','')}")
            out = r.get("output_path", "")
            if out and Path(out).exists():
                st.video(out)
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("重新合成（换 BGM）", key=f"pv_re_{r['production_id']}"):
                        bgm = st.session_state.get("pv_bgm", "")
                        x = _call(app.video.recompose, r["production_id"], bgm)
                        if x:
                            st.success("已重新合成")
                            st.rerun()
                    if st.button("校正时长对齐", key=f"pv_align_{r['production_id']}"):
                        x = _call(app.video.fix_alignment, r["production_id"])
                        if x:
                            verify = (x.get("alignment") or {}).get("verify") or {}
                            st.success(
                                f"已校正：配音 {verify.get('audio_duration', 0):.1f}s · "
                                f"字幕 {verify.get('subtitle_end', 0):.1f}s · "
                                f"视频 {verify.get('video_duration', 0):.1f}s"
                            )
                            st.rerun()
                with c2:
                    with open(out, "rb") as fh:
                        st.download_button("下载 MP4", fh, file_name=Path(out).name, key=f"pv_dl_{r['production_id']}")
    st.text_input("BGM 文件路径（可选，用于重新合成）", key="pv_bgm")
    st.info("要生产新视频，请到「首页 · 生产向导」按 6 步操作。")


def page_export(app) -> None:
    st.title("导出中心")
    st.caption(
        "按选题本地生成中英两套发布文案（标题/简介/标签/封面文字）。"
        "不联网、不调用 AI、不自动发布到任何平台。"
    )
    packs = app.publish.list()
    if not packs:
        st.info("还没有发布包。在向导第 6 步合成完成后，点「生成中英发布包」。")
        return
    for p in packs[:30]:
        with st.expander(f"{_short(p.get('title',''),40)} · {p.get('language','')} · {p.get('pack_id','')[:8]}"):
            for field in ["title", "description", "hashtags", "thumbnail_text", "video_path", "export_dir"]:
                if field in p:
                    st.markdown(f"**{field}**：{p[field]}")


PAID_SERVICE_LABELS = {
    "deepseek": "DeepSeek",
    "ai_image": "AI 图片",
    "ai_video": "AI 视频",
    "online_tts": "在线配音",
    "network_asset": "网络素材",
}


def page_status(app) -> None:
    st.title("运行状态 · 零成本确认")
    st.caption(
        "用这一页自查：链路是否全部本地、有没有意外产生付费调用。"
        "默认方案应该是「本地链路就绪 + 本月 0 次付费调用」。"
    )

    st.subheader("本地链路")
    tts = app.tts.capabilities()
    engine = app.scripts.script_engine
    doubao_text, _ = _doubao_status(app)
    if tts.get("sapi_available"):
        voice = "Windows SAPI（离线免费）"
    elif tts.get("piper_available"):
        voice = "本地 Piper"
    else:
        voice = "上传自己的录音"
    rows = [
        ("文本生成", "桌面豆包（免费）" if engine == "doubao-desktop" else "DeepSeek（付费）",
         f"豆包客户端：{doubao_text}"),
        ("配音", voice, "不联网、不产生费用；豆包朗读不导出音频，故不使用"),
        ("画面素材", "豆包生成 / 本地上传", "图片素材合成前自动转成本地视频片段"),
        ("字幕", "本地规则生成", "按真实配音时长自动对齐"),
        ("视频合成", "本地 FFmpeg", "9:16 / 1080×1920"),
        ("发布包", "本地文件", "只生成文案文件，不自动发布"),
    ]
    for name, value, note in rows:
        c1, c2, c3 = st.columns([1, 2, 3])
        c1.markdown(f"**{name}**")
        c2.write(value)
        c3.caption(note)

    st.subheader("付费服务闸门")
    status = app.guard.status()["services"]
    paid_keys = list(PAID_SERVICE_LABELS)
    cols = st.columns(len(paid_keys))
    for col, key in zip(cols, paid_keys):
        enabled = bool(status.get(key, {}).get("enabled"))
        col.metric(PAID_SERVICE_LABELS[key], "开启" if enabled else "关闭")
    if any(status.get(k, {}).get("enabled") for k in paid_keys):
        st.warning("检测到有付费服务处于开启状态；默认方案应全部关闭，请确认这是你的主动选择。")
    else:
        st.success("所有付费服务均为关闭状态；豆包失败也不会自动回退到付费服务。")

    st.subheader("API 调用账本（审计）")
    month = app.guard.month_cost()
    total = app.guard.total_cost()
    c1, c2, c3 = st.columns(3)
    c1.metric("本月调用", f"{int(month.get('calls', 0) or 0)} 次")
    c2.metric("本月费用", f"${float(month.get('cost_usd', 0.0) or 0.0):.4f}")
    c3.metric("累计费用", f"${float(total.get('cost_usd', 0.0) or 0.0):.4f}")
    st.caption(
        "账本只记录付费 API 的用量；全本地流程不会产生任何新增记录。"
        "历史记录不会被清零（用于审计）。判断当前是否零成本，请看上方开关是否全部为「关闭」。"
    )
    with st.expander("按任务查询调用记录"):
        tid = st.text_input("任务 ID", key="pc_tid")
        if tid and st.button("查询", key="pc_q"):
            st.json(app.guard.task_cost(tid))
    with st.expander("服务开关原始状态（只读）"):
        st.json(status)


def page_settings(app) -> None:
    st.title("设置")
    st.caption(
        "全部本机运行：桌面豆包负责文本，SAPI 负责配音，FFmpeg 负责合成。"
        "默认不需要任何 API Key，也不会产生 API 费用。"
    )

    st.subheader("引擎总览")
    engine = app.scripts.script_engine
    tts_caps = app.tts.capabilities()
    c1, c2, c3 = st.columns(3)
    c1.metric("文本生成", "桌面豆包" if engine == "doubao-desktop" else "DeepSeek（付费）")
    c2.metric("配音", tts_caps.get("engine_configured", "sapi"))
    c3.metric("视频合成", "本地 FFmpeg")

    st.subheader("豆包客户端自检")
    try:
        caps = app.doubao.capabilities()
        st.write(
            f"客户端：{'已安装' if caps.get('client_installed') else '未找到'} · "
            f"运行中：{'是' if caps.get('client_running') else '否'}"
        )
        deps = caps.get("gui_dependencies", {})
        st.write(
            "自动化依赖："
            + " · ".join(f"{name}={'就绪' if ok else '缺失'}" for name, ok in deps.items())
        )
        st.caption(caps.get("note", ""))
    except Exception as exc:  # noqa: BLE001 - 自检失败不应中断设置页
        st.warning(f"豆包状态探测失败：{exc}")
    st.caption(
        "客户端改版导致点击错位时，改 PetAI/config/petai.toml 的 [doubao] / [doubao.coords] 即可，不用改代码。"
    )

    st.subheader("本地配音能力")
    st.json(tts_caps)
    if tts_caps.get("sapi_available"):
        st.caption(f"系统音色：{', '.join(app.sapi.capabilities().get('voices', []))}")

    st.subheader("视频参数")
    st.write(
        f"画幅：{app.config.get('video', 'aspect', '9:16')} → "
        f"{app.config.get('video', 'width', 1080)}×{app.config.get('video', 'height', 1920)}"
    )
    st.write(
        f"时长自动对齐：{'开启' if app.config.get('video', 'auto_align', True) else '关闭'} · "
        f"容差 {app.config.get('video', 'align_tolerance', 0.3)}s"
        "（以真实配音时长为准，字幕末尾与成片末尾自动对齐）"
    )

    st.subheader("零成本说明")
    st.markdown(
        "- 不需要 API Key：文本由本机已登录的豆包客户端生成。\n"
        "- 豆包免费额度与排队由客户端决定，PetAI 不绕过、不付费加速。\n"
        "- 豆包失败会明确报错，不会自动切换到 DeepSeek 或其他付费服务。\n"
        "- 配音/字幕/合成/发布包全部本地完成，不联网。\n"
        "- 上传素材不覆盖原件；高风险知识必须人工审核，AI 不能自批。"
    )

    with st.expander("高级 · 付费 API（默认关闭，不推荐）"):
        ds_on = app.config.is_enabled(Service.DEEPSEEK)
        ds_key = app.config.has_api_key(Service.DEEPSEEK)
        st.write(
            f"DeepSeek 开关：{'开启' if ds_on else '关闭'} · "
            f"Key：{'已配置' if ds_key else '未配置'}"
        )
        st.caption(
            "本项目默认不使用任何付费 API。如确实要启用，请手动编辑 "
            "PetAI/config/petai.toml：把 [services].deepseek 改为 true、"
            "[script].engine 改为 deepseek，并在 .env 填 DEEPSEEK_API_KEY。"
            "界面刻意不提供一键开启按钮，避免误触产生费用。"
        )


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def main() -> None:
    _init_state()
    app = _get_app()
    with st.sidebar:
        st.markdown("## Pet AI Content Factory")
        st.caption("豆包 + 本地 · 零 API 费用 · v1.1")
        st.divider()
        choice = st.radio("功能菜单", MENUS, key="nav")
        st.divider()
        doubao_text, _ = _doubao_status(app)
        st.caption(f"豆包客户端：{doubao_text}")
        st.caption("文本 豆包 · 配音 SAPI · 合成 FFmpeg")
    _show_local_strip(app)
    _render_flashes()
    {
        MENU_HOME: page_home,
        MENU_AUTO: page_auto,
        MENU_TOPICS: page_topics,
        MENU_KNOWLEDGE: page_knowledge,
        MENU_SCRIPTS: page_scripts,
        MENU_ASSETS: page_assets,
        MENU_VIDEO: page_video,
        MENU_EXPORT: page_export,
        MENU_COST: page_status,
        MENU_SETTINGS: page_settings,
    }[choice](app)


if __name__ == "__main__":
    main()
