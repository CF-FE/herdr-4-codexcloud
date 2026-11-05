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
    foot = text(ax, 64, 855, "依据：架构与状态文档 · 观测截至 2026-11-05 · 连线不代表全链路业务验收", 16, MUTED)
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
                          "Date": "2026-11-05", "Creator": "CF-FE"})
    svg.write_text("\n".join(line.rstrip() for line in svg.read_text().splitlines()) + "\n")
    plt.close(fig)
    print(f"{name}: SVG + 3840x2160 PNG")


def architecture():
    fig, ax = canvas(1, "一套系统，职责明确的两层 Agent", "终端发起目标；协调层组织和验收；Worker 执行任务单元。")
    text(ax, 64, 223, "需求与汇报终端", 21, BLUE, "bold")
    box(ax, 248, 195, 360, 108, "公子", "目标 · 授权 · 关键取舍", PALE_BLUE)
    box(ax, 650, 195, 886, 108, "云端Dot", "调研与方案 · 委派 · 感知进展 · 向公子汇报；不承担业务验收", PALE_BLUE)
    arrow(ax, [(611, 249), (645, 249)])
    arrow(ax, [(1090, 306), (1090, 345)])
    text(ax, 1110, 313, "完整目标与验收标准", 16, MUTED)
    text(ax, 64, 381, "01  协调组织层", 21, TEAL, "bold")
    text(ax, 64, 420, "岗位由 Herdr 承载", 17, MUTED)
    box(ax, 248, 350, 1288, 221, color=PALE_TEAL)
    box(ax, 270, 373, 354, 115, "项目协调员", "负责人 · 跨项目资源 · 执行路线", accent=TEAL, title_size=25, body_size=18)
    box(ax, 663, 373, 570, 115, "项目负责人 · 默认验收者", "细化任务 · 维护流程 · 安排 Worker · 核收成果", accent=TEAL, title_size=25)
    box(ax, 1272, 373, 242, 115, "Codex 路由", "唯一原生提交者\n具体 Cloud 作业例外", accent=TEAL, title_size=24, body_size=17)
    arrow(ax, [(626, 429), (657, 429)], TEAL)
    arrow(ax, [(1235, 429), (1266, 429)], TEAL, True)
    text(ax, 277, 518, "项目自有 workflows/ 代码 + README  ·  程序执行确定步骤，模型处理新判断", 22, TEAL)
    arrow(ax, [(947, 573), (947, 594), (448, 594), (448, 614)], TEAL)
    arrow(ax, [(947, 594), (888, 594), (888, 614)], TEAL)
    arrow(ax, [(1393, 573), (1393, 614)], TEAL, True)
    text(ax, 64, 647, "02  Worker 层", 21, BLUE, "bold")
    text(ax, 64, 685, "成果与原生证据交接", 17, MUTED)
    box(ax, 248, 619, 400, 124, "mac-herdr", "本地 Agent · Codex 主力\n开发、工具与项目资产", PALE_BLUE, body_size=19)
    box(ax, 688, 619, 400, 124, "win-herdr", "本地 Agent · 媒体执行\nFFmpeg、本地模型与项目资产", PALE_BLUE, body_size=19)
    box(ax, 1128, 619, 408, 124, "Codex Cloud", "按授权建立托管作业\n任务 ID 关联产物与回传", PALE_BLUE, body_size=19)
    text(ax, 64, 790, "共享基础设施", 19, MUTED, "bold")
    for x, title in [(248, "Task Mailbox · 单件进展"), (688, "GitHub Project · 项目与能力"), (1128, "Hindsight · 项目共享经验")]:
        box(ax, x, 773, 400 if x != 1128 else 408, 47, title, title_size=20, accent=INK)
    export(fig, ax, "super-agent-architecture", "Super-Agent 总架构：终端、协调组织层、Worker 层与共享基础设施；云端Dot不负责业务验收。")


