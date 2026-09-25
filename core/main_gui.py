"""
流水自动导出工具 - GUI主程序
适用于非技术用户的可视化界面
平台通过 core.loader 动态发现加载,新增平台只需在 platforms/ 下建文件夹
"""

import os
import sys

# 项目根目录: 将根加入 sys.path,保证从任意位置启动都能定位 core/ platforms/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 崩溃兜底要早于其余导入装好: 下面 tkinter 和 core.* 任何一句抛错, 在 .vbs 的
# pythonw 隐藏窗口下都是"图标闪一下, 什么也没有"。
from core.crashguard import install as install_crash_guard
install_crash_guard()

import json
import queue
import shutil
import threading
import time
import tkinter as tk
from collections import namedtuple
from datetime import datetime, timedelta
from tkinter import ttk, messagebox, scrolledtext
from core.config import (DOWNLOAD_DIR, BROWSER_DATA_DIR, DEFAULT_SETTINGS,
                         SELECTION_FILE, load_settings, save_settings,
                         write_json_atomic)
from core.loader import discover_platforms, reload_platforms
from core.logger import log, list_history_logs, LOG_DIR, record_stat, load_stats, summarize_stats
from core.outputs import copy_to_summary_dir, sanitize_name
from core.cleanup import describe as describe_cleanup, run_cleanup
from core.keepalive import KeepAliveService
from core.theme import (BG_MAIN, BG_PANEL, BG_BUTTON, BG_BUTTON_HOVER,
                        BG_SUCCESS, BG_WARN, BG_ERROR, BG_LOG, FG_LOG,
                        FG_MAIN, FG_MUTED)
from core.dialogs import show_log_history, show_stats_dashboard
from core.scheduler import pair_job_targets


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


def find_duplicate_merchant(name, existing):
    """existing 中与 name 视为同一家商户的那个名字, 没有则返回空串。

    按 Windows 目录名的规矩比: 忽略首尾空格、不区分大小写(`Shop` 与 `shop` 建出来是
    同一个 profile 目录)。
    """
    norm = (name or "").strip().casefold()
    if not norm:
        return ""
    for e in existing or ():
        if (e or "").strip().casefold() == norm:
            return e
    return ""


def merchant_changes(before, after):
    """两次扫目录的结果({平台key: [商户名]})对比, 返回 (新增的, 消失的)。

    只比"多了/少了哪些", 顺序变了不算改动。
    """
    added, removed = {}, {}
    for k in set(before or {}) | set(after or {}):
        old = set((before or {}).get(k, []))
        new = set((after or {}).get(k, []))
        if new - old:
            added[k] = sorted(new - old)
        if old - new:
            removed[k] = sorted(old - new)
    return added, removed


def merchant_profile_dir(base_dir, plat_key, merchant):
    """按运行期取 profile 的同一套规矩算出目录: browser_data/<key>/<商户>。"""
    from core.browser import BrowserManager
    return os.path.normpath(os.path.join(
        os.path.abspath(base_dir),
        BrowserManager._safe_name(plat_key),
        BrowserManager._safe_name(merchant)))


def delete_merchant_profile(base_dir, plat_key, merchant):
    """删除一个商户的登录目录, 返回 (是否成功, 给用户看的一句话)。

    只碰 `browser_data/<key>/<商户>` 这一层。名字清洗只替换掉 `<>:"/\\` 这类字符,
    `..` 是原样留下的, 所以这里算完路径还要再核一遍"是不是正好深一层"——少一层就会把
    整个平台目录甚至整个 browser_data 删掉。已导出的账单在 downloads 下, 不在本函数射程内。
    """
    from core.browser import BrowserManager
    if not (merchant or "").strip():
        return False, "商户名为空, 已拒绝删除"
    target = merchant_profile_dir(base_dir, plat_key, merchant)
    base_real = os.path.normcase(os.path.abspath(base_dir))
    key_dir = os.path.normcase(os.path.join(base_real, BrowserManager._safe_name(plat_key)))
    parent = os.path.normcase(os.path.dirname(target))
    if (parent != key_dir or os.path.normcase(target) == parent
            or os.path.normcase(os.path.basename(target)) !=
            os.path.normcase(BrowserManager._safe_name(merchant))):
        return False, f"路径不是 browser_data/平台/商户 这一层, 已拒绝删除: {merchant}"
    if not os.path.isdir(target):
        return True, f"目录本来就不存在, 无需删除: {merchant}"
    last_err = None
    for _ in range(4):
        try:
            shutil.rmtree(target)
            return True, f"已删除登录目录: {plat_key}/{merchant}"
        except Exception as e:
            last_err = e
            time.sleep(0.5)      # Chromium 刚退出时目录锁常有几秒残留
    return False, f"删除失败(浏览器可能还占着这个目录): {last_err}"


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



# 工作线程投递的界面回调多久抽一次(毫秒)。太小空转, 太大日志发涩。
_UI_PUMP_MS = 60
_UI_PUMP_BATCH = 300

# 导出结果的用户可读文案与"最差优先"排序(一个平台只有一盏状态灯, 多商户时亮最差的那个)
RESULT_LABEL = {"success": "成功", "manual": "需手动完成", "failed": "失败"}
_RESULT_RANK = {"success": 0, "manual": 1, "failed": 2}

# 超过这么多天就先问一句: 多数后台一次给不出整年的账单(微信支付单次上限 30 天),
# 硬着头皮导只会等满超时再报 manual, 用户白等几个小时的重试。
DATE_MAX_SPAN_DAYS = 92

# check_date_range 的结果: 结论 + 弹窗标题/文案 + 规范化(补零)后的起止日期
DateCheck = namedtuple("DateCheck", "verdict title message start end")


