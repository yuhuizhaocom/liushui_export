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