def lifecycle():
    fig, ax = canvas(2, "向下委派，异步回收", "云端Dot可以暂时离线；确定步骤按本地流程推进，进度恢复后再回收。")
    text(ax, 64, 205, "向下交办  /  一次给清目标、输入、约束和验收者", 22, BLUE, "bold")
    xs = [64, 440, 816, 1192]
    for x, title, body in zip(xs,
            ["云端Dot", "项目协调员", "项目负责人", "Worker"],
            ["调研、拆解、澄清确定性\n提交可委派实施方案", "核项目与现任负责人\n安排环境和资源", "维护工作流与任务单元\n指定执行者和交期", "沿 Herdr／Cloud 原生入口\n执行并交付成果"]):
        box(ax, x, 246, 344, 137, title, body, PALE_BLUE, body_size=20)
    for x in xs[:-1]:
        arrow(ax, [(x + 347, 314), (x + 371, 314)])
    text(ax, 800, 412, "日常推进不等待云端Dot在线或已读 ACK", 24, TEAL, "bold", "center")
    text(ax, 64, 463, "异步回收  /  成果验收与向用户汇报各负其责", 22, TEAL, "bold")
    for x, title, body in zip(xs,
            ["执行者交付", "负责人验收", "两个进度入口", "云端Dot汇报"],
            ["版本、产物、自检与未决项\n保留原生执行引用", "组织复核并决定业务放行\n另有指定则由该验收者决定", "Task Mailbox 查原单\nProject／Issue 查项目", "主动感知实质进展\n向公子汇报结果与需决策事项"]):
        box(ax, x, 504, 344, 145, title, body, PALE_TEAL, accent=TEAL, body_size=20)
    for x in xs[:-1]:
        arrow(ax, [(x + 347, 576), (x + 371, 576)], TEAL)
    text(ax, 800, 666, "进行中与阻塞随时回填；验收只决定业务放行。", 21, TEAL, align="center")
    box(ax, 64, 698, 1472, 111, color=PALE_AMBER)
    text(ax, 87, 718, "阻塞升级", 23, AMBER, "bold")
    text(ax, 278, 718, "Worker → 项目负责人 → 协调员 → 云端Dot → 公子", 25, INK)
    text(ax, 278, 763, "权限内就地解决；原 Issue 留责任、影响、处置和下次检查点，独立工作继续。", 21, MUTED)
    export(fig, ax, "task-lifecycle", "任务闭环：完整目标向下委派，项目负责人验收，信箱与GitHub异步回收；阻塞逐级解决与留痕。")


def workflow():
    fig, ax = canvas(3, "程序负责执行，模型负责决策", "每个项目拥有自己的工作流代码，负责人初始化、评审并持续迭代。")
    for x, title, body in [(64, "负责人设计", "定义任务单元、决定点、交接与证据"),
                          (568, "项目仓版本化", "workflows/ + README + 规范与记忆入口"),
                          (1072, "项目独立运行", "源码、输入与原检查点支持恢复接手")]:
        box(ax, x, 211, 464, 120, title, body, PALE_BLUE, body_size=20)
    arrow(ax, [(531, 269), (563, 269)])
    arrow(ax, [(1035, 269), (1067, 269)])
    text(ax, 64, 380, "一个任务单元的运行与交接", 23, TEAL, "bold")
    xs = [64, 364, 664, 964, 1264]
    titles = ["程序节点", "模型决定 · 按需", "原生交办", "结果回收", "成果交接"]
    bodies = ["核输入、规则与资产\n确定步骤直接执行", "保存问题与证据\n选择允许的后续分支", "Herdr／Cloud 路线\n绑定稳定动作引用", "沿原动作查回执\n不明先对账再继续", "下一单元实际接手\n负责人核收与验收"]
    for x, title, body in zip(xs, titles, bodies):
        box(ax, x, 432, 272, 142, title, body, PALE_TEAL, TEAL, 24, 19)
    for x in xs[:-1]:
        arrow(ax, [(x + 275, 502), (x + 295, 502)], TEAL)
    text(ax, 800, 605, "决定与原生回执保存在检查点；一次有效决定后，确定性步骤继续推进。", 23, TEAL, align="center")
    for x, title, body in [(64, "Git：正式规则与代码", "各项目独立维护，不依赖协调仓在线"),
                          (568, "检查点：本次执行状态", "执行侧私有目录，恢复原任务与动作"),
                          (1072, "Hindsight：共享经验", "带来源按需召回，重要经验随单元沉淀")]:
        box(ax, x, 679, 464, 126, title, body, title_size=25, body_size=20, accent=INK)
    export(fig, ax, "project-workflow", "项目动态工作流：负责人设计并版本化，程序运行，按需模型决定，原生交办、回收与跨Worker成果交接。")


