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
import subprocess
import threading
import time
import tkinter as tk
from collections import namedtuple
from datetime import datetime, timedelta
from tkinter import ttk, messagebox, scrolledtext, filedialog
from core import deps        # 启动前的依赖体检(缺包/版本不对/没内核分开说)
from core.config import (DOWNLOAD_DIR, BROWSER_DATA_DIR, DEFAULT_SETTINGS,
                         SELECTION_FILE, load_settings, save_settings,
                         write_json_atomic)
from core import config as workspace        # 工作空间: 解析结果/更换入口都从这里走
from core import submerchants               # 子商户清单与归属比对(银联这类一次登录导多份)
from core import update as updater          # 检查更新/就地更新(只换程序文件)
from core.version import APP_VERSION
from core.logger import log, list_history_logs, LOG_DIR, record_stat, load_stats, summarize_stats
from core import logger as logger_module    # LOGGER_NOTES: 日志目录退到临时目录时的说明
from core.loader import discover_platforms, reload_platforms
from core.outputs import copy_to_summary_dir, sanitize_name
from core.cleanup import describe as describe_cleanup, run_cleanup
from core.keepalive import KeepAliveService
from core.theme import (BG_MAIN, BG_PANEL, BG_BUTTON, BG_BUTTON_HOVER,
                        BG_SUCCESS, BG_WARN, BG_ERROR, BG_LOG, FG_LOG,
                        FG_MAIN, FG_MUTED)
from core.dialogs import (show_log_history, show_stats_dashboard,
                          show_help, build_help_text)
from core.scheduler import pair_job_targets


# Chromium 在 profile 根目录里写的顶层条目名(小写比对, 前缀命中也算)。
CHROMIUM_INTERNAL_DIRS = frozenset({
    "default", "crashpad", "crashdumps", "browsermetrics", "gpupersistentcache",
    "grshadercache", "shadercache", "safe browsing", "component_crx_cache",
    "extensions_crx_cache", "segmentation_platform", "optimization_guide",
    "optimizationhints", "subresource filter", "crowd deny", "certificate revocation",
    "file type policies", "hybrid certification", "meipreload", "origin trials",
    "pki metadata", "ssl error assistant", "trust token key commitments", "zxcvbn data",
    "autofill states", "amount extraction heuristic regexes", "captcha providers",
    "first party sets preloaded", "navigation throttling", "wdm driver assembly",
    "deferred chat images", "nasdfailures",
})
CHROMIUM_INTERNAL_PREFIXES = ("translation_model_", "translation_models_")
# profile 根一定有这几个文件之一 —— 用来认出"这个平台目录被当成过 profile 根"(旧版本的脏)
PROFILE_ROOT_MARKERS = ("Local State", "Last Version")


def _is_profile_root(path):
    return any(os.path.isfile(os.path.join(path, m)) for m in PROFILE_ROOT_MARKERS)


def _looks_like_chromium_internals(path, parent_is_profile_root=False):
    """这个目录是 Chromium 自己的内部目录吗(而不是某家商户的 profile)。

    **只在父目录被判定"当过 profile 根"时才认** —— 商户目录允许用户手工建、手工拷
    (使用说明里就教过搬登录态), 平白无故不该怀疑他的目录; 而只有旧版本把空商户的 profile
    落在 `browser_data/<平台key>/` 上, 才会在那一层混进浏览器的内部目录。

    认的时候先内容后名字: 商户的 profile 根下面一定有 `Default/` 或我们写的
    `login_state.json`, 内部目录没有 —— 所以哪怕真有商户起名叫 "Default" 也不会被误认;
    名字名单只补一个洞, `Safe Browsing` 这类内部目录常是**空的**, 而"空"又正好是刚点
    「+」建好还没登录的商户目录的样子, 在已经脏了的平台目录里只能按名单认。
    """
    if not parent_is_profile_root:
        return False
    try:
        from core.browser import BrowserManager       # 与 main_gui 一样保持惰性导入
        if os.path.isfile(os.path.join(path, BrowserManager.LOGIN_STATE_FILE)):
            return False
        if os.path.isdir(os.path.join(path, "Default")):
            return False
        low = os.path.basename(path).casefold()
        if low in CHROMIUM_INTERNAL_DIRS or low.startswith(CHROMIUM_INTERNAL_PREFIXES):
            return True
        return bool(os.listdir(path))
    except OSError:
        return False


def discover_merchants(platform_keys, ignored=None):
    """扫描 browser_data/平台key/ 下的子目录,发现已建档的商户
    返回: {platform_key: [商户名, ...]}

    两类不算商户:
      - 程序保留名(`_平台调试`/`_未指定平台`, 见 `BrowserManager.set_browser_profile`);
      - Chromium 的内部目录 —— 但**只在这个平台目录本身被当成过 profile 根时才认**
        (顶层有 `Local State`/`Last Version`)。来历: 以前商户为空时 profile 直接落在
        `browser_data/<key>/`, 于是这一层同时是"商户的父目录"和某个 profile 的根,
        内部目录被当成商户列进左栏还被自动勾上(实测本机 youzan 下 10 个"商户"里只有
        1 个是真的, `selection_state.json` 里那 9 项全是 true)。没被污染过的平台目录
        一律按"子目录=商户"处理, 手工拷进来的半截登录态目录也不会被吞。

    ⚠ 不按"下划线开头"整批过滤: 商户目录也允许用户手工建, 按前缀筛会把人家的目录藏掉。
    `ignored` 传一个 list 时被剔掉的名字按 "平台key/目录名" 塞进去 —— 剔除可以, 静默
    剔除不行(目录突然不见了得能查为什么)。
    """
    from core.browser import BrowserManager
    reserved = {BrowserManager.RESERVED_MERCHANT_DIR, BrowserManager.RESERVED_PLATFORM_DIR}
    result = {}
    base = os.path.abspath(BROWSER_DATA_DIR)
    try:
        if os.path.isdir(base):
            for k in platform_keys:
                pk = os.path.join(base, k)
                if not os.path.isdir(pk):
                    continue
                polluted = _is_profile_root(pk)
                names = []
                for d in sorted(os.listdir(pk)):
                    full = os.path.join(pk, d)
                    if not os.path.isdir(full) or d.startswith("."):
                        continue
                    if d in reserved:
                        continue
                    if _looks_like_chromium_internals(full, polluted):
                        if ignored is not None:
                            ignored.append(f"{k}/{d}")
                        continue
                    names.append(d)
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


_RETRY_SLICE_S = 1.0      # 重试前的等待分多长一片看一眼「中止」


def _sleep_or_aborted(seconds, aborted, slice_s=_RETRY_SLICE_S):
    """分段睡 `seconds`, 每片醒来问一次 `aborted()`; 被打断返回 False(=不该再继续)。

    没传 aborted 时就是一句 `time.sleep`, 与从前逐字一致。第 12 章第 14 条说"中止只在
    边界生效、不许中途掐浏览器"—— 这里等的正是两次尝试之间的边界, 掐掉等待不违反它。
    """
    if aborted is None:
        time.sleep(max(0, seconds))
        return True
    remaining = max(0, seconds)
    while remaining > 0:
        nap = min(slice_s, remaining)
        time.sleep(nap)
        remaining -= nap
        if aborted():
            return False
    return True


