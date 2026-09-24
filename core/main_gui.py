"""
流水自动导出工具 - GUI主程序
适用于非技术用户的可视化界面
平台通过 core.loader 动态发现加载,新增平台只需在 platforms/ 下建文件夹
"""

import json
import os
import sys
import threading
import time
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import ttk, messagebox, scrolledtext

# 项目根目录: 将根加入 sys.path,保证从任意位置启动都能定位 core/ platforms/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import (DOWNLOAD_DIR, BROWSER_DATA_DIR, DEFAULT_SETTINGS,
                         SELECTION_FILE, load_settings, save_settings)
from core.loader import discover_platforms, reload_platforms
from core.logger import log, list_history_logs, LOG_DIR, record_stat, load_stats, summarize_stats
from core.keepalive import KeepAliveService


def discover_merchants(platform_keys):
    """扫描 browser_data/平台key/ 下的子目录,发现已建档的商户
    返回: {platform_key: [商户名, ...]}
    """
    result = {}
    base = os.path.abspath(BROWSER_DATA_DIR)
    try:
        if os.path.isdir(base):
            for k in platform_keys:
                pk = os.path.join(base, k)
                if os.path.isdir(pk):
                    names = [d for d in os.listdir(pk)
                             if os.path.isdir(os.path.join(pk, d)) and not d.startswith(".")]
                    if names:
                        result[k] = sorted(names)
    except Exception:
        pass
    return result


def pair_job_targets(job_platforms, job_merchants, merchants_by_key):
    """把定时任务里的平台/商户配成 (平台key, 商户名)。

    任务编辑框中商户是一整条与平台无关的逗号文本, 若直接做平台×商户笛卡尔积,
    会给不属于该平台的商户拉起一个没有登录态的 profile。这里只保留
    browser_data/<平台key>/<商户> 下确实建档的配对。
    """
    pairs = []
    for key in job_platforms:
        known = set(merchants_by_key.get(key, []))
        for m in job_merchants:
            if m in known:
                pairs.append((key, m))
    return pairs


def run_with_retry(fn, retry_times=0, retry_interval_s=30, log=None):
    """通用重试: fn 返回非 "failed" 视为成功; 失败按 retry_times 重试, 间隔递增(×2)。
    retry_times=0 表示失败一次即返回, 不重试。"""
    interval = max(0, int(retry_interval_s))
    result = fn()
    attempt = 0
    while result == "failed" and attempt < retry_times:
        attempt += 1
        wait = interval * (2 ** (attempt - 1))
        if log:
            log(f"  [重试] 第 {attempt}/{retry_times} 次重试({wait}s 后)")
        time.sleep(wait)
        result = fn()
    return result


# 颜色定义
BG_MAIN = "#f5f6fa"
BG_PANEL = "#ffffff"
BG_BUTTON = "#4a90d9"
BG_BUTTON_HOVER = "#3a7bc8"
BG_SUCCESS = "#27ae60"
BG_WARN = "#e67e22"
BG_ERROR = "#e74c3c"
BG_LOG = "#1e1e2e"
FG_LOG = "#cdd6f4"
FG_MAIN = "#2d3436"
FG_MUTED = "#636e72"