def memory():
    fig, ax = canvas(4, "共享项目记忆，一库多端", "MCP 优先、API 获准兜底；同项目共享 bank，记忆不接管任务状态或验收。")
    box(ax, 64, 235, 300, 114, "云端Dot", "HTTPS MCP / OAuth\n项目级只读已验", PALE_BLUE)
    box(ax, 64, 398, 300, 114, "Codex Cloud", "首选 MCP / API 兜底\n托管作业逐单核采用", dashed=True, accent=AMBER)
    box(ax, 450, 235, 330, 141, "vps-vultr", "认证与薄适配\n有界预算、重试与协议切换\n不复制库、不运行模型", PALE_BLUE, body_size=18)
    box(ax, 900, 235, 270, 141, "Win 代理", "stdio MCP\nAPI relay", PALE_TEAL, TEAL, 26, 21)
    box(ax, 1250, 235, 286, 266, "Win Hindsight", "显式项目 bank\n\nAPI + PostgreSQL + BGE\n\n经验、背景与来源", PALE_TEAL, TEAL, 26, 19)
    arrow(ax, [(368, 292), (445, 292)])
    arrow(ax, [(368, 455), (410, 455), (410, 345), (445, 345)], AMBER, True)
    arrow(ax, [(783, 306), (895, 306)])
    text(ax, 838, 265, "EasyTier / SSH", 16, MUTED, align="center")
    arrow(ax, [(1173, 306), (1245, 306)], TEAL)
    text(ax, 1211, 265, "本机 MCP/API", 14, MUTED, align="center")
    box(ax, 450, 510, 330, 117, "Mac Codex 插件", "App / CLI 共用安装\nLAN 客户端 · 只读已验", PALE_BLUE, body_size=19)
    arrow(ax, [(784, 561), (838, 561), (838, 403), (975, 403), (975, 380)], TEAL)
    text(ax, 687, 467, "LAN SSH 承载 stdio MCP", 18, TEAL, align="center")
    box(ax, 900, 510, 270, 117, "Win Harness", "本机 stdio MCP\n各客户端采用分别核验", PALE_TEAL, TEAL, 25, 17)
    arrow(ax, [(1093, 506), (1093, 380)], TEAL)
    box(ax, 1250, 607, 286, 124, "Web 管理端", "脚本前台启动 · Ctrl+C 关闭\n按需使用，不隐藏、不自启", PALE_AMBER, AMBER, 26, 17)
    arrow(ax, [(1393, 602), (1393, 505)], AMBER)
    text(ax, 1409, 548, "原 API / 认证", 17, MUTED)
    box(ax, 900, 669, 270, 62, "浏览器", title_size=23, accent=INK)
    arrow(ax, [(1174, 699), (1245, 699)], AMBER)
    text(ax, 1210, 652, "本机 / SSH 隧道", 14, MUTED, align="center")
    text(ax, 64, 561, "Mac ↔ Win：局域网", 19, MUTED)
    text(ax, 64, 598, "VPS ↔ Win：EasyTier", 19, MUTED)
    text(ax, 64, 636, "API 不能绕过共同网络故障。", 18, MUTED)
    box(ax, 64, 774, 1472, 43, color=PALE_AMBER)
    text(ax, 86, 785, "已验：Mac／VPS 链路、Skill 导入及召回样本；各端采用分别记录；Web 前台手动启停。", 20, AMBER)
    export(fig, ax, "hindsight-topology", "Hindsight拓扑：云端经VPS与EasyTier，Mac经LAN SSH，Win本机接入共享bank；Web管理端仅按需前台运行。")


def main():
    global FONT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", type=Path, default=Path("/System/Library/Fonts/STHeiti Medium.ttc"))
    args = parser.parse_args()
    if not args.font.is_file():
        parser.error("Supply --font with an installed CJK font file.")
    FONT = FontProperties(fname=args.font)
    matplotlib.rcParams["svg.fonttype"] = "none"
    matplotlib.rcParams["svg.hashsalt"] = "cf-fe-herdr-share-20261105"
    architecture()
    lifecycle()
    workflow()
    memory()


if __name__ == "__main__":
    main()
