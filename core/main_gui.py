"""
流水自动导出工具 - GUI主程序
适用于非技术用户的可视化界面
平台通过 core.loader 动态发现加载,新增平台只需在 platforms/ 下建文件夹
"""

import os
import sys
import threading
import time
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import ttk, messagebox, scrolledtext

# 项目根目录: 将根加入 sys.path,保证从任意位置启动都能定位 core/ platforms/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import DOWNLOAD_DIR, BROWSER_DATA_DIR, load_settings, save_settings
from core.loader import discover_platforms
from core.logger import log


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
        self.merchant_vars = {}   # platform_key -> {商户名: BooleanVar}
        self.merchants = discover_merchants(self.platforms.keys())
        self._login_confirm = threading.Event()  # 登录弹窗确认事件(等待用户)

        self._setup_window()
        # 构建顺序: 右日志 → 左平台 → 中间(设置+历史+操作按钮) → 初始化概览
        self._build_right_panel()
        self._build_left_panel()
        self._build_middle_panel()
        self._refresh_summary()   # 初始化概览条

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

        self.merchant_frames = {}   # platform_key -> Frame(商户 checkbox 容器)
        for key, plat in self.platforms.items():
            if not getattr(plat, "enabled", True):
                continue
            var = tk.BooleanVar(value=True)
            self.platform_vars[key] = var
            var.trace_add("write", lambda *_: self._refresh_summary())

            # 第一行: 平台勾选(联动其商户) + 状态标识 + 添加商户
            row = tk.Frame(list_frame, bg=BG_PANEL)
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
            mer_frame = tk.Frame(list_frame, bg=BG_PANEL)
            mer_frame.pack(fill=tk.X, pady=(0, 2))
            self.merchant_frames[key] = mer_frame
            self.merchant_vars[key] = {}
            merch = self.merchants.get(key, [])
            if merch:
                for m in merch:
                    self._add_merchant_checkbox(key, m, True)
            else:
                self._append_log(f"平台[{plat.name}]暂无商户,请先\"+\"添加并登录")

    def _build_right_panel(self):
        right = tk.Frame(self.root, bg=BG_PANEL, padx=15, pady=15)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(4, 8), pady=8)

        header = tk.Frame(right, bg=BG_PANEL)
        header.pack(fill=tk.X, pady=(0, 8))
        tk.Label(header, text="操作日志", bg=BG_PANEL, fg=FG_MAIN,
                 font=("Microsoft YaHei", 13, "bold")).pack(side=tk.LEFT)
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

        others = [
            ("首次登录", self._action_login_all, BG_BUTTON, 0, 0),
            ("检查登录状态", self._action_check_status, BG_WARN, 0, 1),
            ("打开下载文件夹", self._action_open_folder, "#7f8c8d", 1, 0),
            ("使用说明", self._action_help, "#16a085", 1, 1),
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
        mv = tk.BooleanVar(value=checked)
        mv.trace_add("write", lambda *_: self._refresh_summary())
        self.merchant_vars.setdefault(key, {})[name] = mv
        tk.Checkbutton(
            frame, variable=mv, text=name,
            bg=BG_PANEL, fg=FG_MUTED, font=("Microsoft YaHei", 9),
            activebackground=BG_PANEL).pack(anchor="w", padx=(26, 0))

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

    def _get_selected(self):
        return [k for k, v in self.platform_vars.items() if v.get()]

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
        self._set_status(f"正在导出({date_str})...")
        self.progress.config(maximum=len(tasks), value=0)
        exported = 0
        manual = 0
        failed = 0
        for i, (key, plat, merchant) in enumerate(tasks):
            self._append_log(f">>> 正在导出 {plat.name}({merchant}) 流水...")
            self._set_platform_status(key, "warn")
            try:
                self._append_log(f"日期范围: {date_str}")
                # 每个商户使用独立浏览器 profile(登录态隔离);下载目录含商户层
                self._ensure_browser(plat, merchant)
                self.browser.set_export_context(plat.name, start_date, end_date, merchant)
                result = plat.export(self.browser, start_date, end_date)
                if result == "success":
                    self._append_log(f"[完成] {plat.name} 流水导出成功")
                    self._set_platform_status(key, "ok")
                    exported += 1
                elif result == "manual":
                    self._append_log(f"[提示] {plat.name} 需要手动完成导出")
                    self._set_platform_status(key, "warn")
                    manual += 1
                    self.root.after(0, lambda n=plat.name, d=date_str, g=plat.guide: messagebox.showinfo(
                        "请手动导出",
                        f"【{n}】自动导出未完全成功\n\n"
                        f"日期范围: {d}\n"
                        f"操作指引: {g}\n\n"
                        f"请在浏览器中手动完成导出,下载完成后点击确定继续。"))
                else:
                    self._append_log(f"[失败] {plat.name} 导出失败")
                    self._set_platform_status(key, "error")
                    failed += 1
            except Exception as e:
                self._append_log(f"[失败] {plat.name} 导出异常: {e}")
                self._set_platform_status(key, "error")
                failed += 1
            self.progress.config(value=i + 1)
        self._append_log(f"导出流程完成: 成功{exported} / 手动{manual} / 失败{failed}")
        log(f"导出完成: 成功{exported} / 手动{manual} / 失败{failed}", callback=self._append_log)
        if exported + manual > 0:
            # 导出完成后,把本次导出的文件汇总复制到 downloads/时间文件夹,并自动打开
            ts_dir = self._copy_export_outputs(start_date, end_date)
            if ts_dir:
                try:
                    os.startfile(ts_dir)
                    self._append_log(f"[汇总] 已自动打开文件夹: {ts_dir}")
                except Exception:
                    pass
        self._set_status(f"导出完成(成功{exported}/手动{manual}/失败{failed})")

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
                self.browser.navigate(plat.login_url)
                time.sleep(3)
                page_info = self.browser.get_page_info()
                url = page_info.get("url", "").lower()
                title = page_info.get("title", "")
                if "login" in url or "登录" in title:
                    self._append_log(f"  {plat.name}({merchant}): 未登录")
                    self._set_platform_status(key, "error")
                else:
                    self._append_log(f"  {plat.name}({merchant}): 已登录 ({title[:20]})")
                    self._set_platform_status(key, "ok")
            except Exception as e:
                self._append_log(f"  {plat.name}({merchant}): 检查失败 - {e}")
                self._set_platform_status(key, "error")
            self.progress.config(value=i + 1)
        if not tasks:
            self._append_log("[提示] 未勾选任何平台商户。")
        self._set_status("检查完成")

    def cleanup(self):
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
        root.protocol("WM_DELETE_WINDOW", lambda: (app.cleanup(), root.destroy()))
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