class LiushuiApp:
    def __init__(self, root):
        self.root = root
        self.browser = None
        self.running = False
        self.platform_vars = {}
        self.log_lines = []
        self.platforms = discover_platforms()
        self.settings = load_settings()
        self.login_urls = {key: plat.login_url for key, plat in self.platforms.items()}
        self.merchant_vars = {}   # platform_key -> {商户名: BooleanVar}
        self.merchants = discover_merchants(self.platforms.keys())
        self.selection = self._load_selection()   # 上次勾选状态(重启后恢复)
        self._login_confirm = threading.Event()  # 登录弹窗确认事件(等待用户)

        self._setup_window()
        # 构建顺序: 右日志 → 左平台 → 中间(设置+历史+操作按钮) → 初始化概览
        self._build_right_panel()
        self._build_left_panel()
        self._build_middle_panel()
        self._refresh_summary()   # 初始化概览条

        # 登录保活服务: 后台线程周期刷新各商户登录态
        self.keepalive = KeepAliveService(
            self,
            interval_min=int(self.settings.get("keepalive_interval_min", 30)),
            enabled=bool(self.settings.get("enable_keepalive", True)),
        )
        self.keepalive.start()

        # 定时任务调度器: 后台线程到期触发 self.trigger_job
        from core.scheduler import CronScheduler
        self.scheduler = CronScheduler(self, poll_interval=30)
        self.scheduler.start()

    def _setup_window(self):
        self.root.title("流水自动导出工具 v1.0")
        self.root.geometry("1100x700")
        self.root.minsize(900, 550)
        self.root.configure(bg=BG_MAIN)
        # 默认最大化(全屏)运行,Windows下保留标题栏和任务栏
        try:
            self.root.state("zoomed")
        except Exception:
            pass

    def _build_left_panel(self):
        left = tk.Frame(self.root, bg=BG_PANEL, width=340, padx=15, pady=15)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(8, 4), pady=8)
        left.pack_propagate(False)

        title = tk.Label(left, text="平台选择", bg=BG_PANEL, fg=FG_MAIN,
                         font=("Microsoft YaHei", 13, "bold"))
        title.pack(anchor="w", pady=(0, 8))

        btn_frame = tk.Frame(left, bg=BG_PANEL)
        btn_frame.pack(fill=tk.X, pady=(0, 8))
        tk.Button(btn_frame, text="全选", command=self._select_all,
                  font=("Microsoft YaHei", 9), width=6).pack(side=tk.LEFT, padx=(0, 5))
        tk.Button(btn_frame, text="取消全选", command=self._deselect_all,
                  font=("Microsoft YaHei", 9), width=6).pack(side=tk.LEFT)

        list_canvas = tk.Canvas(left, bg=BG_PANEL, highlightthickness=0)
        list_scroll = tk.Scrollbar(left, orient=tk.VERTICAL, command=list_canvas.yview)
        list_canvas.configure(yscrollcommand=list_scroll.set)
        list_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        list_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        list_frame = tk.Frame(list_canvas, bg=BG_PANEL)
        list_win = list_canvas.create_window((0, 0), window=list_frame, anchor="nw")

        def _on_canvas_configure(_):
            list_canvas.configure(scrollregion=list_canvas.bbox("all"))

        list_frame.bind("<Configure>", _on_canvas_configure)

        self.list_frame = list_frame   # 供 _rebuild_platform_list 重建使用

        self.merchant_frames = {}   # platform_key -> Frame(商户 checkbox 容器)
        for key, plat in self.platforms.items():
            if not getattr(plat, "enabled", True):
                continue
            self._build_platform_row(key, plat)

    def _build_platform_row(self, key, plat):
        """构建单个平台的行(勾选+状态+添加商户)与其商户多选容器。"""
        default = self.selection.get("platform", {}).get(key, True)
        var = tk.BooleanVar(value=bool(default))
        self.platform_vars[key] = var
        var.trace_add("write", lambda *_: self._refresh_summary())
        var.trace_add("write", lambda *_: self._save_selection())

        # 第一行: 平台勾选(联动其商户) + 状态标识 + 添加商户
        row = tk.Frame(self.list_frame, bg=BG_PANEL)
        row.pack(fill=tk.X, pady=(4, 0))

        def _toggle_platform(k=key, v=var, p=plat):
            on = v.get()
            for mv in self.merchant_vars.get(k, {}).values():
                mv.set(on)

        cb = tk.Checkbutton(row, variable=var, text=plat.name,
                            bg=BG_PANEL, fg=FG_MAIN,
                            font=("Microsoft YaHei", 10),
                            activebackground=BG_PANEL,
                            command=_toggle_platform)
        cb.pack(side=tk.LEFT)
        # 商户数徽标(灰字小计数,便于一眼看清)
        merch_cnt = len(self.merchants.get(key, []))
        tk.Label(row, text=str(merch_cnt),
                 bg="#eeeeee", fg="#7f8c8d",
                 font=("Microsoft YaHei", 8)).pack(side=tk.LEFT, padx=(3, 0))
        status_label = tk.Label(row, text="●", fg="#bdc3c7",
                                font=("Microsoft YaHei", 10),
                                bg=BG_PANEL)
        status_label.pack(side=tk.RIGHT)
        plat.status_label = status_label
        tk.Button(row, text="+", width=2, relief=tk.FLAT, fg="#ffffff",
                  bg="#8e44ad", cursor="hand2",
                  font=("Microsoft YaHei", 8, "bold"),
                  command=lambda k=key, n=plat.name: self._prompt_add_merchant(k, n)
                  ).pack(side=tk.RIGHT, padx=(4, 2))

        # 商户多选容器
        mer_frame = tk.Frame(self.list_frame, bg=BG_PANEL)
        mer_frame.pack(fill=tk.X, pady=(0, 2))
        self.merchant_frames[key] = mer_frame
        self.merchant_vars[key] = {}
        merch = self.merchants.get(key, [])
        if merch:
            for m in merch:
                self._add_merchant_checkbox(key, m, True)
        else:
            self._append_log(f"平台[{plat.name}]暂无商户,请先\"+\"添加并登录")
        # 若存在已勾选的商户,平台保持勾选(保证 UI 与状态一致)
        if any(mv.get() for mv in self.merchant_vars.get(key, {}).values()):
            if not var.get():
                var.set(True)

    def _rebuild_platform_list(self):
        """新增/编辑平台后重建左侧勾选列表(重新发现商户,平台/商户可即时反映变更)。"""
        self._save_selection()   # 保存当前勾选,重建后从文件恢复
        self.selection = self._load_selection()
        self.merchants = discover_merchants(self.platforms.keys())
        for child in self.list_frame.winfo_children():
            child.destroy()
        self.platform_vars = {}
        self.merchant_vars = {}
        self.merchant_frames = {}
        for key, plat in self.platforms.items():
            if not getattr(plat, "enabled", True):
                continue
            self._build_platform_row(key, plat)
        self._refresh_summary()

    def _build_right_panel(self):
        right = tk.Frame(self.root, bg=BG_PANEL, padx=15, pady=15)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(4, 8), pady=8)

        header = tk.Frame(right, bg=BG_PANEL)
        header.pack(fill=tk.X, pady=(0, 8))
        tk.Label(header, text="操作日志", bg=BG_PANEL, fg=FG_MAIN,
                 font=("Microsoft YaHei", 13, "bold")).pack(side=tk.LEFT)
        # 右侧按钮组: 历史日志 / 清空屏 / 复制日志
        tk.Button(header, text="清空屏", width=6, relief=tk.FLAT,
                  fg="#ffffff", bg="#7f8c8d", cursor="hand2",
                  font=("Microsoft YaHei", 9),
                  command=self._clear_log_screen).pack(side=tk.RIGHT, padx=(6, 0))
        tk.Button(header, text="历史日志", width=8, relief=tk.FLAT,
                  fg="#ffffff", bg="#16a085", cursor="hand2",
                  font=("Microsoft YaHei", 9),
                  command=self._view_log_history).pack(side=tk.RIGHT, padx=(6, 0))
        tk.Button(header, text="复制日志", width=8, relief=tk.FLAT,
                  fg="#ffffff", bg=BG_BUTTON, cursor="hand2",
                  font=("Microsoft YaHei", 9),
                  command=self._copy_log).pack(side=tk.RIGHT, padx=(6, 0))
        self.status_var = tk.StringVar(value="状态: 就绪")
        tk.Label(header, textvariable=self.status_var, bg=BG_PANEL,
                 fg=FG_MUTED, font=("Microsoft YaHei", 10)).pack(side=tk.RIGHT)

        self.log_text = scrolledtext.ScrolledText(
            right, bg=BG_LOG, fg=FG_LOG, font=("Consolas", 9),
            wrap=tk.WORD, state=tk.DISABLED, relief=tk.FLAT
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

        self.progress = ttk.Progressbar(right, mode="determinate")
        self.progress.pack(fill=tk.X, pady=(8, 0))

    def _build_middle_panel(self):
        """中间栏: 设置区(概览/日期/浏览器/文件名) + 操作按钮"""
        middle = tk.Frame(self.root, bg=BG_PANEL, width=400, padx=15, pady=15)
        middle.pack(side=tk.LEFT, fill=tk.Y, padx=(4, 4))
        middle.pack_propagate(False)

        # 导出日期
        tk.Label(middle, text="导出日期", bg=BG_PANEL, fg=FG_MAIN,
                 font=("Microsoft YaHei", 10, "bold")).pack(anchor="w", pady=(6, 2))
        date_frame = tk.Frame(middle, bg=BG_PANEL)
        date_frame.pack(fill=tk.X, pady=2)
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        tk.Label(date_frame, text="从", bg=BG_PANEL, fg=FG_MUTED,
                 font=("Microsoft YaHei", 9)).pack(side=tk.LEFT)
        self.date_start = tk.StringVar(value=yesterday)
        self.date_start.trace_add("write", lambda *_: self._refresh_summary())
        tk.Entry(date_frame, textvariable=self.date_start, width=12,
                 font=("Microsoft YaHei", 9)).pack(side=tk.LEFT, padx=5)
        tk.Label(date_frame, text="至", bg=BG_PANEL, fg=FG_MUTED,
                 font=("Microsoft YaHei", 9)).pack(side=tk.LEFT)
        self.date_end = tk.StringVar(value=yesterday)
        self.date_end.trace_add("write", lambda *_: self._refresh_summary())
        tk.Entry(date_frame, textvariable=self.date_end, width=12,
                 font=("Microsoft YaHei", 9)).pack(side=tk.LEFT, padx=5)
        quick_frame = tk.Frame(middle, bg=BG_PANEL)
        quick_frame.pack(fill=tk.X, pady=2)
        tk.Button(quick_frame, text="昨天", command=lambda: self._set_date(-1),
                  font=("Microsoft YaHei", 8), width=5).pack(side=tk.LEFT, padx=2)
        tk.Button(quick_frame, text="近7天", command=lambda: self._set_date(-7, -1),
                  font=("Microsoft YaHei", 8), width=5).pack(side=tk.LEFT, padx=2)
        tk.Button(quick_frame, text="近30天", command=lambda: self._set_date(-30, -1),
                  font=("Microsoft YaHei", 8), width=5).pack(side=tk.LEFT, padx=2)

        # 浏览器显示与文件名模式(横向排列,紧凑,在日期下方)
        opt_row = tk.Frame(middle, bg=BG_PANEL)
        opt_row.pack(fill=tk.X, pady=(4, 0))
        self.show_browser_var = tk.BooleanVar(value=self.settings.get("show_browser", True))
        self.show_browser_var.trace_add(
            "write",
            lambda *_: save_settings(
                {"show_browser": bool(self.show_browser_var.get())}))
        tk.Checkbutton(opt_row, variable=self.show_browser_var, text="显示浏览器",
                       bg=BG_PANEL, fg=FG_MUTED, font=("Microsoft YaHei", 9),
                       activebackground=BG_PANEL).pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(opt_row, text="文件名:", bg=BG_PANEL, fg=FG_MUTED,
                 font=("Microsoft YaHei", 9)).pack(side=tk.LEFT)
        self.name_mode_var = tk.StringVar(
            value=self.settings.get("download_name_mode", "unified"))
        self.name_mode_var.trace_add(
            "write",
            lambda *_: save_settings(
                {"download_name_mode": self.name_mode_var.get()}))
        tk.Radiobutton(opt_row, variable=self.name_mode_var, value="unified",
                       text="统一命名", bg=BG_PANEL, fg=FG_MUTED,
                       font=("Microsoft YaHei", 9), activebackground=BG_PANEL
                       ).pack(side=tk.LEFT)
        tk.Radiobutton(opt_row, variable=self.name_mode_var, value="original",
                       text="保留原文件名", bg=BG_PANEL, fg=FG_MUTED,
                       font=("Microsoft YaHei", 9), activebackground=BG_PANEL
                       ).pack(side=tk.LEFT, padx=(6, 0))

        # 登录保活设置行
        ka_row = tk.Frame(middle, bg=BG_PANEL)
        ka_row.pack(fill=tk.X, pady=(2, 0))
        self.enable_ka_var = tk.BooleanVar(value=bool(self.settings.get("enable_keepalive", True)))
        self.enable_ka_var.trace_add("write", self._on_ka_setting)
        tk.Checkbutton(ka_row, variable=self.enable_ka_var, text="登录保活",
                       bg=BG_PANEL, fg=FG_MUTED, font=("Microsoft YaHei", 9),
                       activebackground=BG_PANEL).pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(ka_row, text="间隔(分钟):", bg=BG_PANEL, fg=FG_MUTED,
                 font=("Microsoft YaHei", 9)).pack(side=tk.LEFT)
        self.ka_interval = tk.Spinbox(ka_row, from_=5, to=600, increment=5, width=5,
                                      font=("Microsoft YaHei", 9))
        self.ka_interval.delete(0, tk.END)
        self.ka_interval.insert(0, str(int(self.settings.get("keepalive_interval_min", 30))))
        self.ka_interval.bind("<FocusOut>", lambda *_: self._on_ka_setting())
        self.ka_interval.pack(side=tk.LEFT)

        # 失败重试设置行
        retry_row = tk.Frame(middle, bg=BG_PANEL)
        retry_row.pack(fill=tk.X, pady=(2, 0))
        self.enable_retry_var = tk.BooleanVar(value=int(self.settings.get("retry_times", 2)) > 0)
        self.enable_retry_var.trace_add("write", self._on_retry_setting)
        tk.Checkbutton(retry_row, variable=self.enable_retry_var, text="失败重试",
                       bg=BG_PANEL, fg=FG_MUTED, font=("Microsoft YaHei", 9),
                       activebackground=BG_PANEL).pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(retry_row, text="次数(0-5):", bg=BG_PANEL, fg=FG_MUTED,
                 font=("Microsoft YaHei", 9)).pack(side=tk.LEFT)
        self.retry_times = tk.Spinbox(retry_row, from_=0, to=5, width=5,
                                      font=("Microsoft YaHei", 9))
        self.retry_times.delete(0, tk.END)
        self.retry_times.insert(0, str(int(self.settings.get("retry_times", 2))))
        self.retry_times.bind("<FocusOut>", lambda *_: self._on_retry_setting())
        self.retry_times.pack(side=tk.LEFT)

        # 单步调试开关(脚本调试时开启,导出流程每步暂停弹"继续/中止")
        debug_row = tk.Frame(middle, bg=BG_PANEL)
        debug_row.pack(fill=tk.X, pady=(2, 0))
        self.step_debug_var = tk.BooleanVar(value=False)
        tk.Checkbutton(debug_row, variable=self.step_debug_var, text="单步调试",
                       bg=BG_PANEL, fg=FG_MUTED, font=("Microsoft YaHei", 9),
                       activebackground=BG_PANEL).pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(debug_row, text="(开启后每个关键步骤弹确认,排查用)", bg=BG_PANEL, fg="#95a5a6",
                 font=("Microsoft YaHei", 8)).pack(side=tk.LEFT)

        # 概览条(已选商户/平台/日期,位于设置区与按钮区之间)
        self.summary_var = tk.StringVar(value="")
        tk.Label(middle, textvariable=self.summary_var, bg=BG_PANEL, fg="#2d6cdf",
                 font=("Microsoft YaHei", 9, "bold")).pack(anchor="w", pady=(6, 0))

        # ===== 中间栏底部: 操作按钮("开始导出"大按钮占两行) =====
        bar = tk.Frame(middle, bg=BG_PANEL)
        bar.pack(fill=tk.X, pady=(8, 0))
        for c in range(3):
            bar.grid_columnconfigure(c, weight=1)
        bar.grid_rowconfigure(0, weight=1)
        bar.grid_rowconfigure(1, weight=1)
        bar.grid_rowconfigure(2, weight=1)
        bar.grid_rowconfigure(3, weight=1)
        bar.grid_rowconfigure(4, weight=1)

        others = [
            ("首次登录", self._action_login_all, BG_BUTTON, 0, 0),
            ("检查登录状态", self._action_check_status, BG_WARN, 0, 1),
            ("打开下载文件夹", self._action_open_folder, "#7f8c8d", 1, 0),
            ("使用说明", self._action_help, "#16a085", 1, 1),
            ("平台管理", self._open_platform_manager, "#8e44ad", 2, 0),
            ("脚本调试", self._open_debug_dialog, "#16a085", 2, 1),
            ("定时任务", self._open_scheduler_dialog, "#2c3e50", 3, 0),
            ("录制→脚本", self._open_recording_dialog, "#8e44ad", 3, 1),
            ("稳定性看板", self._open_stats_dialog, "#16a085", 4, 0),
        ]
        for t, cmd, col, r, c in others:
            tk.Button(bar, text=t, command=cmd, bg=col, fg="white",
                      font=("Microsoft YaHei", 9, "bold"), relief=tk.FLAT,
                      padx=10, pady=5, cursor="hand2"
                      ).grid(row=r, column=c, padx=3, pady=3, sticky="ew")
        # 开始导出: 占满右侧两行(大按钮)
        tk.Button(bar, text="开始导出", command=self._action_export_all,
                  bg=BG_SUCCESS, fg="white",
                  font=("Microsoft YaHei", 13, "bold"), relief=tk.FLAT,
                  cursor="hand2").grid(row=0, column=2, rowspan=2,
                                       padx=3, pady=3, sticky="nsew")

    def _set_date(self, start_offset, end_offset=-1):
        self.date_start.set((datetime.now() + timedelta(days=start_offset)).strftime("%Y-%m-%d"))
        self.date_end.set((datetime.now() + timedelta(days=end_offset)).strftime("%Y-%m-%d"))

    def _select_all(self):
        for v in self.platform_vars.values():
            v.set(True)
        for mvs in self.merchant_vars.values():
            for mv in mvs.values():
                mv.set(True)

    def _deselect_all(self):
        for v in self.platform_vars.values():
            v.set(False)
        for mvs in self.merchant_vars.values():
            for mv in mvs.values():
                mv.set(False)

    def _add_merchant_checkbox(self, key, name, checked=True):
        """在平台商户容器中新增一个商户勾选框"""
        frame = self.merchant_frames.get(key)
        if frame is None:
            return
        default = self.selection.get("merchant", {}).get(key, {}).get(name, checked)
        mv = tk.BooleanVar(value=bool(default))
        mv.trace_add("write", lambda *_: self._refresh_summary())
        mv.trace_add("write", lambda *_: self._save_selection())
        # 勾选任一商户时,自动勾选其所属平台(反向联动)
        mv.trace_add("write", lambda *_, k=key: self._sync_platform_on_merchant(k))
        self.merchant_vars.setdefault(key, {})[name] = mv
        # 每商户一行: 勾选 + 右侧"打开"按钮(打开该商户浏览器,供手工测试页面元素)
        mrow = tk.Frame(frame, bg=BG_PANEL)
        mrow.pack(anchor="w", fill=tk.X, padx=(26, 0))
        tk.Checkbutton(
            mrow, variable=mv, text=name,
            bg=BG_PANEL, fg=FG_MUTED, font=("Microsoft YaHei", 9),
            activebackground=BG_PANEL).pack(side=tk.LEFT)
        tk.Button(mrow, text="打开", width=4, relief=tk.FLAT,
                  fg="#ffffff", bg="#e67e22", cursor="hand2",
                  font=("Microsoft YaHei", 8),
                  command=lambda k=key, m=name: self._action_open_merchant(k, m)
                  ).pack(side=tk.RIGHT, padx=(4, 2))

    def _sync_platform_on_merchant(self, key, *_):
        """商户被勾选时自动勾选平台(平台未勾选且存在已勾选商户时补勾)。"""
        pv = self.platform_vars.get(key)
        if pv is None or pv.get():
            return
        for mv in self.merchant_vars.get(key, {}).values():
            if mv.get():
                pv.set(True)
                break

    def _load_selection(self):
        """读取上次保存的平台/商户勾选状态,返回 {"platform": {}, "merchant": {}}"""
        try:
            with open(SELECTION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {"platform": {}, "merchant": {}}

    def _save_selection(self, *_):
        """持久化当前平台/商户勾选状态到文件(重启后自动恢复)。"""
        try:
            data = {
                "platform": {k: bool(v.get()) for k, v in self.platform_vars.items()},
                "merchant": {k: {n: bool(mv.get()) for n, mv in mvs.items()}
                             for k, mvs in self.merchant_vars.items()},
            }
            with open(SELECTION_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _refresh_summary(self, *_):
        """刷新左侧概览条: 已选商户/平台数与日期"""
        try:
            cnt = 0
            plat_cnt = 0
            for mv in self.merchant_vars.values():
                sels = [n for n, v in mv.items() if v.get()]
                if sels:
                    plat_cnt += 1
                    cnt += len(sels)
            date = f"{self.date_start.get().strip()} ~ {self.date_end.get().strip()}"
            self.summary_var.set(f"已选 {cnt} 个商户 · {plat_cnt} 个平台   [{date}]")
        except Exception:
            pass

    def _append_log(self, line):
        self.log_lines.append(line)
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, line + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
        if len(self.log_lines) > 500:
            self.log_lines = self.log_lines[-300:]

    def _copy_log(self):
        """复制当前全部日志文本到系统剪贴板,便于复制给我排查问题。"""
        try:
            text = "\n".join(self.log_lines)
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()   # 确保剪贴板写入生效
            self._set_status(f"已复制 {len(self.log_lines)} 行日志到剪贴板")
        except Exception as e:
            self._set_status(f"复制日志失败: {e}")

    def _clear_log_screen(self):
        """清空当前日志输出屏(仅清显示,历史日志文件不受影响,新日志继续追加显示)"""
        self.log_lines = []
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state=tk.DISABLED)
        self._set_status("日志屏已清空(历史日志文件保留)")

    def _view_log_history(self):
        """打开历史日志窗口: 左侧列表选择某次运行,右侧显示该次完整日志。"""
        win = tk.Toplevel(self.root)
        win.title("历史日志")
        win.geometry("900x550")
        win.configure(bg=BG_MAIN)
        win.transient(self.root)
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
                self.root.clipboard_clear()
                self.root.clipboard_append(c)
                self.root.update()
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

    def _set_status(self, text):
        self.status_var.set(f"状态: {text}")

    def _set_platform_status(self, key, status):
        plat = self.platforms.get(key)
        if not plat:
            return
        label = getattr(plat, "status_label", None)
        if not label:
            return
        colors = {"ok": BG_SUCCESS, "warn": BG_WARN, "error": BG_ERROR, "idle": "#bdc3c7"}
        label.config(fg=colors.get(status, "#bdc3c7"))

    def set_platform_status(self, key, status):
        """保活线程用的公共入口(KeepAliveService._mark 按此名字查找)。
        状态灯控件只能在 UI 线程改, 这里统一切回主线程;
        窗口已销毁时 after 会抛 TclError, 属后台服务噪声, 吞掉不影响导出主流程。"""
        try:
            self.root.after(0, lambda: self._set_platform_status(key, status))
        except Exception:
            pass

    def _get_selected(self):
        return [k for k, v in self.platform_vars.items() if v.get()]

    def _on_ka_setting(self, *_):
        """保活设置变化即时保存并应用到运行中的服务。"""
        enabled = bool(self.enable_ka_var.get())
        try:
            interval = max(1, int(self.ka_interval.get()))
        except Exception:
            interval = 30
        save_settings({"enable_keepalive": enabled, "keepalive_interval_min": interval})
        self.keepalive.enabled = enabled
        self.keepalive.interval_min = interval

    def _on_retry_setting(self, *_):
        """失败重试设置变化即时保存(运行时从 settings 读取,无需运行时状态)。"""
        enabled = bool(self.enable_retry_var.get())
        try:
            times = int(self.retry_times.get())
        except Exception:
            times = 2
        times = max(0, min(5, times))
        retry_times = times if enabled else 0
        save_settings({"retry_times": retry_times,
                       "retry_interval_s": int(DEFAULT_SETTINGS.get("retry_interval_s", 30))})

    def __iter_merchants(self):
        """供保活复用的迭代: (platform_key, merchant)。"""
        for key in self._get_selected():
            for m in self._get_selected_merchants(key):
                yield (key, m)

    iter_selected_merchants = __iter_merchants

    def _run_async(self, target):
        if self.running:
            messagebox.showwarning("提示", "已有任务正在执行,请等待完成。")
            return
        self.running = True
        for key in self.platform_vars:
            self._set_platform_status(key, "idle")
        t = threading.Thread(target=self._thread_wrapper, args=(target,), daemon=True)
        t.start()

    def _thread_wrapper(self, target):
        try:
            target()
        except Exception as e:
            self._append_log(f"[错误] {e}")
            self._set_status(f"出错: {e}")
        finally:
            self.running = False
            self._set_status("就绪")
            self.progress.config(value=0)
            # 任务结束自动关闭浏览器(避免窗口残留/占用,下次任务重新启动)
            if self.browser:
                try:
                    self.browser.close()
                except Exception:
                    pass
                self.browser = None

    def _ensure_browser(self, plat=None, merchant="", force_visible=False):
        """启动浏览器,并按 平台/商户 设置独立数据目录(登录态隔离)。
        force_visible=True(登录流程)时始终显示窗口;
        否则按"显示浏览器窗口"配置决定 headless(显示/后台运行)。
        """
        if self.browser:
            try:
                self.browser.close()
            except Exception:
                pass
        from core.browser import BrowserManager
        show = force_visible or bool(self.show_browser_var.get())
        self.browser = BrowserManager(headless=not show, log_callback=self._append_log)
        if plat is not None:
            self.browser.set_browser_profile(plat.key, merchant)
        self.browser.start()

    def _get_selected_merchants(self, key):
        """返回某平台下已勾选的商户名列表"""
        result = []
        for name, mv in self.merchant_vars.get(key, {}).items():
            if mv.get():
                result.append(name)
        return result

    def _validate_dates(self, show_warning=True):
        """校验日期输入(格式 YYYY-MM-DD、结束>=开始),供导出/登录前调用"""
        s = self.date_start.get().strip()
        e = self.date_end.get().strip()
        try:
            ds = datetime.strptime(s, "%Y-%m-%d")
            de = datetime.strptime(e, "%Y-%m-%d")
        except ValueError:
            if show_warning:
                messagebox.showwarning("日期格式", "日期格式应为 YYYY-MM-DD(如 2026-09-01),请检查后重试。")
            return False
        if de < ds:
            if show_warning:
                messagebox.showwarning("日期范围", "结束日期不能早于开始日期,请检查后重试。")
            return False
        return True

    def _action_login_all(self):
        if not self._validate_dates():
            return
        selected = self._get_selected()
        if not selected:
            messagebox.showwarning("提示", "请至少选择一个平台。")
            return
        cnt = sum(len(self._get_selected_merchants(k)) for k in selected)
        if cnt > 0:
            log(f"首次登录: {cnt} 个商户", callback=self._append_log)
        self._run_async(lambda: self._do_login(selected))

    def _action_export_all(self):
        if not self._validate_dates():
            return
        selected = self._get_selected()
        if not selected:
            messagebox.showwarning("提示", "请至少选择一个平台。")
            return
        # 汇总本次将导出的任务并让用户确认(防误操作)
        tasks = []
        for key in selected:
            plat = self.platforms[key]
            for m in self._get_selected_merchants(key):
                tasks.append(f"{plat.name} · {m}")
        if not tasks:
            messagebox.showwarning("提示", "请至少勾选一个商户后再导出。")
            return
        date_str = f"{self.date_start.get()} ~ {self.date_end.get()}"
        lines = "\n".join(tasks[:12])
        if len(tasks) > 12:
            lines += f"\n…… 等共 {len(tasks)} 项"
        ok = messagebox.askyesno(
            "确认导出",
            f"确认导出以下 {len(tasks)} 个商家流水?\n\n"
            f"日期: {date_str}\n{lines}")
        if not ok:
            return
        log(f"开始导出: {len(tasks)} 个商户 [{date_str}]", callback=self._append_log)
        self._run_async(lambda: self._do_export(selected))

    def _action_check_status(self):
        if not self._validate_dates(show_warning=False):
            return
        selected = self._get_selected()
        if not selected:
            messagebox.showwarning("提示", "请至少选择一个平台。")
            return
        log("检查登录状态", callback=self._append_log)
        self._run_async(lambda: self._do_check(selected))

    def _action_open_folder(self):
        path = os.path.abspath(DOWNLOAD_DIR)
        os.makedirs(path, exist_ok=True)
        os.startfile(path)

    def _action_open_merchant(self, key, merchant):
        """打开指定商户的浏览器窗口(已恢复登录态),供手工测试页面元素,操作完手工关闭即可。"""
        plat = self.platforms.get(key)
        if plat is None:
            return
        self._append_log(f">>> 正在打开 {plat.name}({merchant}) 浏览器窗口(手工测试用)...")
        self._run_async(lambda: self._open_merchant_browser(plat, merchant))

    def _open_merchant_browser(self, plat, merchant):
        """后台: 用该商户独立 profile 启动浏览器并打开导出页,不执行自动化,等待用户手工操作。
        线程保持存活直到用户手工关闭浏览器窗口,防止任务结束逻辑自动关闭浏览器。"""
        try:
            self._ensure_browser(plat, merchant, force_visible=True)
            # 开启操作追踪: 记录页面上的点击/输入元素信息到日志,辅助编写脚本
            self.browser.enable_action_trace()
            self.browser.navigate(plat.export_url)
            self.browser.sleep(3)
            self._append_log(f"已打开 {plat.name}({merchant}) 页面(已恢复登录态)")
            self._append_log("请手工操作测试,日志会记录点击/输入的元素信息;完成后直接关闭浏览器窗口即可")
            # 轮询等待用户手工关闭浏览器窗口(page 关闭后访问会抛异常)
            while True:
                self.browser.sleep(2)
                try:
                    self.browser.page.title()
                except Exception:
                    break
            self._append_log(f"{plat.name}({merchant}) 浏览器窗口已关闭")
        except Exception as e:
            self._append_log(f"打开商户浏览器失败: {e}")
            self._set_platform_status(plat.key, "error")

    def _action_help(self):
        """弹出简要使用说明"""
        messagebox.showinfo(
            "使用说明",
            "【使用步骤】\n\n"
            "1. 添加商户: 点平台右侧的 [ + ] 输入商户名称\n"
            "2. 首次登录: 勾选商户后点\"首次登录\",在浏览器完成登录并点\"确定\"\n"
            "   每个商户只需登录一次,登录状态会自动保存\n"
            "3. 导出流水: 选好日期,点\"开始导出\"并确认,等待完成\n"
            "4. 查看结果: 导出完成后自动打开汇总文件夹\n\n"
            "【提示】\n"
            "- 勾选框控制哪些平台/商户参与本次操作\n"
            "- 可随时点\"检查登录状态\"确认是否已登录\n"
            "- 导出文件在 downloads 目录,按 平台/商户/日期 存放")

    def _open_platform_manager(self):
        """打开平台管理弹窗(新增/编辑脚本)。"""
        from core.platform_admin import PlatformManagerDialog

        def _refresh():
            # 清空平台模块缓存重新发现, 使脚本修改/新增平台立即(无需重启)生效
            self.platforms = reload_platforms()
            try:
                self._rebuild_platform_list()
            except Exception:
                pass
            return self.platforms

        PlatformManagerDialog(self.root, self.platforms, on_refresh=_refresh)

    def _open_debug_dialog(self):
        """脚本调试弹窗。"""
        from core.platform_admin import DebugDialog, DebugProbe

        def _run(key, start_date, end_date):
            plat = self.platforms.get(key)
            if not plat:
                return
            if not start_date:
                start_date = self.date_start.get().strip()
            if not end_date:
                end_date = self.date_end.get().strip()

            def _task():
                self.progress.config(maximum=100, value=0)
                self._set_status(f"调试 {plat.name}…")
                steps = []
                try:
                    self._ensure_browser(plat)
                    probe = DebugProbe(self.browser, steps=steps)
                    result = plat.export(probe, start_date, end_date)
                    for s in steps:
                        line = f"  #{s['i']} {s['action']}{s['args']}"
                        if not s["ok"]:
                            line += f"  [出错] {s['error']}"
                            self._append_log(line)
                            continue
                        self._append_log(line)
                    self._append_log(f"[调试完成] {plat.name}: {result}")
                    self._set_status(f"调试完成: {result}")
                except Exception as e:
                    self._append_log(f"[调试异常] {e}")
                    self._set_status(f"调试异常: {e}")

            self._run_async(_task)

        DebugDialog(self.root, self.platforms, on_run=_run)

    def _open_recording_dialog(self):
        """录制 → 脚本骨架弹窗: 列出 recordings/*.jsonl,选中后生成骨架预览/保存。"""
        from tools.recording_to_script import list_recordings, load_records, render_skeleton

        win = tk.Toplevel(self.root)
        win.title("录制 → 脚本骨架")
        win.geometry("900x600")
        win.configure(bg=BG_MAIN)
        win.transient(self.root)
        win.grab_set()

        top = tk.Frame(win, bg=BG_PANEL)
        top.pack(fill=tk.X, padx=8, pady=8)
        tk.Label(top, text="用\"打开商户\"操作一遍后会生成录制文件,在此可生成脚本骨架",
                bg=BG_PANEL, fg=FG_MUTED, font=("Microsoft YaHei", 9)).pack(side=tk.LEFT)

        body = tk.Frame(win, bg=BG_PANEL)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        left_pane = tk.Frame(body, bg=BG_PANEL, width=260)
        left_pane.pack(side=tk.LEFT, fill=tk.Y)
        left_pane.pack_propagate(False)
        tk.Label(left_pane, text="录制文件", bg=BG_PANEL, fg=FG_MAIN,
                 font=("Microsoft YaHei", 10, "bold")).pack(anchor="w", pady=(0, 4))
        listbox = tk.Listbox(left_pane, font=("Microsoft YaHei", 9))
        listbox.pack(fill=tk.BOTH, expand=True)
        vsb = tk.Scrollbar(left_pane, orient=tk.VERTICAL, command=listbox.yview)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        listbox.config(yscrollcommand=vsb.set)

        right_pane = tk.Frame(body, bg=BG_PANEL)
        right_pane.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
        btn_row = tk.Frame(right_pane, bg=BG_PANEL)
        btn_row.pack(fill=tk.X, pady=(0, 4))
        save_btn = tk.Button(btn_row, text="保存为骨架文件", relief=tk.FLAT,
                             fg="#ffffff", bg=BG_SUCCESS, cursor="hand2",
                             font=("Microsoft YaHei", 9))
        save_btn.pack(side=tk.LEFT)
        copy_btn = tk.Button(btn_row, text="复制骨架", relief=tk.FLAT,
                             fg="#ffffff", bg=BG_BUTTON, cursor="hand2",
                             font=("Microsoft YaHei", 9))
        copy_btn.pack(side=tk.LEFT, padx=(4, 0))
        viewer = scrolledtext.ScrolledText(
            right_pane, bg=BG_LOG, fg=FG_LOG, font=("Consolas", 9),
            wrap=tk.WORD, state=tk.DISABLED, relief=tk.FLAT)
        viewer.pack(fill=tk.BOTH, expand=True)

        files = list_recordings()
        if not files:
            listbox.insert(tk.END, "(暂无录制文件)")
            listbox.config(state=tk.DISABLED)
            save_btn.config(state=tk.DISABLED)
            copy_btn.config(state=tk.DISABLED)
            return

        for f in files:
            mtime = datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M:%S")
            listbox.insert(tk.END, f"{mtime}  {os.path.basename(f)}")
        listbox.selection_set(0)

        def _gen(idx):
            if idx < 0 or idx >= len(files):
                return ""
            recs = load_records(files[idx])
            if not recs:
                return "# 录制文件为空"
            return render_skeleton(recs, platform_name="某平台",
                                   class_name="XxxExporter", platform_key="xxx")

        current_code = {"text": _gen(0)}

        def _refresh_viewer():
            viewer.config(state=tk.NORMAL)
            viewer.delete("1.0", tk.END)
            viewer.insert(tk.END, current_code["text"])
            viewer.config(state=tk.DISABLED)
        _refresh_viewer()

        def _on_select(_evt=None):
            sel = listbox.curselection()
            if sel:
                current_code["text"] = _gen(sel[0])
                _refresh_viewer()
        listbox.bind("<<ListboxSelect>>", _on_select)

        def _save():
            sel = listbox.curselection()
            if not sel:
                return
            from tkinter import filedialog
            path = filedialog.asksaveasfilename(
                title="保存脚本骨架",
                defaultextension=".py",
                initialdir=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "platforms"),
                initialfile="export_skeleton.py",
                filetypes=[("Python", "*.py"), ("All", "*.*")])
            if not path:
                return
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(current_code["text"])
                self._set_status(f"骨架已保存到 {path}")
                self._append_log(f"[录制→脚本] 骨架已保存: {path}")
                messagebox.showinfo("已保存", f"骨架已保存:\n{path}\n\n需手工调整日期/弹窗等复杂控件。")
            except Exception as e:
                messagebox.showerror("保存失败", str(e))
        save_btn.config(command=_save)

        def _copy():
            self.root.clipboard_clear()
            self.root.clipboard_append(current_code["text"])
            self.root.update()
            self._set_status("骨架已复制到剪贴板")
        copy_btn.config(command=_copy)

    def _open_stats_dialog(self):
        """稳定性看板弹窗: 上方按平台汇总成功率,下方最近 N 次明细记录。"""
        win = tk.Toplevel(self.root)
        win.title("稳定性看板")
        win.geometry("900x600")
        win.configure(bg=BG_MAIN)
        win.transient(self.root)
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
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()
            self._set_status("稳定性汇总已复制")
        tk.Button(btn_row, text="复制汇总", command=_copy_stats, relief=tk.FLAT,
                  fg="#ffffff", bg=BG_SUCCESS, cursor="hand2",
                  font=("Microsoft YaHei", 9)).pack(side=tk.LEFT, padx=(4, 0))

    def _open_scheduler_dialog(self):
        """定时任务管理弹窗。"""
        from core.scheduler import SchedulerDialog
        SchedulerDialog(self.root, self.scheduler, rebuild_cb=lambda: None)

    def trigger_job(self, job):
        """CronScheduler 回调: 按任务对象执行导出(昨天~今天)。"""
        tasks = []
        for key, m in pair_job_targets(job.platforms, job.merchants, self.merchants):
            plat = self.platforms.get(key)
            if plat:
                tasks.append((key, plat, m))
        if not tasks:
            # 原来这里静默 return, 用户只会看到"任务没跑"且日志里没有任何痕迹
            detail = (f"(商户 {job.merchants} 在平台 {job.platforms} 下都未建档)"
                      if job.merchants else "(未填写商户)")
            log(f"[提示] 定时任务「{job.name}」没有可执行的平台/商户组合 {detail}",
                callback=self._append_log)
            return
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        log(f"定时任务触发: {job.name} ({len(tasks)} 项)", callback=self._append_log)
        self._run_async(lambda: self._execute_export_tasks(tasks, start, end, label=f"定时任务[{job.name}]"))

    def _copy_export_outputs(self, start_date, end_date):
        """导出完成后,把各平台各商户该日期区间的最新文件复制汇总到 downloads/时间文件夹
        文件夹名: 开始日期_结束日期_导出时间(如 2026-09-01_2026-09-01_20260902_221329)
        """
        if not start_date or not end_date:
            return None
        import shutil
        base = os.path.abspath(DOWNLOAD_DIR)
        folder = f"{start_date}_{end_date}"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        ts_dir = os.path.join(base, f"{start_date}_{end_date}_{ts}")
        copied = 0
        seen = set()
        for plat in self.platforms.values():
            plat_dir = os.path.join(base, plat.name)
            if not os.path.isdir(plat_dir):
                continue
            for sub in os.listdir(plat_dir):
                sub_dir = os.path.join(plat_dir, sub)
                if not os.path.isdir(sub_dir):
                    continue
                candidates = []
                if sub == folder:                       # 旧结构: 平台/日期
                    candidates.append(sub_dir)
                task_dir = os.path.join(sub_dir, folder)  # 新结构: 平台/商户/日期
                if os.path.isdir(task_dir):
                    candidates.append(task_dir)
                for d in candidates:
                    for f in os.listdir(d):
                        fp = os.path.join(d, f)
                        if not os.path.isfile(fp) or f.endswith((".crdownload", ".tmp", ".part")):
                            continue
                        if fp in seen:
                            continue
                        seen.add(fp)
                        try:
                            os.makedirs(ts_dir, exist_ok=True)
                            shutil.copy2(fp, os.path.join(ts_dir, f))
                            copied += 1
                        except Exception:
                            pass
        if copied:
            self._append_log(f"[汇总] 已将 {copied} 个导出文件复制到: {ts_dir}")
        else:
            self._append_log("[汇总] 未找到可汇总的导出文件,跳过打开文件夹")
            ts_dir = None
        return ts_dir

    @staticmethod
    def _sanitize_name(name):
        """商户名清理(去除路径非法字符)"""
        import re
        return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name or "")).strip() or ""

    def _show_login_prompt(self, plat_name, merchant, guide):
        """主线程显示"请登录"弹窗;用户点确定后唤醒登录线程继续"""
        messagebox.showinfo(
            "请登录",
            f"请在浏览器中完成【{plat_name} · {merchant}】登录\n\n"
            f"操作提示: {guide}\n\n登录完成后点击确定继续。")
        self._login_confirm.set()

    def _prompt_add_merchant(self, key, plat_name):
        """弹窗输入商户名 → 创建 browser_data/平台key/商户/ 目录并刷新列表"""
        dlg = tk.Toplevel(self.root)
        dlg.title(f"添加商户 - {plat_name}")
        dlg.configure(bg=BG_PANEL)
        dlg.geometry("400x160")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(dlg, text="商户名称(自定义,如: 旗舰店A)", bg=BG_PANEL, fg=FG_MAIN,
                 font=("Microsoft YaHei", 10)).pack(anchor="w", padx=20, pady=(18, 6))
        entry = tk.Entry(dlg, font=("Microsoft YaHei", 11), width=32)
        entry.pack(padx=20, fill=tk.X)
        entry.focus_set()

        def do_add():
            name = self._sanitize_name(entry.get())
            if not name:
                self._append_log("商户名称不能为空")
                return
            from core.browser import BrowserManager
            plat = self.platforms[key]
            prof = os.path.join(os.path.abspath(BROWSER_DATA_DIR),
                                BrowserManager._safe_name(plat.key),
                                name)
            try:
                os.makedirs(prof, exist_ok=True)
            except Exception as e:
                self._append_log(f"创建商户失败: {e}")
                return
            dlg.destroy()
            self.merchants = discover_merchants(self.platforms.keys())
            self._add_merchant_checkbox(key, name, True)   # 动态加入勾选框
            self._append_log(f"已添加商户:{plat_name} / {name},请对其执行\"首次登录\"")

        def on_enter(_):
            do_add()

        entry.bind("<Return>", on_enter)
        btn_row = tk.Frame(dlg, bg=BG_PANEL)
        btn_row.pack(pady=(18, 0))
        tk.Button(btn_row, text="确定", command=do_add, bg=BG_SUCCESS, fg="white",
                  font=("Microsoft YaHei", 10, "bold"), relief=tk.FLAT, width=8,
                  cursor="hand2").pack(side=tk.LEFT, padx=6)
        tk.Button(btn_row, text="取消", command=dlg.destroy,
                  font=("Microsoft YaHei", 10), relief=tk.FLAT, width=8
                  ).pack(side=tk.LEFT, padx=6)

    def _do_login(self, selected):
        # 统计全部待登录商户
        tasks = []
        for key in selected:
            plat = self.platforms[key]
            for m in self._get_selected_merchants(key):
                tasks.append((key, plat, m))
        self._set_status("正在登录...")
        self.progress.config(maximum=len(tasks), value=0)
        for i, (key, plat, merchant) in enumerate(tasks):
            self._set_platform_status(key, "warn")
            # 登录需要人工操作,始终显示浏览器窗口;每个商户独立 profile
            self._ensure_browser(plat, merchant, force_visible=True)
            self._append_log(f">>> 正在打开 {plat.name}({merchant}) 登录页面...")
            try:
                plat.login(self.browser)
                self._append_log(f"请在浏览器中完成 {plat.name}({merchant}) 登录")
                self._append_log(f"操作提示: {plat.guide}")
                # 弹窗由主线程弹出;后台线程阻塞等待用户点"确定"(期间浏览器保持打开)
                self._login_confirm.clear()
                self.root.after(0, lambda n=plat.name, m=merchant, g=plat.guide:
                                self._show_login_prompt(n, m, g))
                if not self._login_confirm.wait(timeout=600):
                    self._append_log(f"[提示] {plat.name}({merchant}) 等待登录超时,已跳过")
                else:
                    # 用户确认登录后,用平台自身的 check_login 轮询确认页面真正进入后台
                    # (而非停留在扫码/中转页)。确认成功才关闭浏览器,保证登录态已写入持久化目录。
                    self._append_log(f"正在确认 {plat.name}({merchant}) 登录状态...")
                    confirmed = False
                    try:
                        for _ in range(8):
                            if plat.check_login(self.browser):
                                confirmed = True
                                break
                            self.browser.sleep(5)
                    except Exception:
                        pass
                    if confirmed:
                        self._append_log(f"  登录态确认有效,正在导出登录态...")
                        # 主动导出 cookie + localStorage(含 session cookie),
                        # 否则微信支付这类 session cookie 平台关闭浏览器后登录态会丢失
                        self.browser.save_login_state()
                    else:
                        self._append_log(f"  [警告] 未能确认登录成功(页面仍在登录/扫码页),请重新执行首次登录")
            except Exception as e:
                self._append_log(f"打开 {plat.name}({merchant}) 失败: {e}")
                self._set_platform_status(key, "error")
            self.progress.config(value=i + 1)
            # 用户确认后再关闭,进入下一个商户(避免窗口提前被关)
            if self.browser:
                try:
                    self.browser.close()
                except Exception:
                    pass
                self.browser = None
        if not tasks:
            self._append_log("[提示] 未勾选任何平台商户,请先添加/勾选商户。")
        self._append_log("登录流程完成,各商户登录状态已按目录保存")
        self._set_status("登录完成")

    def _do_export(self, selected):
        date_str = f"{self.date_start.get()} 至 {self.date_end.get()}"
        start_date = self.date_start.get()
        end_date = self.date_end.get()
        # 统计全部待导出商户任务
        tasks = []
        for key in selected:
            plat = self.platforms[key]
            for m in self._get_selected_merchants(key):
                tasks.append((key, plat, m))
        self._execute_export_tasks(tasks, start_date, end_date, label="导出")
        self._record_job_done("导出", start_date, end_date)

    def _execute_export_tasks(self, tasks, start_date, end_date, label="导出"):
        """对 tasks((key,plat,merchant)) 依次执行导出(含失败重试), 日志/状态由这里统一驱动。"""
        if not tasks:
            self._append_log("[提示] 未勾选任何平台商户,请先添加/勾选商户。")
            return
        # 需人工操作(扫码/确认)的平台排到最后: 先自动跑完自动化平台,避免人工被打断。
        # 用稳定排序,同一平台的多个商户保持相邻/相对顺序不变。
        tasks.sort(key=lambda t: int(t[1].manual_intervention))
        manual_tasks = [(k, p, m) for k, p, m in tasks if p.manual_intervention]
        if manual_tasks:
            hints = sorted({p.intervention_hint or f"{p.name} 需人工操作" for _, p, _ in manual_tasks})
            self._append_log(
                f"[提示] 以下平台需人工操作,已排到最后执行: {'; '.join(hints)}")
        st = load_settings()
        retry_times = int(st.get("retry_times", DEFAULT_SETTINGS.get("retry_times", 2)))
        retry_interval_s = int(st.get("retry_interval_s", DEFAULT_SETTINGS.get("retry_interval_s", 30)))
        date_str = f"{start_date} 至 {end_date}"
        self._set_status(f"正在导出({date_str})...")
        self.progress.config(maximum=len(tasks), value=0)
        exported = manual = failed = 0
        for i, (key, plat, merchant) in enumerate(tasks):
            self._append_log(f">>> 正在导出 {plat.name}({merchant}) 流水...")
            self._set_platform_status(key, "warn")
            try:
                result = run_with_retry(
                    lambda k=key, p=plat, m=merchant: self._run_single_export(p, m, start_date, end_date),
                    retry_times=retry_times,
                    retry_interval_s=retry_interval_s,
                    log=self._append_log,
                )
            except Exception as e:
                result = "failed"
                self._append_log(f"[失败] {plat.name} 导出异常: {e}")
            self._apply_export_result(key, plat, result, date_str)
            if result == "success":
                exported += 1
            elif result == "manual":
                manual += 1
            else:
                failed += 1
            self.progress.config(value=i + 1)
        self._append_log(f"导出流程完成: 成功{exported} / 手动{manual} / 失败{failed}")
        self._set_status(f"导出完成(成功{exported}/手动{manual}/失败{failed})")
        return exported, manual, failed

    def _run_single_export(self, plat, merchant, start_date, end_date):
        """单个商户导出(给 run_with_retry 调用): 返回 "success"/"manual"/"failed"。"""
        date_str = f"{start_date} 至 {end_date}"
        self._append_log(f"日期范围: {date_str}")
        # 每个商户使用独立浏览器 profile(登录态隔离);下载目录含商户层
        self._ensure_browser(plat, merchant)
        self.browser.set_export_context(plat.name, start_date, end_date, merchant)
        # 注入"等待用户手动操作"回调(如微信扫码确认),生产环境始终启用。
        # 后台线程调用 browser.wait_user() 时,切到 UI 线程弹窗提醒并阻塞等待用户完成。
        def _user_wait_cb(prompt, n=plat.name, m=merchant):
            evt = threading.Event()
            def _ask():
                try:
                    messagebox.showinfo(
                        "需要您操作确认",
                        f"平台: {n} / 商户: {m}\n\n{prompt}\n\n请按提示在浏览器中完成操作(如微信扫码),完成后点击\"确定\"继续。")
                except Exception:
                    pass
                evt.set()
            self.root.after(0, _ask)
            evt.wait(timeout=3600)
        self.browser.set_user_wait_callback(_user_wait_cb)
        # 单步调试模式: 注入回调,平台脚本调用 step_pause() 时弹"继续/中止"
        if self.step_debug_var.get():
            def _step_cb(name, n=plat.name, m=merchant):
                # 通过主线程弹窗,后台线程阻塞等待用户选择
                evt = threading.Event()
                choice = {"v": True}
                def _ask():
                    try:
                        choice["v"] = messagebox.askyesno(
                            "单步调试",
                            f"已到达步骤: {name}\n平台: {n} / 商户: {m}\n\n是=继续 / 否=中止本次导出")
                    except Exception:
                        choice["v"] = True
                    evt.set()
                self.root.after(0, _ask)
                evt.wait(timeout=3600)
                return bool(choice["v"])
            self.browser.set_step_debug(True, _step_cb)
        else:
            self.browser.set_step_debug(False)
        # 登录态预检: 失效则不再执行导出, 避免空等 90s 下载
        # 预检结果同样要落统计: 原来这里直接 return, 登录失效这一最主要的失败原因
        # 永远不进 stats.jsonl, 看板成功率因此虚高。
        t0 = time.time()
        err_msg = ""
        login_ok = True
        try:
            login_ok = plat.check_login(self.browser)
        except Exception as e:
            self._append_log(f"[预检] {plat.name} 登录状态检查异常,继续尝试导出: {str(e)[:80]}")
        if not login_ok:
            self._append_log(
                f"[预检] {plat.name}({merchant}) 登录已失效,请重新完成首次登录(扫码/账号)后再导出")
            result = "manual"
            err_msg = "登录已失效(导出前预检)"
        else:
            try:
                result = plat.export(self.browser, start_date, end_date)
            except Exception as e:
                result = "failed"
                err_msg = str(e)[:200]
                self._append_log(f"[失败] {plat.name} 导出异常: {err_msg}")
        # 导出结束关闭单步调试/用户等待回调,避免影响后续任务
        self.browser.set_step_debug(False)
        self.browser.set_user_wait_callback(None)
        # 落稳定性统计(每次导出一条 JSON,供看板汇总)
        duration = time.time() - t0
        record_stat(plat.name, merchant, start_date, end_date, result,
                    duration_s=duration, error=err_msg)
        if result == "success":
            self._append_log(f"[完成] {plat.name} 流水导出成功({duration:.1f}s)")
        elif result == "manual":
            self._append_log(f"[提示] {plat.name} 需要手动完成导出")
        else:
            self._append_log(f"[失败] {plat.name} 导出失败")
        return result

    def _apply_export_result(self, key, plat, result, date_str):
        """结果状态灯与弹窗(manual 时弹窗)。"""
        if result == "success":
            self._set_platform_status(key, "ok")
        elif result == "manual":
            self._set_platform_status(key, "warn")
            self.root.after(0, lambda n=plat.name, d=date_str, g=plat.guide: messagebox.showinfo(
                "请手动导出",
                f"【{n}】自动导出未完全成功\n\n"
                f"日期范围: {d}\n"
                f"操作指引: {g}\n\n"
                f"请在浏览器中手动完成导出,下载完成后点击确定继续。"))
        else:
            self._set_platform_status(key, "error")

    def _record_job_done(self, label, start_date, end_date):
        """导出任务收尾: 汇总复制文件、打开文件夹与操作日志。"""
        log(f"{label}完成", callback=self._append_log)
        # 直接汇总并打开文件夹(不再依赖 progress,避免 Tk 字符串类型比较等引发静默中断)
        try:
            ts_dir = self._copy_export_outputs(start_date, end_date)
            if ts_dir:
                try:
                    os.startfile(ts_dir)
                    self._append_log(f"[汇总] 已自动打开文件夹: {ts_dir}")
                except Exception as e:
                    self._append_log(f"[汇总] 打开文件夹失败: {e}")
        except Exception as e:
            self._append_log(f"[汇总] 复制汇总失败: {e}")

    def _do_check(self, selected):
        tasks = []
        for key in selected:
            plat = self.platforms[key]
            for m in self._get_selected_merchants(key):
                tasks.append((key, plat, m))
        self._set_status("正在检查登录状态...")
        self.progress.config(maximum=len(tasks), value=0)
        for i, (key, plat, merchant) in enumerate(tasks):
            self._append_log(f"检查 {plat.name}({merchant})...")
            try:
                # 每个商户独立 profile 检查
                self._ensure_browser(plat, merchant)
                logged_in = plat.check_login(self.browser)
                if not logged_in:
                    self._append_log(f"  {plat.name}({merchant}): 未登录")
                    self._set_platform_status(key, "error")
                else:
                    info = self.browser.get_page_info()
                    self._append_log(f"  {plat.name}({merchant}): 已登录 ({info.get('title', '')[:20]})")
                    self._set_platform_status(key, "ok")
            except Exception as e:
                self._append_log(f"  {plat.name}({merchant}): 检查失败 - {e}")
                self._set_platform_status(key, "error")
            self.progress.config(value=i + 1)
        if not tasks:
            self._append_log("[提示] 未勾选任何平台商户。")
        self._set_status("检查完成")

    def cleanup(self):
        self._save_selection()   # 退出前保存勾选状态
        if self.browser:
            self.browser.close()