def check_date_range(start_text, end_text, today=None):
    """校验日期区间, 返回 DateCheck(结论, 弹窗标题, 文案, 规范化的起止日期)。

    结论: ok 放行 / ask 要用户确认 / bad 拦下。只管界面能判的事: 格式、顺序、未来
    日期、超长区间。某个平台具体支持多少天要真页才知道, 这里不替平台猜。
    """
    try:
        ds = datetime.strptime((start_text or "").strip(), "%Y-%m-%d")
        de = datetime.strptime((end_text or "").strip(), "%Y-%m-%d")
    except ValueError:
        return DateCheck("bad", "日期格式",
                         "日期格式应为 YYYY-MM-DD(如 2026-09-01),请检查后重试。", "", "")
    if de < ds:
        return DateCheck("bad", "日期范围",
                         "结束日期不能早于开始日期,请检查后重试。", "", "")
    ref = (today or datetime.now()).replace(hour=0, minute=0, second=0, microsecond=0)
    norm_start, norm_end = ds.strftime("%Y-%m-%d"), de.strftime("%Y-%m-%d")
    if ds > ref or de > ref:
        # 两头都在未来时报"开始日期": 那是用户先看到的那一个框
        which = "开始日期" if ds > ref else "结束日期"
        return DateCheck("bad", "日期范围",
                         f"{which}晚于今天({ref:%Y-%m-%d}): 那之后的账单还没产生,"
                         "现在导只会白等到下载超时。", norm_start, norm_end)
    span = (de - ds).days + 1
    if span > DATE_MAX_SPAN_DAYS:
        return DateCheck("ask", "日期范围",
                         f"这次区间共 {span} 天(超过 {DATE_MAX_SPAN_DAYS} 天)。\n\n"
                         "不少后台一次给不出这么长的账单(例如微信支付单次最多 30 天),"
                         "建议按季度或按月分批导。\n\n仍要按这个区间导出吗?",
                         norm_start, norm_end)
    return DateCheck("ok", "", "", norm_start, norm_end)

