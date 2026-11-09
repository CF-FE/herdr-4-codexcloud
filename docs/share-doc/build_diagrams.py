#!/usr/bin/env python3
"""Render the four presentation diagrams; no services or model calls are used."""

import argparse
import os
from pathlib import Path
import tempfile

os.environ.setdefault("MPLCONFIGDIR", tempfile.mkdtemp(prefix="herdr-share-mpl-"))
os.environ.setdefault("XDG_CACHE_HOME", tempfile.mkdtemp(prefix="herdr-share-font-"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
from matplotlib.path import Path as PlotPath


ROOT = Path(__file__).resolve().parent
INK, MUTED, LINE = "#172F40", "#526577", "#CCD7DF"
BLUE, TEAL, AMBER = "#345ED5", "#137F75", "#9F681B"
PALE_BLUE, PALE_TEAL, PALE_AMBER = "#EDF2FD", "#EAF5F1", "#FCF3E2"
FONT = None


def text(ax, x, y, value, size=22, color=INK, weight="normal", align="left"):
    return ax.text(x, y, value, fontsize=size * 0.72, color=color,
                   fontproperties=FONT, weight=weight, ha=align, va="top",
                   linespacing=1.35, zorder=5)


def box(ax, x, y, width, height, title="", body="", color="white",
        accent=BLUE, title_size=26, body_size=20, dashed=False):
    patch = FancyBboxPatch((x, y), width, height,
                          boxstyle="round,pad=0,rounding_size=14",
                          linewidth=1.25, edgecolor=LINE, facecolor=color,
                          linestyle=(0, (5, 4)) if dashed else "solid", zorder=2)
    ax.add_patch(patch)
    if title:
        text(ax, x + 22, y + 18, title, title_size, accent, "bold")
    if body:
        text(ax, x + 22, y + 61, body, body_size, MUTED)


def arrow(ax, points, color=BLUE, dashed=False):
    if len(points) > 2:
        path = PlotPath(points, [PlotPath.MOVETO] + [PlotPath.LINETO] * (len(points) - 1))
        p = FancyArrowPatch(path=path, arrowstyle="-|>", mutation_scale=13,
                            linewidth=1.8, color=color,
                            linestyle=(0, (5, 4)) if dashed else "solid", zorder=3)
    else:
        p = FancyArrowPatch(*points, arrowstyle="-|>", mutation_scale=13,
                            linewidth=1.8, color=color,
                            linestyle=(0, (5, 4)) if dashed else "solid", zorder=3)
    ax.add_patch(p)


def canvas(number, title, subtitle):
    fig, ax = plt.subplots(figsize=(16, 9), dpi=240)
    fig.subplots_adjust(0, 0, 1, 1)
    fig.patch.set_facecolor("#F6F8FA")
    ax.set(xlim=(0, 1600), ylim=(900, 0))
    ax.axis("off")
    text(ax, 64, 38, "SUPER-AGENT  /  CF-FE", 17, TEAL, "bold")
    text(ax, 64, 79, title, 43, INK, "bold")
    text(ax, 64, 143, subtitle, 22, MUTED)
    text(ax, 1536, 43, f"{number:02d} / 04", 18, MUTED, align="right")
    ax.plot([64, 1536], [838, 838], color=LINE, linewidth=0.9)
    foot = text(ax, 64, 855, "依据：架构与状态文档 · 证据截至 2026-11-09 · 连线不代表全链路业务验收", 16, MUTED)
    foot.set_url("https://github.com/CF-FE/herdr-4-codexcloud/blob/main/docs/architecture.md")
    text(ax, 1536, 855, "程序负责执行 · 模型负责决策", 17, TEAL, align="right")
    return fig, ax


def export(fig, ax, name, description):
    fig.canvas.draw()
    bounds = fig.bbox
    for item in ax.texts:
        b = item.get_window_extent(fig.canvas.get_renderer())
        if b.x0 < 0 or b.y0 < 0 or b.x1 > bounds.x1 or b.y1 > bounds.y1:
            raise ValueError(f"Text outside canvas in {name}: {item.get_text()}")
    fig.savefig(ROOT / f"{name}.png", dpi=240, facecolor=fig.get_facecolor())
    svg = ROOT / f"{name}.svg"
    fig.savefig(svg, facecolor=fig.get_facecolor(),
                metadata={"Title": description, "Description": description,
                          "Date": "2026-11-09", "Creator": "CF-FE"})
    svg.write_text("\n".join(line.rstrip() for line in svg.read_text().splitlines()) + "\n")
    plt.close(fig)
    print(f"{name}: SVG + 3840x2160 PNG")


def architecture():
    fig, ax = canvas(1, "两层 Agent，共用判断与记忆能力", "云端Dot交办和汇报；负责人组织执行与验收；Decision Service 提供有限判断。")
    box(ax, 64, 207, 260, 109, "公子", "目标 · 授权 · 关键取舍", PALE_BLUE, body_size=19)
    box(ax, 364, 207, 720, 109, "云端Dot", "调研与拆解 · 委派 · 感知进展 · 向公子汇报；不业务验收", PALE_BLUE, body_size=19)
    arrow(ax, [(327, 261), (359, 261)])
    arrow(ax, [(724, 320), (724, 357)])
    text(ax, 745, 328, "完整目标与验收标准", 16, MUTED)
    box(ax, 64, 362, 1020, 194, color=PALE_TEAL)
    text(ax, 85, 378, "01  协调组织层 · Herdr 原生岗位", 21, TEAL, "bold")
    box(ax, 85, 416, 330, 118, "项目协调员", "统筹负责人、资源与路线\n依有效任命安排负责人绑定", accent=TEAL, title_size=24, body_size=18)
    box(ax, 456, 416, 606, 118, "项目负责人 · 默认验收者", "维护项目工作流 · 选择、复用或创建 Worker\n安排执行与复核 · 决定成果是否放行", accent=TEAL, title_size=24, body_size=19)
    arrow(ax, [(418, 472), (451, 472)], TEAL)
    arrow(ax, [(759, 559), (759, 582), (224, 582), (224, 608)], TEAL)
    arrow(ax, [(759, 582), (574, 582), (574, 608)], TEAL)
    arrow(ax, [(759, 582), (924, 582), (924, 608)], TEAL, True)
    text(ax, 64, 580, "02  Worker 层", 17, BLUE, "bold")
    for x, title, body, dashed in [
            (64, "Mac Herdr", "本地 Worker · 项目资产\n原生模型与强度读回", False),
            (414, "Win Herdr", "本地 Worker · 媒体等任务\n按单核实际执行能力", False),
            (764, "Codex Cloud 作业", "获准唯一路由原生提交\n仅具体作业是宿主例外", True)]:
        box(ax, x, 612, 320, 123, title, body, PALE_BLUE, title_size=24, body_size=18, dashed=dashed)
    box(ax, 1134, 207, 402, 349, "Decision Service · VPS", "HTTP（程序）/ MCP（Agent）\n同一身份范围与判断核心\n业务判断：Luna\n\n原调用方给合法候选\n原执行器采用与回传结果", PALE_AMBER, AMBER, 25, 19)
    box(ax, 1155, 466, 360, 69, "私有 SQLite · 决策与已收反馈", title_size=19, accent=AMBER)
    arrow(ax, [(1087, 435), (1128, 435)], AMBER)
    arrow(ax, [(1128, 505), (1087, 505)], AMBER)
    text(ax, 1108, 404, "候选", 13, AMBER, align="center")
    text(ax, 1108, 480, "建议", 13, AMBER, align="center")
    box(ax, 1134, 612, 402, 123, "Hindsight · Win 项目记忆", "DS 按需经 VPS 网关召回\n内部 Luna：recall / consolidation", PALE_TEAL, TEAL, 23, 18)
    arrow(ax, [(1285, 559), (1285, 608)], TEAL)
    arrow(ax, [(1390, 608), (1390, 559)], TEAL)
    text(ax, 1406, 578, "经原网关回传", 14, TEAL)
    box(ax, 64, 773, 718, 47, "Task Mailbox 查原单  ·  Project / Issue 查项目", title_size=20, accent=INK)
    box(ax, 814, 773, 722, 47, "角色创建：任命、技术创建与原生接手分开核验", title_size=20, accent=INK)
    export(fig, ax, "super-agent-architecture", "Super-Agent 总架构：Herdr 协调层和本地 Worker、获准 Cloud 作业、独立 Decision Service 与 Win Hindsight；判断不授予派发、任命或验收权限。")


def lifecycle():
    fig, ax = canvas(2, "向下委派，异步回收", "完整目标沿现任绑定交办；独立工作继续，阻塞沿负责人、协调员和云端Dot升级。")
    text(ax, 64, 205, "向下交办  /  一次给清目标、输入、约束和验收者", 22, BLUE, "bold")
    xs = [64, 440, 816, 1192]
    for x, title, body in zip(xs,
            ["云端Dot", "项目协调员", "项目负责人", "Worker"],
            ["调研、拆解与发起委派\n指定验收者、向公子汇报", "核项目与现任负责人\n安排环境、资源与路线", "维护工作流与任务单元\n选择、复用或创建 Worker", "沿 Herdr 原生入口执行\nCloud 仅为获准具体作业"]):
        box(ax, x, 246, 344, 137, title, body, PALE_BLUE, body_size=20)
    for x in xs[:-1]:
        arrow(ax, [(x + 347, 314), (x + 371, 314)])
    text(ax, 800, 405, "日常推进不等待云端Dot在线或已读 ACK", 24, TEAL, "bold", "center")
    text(ax, 64, 456, "向上回收  /  过程进展与验收结论分别回填", 22, TEAL, "bold")
    for x, title, body in zip(xs,
            ["过程进展与阻塞", "验收结论", "两个回收入口", "云端Dot汇报"],
            ["执行中随时回填\n保留版本与原生执行引用", "指定验收者独立记录结论\n项目负责人默认验收", "Task Mailbox 查原单\nProject / Issue 查项目", "分别读取进展和验收结论\n向公子汇报，不业务验收"]):
        box(ax, x, 495, 344, 139, title, body, PALE_TEAL, accent=TEAL, body_size=20)
    arrow(ax, [(236, 492), (236, 482), (988, 482), (988, 492)], TEAL)
    for x in xs[1:-1]:
        arrow(ax, [(x + 347, 564), (x + 371, 564)], TEAL)
    text(ax, 800, 654, "原执行器另向 Decision Service 直传采用与结果，业务验收结论沿原任务记录。", 20, TEAL, align="center")
    for x, width, title, body in [
            (64, 464, "协调员怎样建立", "有效任命方定职责；独立受信宿主管理入口\n在 Herdr 初始化 / 恢复，核原生接手"),
            (568, 464, "负责人怎样建立", "协调员依有效任命安排创建、绑定或交接\n获准入口技术执行，核本人接手"),
            (1072, 464, "Worker 怎样建立", "负责人 / 获准唯一派发者按任务决定\n复用或沿获准入口创建，核实际接手")]:
        box(ax, x, 704, width, 113, title, body, PALE_AMBER, AMBER, 23, 17)
    export(fig, ax, "task-lifecycle", "任务闭环与角色创建职责：目标向下委派、成果按指定验收者验收、信箱与项目记录异步回收；任命、技术创建、原生接手分别确认。")


def workflow():
    fig, ax = canvas(3, "判断交服务，执行与恢复留在原端", "项目拥有 workflows/ 与检查点；固定规则直接运行，需要判断时提交合法候选。")
    box(ax, 64, 226, 400, 181, "1  原 workflow / 创建者", "任务快照 + 合法动作 / 模型候选\n模型候选须可用且获组织允许\nMac new-unit 校验发布快照", PALE_BLUE, title_size=24, body_size=20)
    box(ax, 568, 226, 464, 181, "2  Decision Service · VPS", "HTTP / MCP 共用核心与身份范围\n按需取记忆 → 业务 Luna 有限判断\n返回候选建议，保留来源", PALE_AMBER, AMBER, 24, 20)
    box(ax, 1136, 226, 400, 181, "3  原执行器采用", "沿合法分支 / 原生宿主执行\n角色实际模型 / 强度单独读回\n工程例：gpt-6.1-sol / low", PALE_BLUE, title_size=24, body_size=20)
    arrow(ax, [(467, 316), (562, 316)])
    text(ax, 516, 277, "合法候选", 17, MUTED, align="center")
    arrow(ax, [(1035, 316), (1130, 316)], AMBER)
    text(ax, 1083, 277, "建议", 17, AMBER, align="center")
    box(ax, 64, 484, 400, 149, "原端检查点", "执行状态 · 决策关联 · 待传事件\n显式恢复 / 后续适配入口补传\n未知先对账，不重跑业务", accent=INK, title_size=24, body_size=19)
    arrow(ax, [(264, 410), (264, 479)], BLUE)
    box(ax, 568, 484, 464, 149, "按需召回 · Hindsight", "实际经 VPS gateway → Win 项目记忆\n返回带来源片段；内部 Luna 独立\nnative 仅显式适配，无自动换路", PALE_TEAL, TEAL, 24, 19)
    arrow(ax, [(745, 410), (745, 479)], TEAL)
    arrow(ax, [(855, 479), (855, 410)], TEAL)
    text(ax, 800, 444, "检索 / 证据", 16, TEAL, align="center")
    box(ax, 1136, 484, 400, 149, "4  原执行器直传反馈", "application / result：采用、结果与质量\n先存原端待传，再在事务外发送\n沿 event_id / revision 回传与补传", PALE_BLUE, title_size=24, body_size=18)
    arrow(ax, [(1336, 410), (1336, 479)])
    box(ax, 64, 704, 400, 113, "业务验收仍归原任务", "项目负责人 / 本单指定验收者决定\nDS 不任命、不派发、不审批或验收", accent=INK, title_size=24, body_size=18)
    box(ax, 568, 704, 968, 113, "5  Decision Service 接收反馈 → 私有 SQLite", "持久保存后确认；get_decision 只读对账。库只保存已收事实，原端保有待传与恢复。\nHindsight retain 不是必经步骤；webhook / 自动补偿尚未启用。", PALE_AMBER, AMBER, 24, 19)
    arrow(ax, [(1336, 636), (1336, 699)], AMBER)
    text(ax, 1356, 660, "原事件 / 修订", 16, AMBER)
    export(fig, ax, "project-workflow", "Decision Service 接入项目工作流：合法候选、按需记忆和 Luna 判断、原执行器采用、application/result 直传、SQLite 持久与只读对账；原端拥有执行检查点、待传和恢复。")


def memory():
    fig, ax = canvas(4, "共享记忆与三类模型，边界分开", "Decision Service 实际经 VPS 网关取证据；记忆、业务判断、执行角色各有独立职责与配置。")
    box(ax, 64, 225, 304, 128, "云端Dot / Cloud", "Dot：已有项目级读取\nCloud：逐单核工具与权限", PALE_BLUE, title_size=24, body_size=19)
    box(ax, 466, 225, 340, 128, "VPS 记忆薄网关", "认证、范围与传输适配\n不复制 Win 记忆数据库", PALE_BLUE, title_size=24, body_size=19)
    box(ax, 908, 225, 250, 128, "Win 代理", "stdio MCP\nAPI relay", PALE_TEAL, TEAL, 25, 20)
    box(ax, 1238, 225, 298, 257, "Win Hindsight", "项目记忆与带来源片段\nAPI + PostgreSQL + BGE\n\n内部 Luna\nrecall / consolidation\nreflect 沿原 LLM 路径", PALE_TEAL, TEAL, 24, 18)
    arrow(ax, [(371, 284), (460, 284)])
    arrow(ax, [(809, 284), (902, 284)])
    text(ax, 857, 246, "EasyTier / SSH", 14, MUTED, align="center")
    arrow(ax, [(1161, 284), (1232, 284)], TEAL)
    text(ax, 1198, 246, "MCP / API", 14, MUTED, align="center")
    box(ax, 64, 431, 304, 194, "Decision Service · VPS", "业务 Luna 有限判断\nHTTP / MCP 同核心\n私有 SQLite 存决策与反馈\n\n不拥有 Win 记忆库", PALE_AMBER, AMBER, 23, 18)
    arrow(ax, [(371, 472), (410, 472), (410, 381), (584, 381), (584, 357)], AMBER)
    arrow(ax, [(694, 357), (694, 399), (428, 399), (428, 554), (371, 554)], TEAL)
    text(ax, 466, 435, "按需召回 / 经原网关带来源返回", 17, AMBER)
    box(ax, 466, 491, 340, 134, "Mac / Win Harness", "Mac：插件经 LAN SSH\nWin：本机 stdio MCP\n各端采用分别核验", PALE_BLUE, title_size=24, body_size=18)
    arrow(ax, [(809, 553), (857, 553), (857, 393), (1033, 393), (1033, 357)], TEAL)
    box(ax, 908, 491, 250, 134, "执行角色模型", "独立于两个 Luna 用途\n按宿主实际配置读回\n模型 / 强度分别留证", accent=INK, title_size=22, body_size=17)
    box(ax, 1238, 551, 298, 134, "Web 管理端", "脚本前台启动 · Ctrl+C 关闭\n本机浏览 / Mac LAN 隧道\n按需使用，不自启", PALE_AMBER, AMBER, 24, 17)
    arrow(ax, [(1387, 546), (1387, 487)], AMBER)
    text(ax, 1405, 506, "原 API / 认证", 15, MUTED)
    box(ax, 64, 699, 742, 118, "三类模型分别配置，凭据不合并", "业务选择用 DS Luna；记忆排序 / 整合用 Win 内部 Luna；\n角色执行模型单独选择和读回。补丁后继续 Luna，无自动 Q8 切换。", title_size=23, body_size=18, accent=INK)
    box(ax, 846, 723, 690, 94, "工程链路已验；自然业务效果仍待观察", "拒答原因仍 UNKNOWN。webhook 已入计划，事件接线待验。", PALE_AMBER, AMBER, 23, 17)
    export(fig, ax, "hindsight-topology", "Hindsight 和 Decision Service 拓扑：VPS 薄网关接入 Win 记忆，DS 按需取证后独立使用业务 Luna；Win 内部 recall/consolidation Luna 与执行角色模型分别配置。")


def main():
    global FONT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", type=Path, default=Path("/System/Library/Fonts/STHeiti Medium.ttc"))
    args = parser.parse_args()
    if not args.font.is_file():
        parser.error("Supply --font with an installed CJK font file.")
    FONT = FontProperties(fname=args.font)
    matplotlib.rcParams["svg.fonttype"] = "none"
    matplotlib.rcParams["svg.hashsalt"] = "cf-fe-herdr-share-20261109"
    architecture()
    lifecycle()
    workflow()
    memory()


if __name__ == "__main__":
    main()