def check_dependencies():
    """检查依赖是否已安装"""
    missing = []
    try:
        import playwright
    except ImportError:
        missing.append("playwright")
    if missing:
        root = tk.Tk()
        root.withdraw()
        result = messagebox.askyesno(
            "缺少依赖",
            "检测到缺少必要的Python依赖包(playwright)。\n\n"
            "是否现在自动安装?(需要联网,约30秒)\n\n"
            "如果选择否,程序将退出。"
        )
        if result:
            import subprocess
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright"])
                subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
                messagebox.showinfo("安装完成", "依赖安装成功!程序将重新启动。")
                os.execv(sys.executable, [sys.executable] + sys.argv)
            except Exception as e:
                messagebox.showerror("安装失败", f"自动安装失败: {e}\n\n请手动运行:\npip install playwright\npython -m playwright install chromium")
                return False
        else:
            return False
    return True


def main():
    try:
        if not check_dependencies():
            return
        root = tk.Tk()
        app = LiushuiApp(root)

        def _on_close(app, root):
            app.scheduler.stop()
            app.keepalive.stop()
            app.cleanup()
            root.destroy()

        root.protocol("WM_DELETE_WINDOW", lambda: _on_close(app, root))
        root.mainloop()
    except Exception as e:
        # 业务用户容错: 任何异常都记录日志并以弹窗展示,不让程序静默崩溃
        import traceback
        tb = traceback.format_exc()
        try:
            from logger import log
            log(f"程序运行异常: {e}\n{tb}", "error")
        except Exception:
            pass
        try:
            err_root = tk.Tk()
            err_root.withdraw()
            messagebox.showerror(
                "程序出错",
                f"程序发生异常,请把以下信息反馈给维护人员:\n\n{e}\n\n"
                f"详细信息已记录到 logs 文件夹。")
            err_root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    main()
