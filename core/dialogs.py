"""查看类对话框: 历史日志、稳定性看板。

从 LiushuiApp 里原样搬出来(仅把 self.root 换成参数), 因为它们只读根窗口和状态栏,
与导出流程无关; 搬出后 main_gui 少了约 185 行, 这两个窗口也第一次能被测试构造。
配色统一从 core/theme.py 取, 避免 import 成环。
"""
import tkinter as tk
from tkinter import ttk, scrolledtext

from core.logger import list_history_logs, load_stats, summarize_stats
from core.theme import (BG_MAIN, BG_PANEL, BG_BUTTON, BG_SUCCESS, BG_LOG,
                        FG_LOG, FG_MAIN, FG_MUTED)


def show_log_history(root):
    """打开历史日志窗口: 左侧列表选择某次运行,右侧显示该次完整日志。"""
    win = tk.Toplevel(root)
    win.title("历史日志")
    win.geometry("900x550")
    win.configure(bg=BG_MAIN)
    win.transient(root)
    win.grab_set()

    top = tk.Frame(win, bg=BG_PANEL)
    top.pack(fill=tk.X, padx=8, pady=8)
    tk.Label(top, text="双击左侧某次运行查看完整日志", bg=BG_PANEL, fg=FG_MUTED,
            font=("Microsoft YaHei", 9)).pack(side=tk.LEFT)

    body = tk.Frame(win, bg=BG_PANEL)
    body.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
    # 左侧列表
    left_pane = tk.Frame(body, bg=BG_PANEL, width=240)
    left_pane.pack(side=tk.LEFT, fill=tk.Y)
    left_pane.pack_propagate(False)
    tk.Label(left_pane, text="运行记录", bg=BG_PANEL, fg=FG_MAIN,
             font=("Microsoft YaHei", 10, "bold")).pack(anchor="w", pady=(0, 4))
    listbox = tk.Listbox(left_pane, font=("Microsoft YaHei", 9),
                        activestyle="dotbox", selectbackground="#d6e4ff")
    listbox.pack(fill=tk.BOTH, expand=True)
    vsb = tk.Scrollbar(left_pane, orient=tk.VERTICAL, command=listbox.yview)
    vsb.pack(side=tk.RIGHT, fill=tk.Y)
    listbox.config(yscrollcommand=vsb.set)

    # 右侧日志显示
    right_pane = tk.Frame(body, bg=BG_PANEL)
    right_pane.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
    btn_row = tk.Frame(right_pane, bg=BG_PANEL)
    btn_row.pack(fill=tk.X, pady=(0, 4))
    copy_btn = tk.Button(btn_row, text="复制此日志", relief=tk.FLAT,
                         fg="#ffffff", bg=BG_BUTTON, cursor="hand2",
                         font=("Microsoft YaHei", 9))
    copy_btn.pack(side=tk.LEFT)
    viewer = scrolledtext.ScrolledText(
        right_pane, bg=BG_LOG, fg=FG_LOG, font=("Consolas", 9),
        wrap=tk.WORD, state=tk.DISABLED, relief=tk.FLAT)
    viewer.pack(fill=tk.BOTH, expand=True)

    # 加载历史日志列表(按修改时间倒序)
    logs = list_history_logs()
    if not logs:
        listbox.insert(tk.END, "(暂无历史日志)")
        listbox.config(state=tk.DISABLED)
        return

    def _show(idx):
        if idx < 0 or idx >= len(logs):
            return
        _, path, _ = logs[idx]
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            content = f"读取日志失败: {e}"
        viewer.config(state=tk.NORMAL)
        viewer.delete("1.0", tk.END)
        viewer.insert(tk.END, content)
        viewer.config(state=tk.DISABLED)
        viewer.see("1.0")
        def _do_copy(c=content):
            root.clipboard_clear()
            root.clipboard_append(c)
            root.update()
        copy_btn.config(command=_do_copy)

    for i, (name, _, mtime) in enumerate(logs):
        # 文件名形如 run_20260913_203000.log → 显示为 09-13 20:30:00
        label = f"{mtime}"
        listbox.insert(tk.END, label)
    listbox.selection_set(0)
    _show(0)

    def _on_select(_evt=None):
        sel = listbox.curselection()
        if sel:
            _show(sel[0])
    listbox.bind("<<ListboxSelect>>", _on_select)
    listbox.bind("<Double-Button-1>", _on_select)