class LiushuiApp:
    def __init__(self, root):
        self.root = root
        self.browser = None
        self.running = False
        # running 由 UI 线程与定时任务线程共同读写, 检查+置位必须在同一把锁里完成,
        # 否则两条线程可能同时进入任务(同一 profile 被两个浏览器占用)。
        self._task_lock = threading.Lock()
        # 界面更新一律经这里回主线程执行(见 _ui/_pump_ui);Tk 控件不能跨线程改。
        self._ui_thread = threading.get_ident()
        self._uiq = queue.Queue()
        self.platform_vars = {}
        self.log_lines = []
        self.platforms = discover_platforms()
        self.settings = load_settings()
        self.login_urls = {key: plat.login_url for key, plat in self.platforms.items()}
        self.merchant_vars = {}   # platform_key -> {商户名: BooleanVar}
        self._merchant_memory = {}   # platform_key -> {商户名: bool} 平台被取消勾选那一刻的样子
        self.merchants = discover_merchants(self.platforms.keys())
        self.selection = self._load_selection()   # 上次勾选状态(重启后恢复)
        self._login_confirm = threading.Event()  # 登录弹窗确认事件(等待用户)
        self._abort = threading.Event()          # 中止请求: 在任务边界生效(见 _aborted)

        self._setup_window()
        # 构建顺序: 右日志 → 左平台 → 中间(设置+历史+操作按钮) → 初始化概览
        self._build_right_panel()
        self._build_left_panel()
        self._build_middle_panel()
        self._refresh_summary()   # 初始化概览条
        self.root.after(_UI_PUMP_MS, self._pump_ui)   # 须在控件建好后启动

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

        # 老日志/老汇总副本的清理: 后台跑一次, 不挡界面也不碰账单原件
        self._start_cleanup()

    def _run_cleanup_once(self):
        """跑一次清理并把结果写日志。

        清理是家务事: 目录不存在、文件被占用、设置里写了怪东西, 都只配在日志上写一句,
        绝不能把启动带崩(业务用户看到的是"双击没反应"的话, 锅不该由清理来背)。
        """
        default = int(DEFAULT_SETTINGS.get("cleanup_keep_days", 365))
        try:
            keep = int(load_settings().get("cleanup_keep_days", default))
        except Exception:
            keep = default
        try:
            report = run_cleanup(LOG_DIR, DOWNLOAD_DIR, keep)
        except Exception as e:
            self._append_log(f"[清理] 自动清理没跑成, 已跳过: {e}")
            return
        self._append_log(f"[清理] {describe_cleanup(report, keep)}")

    def _start_cleanup(self):
        threading.Thread(target=self._run_cleanup_once, daemon=True).start()

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
        # 商户是扫目录发现的: 手工建/从别的电脑拷进来的目录不刷新就看不见
        tk.Button(btn_frame, text="刷新商户", command=self._refresh_merchants,
                  font=("Microsoft YaHei", 9), width=8).pack(side=tk.RIGHT)

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
        self.merchant_rows = {}     # platform_key -> {商户名: 该行的 Frame(删除时要拆行)}
        self.merchant_badges = {}   # platform_key -> 商户数徽标 Label
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

        def _toggle_platform(k=key):
            self._on_platform_toggled(k)

        cb = tk.Checkbutton(row, variable=var, text=plat.name,
                            bg=BG_PANEL, fg=FG_MAIN,
                            font=("Microsoft YaHei", 10),
                            activebackground=BG_PANEL,
                            command=_toggle_platform)
        cb.pack(side=tk.LEFT)
        # 商户数徽标(灰字小计数,便于一眼看清);增删商户后要刷新,所以留引用
        badge = tk.Label(row, text=str(len(self.merchants.get(key, []))),
                         bg="#eeeeee", fg="#7f8c8d",
                         font=("Microsoft YaHei", 8))
        badge.pack(side=tk.LEFT, padx=(3, 0))
        self.merchant_badges[key] = badge
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
        self.merchant_rows = {}
        self.merchant_badges = {}
        for key, plat in self.platforms.items():
            if not getattr(plat, "enabled", True):
                continue
            self._build_platform_row(key, plat)
        self._refresh_summary()

    def _refresh_merchants(self):
        """重新扫 browser_data 并重建左栏: 外部建的商户目录不用重启也能看见。

        勾选状态按 selection 恢复, 所以刷新不会把用户已勾/已取消的东西洗掉。
        """
        before = {k: list(v) for k, v in (self.merchants or {}).items()}
        try:
            self._rebuild_platform_list()
        except Exception as e:
            self._append_log(f"[刷新商户] 刷新失败: {e}")
            return
        added, removed = merchant_changes(before, self.merchants)

        def label(key, names):
            plat = (self.platforms or {}).get(key)
            return f"{getattr(plat, 'name', key)}/{', '.join(names)}"

        if not added and not removed:
            total = sum(len(v) for v in self.merchants.values())
            self._append_log(f"[刷新商户] 没有变化(当前共 {total} 家商户)")
            return
        parts = [f"新增 {label(k, v)}" for k, v in sorted(added.items())]
        parts += [f"已消失 {label(k, v)}" for k, v in sorted(removed.items())]
        self._append_log("[刷新商户] " + "; ".join(parts))

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

        # 日志/汇总副本保留天数(0=不清理)
        clean_row = tk.Frame(middle, bg=BG_PANEL)
        clean_row.pack(fill=tk.X, pady=(2, 0))
        tk.Label(clean_row, text="日志/汇总保留(天,0=不清理):", bg=BG_PANEL, fg=FG_MUTED,
                 font=("Microsoft YaHei", 9)).pack(side=tk.LEFT)
        self.cleanup_days = tk.Spinbox(clean_row, from_=0, to=3650, width=6,
                                       font=("Microsoft YaHei", 9))
        self.cleanup_days.delete(0, tk.END)
        self.cleanup_days.insert(0, str(int(self.settings.get(
            "cleanup_keep_days", DEFAULT_SETTINGS.get("cleanup_keep_days", 365)))))
        self.cleanup_days.bind("<FocusOut>", lambda *_: self._on_cleanup_setting())
        self.cleanup_days.bind("<Return>", lambda *_: self._on_cleanup_setting())
        self.cleanup_days.pack(side=tk.LEFT, padx=(4, 0))
        tk.Label(clean_row, text="(只清很旧的运行日志和汇总副本, 账单原件不动)",
                 bg=BG_PANEL, fg="#95a5a6", font=("Microsoft YaHei", 8)
                 ).pack(side=tk.LEFT, padx=(6, 0))

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
        # 中止: 任务跑着才可点, 见 _run_async/_thread_wrapper 的启停
        self.abort_btn = tk.Button(bar, text="中止本次任务", command=self._request_abort,
                                   bg=BG_ERROR, fg="white",
                                   font=("Microsoft YaHei", 9, "bold"), relief=tk.FLAT,
                                   state=tk.DISABLED, cursor="hand2")
        self.abort_btn.grid(row=2, column=2, rowspan=3, padx=3, pady=3, sticky="nsew")

    def _request_abort(self):
        """请求中止正在跑的任务。

        只在一个商户/平台做完的边界上生效, 不会中途掐断浏览器 —— 那会让下载停在
        半截文件上, 反而更难收拾。
        """
        if not self.running:
            return
        self._abort.set()
        self._append_log("[中止] 已请求中止: 当前商户跑完后就停, 剩余商户不再执行")
        self._set_status("正在中止...")

    def _aborted(self):
        return bool(getattr(self, "_abort", None) and self._abort.is_set())

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
        # 每商户一行: 勾选 + 右侧"打开"(手工测试页面) 与 "删"(移除该商户)
        mrow = tk.Frame(frame, bg=BG_PANEL)
        mrow.pack(anchor="w", fill=tk.X, padx=(26, 0))
        self.merchant_rows.setdefault(key, {})[name] = mrow
        tk.Checkbutton(
            mrow, variable=mv, text=name,
            bg=BG_PANEL, fg=FG_MUTED, font=("Microsoft YaHei", 9),
            activebackground=BG_PANEL).pack(side=tk.LEFT)
        tk.Button(mrow, text="打开", width=4, relief=tk.FLAT,
                  fg="#ffffff", bg="#e67e22", cursor="hand2",
                  font=("Microsoft YaHei", 8),
                  command=lambda k=key, m=name: self._action_open_merchant(k, m)
                  ).pack(side=tk.RIGHT, padx=(4, 2))
        tk.Button(mrow, text="删", width=2, relief=tk.FLAT,
                  fg="#ffffff", bg="#95a5a6", cursor="hand2",
                  font=("Microsoft YaHei", 8),
                  command=lambda k=key, m=name: self._prompt_delete_merchant(k, m)
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

    def _on_platform_toggled(self, key):
        """平台勾选框切换: 取消时先记住每家商户的勾选, 再勾回来时按原样恢复。

        以前是"取消=商户全不勾, 勾回=商户全勾", 于是取消勾一下再勾回来, 用户辛苦
        取消掉的那几家(比如同一平台下已停业的店)又全被勾上了, 下一次导出就把它们也跑了。
        """
        pv = self.platform_vars.get(key)
        if pv is None:
            return
        mvs = self.merchant_vars.get(key, {})
        if not pv.get():
            self._merchant_memory[key] = {n: bool(mv.get()) for n, mv in mvs.items()}
            for mv in mvs.values():
                mv.set(False)
            return
        saved = self._merchant_memory.get(key)
        for n, mv in mvs.items():
            # 从没记过 = 第一次勾上, 沿用"整平台全选"的旧行为; 记过就按当时的样子还原
            mv.set(True if saved is None else bool(saved.get(n, True)))

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
        """持久化当前平台/商户勾选状态到文件(重启后自动恢复)。
        同时在 self.selection 留一份纯数据镜像: 保活线程需要读勾选, 而跨线程读
        Tk 变量不安全。"""
        try:
            data = {
                "platform": {k: bool(v.get()) for k, v in self.platform_vars.items()},
                "merchant": {k: {n: bool(mv.get()) for n, mv in mvs.items()}
                             for k, mvs in self.merchant_vars.items()},
            }
            self.selection = data
            write_json_atomic(SELECTION_FILE, data)
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
        """任意线程可调: 文本缓冲就地追加, 控件刷新投递到主线程。

        这条是量最大的跨线程入口(BrowserManager 每个动作、保活/调度线程都会调)。
        """
        self.log_lines.append(line)
        if len(self.log_lines) > 500:
            self.log_lines = self.log_lines[-300:]

        def _paint():
            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, line + "\n")
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)
        self._ui(_paint)

    def _ui(self, fn):
        """把只在主线程安全的操作(改控件、弹窗)投递给主线程;工作线程立即返回不等待。
        已在主线程则就地执行, 保证点击响应没有额外延迟。"""
        if threading.get_ident() == self._ui_thread:
            fn()
        else:
            self._uiq.put(fn)

    def _pump_ui(self):
        """主线程周期性消费 _ui 队列。单个回调抛错只丢这一条, 不能停掉泵。"""
        for _ in range(_UI_PUMP_BATCH):
            try:
                fn = self._uiq.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:
                pass
        try:
            self.root.after(_UI_PUMP_MS, self._pump_ui)
        except Exception:
            pass   # 窗口已销毁, 泵自然结束

    def _set_progress(self, **kwargs):
        self._ui(lambda: self.progress.config(**kwargs))

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
        """打开历史日志窗口(实现见 core/dialogs.py)。"""
        show_log_history(self.root)
    def _set_status(self, text):
        self._ui(lambda: self.status_var.set(f"状态: {text}"))

    def _paint_platform_status(self, key, status):
        """仅主线程调用;跨线程请用 set_platform_status。"""
        plat = self.platforms.get(key)
        if not plat:
            return
        label = getattr(plat, "status_label", None)
        if not label:
            return
        colors = {"ok": BG_SUCCESS, "warn": BG_WARN, "error": BG_ERROR, "idle": "#bdc3c7"}
        label.config(fg=colors.get(status, "#bdc3c7"))

    def set_platform_status(self, key, status):
        """平台状态灯的线程安全入口(保活线程和任务线程都走这里)。
        保活服务按这个名字查找回调, 改名要同步 core/keepalive.py::_mark。"""
        self._ui(lambda: self._paint_platform_status(key, status))

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
        """失败重试设置变化即时保存(运行时从 settings 读取,无需运行时状态)。

        只写 retry_times: 首次间隔 `retry_interval_s` 界面上没有输入框, 以前每次点勾
        都被这里写回默认 30, 手工在 settings.json 里调过的值等于一勾就没了。
        """
        enabled = bool(self.enable_retry_var.get())
        try:
            times = int(str(self.retry_times.get()).strip())
        except Exception:
            times = int(DEFAULT_SETTINGS.get("retry_times", 2))
        times = max(0, min(5, times))
        retry_times = times if enabled else 0
        save_settings({"retry_times": retry_times})
        self.settings["retry_times"] = retry_times

    def _on_cleanup_setting(self, *_):
        """保留天数改动即时保存(0=不清理); 读不到合理数字时退回默认值。"""
        default = int(DEFAULT_SETTINGS.get("cleanup_keep_days", 365))
        try:
            days = int(str(self.cleanup_days.get()).strip())
        except Exception:
            days = default
        days = max(0, min(3650, days))
        save_settings({"cleanup_keep_days": days})
        self.settings["cleanup_keep_days"] = days
        self._append_log(f"[设置] 日志/汇总副本保留 {days} 天"
                         f"{'(已关闭自动清理)' if days == 0 else ''},下次启动时生效")

    def __iter_merchants(self):
        """供保活复用的迭代: (platform_key, merchant)。
        保活线程调用, 因此读 self.selection 镜像而不是 Tk 勾选框变量。"""
        sel = self.selection or {}
        plats = sel.get("platform", {})
        merchants = sel.get("merchant", {})
        for key in self.platforms:
            if not plats.get(key):
                continue
            for name, on in merchants.get(key, {}).items():
                if on:
                    yield (key, name)

    iter_selected_merchants = __iter_merchants

    def _run_async(self, target, notify_busy=True):
        """起后台线程跑 target; running 标志由 _task_lock 保护。
        返回是否真的开始执行(忙则 False), 调用方据此决定要不要重试。
        notify_busy=False 用于定时任务: 到点却撞上手动导出时, 不该弹窗打扰用户。"""
        with self._task_lock:
            if self.running:
                busy = True
            else:
                self.running = True
                busy = False
        if busy:
            if notify_busy:
                # 定时任务线程也会走到这里, 弹窗只能在主线程做
                self._ui(lambda: messagebox.showwarning("提示", "已有任务正在执行,请等待完成。"))
            return False
        for key in self.platform_vars:
            self.set_platform_status(key, "idle")
        self._abort.clear()
        self._ui(lambda: self.abort_btn.config(state=tk.NORMAL))
        t = threading.Thread(target=self._thread_wrapper, args=(target,), daemon=True)
        t.start()
        return True

    def _thread_wrapper(self, target):
        try:
            target()
        except Exception as e:
            self._append_log(f"[错误] {e}")
            self._set_status(f"出错: {e}")
        finally:
            with self._task_lock:
                self.running = False
            self._set_status("就绪")
            self._set_progress(value=0)
            self._ui(lambda: self.abort_btn.config(state=tk.DISABLED))
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
        # 该复选框的 trace 会把值同步进 settings.json, 这里读文件而不是读 BooleanVar:
        # _ensure_browser 跑在任务线程上, 跨线程读 Tk 变量不安全。
        show = force_visible or bool(load_settings().get("show_browser", True))
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

    def _collect_tasks(self, selected=None):
        """把"已勾选平台 + 该平台已勾选商户"展成 (key, plat, merchant) 列表。
        只能在主线程调用(要读勾选框);任务线程一律用主线程拍好的这份列表, 不再回读界面。"""
        tasks = []
        for key in (selected if selected is not None else self._get_selected()):
            plat = self.platforms.get(key)
            if not plat:
                continue
            for m in self._get_selected_merchants(key):
                tasks.append((key, plat, m))
        return tasks

    def _validate_dates(self, show_warning=True):
        """校验日期输入(格式、起止顺序、不晚于今天),供导出/登录前调用。

        超长区间不拦、只问: 有人确实要一次导一年, 那是他的选择; 但未来日期一定导不出
        东西, 直接拦下, 免得他等满重试才发现。`show_warning=False`(检查登录状态)时
        不打扰用户: 该放行就放行, 只是不弹窗。

        校验通过时顺手把输入框写成补零的标准写法(`2026-9-2` → `2026-09-02`): 这个字符
        串会直接进日期框填写、归档目录名和汇总文件夹名, 两种写法混着用会分成两个目录。
        """
        check = check_date_range(self.date_start.get(), self.date_end.get())
        if check.verdict == "bad":
            if show_warning:
                messagebox.showwarning(check.title, check.message)
            return False
        if check.verdict == "ask" and show_warning:
            if not messagebox.askyesno(check.title, check.message):
                return False
        for var, value in ((self.date_start, check.start), (self.date_end, check.end)):
            if value and var.get().strip() != value:
                var.set(value)
        return True

    def _action_login_all(self):
        if not self._validate_dates():
            return
        selected = self._get_selected()
        if not selected:
            messagebox.showwarning("提示", "请至少选择一个平台。")
            return
        tasks = self._collect_tasks(selected)
        if tasks:
            log(f"首次登录: {len(tasks)} 个商户", callback=self._append_log)
        self._run_async(lambda: self._do_login(tasks))

    def _action_export_all(self):
        if not self._validate_dates():
            return
        selected = self._get_selected()
        if not selected:
            messagebox.showwarning("提示", "请至少选择一个平台。")
            return
        # 汇总本次将导出的任务并让用户确认(防误操作)
        tasks = self._collect_tasks(selected)
        if not tasks:
            messagebox.showwarning("提示", "请至少勾选一个商户后再导出。")
            return
        # 日期与"单步调试"开关在此处(主线程)取值, 任务线程不再回读界面
        start_date = self.date_start.get().strip()
        end_date = self.date_end.get().strip()
        step_debug = bool(self.step_debug_var.get())
        date_str = f"{start_date} ~ {end_date}"
        labels = [f"{plat.name} · {m}" for _, plat, m in tasks]
        lines = "\n".join(labels[:12])
        if len(labels) > 12:
            lines += f"\n…… 等共 {len(labels)} 项"
        ok = messagebox.askyesno(
            "确认导出",
            f"确认导出以下 {len(labels)} 个商家流水?\n\n"
            f"日期: {date_str}\n{lines}")
        if not ok:
            return
        log(f"开始导出: {len(labels)} 个商户 [{date_str}]", callback=self._append_log)
        self._run_async(lambda: self._do_export(tasks, start_date, end_date, step_debug))

    def _action_check_status(self):
        if not self._validate_dates(show_warning=False):
            return
        selected = self._get_selected()
        if not selected:
            messagebox.showwarning("提示", "请至少选择一个平台。")
            return
        log("检查登录状态", callback=self._append_log)
        tasks = self._collect_tasks(selected)
        self._run_async(lambda: self._do_check(tasks))

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

    MERCHANT_PROBE_TIMEOUT_S = 30 * 60   # 手工测试窗口最长占用时间

    def _open_merchant_browser(self, plat, merchant):
        """后台: 用该商户独立 profile 启动浏览器并打开导出页,不执行自动化,等待用户手工操作。
        线程保持存活直到用户手工关闭浏览器窗口,防止任务结束逻辑自动关闭浏览器。

        这个窗口开着期间 running 一直为 True, 手动导出会被挡、定时任务会被推迟,
        所以要能中止, 也不能无限期挂着(见 MERCHANT_PROBE_TIMEOUT_S)。
        """
        try:
            self._ensure_browser(plat, merchant, force_visible=True)
            # 开启操作追踪: 记录页面上的点击/输入元素信息到日志,辅助编写脚本
            self.browser.enable_action_trace()
            self.browser.navigate(plat.export_url)
            self.browser.sleep(3)
            self._append_log(f"已打开 {plat.name}({merchant}) 页面(已恢复登录态)")
            self._append_log("请手工操作测试,日志会记录点击/输入的元素信息;"
                             "完成后直接关闭浏览器窗口即可(也可点\"中止本次任务\"关掉)")
            # 轮询等待用户手工关闭浏览器窗口(page 关闭后访问会抛异常)
            deadline = time.time() + self.MERCHANT_PROBE_TIMEOUT_S
            while True:
                self.browser.sleep(2)
                if self._aborted():
                    self._append_log(f"[中止] 关闭 {plat.name}({merchant}) 手工测试窗口")
                    break
                if time.time() > deadline:
                    self._append_log(f"[提示] 手工测试窗口超过 {self.MERCHANT_PROBE_TIMEOUT_S // 60}"
                                     " 分钟未关闭, 自动关闭以释放任务执行权")
                    break
                try:
                    self.browser.page.title()
                except Exception:
                    break
            self._append_log(f"{plat.name}({merchant}) 浏览器窗口已关闭")
        except Exception as e:
            self._append_log(f"打开商户浏览器失败: {e}")
            self.set_platform_status(plat.key, "error")

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
                self._set_progress(maximum=100, value=0)
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
        from tools.recording_to_script import (list_recordings, load_records,
                                               render_skeleton, write_skeleton)

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
            # 默认落点在 platforms/ 下, 很容易选中已手工调过的 export.py
            if os.path.exists(path) and not messagebox.askyesno(
                    "覆盖已有文件",
                    f"目标文件已存在:\n{path}\n\n"
                    f"覆盖会丢掉里面手工调整过的逻辑。\n"
                    f"建议先另存为 export_skeleton.py 再对比合并。确定仍要覆盖吗?"):
                return
            try:
                write_skeleton(path, current_code["text"], force=True)
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
        """稳定性看板(实现见 core/dialogs.py)。"""
        show_stats_dashboard(self)
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
        started = self._run_async(
            lambda: self._execute_export_tasks(tasks, start, end, label=f"定时任务[{job.name}]"),
            notify_busy=False)
        if not started:
            # 到点时手动导出还在跑: 记日志而不是弹窗, 且不写 last_run,
            # 让调度器下个轮询周期再试一次(见 CronScheduler.trigger)。
            log(f"[定时] 「{job.name}」到点但有任务正在执行, 稍后自动重试",
                callback=self._append_log)
        return started

    def _copy_export_outputs(self, start_date, end_date):
        """导出完成后汇总本次日期区间的文件到 downloads/开始_结束_时间戳/ 并返回该目录。
        具体查找/复制逻辑在 core/outputs.py(纯文件操作, 可脱离界面单测)。
        """
        return copy_to_summary_dir(os.path.abspath(DOWNLOAD_DIR),
                                   [p.name for p in self.platforms.values()],
                                   start_date, end_date, log=self._append_log)

    @staticmethod
    def _sanitize_name(name):
        """商户名清理(去除路径非法字符);空结果返回空串以便提示用户重填。"""
        return sanitize_name(name)

    def _show_login_prompt(self, plat_name, merchant, guide):
        """主线程显示"请登录"弹窗;用户点确定后唤醒登录线程继续"""
        messagebox.showinfo(
            "请登录",
            f"请在浏览器中完成【{plat_name} · {merchant}】登录\n\n"
            f"操作提示: {guide}\n\n登录完成后点击确定继续。")
        self._login_confirm.set()

    def _add_merchant(self, key, raw_name):
        """建商户 profile 目录并加一行勾选; 返回 (是否成功, 给用户看的一句话)。

        重名以前不拦: `makedirs(exist_ok=True)` 照样建、界面照样塞一行, 而
        `merchant_vars[key][name]` 是字典 —— 新 Var 把旧的顶掉, 于是屏幕上两行同名,
        勾上面那一行等于没勾(两个勾选框共用一个 key), 重启后按目录重建才塌成一行。
        """
        name = self._sanitize_name(raw_name)
        if not name:
            return False, "商户名称不能为空"
        if not name.strip("._ "):
            # sanitize 只换掉 <>:"/\ 这类字符, ".." 原样留着 —— 直接拼进 profile 路径
            # 会指到 browser_data 本身, 把别的商户的登录目录当成自己的 profile
            return False, "商户名称得有点实际内容(不能只用 . 或 _)"
        plat = self.platforms[key]
        known = list(self.merchants.get(key, [])) + list(self.merchant_vars.get(key, {}))
        dup = find_duplicate_merchant(name, known)
        if dup:
            return False, f"商户「{dup}」已经存在, 请勿重复添加"
        from core.browser import BrowserManager
        prof = os.path.join(os.path.abspath(BROWSER_DATA_DIR),
                            BrowserManager._safe_name(plat.key), name)
        try:
            os.makedirs(prof, exist_ok=True)
        except Exception as e:
            return False, f"创建商户失败: {e}"
        self.merchants = discover_merchants(self.platforms.keys())
        self._add_merchant_checkbox(key, name, True)       # 动态加入勾选框
        self._refresh_merchant_badge(key)
        return True, f"已添加商户:{plat.name} / {name},请对其执行\"首次登录\""

    def _refresh_merchant_badge(self, key):
        """商户数徽标跟着当前列表刷新(建好就不管的话, 加/删商户后数字是骗人的)。"""
        badge = self.merchant_badges.get(key)
        if badge is None:
            return
        try:
            badge.config(text=str(len(self.merchant_vars.get(key, {}))))
        except Exception:
            pass

    def _remove_merchant_row(self, key, name):
        """从界面上摘掉一家商户的那一行(目录由 delete_merchant_profile 负责)。"""
        row = self.merchant_rows.get(key, {}).pop(name, None)
        if row is not None:
            try:
                row.destroy()
            except Exception:
                pass
        self.merchant_vars.get(key, {}).pop(name, None)
        self.merchants = discover_merchants(self.platforms.keys())
        self._refresh_merchant_badge(key)
        self._refresh_summary()
        self._save_selection()

    def _prompt_delete_merchant(self, key, merchant):
        """删商户: 二次确认 → 删 profile 目录 → 摘掉那一行。已导出的账单不动。"""
        plat = self.platforms.get(key)
        if plat is None:
            return
        if self.running:
            messagebox.showwarning("删除商户",
                                   "任务正在导出, 请先等它跑完或点「中止」再删除。")
            return
        prof = merchant_profile_dir(BROWSER_DATA_DIR, plat.key, merchant)
        if not messagebox.askyesno(
                "删除商户",
                f"确定删除 {plat.name} / {merchant} 吗?\n\n"
                "会删掉这家商户的浏览器登录目录(以后要重新登录),\n"
                "已经导出到 downloads 的账单不受影响。\n\n"
                f"要删除的目录:\n{prof}"):
            return
        ok, msg = delete_merchant_profile(BROWSER_DATA_DIR, plat.key, merchant)
        if not ok:
            self._append_log(f"[删除商户失败] {plat.name} / {merchant}: {msg}")
            messagebox.showerror("删除商户", msg + "\n\n目录还在, 商户列表也没改动。")
            return
        self._remove_merchant_row(key, merchant)
        self._append_log(f"已删除商户:{plat.name} / {merchant}"
                         f"(如需重建, 点平台右侧\"+\");{msg}")

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
            ok, msg = self._add_merchant(key, entry.get())
            self._append_log(msg)
            if not ok:
                # 名字留着让用户改, 窗口不关
                messagebox.showwarning("添加商户", msg, parent=dlg)
                return
            dlg.destroy()

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

    def _do_login(self, tasks):
        # tasks 由主线程 _collect_tasks 备好, 这里只消费(工作线程不读界面)
        self._set_status("正在登录...")
        self._set_progress(maximum=len(tasks), value=0)
        for i, (key, plat, merchant) in enumerate(tasks):
            if self._aborted():
                self._append_log(f"[中止] 剩余 {len(tasks) - i} 项未登录")
                break
            self.set_platform_status(key, "warn")
            # 登录需要人工操作,始终显示浏览器窗口;每个商户独立 profile
            self._ensure_browser(plat, merchant, force_visible=True)
            self._append_log(f">>> 正在打开 {plat.name}({merchant}) 登录页面...")
            try:
                plat.login(self.browser)
                self._append_log(f"请在浏览器中完成 {plat.name}({merchant}) 登录")
                self._append_log(f"操作提示: {plat.guide}")
                # 弹窗由主线程弹出;后台线程阻塞等待用户点"确定"(期间浏览器保持打开)
                self._login_confirm.clear()
                self._ui(lambda n=plat.name, m=merchant, g=plat.guide:
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
                self.set_platform_status(key, "error")
            self._set_progress(value=i + 1)
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

    def _do_export(self, tasks, start_date, end_date, step_debug=False):
        """tasks/日期/单步开关均由主线程取好再传进来, 本函数跑在工作线程上。"""
        self._execute_export_tasks(tasks, start_date, end_date, label="导出",
                                   step_debug=step_debug)
        self._record_job_done("导出", start_date, end_date)

    def _execute_export_tasks(self, tasks, start_date, end_date, label="导出",
                              step_debug=False):
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
        self._set_progress(maximum=len(tasks), value=0)
        exported = manual = failed = 0
        manual_items = []
        worst = {}        # 一个平台只有一盏状态灯: 记它所有商户里最差的结果
        for i, (key, plat, merchant) in enumerate(tasks):
            if self._aborted():
                self._append_log(f"[中止] 剩余 {len(tasks) - i} 项未执行")
                break
            self._append_log(f">>> 正在导出 {plat.name}({merchant}) 流水...")
            self.set_platform_status(key, "warn")
            try:
                result = run_with_retry(
                    lambda k=key, p=plat, m=merchant: self._run_single_export(
                        p, m, start_date, end_date, step_debug=step_debug),
                    retry_times=retry_times,
                    retry_interval_s=retry_interval_s,
                    log=self._append_log,
                )
            except Exception as e:
                result = "failed"
                self._append_log(f"[失败] {plat.name}({merchant}) 导出异常: {e}")
            # 状态灯按"最差"亮: 同平台先失败后成功时, 绿灯会把失败那次盖掉, 界面上
            # 看着全绿、其实有一家店没出账单。
            prev = worst.get(key)
            rank = _RESULT_RANK.get(result, len(_RESULT_RANK))
            if prev is None or rank > _RESULT_RANK.get(prev, 0):
                worst[key] = result
            self._apply_export_result(key, plat, worst[key])
            self._append_log(f"[结果] {plat.name}({merchant}): {RESULT_LABEL.get(result, result)}")
            if result == "success":
                exported += 1
            elif result == "manual":
                manual += 1
                manual_items.append((plat.name, merchant, plat.guide))
            else:
                failed += 1
            self._set_progress(value=i + 1)
        self._append_log(f"导出流程完成: 成功{exported} / 手动{manual} / 失败{failed}")
        self._set_status(f"导出完成(成功{exported}/手动{manual}/失败{failed})")
        self._prompt_manual_leftovers(manual_items, date_str)
        return exported, manual, failed

    def _prompt_manual_leftovers(self, items, date_str):
        """把需要人工完成的平台一次性列出来。

        以前是每个平台一个 messagebox, 十个商户里五个要手动就会叠五个窗, 用户既看
        不全也不知道还差几个。
        """
        if not items:
            return
        lines = [f"日期范围: {date_str}", ""]
        lines += [f"· {name}({merchant}) —— {guide}" for name, merchant, guide in items]
        lines += ["", "可在左侧该平台/商户右侧点\"打开\"重新进入页面手动导出,"
                  "完成后文件同样会归到 downloads 目录。"]
        text = "\n".join(lines)
        self._ui(lambda: messagebox.showinfo(f"{len(items)} 个平台需要您手动导出", text))

    def _run_single_export(self, plat, merchant, start_date, end_date, step_debug=False):
        """单个商户导出(给 run_with_retry 调用): 返回 "success"/"manual"/"failed"。

        本函数不允许向外抛异常: run_with_retry 只对返回值 "failed" 重试, 抛出会被
        调用方的 except 折成"一次失败", 既不计入 stats 也跳过全部重试 —— 而"浏览器
        起不来"恰是最值得重试的那种失败。
        """
        date_str = f"{start_date} 至 {end_date}"
        self._append_log(f"日期范围: {date_str}")
        result = "failed"
        err_msg = ""
        t0 = time.time()   # 启动阶段就抛错时用它, 耗时记为约 0
        try:
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
                self._ui(_ask)
                evt.wait(timeout=3600)
            self.browser.set_user_wait_callback(_user_wait_cb)
            # 单步调试模式: 注入回调,平台脚本调用 step_pause() 时弹"继续/中止"
            if step_debug:
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
                    self._ui(_ask)
                    evt.wait(timeout=3600)
                    return bool(choice["v"])
                self.browser.set_step_debug(True, _step_cb)
            else:
                self.browser.set_step_debug(False)
            # 登录态预检: 失效则不再执行导出, 避免空等 90s 下载
            # 预检结果同样要落统计: 原来这里直接 return, 登录失效这一最主要的失败原因
            # 永远不进 stats.jsonl, 看板成功率因此虚高。
            t0 = time.time()   # 耗时口径与旧实现一致: 不含浏览器启动
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
                self._self_check_selectors(plat)
                result = plat.export(self.browser, start_date, end_date)
        except Exception as e:
            result = "failed"
            err_msg = str(e)[:200]
            self._append_log(f"[失败] {plat.name} 导出异常: {err_msg}")
        finally:
            # 导出结束关闭单步调试/用户等待回调,避免影响后续任务(浏览器可能没起来)
            if self.browser:
                try:
                    self.browser.set_step_debug(False)
                    self.browser.set_user_wait_callback(None)
                except Exception:
                    pass
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

    def _self_check_selectors(self, plat):
        """导出前校验平台声明的 SELECTORS, 页面改版时在日志里先指出缺哪个元素。

        只报警不拦截: 缺元素不代表这次导出一定失败; 自检自身出错更不能影响导出。
        没声明 SELECTORS 的平台直接跳过(目前只有微信支付声明了)。
        """
        if not getattr(plat, "SELECTORS", None):
            return
        try:
            ok, missing = plat.check_selectors(self.browser)
            if not ok:
                self._append_log(f"[自检] {plat.name} 页面缺少关键元素: {', '.join(missing)}")
        except Exception as e:
            self._append_log(f"[自检] {plat.name} 自检异常,跳过: {str(e)[:80]}")

    def _apply_export_result(self, key, plat, result):
        """只负责平台状态灯;需要人工完成的平台由 _prompt_manual_leftovers 一次性汇总。"""
        if result == "success":
            self.set_platform_status(key, "ok")
        elif result == "manual":
            self.set_platform_status(key, "warn")
        else:
            self.set_platform_status(key, "error")

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

    def _do_check(self, tasks):
        """tasks 由主线程 _collect_tasks 备好;本函数跑在工作线程上。"""
        self._set_status("正在检查登录状态...")
        self._set_progress(maximum=len(tasks), value=0)
        for i, (key, plat, merchant) in enumerate(tasks):
            if self._aborted():
                self._append_log(f"[中止] 剩余 {len(tasks) - i} 项未检查")
                break
            self._append_log(f"检查 {plat.name}({merchant})...")
            try:
                # 每个商户独立 profile 检查
                self._ensure_browser(plat, merchant)
                logged_in = plat.check_login(self.browser)
                if not logged_in:
                    self._append_log(f"  {plat.name}({merchant}): 未登录")
                    self.set_platform_status(key, "error")
                else:
                    info = self.browser.get_page_info()
                    self._append_log(f"  {plat.name}({merchant}): 已登录 ({info.get('title', '')[:20]})")
                    self.set_platform_status(key, "ok")
            except Exception as e:
                self._append_log(f"  {plat.name}({merchant}): 检查失败 - {e}")
                self.set_platform_status(key, "error")
            self._set_progress(value=i + 1)
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


def report_crash(summary, detail=""):
    """崩溃兜底: 先写日志, 再弹窗。返回值 = 日志到底写没写成。

    这里以前写的是 `from logger import log`, 而根目录并没有 logger.py, 这句必然
    ModuleNotFoundError 又被自己的 except 吞掉 —— 弹窗却告诉用户"详细信息已记录到
    logs 文件夹"。日志通道坏掉时不能再这么讲。
    """
    logged = True
    try:
        log(f"程序运行异常: {summary}" + (f"\n{detail}" if detail else ""),
            level="error")
    except Exception:
        logged = False
    try:
        err_root = tk.Tk()
        err_root.withdraw()
        tail = ("详细信息已记录到 logs 文件夹。" if logged else
                "日志没能写成功, 请把上面这段信息截图发给维护人员。")
        messagebox.showerror(
            "程序出错",
            f"程序发生异常,请把以下信息反馈给维护人员:\n\n{summary}\n\n{tail}")
        err_root.destroy()
    except Exception:
        pass
    return logged


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
        report_crash(str(e), traceback.format_exc())


if __name__ == "__main__":
    main()