def run_with_retry(fn, retry_times=0, retry_interval_s=30, log=None, aborted=None):
    """通用重试: fn 返回非 "failed" 视为成功; 失败按 retry_times 重试, 间隔递增(×2)。
    retry_times=0 表示失败一次即返回, 不重试。

    `aborted` 是无参可调用(界面传 `self._aborted`)。**以前这里完全不看中止**:
    实测按下「中止」后这一家仍会把默认的 2 次重试跑满, 中间还要睡 30 + 60 = 90 秒,
    界面上"正在中止…"就那么挂着几分钟, 剩下的商户要等这一整轮走完才 break。
    两处都看: 决定重试之前看一次, 等待本身分段睡、每片再看一次。
    """
    interval = max(0, int(retry_interval_s))
    result = fn()
    attempt = 0
    while result == "failed" and attempt < retry_times:
        if aborted is not None and aborted():
            if log:
                log("  [中止] 已请求中止, 这一家不再重试")
            return result
        attempt += 1
        wait = interval * (2 ** (attempt - 1))
        if log:
            log(f"  [重试] 第 {attempt}/{retry_times} 次重试({wait}s 后)")
        if not _sleep_or_aborted(wait, aborted):
            if log:
                log("  [中止] 等待重试期间收到中止请求, 这一家不再重试")
            return result
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
        self._ignored_profile_dirs = []
        self.merchants = discover_merchants(self.platforms.keys(),
                                            ignored=self._ignored_profile_dirs)
        self.selection = self._load_selection()   # 上次勾选状态(重启后恢复)
        self._login_hint = None        # 首次登录的非模态提示窗(登录完关掉浏览器窗口即算完成)
        self._abort = threading.Event()          # 中止请求: 在任务边界生效(见 _aborted)

        self._setup_window()
        # 构建顺序: 右日志 → 左平台 → 中间(设置+历史+操作按钮) → 初始化概览
        self._build_right_panel()
        self._build_left_panel()
        self._build_middle_panel()
        self._refresh_summary()   # 初始化概览条
        self._report_paths()      # 工作空间在哪 / 解析与日志目录有没有兜底, 一次说清
        self._note_ignored_profile_dirs("启动")
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

        # 启动后悄悄查一次有没有新版: 只往日志写一行, 不弹窗、不挡导出(内网查不到是常态)
        self._start_update_check()

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
        # 版本号以前写死成 v1.0, 而发布用的其实是 vportable_0.0.x —— 用户报问题时看不出
        # 自己手上是哪一版。现在跟着 core/version.APP_VERSION 走(CI 打 tag 时核一致)。
        self.root.title(f"流水自动导出工具 v{APP_VERSION}")
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
        ignored = []
        self.merchants = discover_merchants(self.platforms.keys(), ignored=ignored)
        self._ignored_profile_dirs = ignored
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

    def _note_ignored_profile_dirs(self, where="刷新商户"):
        """被剔掉的"浏览器内部目录"要说一声。

        静默剔不行: 用户手工放过东西进 `browser_data` 的话, 会发现"目录明明在, 界面上
        却没有", 只能靠这行日志对上号。剔的东西也不是商户, 是老版本把「平台级」浏览器
        数据直接放在 `browser_data/平台/` 这一层留下的 Chromium 内部目录(不影响账单,
        也不影响任何商户的登录态)。
        """
        ignored = getattr(self, "_ignored_profile_dirs", None) or []
        if not ignored:
            return
        shown = "、".join(ignored[:8]) + ("…" if len(ignored) > 8 else "")
        self._append_log(f"[{where}] 已忽略 {len(ignored)} 个浏览器自己的目录, "
                         f"它们不算商户: {shown}")

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
        self._note_ignored_profile_dirs("刷新商户")
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
            lambda *_: self._save_setting(show_browser=bool(self.show_browser_var.get())))
        tk.Checkbutton(opt_row, variable=self.show_browser_var, text="显示浏览器",
                       bg=BG_PANEL, fg=FG_MUTED, font=("Microsoft YaHei", 9),
                       activebackground=BG_PANEL).pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(opt_row, text="文件名:", bg=BG_PANEL, fg=FG_MUTED,
                 font=("Microsoft YaHei", 9)).pack(side=tk.LEFT)
        self.name_mode_var = tk.StringVar(
            value=self.settings.get("download_name_mode", "unified"))
        self.name_mode_var.trace_add(
            "write",
            lambda *_: self._save_setting(download_name_mode=self.name_mode_var.get()))
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

        # 导出前登录预检(把要人操作的事集中到开跑前, 见 _preflight_login_check)
        pre_row = tk.Frame(middle, bg=BG_PANEL)
        pre_row.pack(fill=tk.X, pady=(2, 0))
        self.preflight_var = tk.BooleanVar(
            value=bool(self.settings.get("preflight_login_check",
                                         DEFAULT_SETTINGS.get("preflight_login_check", True))))
        self.preflight_var.trace_add(
            "write",
            lambda *_: self._save_setting(preflight_login_check=bool(self.preflight_var.get())))
        tk.Checkbutton(pre_row, variable=self.preflight_var, text="导出前检查登录",
                       bg=BG_PANEL, fg=FG_MUTED, font=("Microsoft YaHei", 9),
                       activebackground=BG_PANEL).pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(pre_row, text="(失效的一次列出, 集中登录后再开导)", bg=BG_PANEL,
                 fg="#95a5a6", font=("Microsoft YaHei", 8)).pack(side=tk.LEFT)

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
        bar.grid_rowconfigure(5, weight=1)

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
            ("工作空间", self._action_workspace, "#34495e", 4, 1),
            ("检查更新", self._action_check_update, "#7f8c8d", 5, 0),
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
        self.abort_btn.grid(row=2, column=2, rowspan=4, padx=3, pady=3, sticky="nsew")

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
        # 只有声明支持子商户的平台才给这个入口, 否则点了只会得到一句"没用"
        plat = self.platforms.get(key)
        if plat is not None and getattr(plat, "supports_sub_merchants", False):
            n = len(submerchants.get(key, name)[0])
            tk.Button(mrow, text=(f"子商户 {n}" if n else "子商户"), width=7, relief=tk.FLAT,
                      fg="#ffffff", bg="#8e44ad", cursor="hand2",
                      font=("Microsoft YaHei", 8),
                      command=lambda k=key, m=name: self._prompt_sub_merchants(k, m)
                      ).pack(side=tk.RIGHT, padx=(4, 2))

    def _prompt_sub_merchants(self, key, merchant):
        """录入这家商户名下的子商户: 一行一个, 导出时按这个顺序逐个切过去导一份。

        刻意不做勾选树 —— 想少导几家就把那几行删掉, 比再维护一套"选了但没导"的状态清楚。
        """
        plat = self.platforms.get(key)
        subs, note = submerchants.get(key, merchant)
        win = tk.Toplevel(self.root)
        win.title(f"{getattr(plat, 'name', key)} · {merchant} —— 子商户")
        win.configure(bg=BG_MAIN)
        tip = ("一次登录里按下面的顺序逐个切换子商户并各导一份, 成品会多一层子商户目录。\n"
               "一行一个子商户(商户号或商户名都行); 留空保存 = 取消子商户, 这家按普通商户导。\n"
               "每次切换后都会核对页面上当前是哪个商户, 对不上就停手转人工。")
        if note:
            tip += f"\n⚠ {note}"
        tk.Label(win, text=tip, bg=BG_MAIN, fg=FG_MUTED, justify=tk.LEFT,
                 font=("Microsoft YaHei", 9)).pack(anchor="w", padx=10, pady=(10, 4))
        box = tk.Text(win, width=52, height=8, font=("Consolas", 10))
        box.pack(fill=tk.BOTH, expand=True, padx=10)
        box.insert("1.0", "\n".join(subs))
        row = tk.Frame(win, bg=BG_MAIN)
        row.pack(fill=tk.X, padx=10, pady=10)

        def _save():
            raw = box.get("1.0", tk.END)
            ok, cleaned, why = submerchants.save_one(key, merchant, raw)
            if not ok:
                messagebox.showerror("没能保存", why, parent=win)
                return
            win.destroy()
            self._rebuild_platform_list()      # 按钮上的数量要跟着变
            self._append_log(f"[子商户] {getattr(plat, 'name', key)}({merchant}): "
                             f"已保存 {len(cleaned)} 个"
                             + ("" if cleaned else "(已清空, 这一家按普通商户导出)"))

        tk.Button(row, text="保存", command=_save, bg="#27ae60", fg="#ffffff",
                  relief=tk.FLAT, width=8).pack(side=tk.RIGHT, padx=(6, 0))
        tk.Button(row, text="取消", command=win.destroy, bg="#95a5a6", fg="#ffffff",
                  relief=tk.FLAT, width=8).pack(side=tk.RIGHT)
        if plat is not None and not plat.verifies_sub_merchant_identity():
            tk.Label(win, text="提示: 这个平台的脚本还没实现「读回当前子商户」, "
                               "导出时归属不会被校验。",
                     bg=BG_MAIN, fg="#c0392b", justify=tk.LEFT,
                     font=("Microsoft YaHei", 9)).pack(anchor="w", padx=10, pady=(0, 8))
        win.transient(self.root)
        win.grab_set()

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
        self._save_setting(enable_keepalive=enabled, keepalive_interval_min=interval)
        self.keepalive.enabled = enabled
        self.keepalive.interval_min = interval

    def _save_setting(self, **kv):
        """界面上每一次设置改动都走这里: 没落盘就在日志窗里说一声。

        `config.save_settings` 只写文件日志(它不认识界面), 而用户看的是这个窗口 ——
        点一下勾而 settings.json 正被云盘/Excel 占住时, 这里是唯一的出口。
        (以前是 `except: pass`: 界面显示已改、盘上没改、重启回旧值, 全程零提示。)
        """
        if save_settings(kv):
            return True
        self._append_log(f"[设置] 没能保存 {', '.join(kv.keys())}: settings.json 写不进去"
                         "(多半被云盘同步或其它程序占用), 重启后会回到原值")
        return False

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
        self._save_setting(retry_times=retry_times)
        self.settings["retry_times"] = retry_times

    def _on_cleanup_setting(self, *_):
        """保留天数改动即时保存(0=不清理); 读不到合理数字时退回默认值。"""
        default = int(DEFAULT_SETTINGS.get("cleanup_keep_days", 365))
        try:
            days = int(str(self.cleanup_days.get()).strip())
        except Exception:
            days = default
        days = max(0, min(3650, days))
        self._save_setting(cleanup_keep_days=days)
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

    def _ensure_browser(self, plat=None, merchant="", force_visible=False,
                        force_headless=False):
        """启动浏览器,并按 平台/商户 设置独立数据目录(登录态隔离)。
        force_visible=True(登录流程)时始终显示窗口;
        force_headless=True(登录后的核实)时始终不显示, 免得多弹一个窗口闪一下;
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
        show = (not force_headless) and (
            force_visible or bool(load_settings().get("show_browser", True)))
        self.browser = BrowserManager(headless=not show, log_callback=self._append_log)
        if plat is not None:
            self.browser.set_browser_profile(plat.key, merchant)
        self.browser.start()

    def _close_browser(self):
        """关掉当前浏览器实例并置空。

        `BrowserManager.close()` 内部会先导出登录态再关(Chromium 一退 session cookie
        就没了), 所以"该存的"都在这里落盘。关失败只说一声, 不影响下一次任务。
        """
        if not self.browser:
            return
        try:
            self.browser.close()
        except Exception as e:
            self._append_log(f"[提示] 关闭浏览器时出错(不影响下一次任务): {str(e)[:80]}")
        self.browser = None

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
        preflight = bool(self.preflight_var.get())
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
        self._run_async(lambda: self._do_export(tasks, start_date, end_date,
                                                step_debug, preflight))

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

    # ===== 检查更新(只换程序文件, 闸门都在 core/update.py) =====

    def _action_check_update(self):
        """「检查更新」: 先查, 确实有新版才问, 点头才动手。

        查询这一步不占 running —— 网络慢 6 秒不该把导出挡在门外; 真要覆盖文件了才交给
        _run_async, 那时导出与定时任务都会自动让路(同一时间只该有一件事在改程序目录)。
        """
        threading.Thread(target=self._update_check_worker, args=(True,), daemon=True).start()

    def _start_update_check(self):
        """启动后后台查一次: 只往日志写一行, 绝不弹窗 —— 内网机器每次启动弹一次是骚扰。"""
        threading.Thread(target=self._update_check_worker, args=(False,), daemon=True).start()

    def _update_check_worker(self, manual):
        """子线程: 只碰网络, 所有弹窗都投递回主线程(在非主线程拉 Tk 窗口可能把界面吊住)。"""
        try:
            release, why = updater.fetch_latest()
        except Exception as e:            # fetch_latest 自己已经折过异常, 这是第二道保险
            self._append_log(f"[更新] 检查没跑成({str(e)[:60]}), 不影响使用")
            if manual:                    # 用户点了按钮就得有回话, 不能只默默写日志
                self._ui(lambda: messagebox.showwarning(
                    "检查更新", "检查更新这一步自己出错了:\n\n%s\n\n程序一切照常。" % str(e)[:200]))
            return
        if release is None:
            self._append_log(f"[更新] 没查到新版本({why[:70]}), 不影响继续使用")
            if manual:
                self._ui(lambda: messagebox.showwarning(
                    "检查更新", "没能查到新版本:\n\n%s\n\n程序一切照常, 这一条只是问一句。" % why))
            return
        if not updater.is_newer(release):
            self._append_log(f"[更新] 已是最新版({release.tag})")
            if manual:
                self._ui(lambda: messagebox.showinfo(
                    "检查更新", "已经是最新版了(%s)。" % release.tag))
            return
        self._append_log(f"[更新] 发现新版本 {release.tag}, 当前 v{APP_VERSION}; "
                         f"发布页 {release.page_url}")
        if manual:
            self._ui(lambda: self._ask_apply_update(release))
        else:
            self._append_log("[更新] 要升级就点「检查更新」: 只换程序文件, 账单与登录态都不碰")

    def _ask_apply_update(self, release):
        """主线程: 问一句。答"是"之后才开始改文件。"""
        if self.running:
            messagebox.showwarning("提示", "已有任务在跑, 等它结束再点「检查更新」。")
            return
        if not messagebox.askyesno(
                "发现新版本",
                "最新版 %s, 当前 v%s。\n\n要现在下载并替换程序文件吗?\n"
                "  · 只换 core/ platforms/ tools/ 和说明文档, 账单、登录态、日志都不碰\n"
                "  · 被替换的旧文件先备份到 更新目录/backup/\n"
                "  · 换完要重启工具才生效\n\n发布页: %s"
                % (release.tag, APP_VERSION, release.page_url)):
            self._append_log("[更新] 用户选择暂不更新")
            return
        if not self._run_async(lambda: self._update_apply_worker(release)):
            self._append_log("[更新] 刚好有任务在跑, 这次没更新")

    def _update_apply_worker(self, release):
        """任务线程: 下载 → 三道闸门 → 覆盖。每一步失败都保持原样并把话说清。"""
        path, why = updater.download(release)
        if not path:
            self._notify_update_failed(why)
            return
        self._append_log(f"[更新] 核心包已下到 {path}")
        ok, why = updater.dependency_gate(path)
        if not ok:
            self._park_package(path, why)
            return
        ok, why = updater.program_writable()
        if not ok:
            self._park_package(path, "程序目录不能写入(%s)" % why)
            return
        done, msg = updater.apply_update(path, log=self._append_log)
        if not done:
            self._notify_update_failed(msg)
            return
        self._append_log(f"[更新] {msg}")
        self._ui(lambda: messagebox.showinfo(
            "更新完成", msg + "\n\n现在关掉这个窗口再重开, 用的就是新版本。"))

    def _notify_update_failed(self, msg):
        self._append_log(f"[更新] {msg}")
        self._ui(lambda: messagebox.showerror("更新没做成", msg + "\n\n现有程序一个字没改。"))

    def _park_package(self, path, why):
        """闸门没过: 一个字不改, 但把已经下好的包留在原地并说清在哪。"""
        self._append_log(f"[更新] 没有更新: {why}(包留在 {path})")
        self._ui(lambda: messagebox.showwarning(
            "没有更新", "%s\n\n下载好的包留在:\n%s" % (why, path)))

    def _report_paths(self):
        """启动时把"东西存哪儿"和两处兜底一次说清。

        工作空间与程序目录分开后, 最常见的疑问变成"我的账单去哪了"; 而书签指向的盘没了、
        日志目录退到临时目录这两件事若不在界面上说一声, 用户只会以为程序坏了。
        """
        self._append_log(workspace.describe_data_root())
        if os.path.abspath(workspace.DATA_ROOT) != os.path.abspath(workspace.ROOT_DIR):
            self._append_log(f"程序目录(只读即可): {workspace.ROOT_DIR}")
        for note in list(workspace.RESOLVE_NOTES) + list(logger_module.LOGGER_NOTES):
            self._append_log(f"[提示] {note}")

    def _open_workspace_dir(self):
        try:
            os.makedirs(workspace.DATA_ROOT, exist_ok=True)
            os.startfile(workspace.DATA_ROOT)
        except Exception as e:
            messagebox.showwarning("打不开", f"打开工作空间文件夹失败: {e}")

    def _change_workspace(self):
        """换工作空间 = 换以后所有产出写去哪; 因为各模块取的是常量快照, 只能重开一次。"""
        path = filedialog.askdirectory(title="选择产出工作空间(账单/登录数据放这里)",
                                       initialdir=workspace.DATA_ROOT)
        if not path:
            return
        path = os.path.abspath(path)
        if path == os.path.abspath(workspace.DATA_ROOT):
            messagebox.showinfo("不用换", "选的就是当前正在用的工作空间。")
            return
        if not messagebox.askyesno(
                "确认更换工作空间",
                f"以后导出会写到:\n\n{path}\n\n"
                "程序会重启一次。原来工作空间里已经导出的账单不会搬走。"):
            return
        ok, msg = workspace.apply_data_root(path)
        if not ok:
            messagebox.showwarning("这个目录不能用", f"{msg}\n\n换一个位置再试"
                                                     "(只读盘、被别的程序占着的目录不行)。")
            return
        relaunch_for_workspace(msg)

    def _action_workspace(self):
        """「工作空间」按钮: 看当前路径 / 打开它 / 换到别的文件夹。"""
        win = tk.Toplevel(self.root)
        win.title("工作空间")
        win.geometry("540x200")
        win.configure(bg=BG_PANEL)
        win.transient(self.root)
        win.grab_set()
        tk.Label(win, text="账单、各商户登录数据、日志都存放在这里", bg=BG_PANEL,
                 fg=FG_MAIN, font=("Microsoft YaHei", 11, "bold")).pack(pady=(16, 4))
        tk.Label(win, text=workspace.DATA_ROOT, bg=BG_PANEL, fg="#2d6cdf",
                 font=("Microsoft YaHei", 10), wraplength=500, justify="left"
                 ).pack(pady=(2, 6))
        if os.path.abspath(workspace.DATA_ROOT) == os.path.abspath(workspace.ROOT_DIR):
            tk.Label(win, text="(与程序同一个目录 = 就地模式; 想分开就换到别处)", bg=BG_PANEL,
                     fg=FG_MUTED, font=("Microsoft YaHei", 9)).pack()
        row = tk.Frame(win, bg=BG_PANEL)
        row.pack(pady=14)
        tk.Button(row, text="打开这个文件夹", command=lambda: (self._open_workspace_dir(), win.destroy()),
                  font=("Microsoft YaHei", 10), relief=tk.FLAT, width=14).pack(side=tk.LEFT, padx=6)
        tk.Button(row, text="换到别的文件夹…", command=lambda: (win.destroy(), self._change_workspace()),
                  font=("Microsoft YaHei", 10), relief=tk.FLAT, width=16).pack(side=tk.LEFT, padx=6)
        tk.Button(row, text="关闭", command=win.destroy,
                  font=("Microsoft YaHei", 10), relief=tk.FLAT, width=8).pack(side=tk.LEFT, padx=6)

    def _action_open_merchant(self, key, merchant):
        """打开指定商户的浏览器窗口(已恢复登录态),供手工测试页面元素,操作完手工关闭即可。"""
        plat = self.platforms.get(key)
        if plat is None:
            return
        self._append_log(f">>> 正在打开 {plat.name}({merchant}) 浏览器窗口(手工测试用)...")
        self._run_async(lambda: self._open_merchant_browser(plat, merchant))

    MERCHANT_PROBE_TIMEOUT_S = 30 * 60   # 手工测试窗口最长占用时间

    # 首次登录: 等人自己关掉浏览器窗口, 最长等 30 分钟(扫码/短信/企业认证都够), 2 秒一轮
    LOGIN_WAIT_TIMEOUT_S = 30 * 60
    LOGIN_POLL_S = 2.0

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
        """打开「使用说明」窗口。

        以前这里是一整串 messagebox.showinfo 参数: 那种框不能滚动, 长了只看得见开头几行,
        而界面上十来个按钮(检查更新/子商户/定时任务/录制→脚本/稳定性看板/工作空间…)
        压根没写进去。文案抽到 `dialogs.build_help_text`(纯函数), 窗口只负责显示,
        改流程时 tests/test_action_help_text.py 盯着文案别退回旧说法。
        """
        show_help(self.root,
                  build_help_text(version=APP_VERSION, data_root=workspace.DATA_ROOT),
                  on_open_manual=self._open_manual_doc)

    def _open_manual_doc(self):
        """打开随包的《使用说明.md》; 打不开就在日志里说清它在哪, 不许什么都不说。"""
        path = os.path.join(workspace.ROOT_DIR, "使用说明.md")
        try:
            os.startfile(path)
        except Exception as e:
            self._append_log(f"[说明] 没能打开完整使用说明({str(e)[:60]}), 文件在: {path}")

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

    def _show_login_hint(self, plat_name, merchant, guide):
        """主线程弹一个**不挡事**的提示窗: 登录完直接关掉浏览器窗口, 不用回来点确定。

        用 Toplevel 而不是 messagebox: 模态框又要把人拽回来点一下, 正是这次要去掉的
        动作。窗口只提示, 不参与流程。
        """
        try:
            win = tk.Toplevel(self.root)
            win.title("请登录")
            win.configure(bg=BG_PANEL)
            win.geometry("440x190")
            win.attributes("-topmost", True)
            tk.Label(win, text=f"{plat_name} · {merchant}", bg=BG_PANEL, fg=FG_MAIN,
                     font=("Microsoft YaHei", 11, "bold")).pack(pady=(14, 2))
            tk.Label(win, text="请在已经打开的浏览器窗口里完成登录。\n\n"
                               "登录好之后, 直接把那个浏览器窗口关掉就行 ——\n"
                               "程序会自动保存登录态并接着做下一个, 不用回来点确定。",
                     bg=BG_PANEL, fg=FG_MAIN, justify="left",
                     font=("Microsoft YaHei", 10)).pack(padx=16, pady=6)
            tk.Label(win, text=f"操作提示: {guide}", bg=BG_PANEL, fg=FG_MUTED,
                     wraplength=400, justify="left",
                     font=("Microsoft YaHei", 9)).pack(padx=16, pady=(0, 12))
            self._login_hint = win
        except Exception as e:
            # 提示窗弹不出来不影响登录(浏览器窗口本身就是登录页), 但要说一声
            self._append_log(f"[提示] 登录提示窗没弹出来: {e}")

    def _close_login_hint(self):
        win = self._login_hint
        self._login_hint = None
        if win is None:
            return
        try:
            win.destroy()
        except Exception:
            pass

    def _wait_login_window_closed(self, browser):
        """等用户自己关掉登录窗口; 期间 cookie 一变就顺手导出登录态。

        为什么边等边存: persistent context 在 Chromium 退出时**不**保留 session cookie
        (微信支付这类就是靠它), 而"窗口被关掉"这个信号到达时浏览器已经没了, 再调
        storage_state 也来不及。所以每次检测到 cookie 有写入就先导出一次。

        返回 (结果, 是否导出过登录态), 结果: closed / aborted / timeout。
        """
        last_sig = None
        saved = False
        deadline = time.time() + self.LOGIN_WAIT_TIMEOUT_S
        while time.time() < deadline:
            if self._aborted():
                return "aborted", saved
            if browser.window_closed():
                return "closed", saved
            sig = browser.login_signature()
            if sig is not None:
                # 第一次只记基线: 老 profile 本来就带 cookie, 不该一上来就"已保存"
                if last_sig is not None and sig != last_sig and \
                        browser.save_login_state(quiet=True):
                    saved = True
                    self._append_log("  检测到登录写入, 已保存登录态 —— "
                                     "确认登好了就把浏览器窗口关掉")
                last_sig = sig
            time.sleep(self.LOGIN_POLL_S)
        return "timeout", saved

    def _verify_login_after_close(self, plat, key, merchant, saved_seen):
        """用户关掉登录窗口后, 用同一 profile 后台重开浏览器**真的**核实一次。

        只看 cookie 变化不足以说明登录成功(可能只是页面种了个埋点 cookie), 而"能不能
        打开后台"这件事只有 `check_login` 说得清 —— 此时用户已经不在那个窗口里了, 导航
        走不会打扰任何人。这里只报结果, **要不要再给用户一次机会由调用方问**
        (`_ask_login_retry`)。返回 True=已登录 / False=未登录 / None=没能核实。
        """
        self._set_status(f"正在核实 {plat.name}({merchant}) 的登录状态...")
        try:
            self._ensure_browser(plat, merchant, force_headless=True)
            logged_in = bool(plat.check_login(self.browser))
        except Exception as e:
            self._append_log(f"  [核实] {plat.name}({merchant}) 没能核实"
                             f"(不影响其它平台): {str(e)[:80]}")
            self.set_platform_status(key, "warn")
            return None
        if logged_in:
            extra = "" if saved_seen else "(等待期间没看到 cookie 变化, 靠的是 profile 里已有的登录态)"
            self._append_log(f"  {plat.name}({merchant}) 已核实登录成功{extra}")
            self.set_platform_status(key, "ok")
            return True
        why = ("cookie 有写入但页面仍未登录(可能登录被服务端拒绝或还没走完)"
               if saved_seen else "关窗口时没检测到任何登录写入")
        self._append_log(f"  [警告] {plat.name}({merchant}) 核实结果: 未登录 —— {why}")
        self.set_platform_status(key, "error")
        self._last_verify_reason = why
        return False

    LOGIN_RETRY_LIMIT = 3      # 每家商户总共给几次机会(含第一次), 防止无限回圈

    def _login_one_round(self, plat, key, merchant):
        """一轮完整的登录: 开可见浏览器 → 等用户关窗 → 释放 profile → 后台核实。

        返回 `_verify_login_after_close` 的 True/False/None, 或 "skip" 表示这一轮没
        走到核实(用户没关窗口/被中止/登录页就没打开)。
        """
        self.set_platform_status(key, "warn")
        self._ensure_browser(plat, merchant, force_visible=True)
        self._append_log(f">>> 正在打开 {plat.name}({merchant}) 登录页面...")
        try:
            plat.login(self.browser)
            self._append_log(f"请在浏览器中完成 {plat.name}({merchant}) 登录")
            self._append_log(f"操作提示: {plat.guide}")
            self._append_log("  登录好之后直接关掉那个浏览器窗口即可, "
                             "程序会自动保存并继续下一个(不用回来点确定)")
            self._ui(lambda n=plat.name, m=merchant, g=plat.guide:
                     self._show_login_hint(n, m, g))
            outcome, saved = self._wait_login_window_closed(self.browser)
            self._ui(self._close_login_hint)
            if outcome != "closed":
                self._append_log(
                    f"[提示] {plat.name}({merchant}) {'已中止' if outcome == 'aborted' else '等待超时'}"
                    f", 本轮不核实")
                return "skip"
            # 先释放这个 profile(Chromium 同时只允许一个进程占用), 才能后台重开核实
            self._close_browser()
            return self._verify_login_after_close(plat, key, merchant, saved)
        except Exception as e:
            self._append_log(f"打开 {plat.name}({merchant}) 失败: {e}")
            self.set_platform_status(key, "error")
            return "skip"
        finally:
            self._close_browser()

    def _ask_login_retry(self, plat, merchant, attempt):
        """未登录时让用户二选一: 回去继续登录 / 确认放弃这家。

        用 `askretrycancel`(按钮上就是"重试/取消"), 并写明各自对应什么 —— 有些平台
        (微信支付那类扫码页与后台同域的)只能靠页面文案判断, 存在"其实登上了但被判未
        登录"的可能, 所以这个选择必须交给用户, 程序不自己决定。
        返回 True=再登录一轮, False=放弃。
        """
        answer = {"v": False}
        done = threading.Event()

        def _ask():
            try:
                answer["v"] = messagebox.askretrycancel(
                    "这家还没登录成功",
                    f"{plat.name} · {merchant}\n\n"
                    f"核实结果: 仍是未登录状态"
                    f"({getattr(self, '_last_verify_reason', '未登录')})。\n\n"
                    "「重试」= 重新打开这家商户的登录页, 继续登录\n"
                    "「取消」= 先放弃这家, 继续后面的平台"
                    "(稍后可单独对它做一次「首次登录」)")
            except Exception:
                answer["v"] = False
            finally:
                done.set()

        self._ui(_ask)
        # 与"等待用户扫码确认"同一套上限; 真到点没人理, 按放弃处理, 不能把整批卡死
        if not done.wait(timeout=3600):
            self._append_log(f"[提示] {plat.name}({merchant}) 的确认框一小时无人应答, 按放弃处理")
            return False
        return bool(answer["v"])

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
        from core.browser import BrowserManager
        if name in (BrowserManager.RESERVED_MERCHANT_DIR, BrowserManager.RESERVED_PLATFORM_DIR):
            # 这两个名字是"没有商户时"的 profile 落点, 建成商户的话商户发现会把它剔掉,
            # 于是这家商户建完就不见了 —— 当场拒绝比事后找不到好。
            return False, f"「{name}」是程序保留的名字(平台级调试目录), 请换个商户名"
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
        """逐家走一遍登录流程, 返回与 tasks 对齐的核实结论列表。

        结论取值: "ok"=已核实登录成功 / "bad"=核实过仍未登录 / "unknown"=没能核实 /
        "skip"=这一轮没走到核实(用户没关窗口、被中止、登录页没打开)。
        导出前的登录预检靠这个返回值决定"哪些家这次不导", 见 _preflight_login_check。
        """
        # tasks 由主线程 _collect_tasks 备好, 这里只消费(工作线程不读界面)
        self._set_status("正在登录...")
        self._set_progress(maximum=len(tasks), value=0)
        results = []   # 每家商户的最终核实结果, 收尾统计用
        for i, (key, plat, merchant) in enumerate(tasks):
            if self._aborted():
                self._append_log(f"[中止] 剩余 {len(tasks) - i} 项未登录")
                break
            final = "skip"
            for attempt in range(1, self.LOGIN_RETRY_LIMIT + 1):
                verdict = self._login_one_round(plat, key, merchant)
                final = ("ok" if verdict is True else
                         "unknown" if verdict is None else
                         "bad" if verdict is False else "skip")
                if final != "bad":
                    break                      # 成功/没能核实/这一轮没走到核实: 都不问
                if self._aborted():
                    self._append_log(f"[中止] {plat.name}({merchant}) 不再重试")
                    break
                if attempt >= self.LOGIN_RETRY_LIMIT:
                    self._append_log(f"  [提示] {plat.name}({merchant}) 已试过 {attempt} 次"
                                     "仍未登录, 跳过这家(稍后可单独对它做一次首次登录)")
                    break
                if not self._ask_login_retry(plat, merchant, attempt):
                    self._append_log(f"  {plat.name}({merchant}) 按您的选择先放弃这家")
                    break
                self._append_log(
                    f"  {plat.name}({merchant}) 重新打开登录页"
                    f"(第 {attempt + 1}/{self.LOGIN_RETRY_LIMIT} 次尝试)")
            results.append(final)
            self._set_progress(value=i + 1)
        if not tasks:
            self._append_log("[提示] 未勾选任何平台商户,请先添加/勾选商户。")
        else:
            self._append_log(
                f"登录流程完成: 已核实登录成功 {results.count('ok')} / "
                f"未登录 {results.count('bad')} / 没能核实 {results.count('unknown')} / "
                f"未完成 {results.count('skip')}"
                "(登录态按 browser_data/平台/商户/ 目录保存)")
        self._set_status("登录完成")
        return results

    def _do_export(self, tasks, start_date, end_date, step_debug=False,
                   preflight=True):
        """tasks/日期/单步开关均由主线程取好再传进来, 本函数跑在工作线程上。

        preflight=False 留给无人值守的调用方(定时任务): 预检要人决定"去登录还是跳过",
        没人应答时弹窗会把整批吊在那里。
        """
        if preflight:
            tasks = self._preflight_login_check(tasks)
            if not tasks:
                self._set_status("未导出(登录预检后没有可导出的商户)")
                return
        self._execute_export_tasks(tasks, start_date, end_date, label="导出",
                                   step_debug=step_debug)
        self._record_job_done("导出", start_date, end_date)

    def _probe_login(self, plat, merchant, force_headless=False):
        """用这家商户自己的 profile 真跑一次 check_login, 返回 (结论, 出错信息)。

        结论: True=已登录 / False=未登录 / None=没能核实(浏览器起不来、页面没渲染完、
        平台脚本自己抛异常)。出错信息供调用方写日志。
        本函数不向外抛: 一个附加检查把用户本该到手的账单挡掉, 是最坏的结果。
        """
        try:
            self._ensure_browser(plat, merchant, force_headless=force_headless)
            return bool(plat.check_login(self.browser)), ""
        except Exception as e:
            return None, str(e)

    def _preflight_login_check(self, tasks):
        """导出前统一查一遍登录, 把要人操作的事集中在开跑前处理完。

        以前登录失效是"跑到那一家才发现": _run_single_export 的 inline 预检把这家折成
        manual 跳过, 于是人工介入点摊在整批中间(第 3 家要登录、第 7 家要扫码), 用户
        走开一会儿回来, 批任务已经在等第 N 个框。这里改成: 先逐家查一遍 → 一次弹窗列出
        所有失效的 → 集中重登 → 登不上的从本批剔除, 剩下的才进导出循环
        (需扫码的平台仍按 manual_intervention 排在队尾)。

        返回本次应当继续导出的任务列表。
        """
        if not tasks:
            return tasks
        self._set_status("导出前检查登录状态...")
        expired = []            # [(下标, 平台, 商户)]
        for i, (_key, plat, merchant) in enumerate(tasks):
            if self._aborted():
                self._append_log("[中止] 登录预检中止, 本次不导出")
                return []
            # 后台核实: 预检连着探 N 家, 跟着"显示浏览器"设置会一个接一个闪窗口
            verdict, err = self._probe_login(plat, merchant, force_headless=True)
            if verdict is False:
                expired.append((i, plat, merchant))
                self._append_log(f"[预检] {plat.name}({merchant}) 登录已失效")
            elif verdict is None:
                # 判不出不等于没登录: 照常导出, 真失效由 inline 预检再兜一次
                self._append_log(f"[预检] {plat.name}({merchant}) 没能核实登录状态, "
                                 f"照常导出: {err[:80]}")
        if not expired:
            self._append_log(f"[预检] {len(tasks)} 家商户登录状态检查通过")
            return tasks
        self._append_log(f"[预检] 共 {len(expired)} 家登录已失效, "
                         f"其余 {len(tasks) - len(expired)} 家有效")
        if not self._ask_preflight_login(expired, len(tasks) - len(expired)):
            self._append_log(f"[预检] 按您的选择本次跳过这 {len(expired)} 家"
                             "(稍后可单独对它们做「首次登录」)")
            return self._drop_by_index(tasks, {i for i, _, _ in expired})
        # 集中重登: _do_login 自己会逐家开登录页、等关窗、核实, 失败最多问 3 轮
        verdicts = self._do_login([tasks[i] for i, _, _ in expired]) or []
        dropped = {i for (i, _p, _m), v in zip(expired, verdicts) if v == "bad"}
        if dropped:
            self._append_log(f"[预检] 重登后仍未登录 {len(dropped)} 家, 本次不导出这些"
                             "(可稍后单独做「首次登录」)")
        kept = self._drop_by_index(tasks, dropped)
        if kept:
            self._append_log(f"[预检] 登录处理完毕, 开始导出 {len(kept)} 家")
        return kept

    @staticmethod
    def _drop_by_index(tasks, indexes):
        """按下标集合剔除任务(同一平台多个商户时按对象比会误伤, 所以记下标)。"""
        if not indexes:
            return list(tasks)
        return [t for i, t in enumerate(tasks) if i not in indexes]

    def _ask_preflight_login(self, expired, ok_count):
        """一次弹窗问完所有失效的商户: 现在集中重登 / 本次先跳过它们。

        只弹一个框, 每家一个框正是这次要去掉的动作。沿用 `_ask_login_retry` 的
        "主线程弹、后台线程等"写法; 一小时无人应答按"跳过"处理, 不把整批吊死。
        """
        answer = {"v": False}
        done = threading.Event()
        lines = [f"· {plat.name}({m})" for _, plat, m in expired[:12]]
        if len(expired) > 12:
            lines.append(f"…… 等共 {len(expired)} 家")
        text = ("\n".join(lines)
                + f"\n\n「重试」= 现在集中重新登录这 {len(expired)} 家"
                  "(逐个打开登录页, 登录好把浏览器窗口关掉即可)\n"
                  f"「取消」= 本次先跳过它们, 只导出已登录的 {ok_count} 家")

        def _ask():
            try:
                answer["v"] = messagebox.askretrycancel(
                    f"有 {len(expired)} 家登录已失效", text)
            except Exception:
                answer["v"] = False
            finally:
                done.set()

        self._ui(_ask)
        if not done.wait(timeout=3600):
            self._append_log("[预检] 确认框一小时无人应答, 按「跳过这些、只导其余」处理")
            return False
        return bool(answer["v"])

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
                    aborted=self._aborted,      # 按了「中止」就别再重试、别再睡那 30/60 秒
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

    USER_WAIT_TIMEOUT_S = 10 * 60    # 等人扫码/手工确认的上限, 到点按"没确认"处理
    _CONFIRM_POLL_S = 1.0            # 等待期间多久看一眼「中止」

    def _ask_user_confirmed(self, prompt, plat_name, merchant):
        """请用户确认"我已完成那步手工操作"; 返回他**是否真的**点了「我已完成」。

        三条 False 的路: 点「这一家先放弃」/ 窗没建起来 / 到点没人应答。

        为什么是自建 Toplevel 而不是 `messagebox`: 两条都是实测出来的坑 ——
        ① 模态框压住主窗口, 「中止」按不动, 等待形同不可打断;
        ② 超时那一路 worker 先返回, `_ask` 还卡在模态框里, `finally` 根本没跑,
           于是主窗口的 topmost 永不收回(前一轮量到 `attributes` 序列只有 `[True]`)、
           那张没人应答的框一直留在屏上, 下一家再叠一张。
        现在 topmost 设在**这张窗自己身上**, 窗随关随没, 不会把主窗口带成永久置顶。
        线程契约照第 12 章第 12 条: 建窗与关窗都投递给主线程, 本线程用 answer + Event 等。
        """
        evt = threading.Event()
        answer = {"ok": False}

        def _ask():
            try:
                # 上一张理论上不该还在, 在就先收掉 —— **不带本次的 evt**, 否则这次等待
                # 会当场被 set 成"没答复"(第一次实测就把这条踩出来了)。
                self._close_confirm_window()
                win = tk.Toplevel(self.root)
                self._confirm_win = win
                win.title("需要您操作确认")
                win.attributes("-topmost", True)   # Chromium 常常盖在主窗口上面
                win.resizable(False, False)
                tk.Label(win, text=f"{plat_name} · {merchant}", bg=BG_PANEL,
                         fg=FG_MAIN, font=("Microsoft YaHei", 11, "bold")
                         ).pack(padx=16, pady=(14, 4))
                tk.Label(win, text=prompt, bg=BG_PANEL, fg=FG_MAIN, justify="left",
                         wraplength=420, font=("Microsoft YaHei", 10)
                         ).pack(padx=16, pady=6)
                tk.Label(win, text="请先完成上面的操作, 再点「我已完成」;\n"
                                   "点「这一家先放弃」= 本次不导这一家 —— 程序不会替你确认。",
                         bg=BG_PANEL, fg=FG_MUTED, justify="left", wraplength=420,
                         font=("Microsoft YaHei", 9)).pack(padx=16, pady=(0, 10))
                row = tk.Frame(win, bg=BG_PANEL)
                row.pack(pady=(0, 12))
                buttons = getattr(self, "_confirm_buttons", None)   # 测试从这里取按钮
                for label, verdict in (("我已完成, 继续下载", True), ("这一家先放弃", False)):
                    btn = tk.Button(row, text=label, font=("Microsoft YaHei", 9),
                                    command=lambda v=verdict: self._answer_confirm(evt, answer, v))
                    btn.pack(side=tk.LEFT, padx=6)
                    if buttons is not None:
                        buttons.append(btn)
                win.protocol("WM_DELETE_WINDOW",
                             lambda: self._answer_confirm(evt, answer, False))
                win.lift()
            except Exception as e:
                self._append_log(f"[待处理] 确认窗没弹出来, 这一家按未完成处理: {str(e)[:60]}")
                evt.set()

        self._ui(_ask)
        deadline = time.time() + self.USER_WAIT_TIMEOUT_S
        timed_out = True
        while time.time() < deadline:
            if evt.wait(self._CONFIRM_POLL_S):
                return bool(answer["ok"])
            if self._aborted():
                self._append_log("[中止] 等待确认期间收到中止请求, 这一家按未完成处理")
                timed_out = False
                break
        if timed_out:
            self._append_log(f"[待处理] {max(1, self.USER_WAIT_TIMEOUT_S // 60)} 分钟内"
                             "没等到确认, 不再继续往下点 —— 这一家转人工")
        # 关窗同样投递给主线程, 而且**等它真关掉了**再返回: 否则超时/中止之后那张窗
        # 会一直留在屏上(用户以为还能点, 而 worker 已经走开了)。
        self._ui(lambda: self._close_confirm_window(evt))
        evt.wait(5)
        return False

    def _answer_confirm(self, evt, answer, verdict):
        """按钮/关窗回调: 记下答复, 再把这张窗收掉(主线程里就地执行)。"""
        answer["ok"] = bool(verdict)
        self._close_confirm_window(evt)

    def _close_confirm_window(self, evt=None):
        """收掉当前那张确认窗; 只有明确传入本次的 evt 时才回话(见 `_ask` 里那次清场)。"""
        win = getattr(self, "_confirm_win", None)
        self._confirm_win = None
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass
        if evt is not None:
            evt.set()

    def _run_single_export(self, plat, merchant, start_date, end_date, step_debug=False):
        """一个商户这一档的导出; 配了子商户时, 这里是"一次登录切着导 N 份"。

        只有 `supports_sub_merchants=True` 且真的录了清单的商户才会进循环 —— 其余平台
        走的还是原来那一条路, 一个字都不变。
        """
        if getattr(plat, "supports_sub_merchants", False):
            subs, note = submerchants.get(plat.key, merchant)
            if note:
                self._append_log("[提醒] " + note)
            if subs:
                return self._run_sub_merchants(plat, merchant, subs, start_date, end_date,
                                               step_debug)
        return self._export_one_merchant(plat, merchant, start_date, end_date, step_debug)

    def _run_sub_merchants(self, plat, merchant, subs, start_date, end_date, step_debug):
        """同一份 profile、同一次登录里按清单逐个切子商户导出, 结论按"最差的算"。

        重试放在**每个子商户自己身上**(`run_with_retry`), 整家重跑没有意义: 成功的几家
        会再导一遍, 归档里多堆一层 `历史/`, 反而更难看清。因此这一档的汇总结论永远不是
        "failed" —— 全失败也报 "manual", 免得外层再把整家捞一次。
        """
        st = load_settings()
        # 取值口径与 _execute_export_tasks 完全一致(包括"0 就是 0"): 以前这里写成
        # `int(x or 30)`, 于是把间隔设成 0 的机器每次重试反而要睡 30 秒。
        retry_times = int(st.get("retry_times", DEFAULT_SETTINGS.get("retry_times", 2)))
        retry_interval_s = int(st.get("retry_interval_s",
                                      DEFAULT_SETTINGS.get("retry_interval_s", 30)))
        verdicts = []
        for i, sub in enumerate(subs, 1):
            if self._aborted():
                self._append_log(f"[中止] {plat.name}({merchant}) 还剩 {len(subs) - i + 1} 个"
                                 f"子商户未导出")
                verdicts.append("manual")
                break
            self._append_log(f">>> 子商户 {i}/{len(subs)}: {sub}")
            if not self._switch_sub_merchant(plat, merchant, sub):
                # 切换没确认就停整家: 后面几家同样不可信, 而且已经证明这条路径会错
                verdicts.append("manual")
                break
            verdict = run_with_retry(
                lambda s=sub: self._export_one_merchant(plat, merchant, start_date, end_date,
                                                        step_debug, sub_merchant=s),
                retry_times=retry_times,
                retry_interval_s=retry_interval_s,
                log=self._append_log,
                aborted=self._aborted)        # 子商户这一档同理: 中止就不再重试
            verdicts.append(verdict)
        if verdicts and all(v == "success" for v in verdicts):
            return "success"
        return "manual"

    def _switch_sub_merchant(self, plat, merchant, sub):
        """切到某个子商户, 并确认页面上"现在是谁"。返回 False = 这一家就此停手转人工。

        这是整套机制里唯一防住"A 的账单记到 B 名下"的地方, 所以三种情况分得很清:
          1. 切换动作失败 → 停;
          2. 读回的当前商户和要导的对不上 → 停(顺带截图, 日志里写清页面是谁、要的是谁);
          3. 平台没实现读回 / 读回为空 → **不停**, 但必须留一句"归属未经校验" —— 读不到
             不等于读对了, 也不能因为没实现读回就彻底没法用。
        """
        browser = self.browser
        try:
            if not plat.switch_sub_merchant(browser, sub):
                self._append_log(f"[中止] {plat.name}({merchant}): 没能切到子商户「{sub}」, "
                                 f"已停止本次导出 —— 带着没确认过的归属继续点导出, 最坏就是把"
                                 f"别的商户的账单记到「{sub}」名下。", "warning")
                return False
            try:
                on_page = plat.current_sub_merchant(browser) or ""
            except Exception as e:
                self._append_log(f"[提醒] {plat.name}({merchant}): 读回当前子商户出错"
                                 f"({str(e)[:60]}), 「{sub}」这一轮归属未经校验", "warning")
                return True
            if not on_page:
                self._append_log(f"[提醒] {plat.name}({merchant}): 该平台没有实现「读回当前"
                                 f"子商户」, 「{sub}」这一轮的归属未经校验", "warning")
                return True
            if not submerchants.matches(sub, on_page):
                self._append_log(f"[中止] {plat.name}({merchant}): 页面上当前子商户是"
                                 f"「{on_page}」, 不是要导的「{sub}」, 已停止导出(避免把账单"
                                 f"记错商户)。若反复出现, 需要核对切换与读回的选择器。",
                                 "warning")
                try:
                    browser.snapshot(f"子商户不匹配_{sub}")
                except Exception:
                    pass
                return False
            self._append_log(f"[子商户] 已切到「{sub}」(页面显示「{on_page}」)")
            return True
        except Exception as e:
            self._append_log(f"[中止] {plat.name}({merchant}): 切换子商户「{sub}」时出错"
                             f"({str(e)[:80]}), 这一家转人工", "warning")
            return False

    def _export_one_merchant(self, plat, merchant, start_date, end_date, step_debug=False,
                             sub_merchant=""):
        """跑一次导出(浏览器就绪、上下文已设好): 返回 "success"/"manual"/"failed"。

        本函数不允许向外抛异常: run_with_retry 只对返回值 "failed" 重试, 抛出会被
        调用方的 except 折成"一次失败", 既不计入 stats 也跳过全部重试 —— 而"浏览器
        起不来"恰是最值得重试的那种失败。
        """
        who = f"{merchant}/{sub_merchant}" if sub_merchant else merchant
        tag = f"({sub_merchant})" if sub_merchant else ""
        date_str = f"{start_date} 至 {end_date}"
        self._append_log(f"日期范围: {date_str}")
        result = "failed"
        err_msg = ""
        t0 = time.time()   # 启动阶段就抛错时用它, 耗时记为约 0
        try:
            # 每个商户使用独立浏览器 profile(登录态隔离);下载目录含商户层
            self._ensure_browser(plat, merchant)
            self.browser.set_export_context(plat.name, start_date, end_date, merchant,
                                            sub_merchant)
            # 注入"等待用户手动操作"回调(如微信扫码确认),生产环境始终启用。
            # 后台线程调用 browser.wait_user() 时,切到 UI 线程弹窗提醒并阻塞等待用户完成。
            # 回调**必须回话**(True=用户亲手点了确定), 否则 wait_user 会把"没人应答"
            # 当成"已确认"继续往下点。见 _ask_user_confirmed。
            self.browser.set_user_wait_callback(
                lambda prompt, n=plat.name, m=who: self._ask_user_confirmed(prompt, n, m))
            # 单步调试模式: 注入回调,平台脚本调用 step_pause() 时弹"继续/中止"
            if step_debug:
                def _step_cb(name, n=plat.name, m=who):
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
                    f"[预检] {plat.name}({who}) 登录已失效,请重新完成首次登录(扫码/账号)后再导出")
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
        # 落稳定性统计(每次导出一条 JSON,供看板汇总)。子商户那一档记成 "主/子",
        # 一家一次 —— 看板上要能看出是"哪个子商户没成功", 而不是整家糊在一起。
        duration = time.time() - t0
        record_stat(plat.name, who, start_date, end_date, result,
                    duration_s=duration, error=err_msg)
        if result == "success":
            self._append_log(f"[完成] {plat.name} 流水导出成功({duration:.1f}s)" + tag)
        elif result == "manual":
            self._append_log(f"[提示] {plat.name} 需要手动完成导出" + tag)
        else:
            self._append_log(f"[失败] {plat.name} 导出失败" + tag)
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
                # 每个商户独立 profile 检查(与导出前预检同一条探测路径)
                verdict, err = self._probe_login(plat, merchant)
                if verdict is False:
                    self._append_log(f"  {plat.name}({merchant}): 未登录")
                    self.set_platform_status(key, "error")
                elif verdict is None:
                    self._append_log(f"  {plat.name}({merchant}): 检查失败 - {err}")
                    self.set_platform_status(key, "error")
                else:
                    info = self.browser.get_page_info()
                    self._append_log(f"  {plat.name}({merchant}): 已登录 ({info.get('title', '')[:20]})")
                    self.set_platform_status(key, "ok")
            except Exception as e:
                # 读页面信息出错也只算这一家没查成, 不能让整批检查停在半截
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


def _install_dependencies(plan):
    """按体检开的单子装。返回 (成功?, 给人看的收尾)。

    失败时把 pip/playwright 自己写的最后几行原样带回去 —— 以前只把异常对象塞进弹窗,
    用户看到的是 `command returned non-zero exit status 1`, 真正的原因(连不上索引、
    证书被拦、磁盘满)在它下面几行, 而那几行才是 IT 需要的东西。
    """
    tail = []
    for title, argv in plan:
        log("%s: %s" % (title, deps.show(argv)))
        try:
            r = subprocess.run(argv, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=1800)
        except Exception as e:
            return False, "%s 没能执行: %r" % (title, e)
        tail += ((r.stdout or "") + "\n" + (r.stderr or "")).strip().splitlines()[-6:]
        if r.returncode != 0:
            # 结论 + 已经攒下的输出: 光说"失败了"等于什么都没说
            return False, "%s 失败(退出码 %s)\n%s" % (title, r.returncode, "\n".join(tail[-8:]))
    return True, "\n".join(tail[-8:])


def _how_to_fix(d):
    """给弹窗/日志看的"怎么办": 命令 + 装不上时的两条退路。"""
    plan = deps.fix_commands(d)
    lines = ["可以手动执行:" if plan else "没有可自动执行的命令。"]
    lines += ["  %s" % deps.show(a) for _t, a in plan]
    lines.append("")
    lines.append(deps.OFFLINE_HINT)
    return "\n".join(lines)


def check_dependencies():
    """启动前的依赖体检: 缺哪样说哪样, 能顺手装就装, 装不上把话说清。

    两版绿色包的分工都在这: 全量版什么都带, 这里一趟就过; 核心版用本机 Python, 缺的多半是
    playwright 包或浏览器内核(见 core/deps.py)。

    红线: 这是**附加**检查 —— 只有"连 playwright 都没有、程序根本跑不起来"这一种情况才让
    程序退出, 版本不对、内核没下都只提示不拦人(以前这两种本来就不检查, 放行不是新行为)。
    """
    d = deps.probe()
    for line in deps.describe(d):
        log(line)
    if d.verdict in (deps.OK, deps.UNKNOWN):
        return True

    try:
        root = tk.Tk()
        root.withdraw()
    except Exception as e:
        # 连弹窗都起不来(远程会话、没有窗口站): 结论留在日志里放行。真缺依赖的话,
        # 后面 import playwright 会自己报那一句, 不该由体检来当门神。
        log("[提醒] 依赖提示的弹窗起不来(%r), 继续启动" % (e,), level="warning")
        return True

    try:
        if d.verdict == deps.BAD_VERSION:
            # 只问不拦: 换版本要重下一百多 MB, 有人就是不想要那个改动
            go = messagebox.askyesno(
                "依赖版本不一致",
                "本机 playwright 版本 %s, 而这个工具是跟着 %s 开发和实测的。\n\n"
                "是: 现在换成 %s(需要联网)\n"
                "否: 就用当前版本继续 —— 真出问题时, 第一个该怀疑的就是这里。"
                % (d.version, d.expected, d.expected))
            if not go:
                log("[提醒] 用户选择继续用 playwright %s" % d.version)
                return True
            plan = deps.fix_commands(d)
        elif d.verdict == deps.NO_KERNEL:
            go = messagebox.askyesno(
                "缺少浏览器内核",
                "%s\n\n"
                "导出账单要用它。是: 现在自动下载(需要联网, 约 1-3 分钟)\n"
                "否: 先进程序看看设置和日志(导出会失败)。" % "\n".join(deps.describe(d)))
            if not go:
                log("[提醒] 没有浏览器内核, 导出会失败。\n" + _how_to_fix(d))
                return True
            plan = deps.fix_commands(d)
        else:                                    # 连 playwright 包都没有, 程序确实跑不动
            go = messagebox.askyesno(
                "缺少依赖",
                "%s\n\n是否现在自动安装?(需要联网, 约 1-3 分钟)\n\n"
                "如果选择否, 程序将退出。" % "\n".join(deps.describe(d)))
            if not go:
                return False
            plan = deps.fix_commands(d)

        ok, tail = _install_dependencies(plan)
        if ok:
            messagebox.showinfo("安装完成", "依赖安装成功!程序将重新启动。")
            os.execv(sys.executable, [sys.executable] + sys.argv)
            return True                          # 走不到这里(execv 成功就不返回)
        messagebox.showerror(
            "安装失败",
            "%s\n\n%s\n\n%s" % (tail, _how_to_fix(d),
                                "程序将退出。" if d.verdict == deps.NO_PACKAGE else "程序继续打开, 但导出会失败。"))
        return d.verdict != deps.NO_PACKAGE
    finally:
        try:
            root.destroy()
        except Exception:
            pass


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


def decide_workspace(use_suggested, picked, suggested):
    """把用户在首启对话框里的选择折成结论: ("use", 路径) 或 ("cancel", "")。

    「否」之后又没选目录(关掉选择框)按取消处理 —— 半路放弃比用一个没确认的路径好。
    """
    if use_suggested:
        return "use", os.path.abspath(suggested)
    return ("use", os.path.abspath(picked)) if picked else ("cancel", "")


def relaunch_for_workspace(path):
    """换好工作空间后重开一次进程。

    为什么必须重开而不是就地改: 各模块是 `from .config import DOWNLOAD_DIR` 这种**取值
    拷贝**, 界面也已经按旧路径扫过商户列表 —— 中途换路径不会传导下去。程序目录只读写不
    下书签时, 改用环境变量把路径递给下一个进程(否则重启后又回到"待用户选")。
    """
    env = dict(os.environ)
    if not workspace.write_pointer(path):
        env[workspace.WORKSPACE_ENV] = os.path.abspath(path)
    try:
        os.execve(sys.executable, [sys.executable] + list(sys.argv), env)
    except Exception as e:                      # execve 成功就不返回了
        messagebox.showerror("需要重开一次",
                             f"自动重启没成功: {str(e)[:120]}\n\n"
                             "请关掉工具, 再双击一次启动。")
    return False


def ensure_workspace(root):
    """全新的一份包(绿色版第一次启动)问一次"账单和登录数据存哪儿"。

    必须在建界面之前跑完: `LiushuiApp.__init__` 会按当前路径扫商户、建日志与浏览器目录。
    返回 False 表示用户没选定, 不要继续启动(比把账单写进一个没确认的目录再崩掉好)。
    """
    if not workspace.PENDING_PICK:
        return True
    root.withdraw()
    suggested = workspace.default_workspace_suggestion()
    use_default = messagebox.askyesno(
        "第一步: 选一个存放账单和登录数据的文件夹",
        "导出的账单、各商户的登录状态、运行日志会统一放在一个「工作空间」目录里。\n\n"
        f"建议位置:\n{suggested}\n\n"
        "「是」= 就用这个位置\n「否」= 我自己选一个文件夹")
    picked = "" if use_default else filedialog.askdirectory(
        title="选择工作空间文件夹(账单、登录数据放这里)")
    action, path = decide_workspace(use_default, picked, suggested)
    if action == "cancel":
        return False
    ok, msg = workspace.apply_data_root(path)
    if not ok:
        messagebox.showwarning("这个目录不能用", f"{msg}\n\n"
                                                 "换一个位置再试(不能是只读盘或没有权限的目录)。")
        return False
    log(f"工作空间已设为: {msg}")
    relaunch_for_workspace(msg)
    return False


def main():
    try:
        if not check_dependencies():
            return
        root = tk.Tk()
        if not ensure_workspace(root):
            try:
                root.destroy()
            except Exception:
                pass
            return
        # 单实例守卫: 商户目录=Chromium profile, 一个 profile 同一时刻只能被一个进程用
        # (12 章第 1 条)。双开时另一边只会报"Sync API inside asyncio loop"这种莫名错。
        # 只问不拦: 锁可能是上次崩溃留下的, 拦死就等于让人永远开不了。
        try:
            from core import instance
            import atexit
            _allowed, other_pid = instance.claim(workspace.DATA_ROOT)
            atexit.register(instance.release)
            if other_pid:
                if not messagebox.askyesno(
                        "已经开着另一个导出工具",
                        f"这份工作空间正被另一个实例使用(进程号 {other_pid})。\n\n"
                        "同时开两份会互相抢浏览器的登录目录(表现为莫名的启动失败),"
                        "也会把设置与统计写乱。\n\n建议先关掉另一个窗口。仍要继续打开吗?"):
                    root.destroy()
                    return
        except Exception:
            pass                     # 守卫自己坏了绝不影响启动(第 28 条同一条红线)
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