def show_stats_dashboard(app):
    """稳定性看板弹窗: 上方按平台汇总成功率,下方最近 N 次明细记录。"""
    win = tk.Toplevel(app.root)
    win.title("稳定性看板")
    win.geometry("900x600")
    win.configure(bg=BG_MAIN)
    win.transient(app.root)
    win.grab_set()

    # 上方: 汇总表
    top = tk.Frame(win, bg=BG_PANEL)
    top.pack(fill=tk.X, padx=8, pady=8)
    tk.Label(top, text="按平台汇总(成功率 = success / total)",
            bg=BG_PANEL, fg=FG_MAIN, font=("Microsoft YaHei", 10, "bold")
            ).pack(anchor="w", pady=(0, 4))

    cols = ("平台", "总次数", "成功", "手动", "失败", "成功率%")
    tree = ttk.Treeview(top, columns=cols, show="headings", height=8)
    for c in cols:
        tree.column(c, anchor="center", width=120)
        tree.heading(c, text=c)
    tree.pack(fill=tk.X, padx=4)
    # 给成功率列加颜色: <60 红,<85 橙,其他绿
    tree.tag_configure("bad", background="#fadbd8")
    tree.tag_configure("warn", background="#fdf2cf")
    tree.tag_configure("good", background="#d5f5e3")

    summary = summarize_stats()
    for plat, total, succ, man, fail, rate in summary:
        tag = "bad" if rate < 60 else ("warn" if rate < 85 else "good")
        tree.insert("", tk.END,
                    values=(plat, total, succ, man, fail, rate), tags=(tag,))

    # 下方: 最近 200 条明细
    bottom = tk.Frame(win, bg=BG_PANEL)
    bottom.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 8))
    tk.Label(bottom, text="最近导出记录(最新在前)", bg=BG_PANEL, fg=FG_MAIN,
             font=("Microsoft YaHei", 10, "bold")).pack(anchor="w", pady=(0, 4))

    dcols = ("时间", "平台", "商户", "日期范围", "结果", "耗时(s)", "错误")
    dtree = ttk.Treeview(bottom, columns=dcols, show="headings", height=12)
    dcol_widths = [140, 100, 100, 160, 60, 70, 200]
    for i, c in enumerate(dcols):
        dtree.column(c, anchor="center", width=dcol_widths[i])
        dtree.heading(c, text=c)
    dtree.tag_configure("success", background="#d5f5e3")
    dtree.tag_configure("manual", background="#fdf2cf")
    dtree.tag_configure("failed", background="#fadbd8")
    vsb2 = ttk.Scrollbar(bottom, orient=tk.VERTICAL, command=dtree.yview)
    vsb2.pack(side=tk.RIGHT, fill=tk.Y)
    dtree.configure(yscrollcommand=vsb2.set)
    dtree.pack(fill=tk.BOTH, expand=True)

    records = load_stats(limit=200)
    for r in records:
        date_range = f"{r.get('start_date','')}~{r.get('end_date','')}"
        dtree.insert("", tk.END,
                     values=(r.get("ts", ""), r.get("platform", ""),
                             r.get("merchant", ""), date_range,
                             r.get("result", ""), r.get("duration_s", ""),
                             r.get("error", "")),
                     tags=(r.get("result", "failed"),))

    btn_row = tk.Frame(win, bg=BG_MAIN)
    btn_row.pack(fill=tk.X, padx=8, pady=(0, 8))
    def _refresh():
        # 刷新数据(用户跑完新任务后想看最新)
        for i in tree.get_children():
            tree.delete(i)
        for i in dtree.get_children():
            dtree.delete(i)
        for plat, total, succ, man, fail, rate in summarize_stats():
            tag = "bad" if rate < 60 else ("warn" if rate < 85 else "good")
            tree.insert("", tk.END,
                        values=(plat, total, succ, man, fail, rate), tags=(tag,))
        for r in load_stats(limit=200):
            date_range = f"{r.get('start_date','')}~{r.get('end_date','')}"
            dtree.insert("", tk.END,
                         values=(r.get("ts", ""), r.get("platform", ""),
                                 r.get("merchant", ""), date_range,
                                 r.get("result", ""), r.get("duration_s", ""),
                                 r.get("error", "")),
                         tags=(r.get("result", "failed"),))
    tk.Button(btn_row, text="刷新", command=_refresh, relief=tk.FLAT,
              fg="#ffffff", bg=BG_BUTTON, cursor="hand2",
              font=("Microsoft YaHei", 9)).pack(side=tk.LEFT)
    def _copy_stats():
        # 汇总表文本(便于复制沟通)
        lines = ["平台\t总次数\t成功\t手动\t失败\t成功率%"]
        for plat, total, succ, man, fail, rate in summarize_stats():
            lines.append(f"{plat}\t{total}\t{succ}\t{man}\t{fail}\t{rate}")
        text = "\n".join(lines)
        app.root.clipboard_clear()
        app.root.clipboard_append(text)
        app.root.update()
        app._set_status("稳定性汇总已复制")
    tk.Button(btn_row, text="复制汇总", command=_copy_stats, relief=tk.FLAT,
              fg="#ffffff", bg=BG_SUCCESS, cursor="hand2",
              font=("Microsoft YaHei", 9)).pack(side=tk.LEFT, padx=(4, 0))


# ===== 「使用说明」窗口 =====
#
# 文案以前是 main_gui._action_help 里 messagebox.showinfo 的一整串参数。messagebox 不能滚动,
# 内容一长用户只看得见开头几行, 而界面上十来个功能按钮(检查更新/子商户/定时任务/录制→脚本/
# 稳定性看板/工作空间/待确认 目录...)压根没提。所以: 文案抽成 build_help_text(纯函数, 不碰控件,
# 测试钉的就是它), 窗口只负责显示。
#
# 改这里之前先核对界面: 说没有的功能比功能少更伤人 —— 用户会照着找那个按钮。

HELP_SECTIONS = (
    ("怎么开始", (
        "1. 加商户: 点平台右侧的「+」(添加)起个名字; 左栏「全选 / 取消全选」整片勾, 商户行",
        "   前面的框勾单家。每家商户有自己的浏览器数据目录, 互不串登录态。",
        "2. 首次登录: 勾上商户 → 点「首次登录」→ 在弹出的浏览器里把后台登进去,",
        "   「登完直接把那个浏览器窗口关掉就行」, 不用在页面里找按钮, 也不用回来点确定。",
        "   等你的这段时间程序一边检测一边把登录态存下来; 窗口关掉后它还会在后台无头重开一次",
        "   核实是否真的登进去了, 没登进去会再问一轮(每家最多 3 轮: 回去继续登 / 这家先放弃)。",
        "   每家商户只要登成功一次, 之后反复导出都不用再登。",
        "3. 选日期(可点 昨天 / 近7天 / 近30天) → 勾商户 → 点「开始导出」。",
        "   日期没填全或区间不对会先问一句, 不会拿一个空区间去跑。",
        "4. 跑完自动打开本次的汇总文件夹, 里面是这一批成品文件的副本, 按 开始_结束_时刻 命名。",
    )),
    ("跑起来之后会发生什么", (
        "· 开跑前自动把勾上的商户逐个查一遍登录状态(设置里可关), 失效的一次列出来问你:",
        "  集中重登 / 本次跳过它们。默认不闪一堆窗口, 是无头连着探。",
        "· 「中止本次任务」: 把手上这一家做完再停, 重试与等待也会被它掐断; 不会掐断浏览器,",
        "  也不会在账单目录里留下半截文件。",
        "· 失败自动重试: 次数与首次间隔在设置区改, 之后每次间隔翻倍等。",
        "· 要人工的场合(例如微信资金流水要扫码确认)会弹一张「不挡主界面」的窗:",
        "  点「我已完成」它才继续往下点下载; 点「这一家先放弃」、把那张窗直接关掉、",
        "  或者你走开超过 10 分钟, 都按未完成收尾, 不会替你确认。",
        "· 左栏每个平台的颜色是状态灯, 同平台多家商户时显示「最差的那家」。",
    )),
    ("文件放在哪", (
        "downloads/平台/商户[/子商户]/开始_止/   最新一份在这个目录的顶层",
        "        同区间的旧版本 → 历史/      (带它自己的时间戳, 覆盖前一定先留一份)",
        "        每一步的取证图 → snapshots/ (某家突然导不出来时先看最后那张图)",
        "downloads/待确认/            对不上归哪个商户的文件, 原样放这里等你认领",
        "· 账单、登录态、日志、几个配置 json 都在「工作空间」里(第一次启动会让你选一个目录);",
        "  程序目录只读也能跑(U 盘、光碟都行)。点「工作空间」可以看路径或换地方, 换完重启一次。",
        "· 商户行右侧「删」只删这家的登录目录, 已导出的账单一个字都不动。",
        "· 「打开下载文件夹」随时跳到 downloads/, 不用自己去翻路径。",
        "· 「刷新商户」能认出你手工放进 browser_data 或从别的电脑拷过来的商户目录, 不用重启。",
        "· 文件名默认是 平台_商户_[子商户_]起_止_原名, 设置里可改成只加商户前缀、保留原始名;",
        "  扩展名一律照后台给的原样保留。",
    )),
    ("商户与登录", (
        "· 子商户(银联这类「一次登录、切着导多份」): 商户行点「子商户」填清单,",
        "  一家一家切着跑。归属没确认下来之前不会替你点导出 —— 拿错人的对账单比导不出来更糟。",
        "· 「检查登录状态」: 逐个进这家后台看会不会被跳回登录页, 不下载任何东西。",
        "· 登录保活: 后台按你设的间隔把各商户页面刷一遍续 cookie, 设置里可关或改间隔。",
        "· 首次登录时弹的「请登录」小窗: 自己倒计时关掉(默认 20 秒, 设置区「登录提示窗」",
        "  可改), 关它不代表登录完成; 不想再看到就在窗里勾「以后不再弹出」。",
        "· 一个 Chromium 登录目录同时只能被一个浏览器占用, 所以导出、保活、定时任务",
        "  会互相让路, 不会同时抢同一家。",
    )),
    ("自动与给维护的人", (
        "· 定时任务: 填 cron 表达式, 到点自动跑当时勾着的商户;",
        "  那一刻若正有手动任务在跑, 这次会往后推, 不会挤掉它。",
        "· 商户行「打开」: 单独进这家后台手工核对, 同时把你的点击与输入录下来,",
        "  录完用「录制→脚本」转成平台脚本骨架(生成后「平台 key 与类名必须自己改」)。",
        "· 平台管理: 新增/修改平台脚本, 保存后免重启生效; 脚本调试: 只跑一家, 看流程走到哪步。",
        "· 稳定性看板: 各平台成功/失败/需人工的统计与最近明细;「复制汇总」可整段拷走。",
        "· 历史日志: 每次运行一个日志文件, 窗口里能翻; 「复制日志」一次拷走当前屏,",
        "  「清空屏」只清显示, 日志文件一个字不动。",
        "· 程序崩溃会把堆栈写进 logs/crash_日期_时刻.txt, 并弹窗告诉你文件在哪。",
    )),
    ("版本与更新", (
        "· 窗口标题上的版本号就是当前版本, 报问题时请把它一起说。",
        "· 「检查更新」: 有新版本会先问一句, 你点头才开始下载并替换「程序文件」;",
        "  账单、登录态、日志以及自带的 Python 和浏览器内核都不碰; 换完要重启工具才生效。",
        "  被替换的旧文件先备份到工作空间的 updates/backup/, 任何一个文件写不进去就整批退回。",
        "· 全量版如果「新版本要求的依赖与包内自带的不是同一个」, 会拒绝就地更新并说明原因,",
        "  那种情况请整包换新的全量版; U 盘/光碟这类只读目录也只把包留下、不动现有文件。",
        "· 公司内网连不上发布服务器时只是查不到新版, 不影响任何导出功能。",
    )),
    ("几条要当心的", (
        "· 同一份工作空间不要同时开两份工具: 会先问一句, 选「仍要打开」就照开,",
        "  但两份会抢同一个商户的登录目录, 后启动的那份容易起不来浏览器。",
        "· 双击启动器没反应时, 改用同目录里的 .bat: 报错会留在黑色窗口里, 核心版还会打印",
        "  它到底用的哪个 Python。",
        "· 完整文档是随包的《使用说明.md》(下面那个按钮直接打开它); 平台后台改版是常态,",
        "  某一家突然导不出来时先看 downloads/.../snapshots/ 里最后那张图。",
    )),
)


def build_help_text(version="", data_root=""):
    """拼出「使用说明」窗口的正文。纯文本、不碰控件, 所以能被测试逐条钉住。"""
    head = ["流水自动导出工具 —— 使用说明"]
    if version:
        head.append("当前版本 v%s%s" % (version, ("  ·  工作空间: %s" % data_root) if data_root else ""))
    elif data_root:
        head.append("工作空间: %s" % data_root)
    parts = ["\n".join(head)]
    for title, lines in HELP_SECTIONS:
        parts.append("【%s】\n%s" % (title, "\n".join(lines)))
    return "\n\n".join(parts)


def show_help(root, text, on_open_manual=None):
    """「使用说明」窗口: 可以滚动、可以整段复制, 底部带一个打开完整文档的按钮。

    刻意不 grab_set(非模态) —— 用户常常要照着说明去点主界面, 把它压住就等于白写。
    """
    win = tk.Toplevel(root)
    win.title("使用说明")
    win.geometry("780x640")
    win.configure(bg=BG_MAIN)
    win.transient(root)
    win.lift()

    body = tk.Frame(win, bg=BG_PANEL)
    body.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 4))
    viewer = scrolledtext.ScrolledText(body, bg=BG_LOG, fg=FG_LOG, relief=tk.FLAT,
                                       font=("Microsoft YaHei", 10), wrap=tk.WORD)
    viewer.pack(fill=tk.BOTH, expand=True)
    viewer.insert("1.0", text)
    viewer.config(state=tk.DISABLED)

    bar = tk.Frame(win, bg=BG_MAIN)
    bar.pack(fill=tk.X, padx=8, pady=(0, 8))

    def _copy():
        try:
            win.clipboard_clear()
            win.clipboard_append(text)
            win.update()
        except Exception:
            pass                    # 剪贴板被别的程序占着不该影响读说明

    tk.Button(bar, text="复制全文", command=_copy, relief=tk.FLAT, fg="#ffffff",
              bg=BG_BUTTON, cursor="hand2", font=("Microsoft YaHei", 9)).pack(side=tk.LEFT)
    if on_open_manual:
        tk.Button(bar, text="打开完整使用说明", command=on_open_manual, relief=tk.FLAT,
                  fg="#ffffff", bg=BG_SUCCESS, cursor="hand2",
                  font=("Microsoft YaHei", 9)).pack(side=tk.LEFT, padx=(6, 0))
    tk.Button(bar, text="关闭", command=win.destroy, relief=tk.FLAT, fg="#ffffff",
              bg="#95a5a6", cursor="hand2", font=("Microsoft YaHei", 9)).pack(side=tk.RIGHT)
    return win
