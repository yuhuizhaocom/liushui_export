"""
浏览器管理模块 - 持久化上下文 + 容错重试
"""

import csv
import glob
import json
import os
import re
import shutil
import time
import zipfile
from datetime import datetime

PW_BROWSERS_PATH = r"C:\pw_browsers"
if os.path.isdir(PW_BROWSERS_PATH):
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = PW_BROWSERS_PATH

from playwright.sync_api import sync_playwright
from .config import BROWSER_DATA_DIR, DOWNLOAD_DIR, ROOT_DIR
from .logger import log

class BrowserManager:
    def __init__(self, headless=False, log_callback=None):
        self.headless = headless
        self.log_callback = log_callback
        self.playwright = None
        self.context = None
        self.page = None
        self._profile_dir = os.path.abspath(BROWSER_DATA_DIR)  # 默认使用全局数据目录
        self._export_platform = ""
        self._export_start = ""
        self._export_end = ""
        self._export_merchant = ""   # 当前导出商户(用于下载目录隔离)
        self._export_task_id = ""   # 当前导出任务ID(如 20260901_203000),用于按任务分文件夹
        self._dl_queue = []        # 队列: 已触发的浏览器下载([{"dl":Download}])
        self._dl_capture_on = False  # 当前是否处于下载捕获模式
        self._dl_capture_t0 = 0.0    # 开始捕获的时刻(用于判断新文件)
        self._dl_temp_dir = ""       # 本次任务临时下载目录(完成后清空)
        self._recording_file = ""    # 操作录制文件路径(开启追踪后写入)
        self._step_debug = False     # 单步调试模式(开启后 step_pause() 会暂停等待)
        self._step_callback = None   # 单步暂停回调(由 GUI 注入,用于弹"继续"确认框)
        self._user_wait_callback = None  # "等待用户手动操作"回调(如扫码确认,由 GUI 注入)
        self._setup_dirs()

    @staticmethod
    def _safe_name(name):
        """清理名称中的路径非法字符,避免目录逃逸(profile 目录不能为空, 兜底 default)"""
        from .outputs import sanitize_name
        return sanitize_name(name, fallback="default")

    def set_browser_profile(self, platform_key="", merchant=""):
        """按 平台key/商户 设置独立浏览器数据目录(登录态隔离)
        - platform_key: 平台标识(如 youzan)
        - merchant: 商户自定义名称(如"旗舰店A"); 空则用平台级目录
        """
        base = os.path.abspath(BROWSER_DATA_DIR)
        parts = []
        if platform_key:
            parts.append(self._safe_name(platform_key))
        if merchant:
            parts.append(self._safe_name(merchant))
        self._profile_dir = os.path.join(base, *parts) if parts else base
        try:
            os.makedirs(self._profile_dir, exist_ok=True)
        except Exception:
            pass

    def set_export_context(self, platform="", start_date="", end_date="", merchant=""):
        """设置当前导出上下文(平台名/商户/日期)
        下载文件按 平台/商户/日期范围 分文件夹;同商户同日期范围的最新文件在顶层,
        之前的版本自动归档到 历史/ 子目录并加时间戳
        """
        self._export_platform = platform
        self._export_start = start_date
        self._export_end = end_date
        self._export_merchant = merchant or ""
        # 任务ID = 日期范围,保证同一平台/商户同一区间的所有导出都落在同一个文件夹
        self._export_task_id = f"{start_date}_{end_date}" if start_date and end_date else ""

    def _task_base_dir(self):
        """任务根目录: downloads/平台/商户/日期范围(无商户时省略商户层)"""
        base = os.path.abspath(DOWNLOAD_DIR)
        if self._export_platform:
            base = os.path.join(base, self._export_platform)
        if self._export_merchant:
            base = os.path.join(base, self._export_merchant)
        if self._export_task_id:
            base = os.path.join(base, self._export_task_id)
        return base

    def _setup_dirs(self):
        for d in [BROWSER_DATA_DIR, DOWNLOAD_DIR]:
            os.makedirs(d, exist_ok=True)

    def _log(self, msg, level="info"):
        log(msg, level=level, callback=self.log_callback)

    def _cleanup_lock_files(self):
        """清理浏览器数据目录中的残留锁文件,防止Chrome无法启动"""
        try:
            patterns = ["SingletonLock", "SingletonCookie", "SingletonSocket",
                        "Singleton*", "lockfile", "*.lock"]
            removed = 0
            for pattern in patterns:
                for f in glob.glob(os.path.join(self._profile_dir, pattern)):
                    try:
                        os.remove(f)
                        removed += 1
                    except Exception:
                        pass
            if removed:
                self._log(f"已清理 {removed} 个残留锁文件")
        except Exception as e:
            self._log(f"清理锁文件失败: {e}", "warning")

    def start(self):
        self._cleanup_lock_files()
        for attempt in range(1, 4):
            try:
                self._log(f"正在启动浏览器(第{attempt}次)...")
                self.playwright = sync_playwright().start()
                self.context = self.playwright.chromium.launch_persistent_context(
                    user_data_dir=self._profile_dir,
                    headless=self.headless,
                    viewport=None,   # 跟随窗口尺寸
                    locale="zh-CN",
                    accept_downloads=True,
                    downloads_path=os.path.abspath(DOWNLOAD_DIR),
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--start-maximized",   # 最大化窗口启动(替代 --start-fullscreen,避免内容只显示左上角)
                        "--window-size=1920,1080",  # 兜底窗口尺寸,确保 viewport 足够大
                        "--disable-features=Translate",
                    ],
                )
                if len(self.context.pages) > 0:
                    self.page = self.context.pages[0]
                else:
                    self.page = self.context.new_page()
                # 监听浏览器下载事件,推入队列(即使文件先写完也不丢事件)
                self.context.on("download", self._on_download)
                # 恢复上次保存的登录态(session cookie 也能跨会话保留,如微信支付)
                self._restore_login_state()
                self._log("浏览器启动成功")
                return self.page
            except Exception as e:
                self._log(f"浏览器启动失败(第{attempt}次): {str(e)[:100]}", "warning")
                # 失败后必须停掉已启动的 playwright,否则其事件循环仍处于运行状态,
                # 下一次 start() 会被误判为 "Sync API inside asyncio loop" 而掩盖真实错误
                if self.playwright:
                    try:
                        self.playwright.stop()
                    except Exception:
                        pass
                    self.playwright = None
                self.context = None
                self.page = None
                self._cleanup_lock_files()
                if attempt < 3:
                    time.sleep(3)
                else:
                    self._log("浏览器启动最终失败", "error")
                    raise

    def navigate(self, url, retries=3, skip_if_same=True):
        """带重试的页面导航。skip_if_same 时,若当前页已在该地址则跳过重复加载
        (用于登录预检刚导航完导出页、export 又导航同一地址的场景,省掉一次完整加载)。"""
        if skip_if_same:
            try:
                if self._same_target(self.page.url, url):
                    self._log(f"已在目标页(跳过重复导航): {url[:60]}")
                    return True
            except Exception:
                pass
        for attempt in range(1, retries + 1):
            try:
                self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
                self._log(f"页面加载成功: {url[:60]}")
                return True
            except Exception as e:
                self._log(f"页面加载失败(第{attempt}次): {e}", "warning")
                if attempt < retries:
                    time.sleep(3)
                else:
                    self._log(f"页面加载最终失败: {url}", "error")
                    return False
        return False

    @staticmethod
    def _same_target(cur, target):
        """判断当前页面 URL 与目标是否指向同一地址。
        比较 scheme/netloc/path/query(忽略 fragment/hash),query 不同则算不同地址,
        避免"预检含默认参数、导出含具体日期参数"的页面(如有赞)被误判为相同而跳过导航。"""
        try:
            from urllib.parse import urlsplit
            c, t = urlsplit(cur or ""), urlsplit(target or "")
            return (c.scheme, c.netloc, c.path, c.query) == \
                   (t.scheme, t.netloc, t.path, t.query)
        except Exception:
            return False

    def safe_click(self, selector, description="", retries=3):
        """安全点击元素,带重试和等待;失败后 JS 回退(绕过遮挡/不可见拦截)"""
        for attempt in range(1, retries + 1):
            try:
                el = self.page.wait_for_selector(selector, timeout=10000)
                if el:
                    el.click(timeout=5000)
                    self._log(f"点击成功: {description or selector}")
                    return True
            except Exception as e:
                self._log(f"点击失败(第{attempt}次): {description or selector} - {e}", "warning")
                # JS 回退
                try:
                    el = self.page.wait_for_selector(selector, timeout=3000)
                    if el:
                        el.evaluate("e => e.click()")
                        self._log(f"点击成功(JS回退): {description or selector}")
                        return True
                except Exception:
                    pass
                if attempt < retries:
                    time.sleep(2)
        self._log(f"点击最终失败: {description or selector}", "error")
        return False

    def safe_fill(self, selector, value, description="", retries=2):
        """安全填写输入框"""
        for attempt in range(1, retries + 1):
            try:
                el = self.page.wait_for_selector(selector, timeout=10000)
                if el:
                    el.fill(value)
                    self._log(f"填写成功: {description or selector}")
                    return True
            except Exception as e:
                self._log(f"填写失败(第{attempt}次): {description or selector} - {e}", "warning")
                if attempt < retries:
                    time.sleep(2)
        return False

    def begin_wait_download(self):
        """开始捕获模式: 清空旧队列,后续触发的下载都会进入队列
        必须在点击"下载"按钮之前调用(异步下载文件先落地也不丢失事件)
        本次下载先落入 平台/日期范围/临时 目录,完成后移动到正式位置
        """
        self._dl_queue = []
        self._dl_capture_on = True
        self._dl_capture_t0 = time.time()
        self._carry_over_orphans()  # 上一轮残留的 UUID 文件收进 待确认/(不冒充本次)
        # 创建本次任务的临时下载目录(删除旧残留)
        tmp = self._task_tmp_dir()
        try:
            if tmp and os.path.isdir(tmp):
                shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass
        if tmp:
            try:
                os.makedirs(tmp, exist_ok=True)
            except Exception:
                pass
        self._dl_temp_dir = tmp

    def _task_tmp_dir(self):
        """本次任务临时目录: downloads/平台/商户/日期范围/临时"""
        return os.path.join(self._task_base_dir(), "临时")

    def _carry_over_orphans(self):
        """把下载根目录里没人认领的 UUID 残留文件移到 downloads/待确认/。

        以前是直接 _finalize_download 到"当前任务"目录 —— 可 begin_wait_download
        是在 set_export_context 已经切到下一个平台/商户之后调的, 于是上一轮残留会
        被安上新商户的文件名、放进新商户的文件夹, 而且这条路径不经过任何内容校验。
        对账单工具里"哪个商户的文件"是不能猜的, 归属不明就单独放, 并在日志里说清。
        """
        try:
            root = os.path.abspath(DOWNLOAD_DIR)
            orphan_ok = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                                   r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
            pending_dir = os.path.join(root, self.ORPHAN_DIR_NAME)
            moved = []
            for f in os.listdir(root):
                fp = os.path.join(root, f)
                if not os.path.isfile(fp):
                    continue
                name, ext = os.path.splitext(f)
                if not orphan_ok.match(name):
                    continue          # 截图/命名文件等不在考虑范围
                os.makedirs(pending_dir, exist_ok=True)
                dst = os.path.join(pending_dir, f)
                if os.path.exists(dst):
                    dst = os.path.join(pending_dir, "%s_%s%s" % (
                        name, datetime.now().strftime("%H%M%S"), ext))
                shutil.move(fp, dst)
                moved.append(os.path.basename(dst))
            if moved:
                self._log(f"[归位] {len(moved)} 个无法确认归属的下载文件已移到 "
                          f"{self.ORPHAN_DIR_NAME}/ 目录, 请人工核对: "
                          + ", ".join(moved[:5]), "warning")
        except Exception:
            pass

    def end_wait_download(self):
        """结束捕获模式(一次等待结束,清空队列、收走根目录残留、清理临时目录)"""
        self._dl_queue = []
        self._dl_capture_on = False
        self._carry_over_orphans()  # 收走根目录残留的原始下载文件(UUID) -> 待确认/
        self._cleanup_temp_dir()

    def _cleanup_temp_dir(self):
        """清理本次任务的临时下载目录(目录为空时仅 rmdir,带重试防瞬时文件锁)"""
        tmp = self._dl_temp_dir
        self._dl_temp_dir = ""
        if not tmp or not os.path.isdir(tmp):
            return
        for _ in range(8):
            try:
                # 目录已是空目录时删除目录本身(避免无效递归删除)
                if not os.listdir(tmp):
                    os.rmdir(tmp)
                else:
                    shutil.rmtree(tmp)
                return
            except Exception:
                time.sleep(0.8)
        # 最后兜底: 尽力而为(万一仍有锁,留待下次任务 begin 时清理)
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass

    def _on_download(self, download):
        """浏览器下载事件回调(Playwright会自动把文件写入downloads目录)"""
        try:
            name = download.suggested_filename or ""
        except Exception:
            name = ""
        self._log(f"触发下载: {name or '(无文件名)'}")
        if not self._dl_capture_on:
            self._log("  当前未开启捕获模式,忽略本次下载", "warning")
            return
        self._dl_queue.append({"dl": download, "name": name})

    def _save_download_to_temp(self, dl):
        """把下载内容落到本次任务临时目录,返回临时文件路径(失败返回None)
        采用"移动"而非复制,避免 downloads 根目录残留原始文件
        """
        try:
            if not self._dl_temp_dir:
                return None
            os.makedirs(self._dl_temp_dir, exist_ok=True)
            try:
                name = dl.suggested_filename or ""
            except Exception:
                name = ""
            save_name = self._normalize_download_name(
                name, self._export_platform, self._export_start, self._export_end)
            tmp = os.path.join(self._dl_temp_dir, save_name)
            # 1) 优先移动浏览器实际落盘文件(download.path() 阻塞到下载完成)
            try:
                src = dl.path()
                if src and os.path.isfile(src) and os.path.abspath(src) != os.path.abspath(tmp):
                    os.replace(src, tmp)
                    return tmp
            except Exception:
                pass
            # 2) 兜底: save_as 复制,随后删除根目录原始文件
            dl.save_as(tmp)
            if os.path.isfile(tmp):
                try:
                    src = dl.path()  # 若失败此处抛异常,跳过清理亦可接受
                    if src and os.path.isfile(src) and os.path.abspath(src) != os.path.abspath(tmp):
                        os.remove(src)
                except Exception:
                    pass
                return tmp
            # 极少情况下落盘有延迟,轮询确认
            for _ in range(30):
                time.sleep(1)
                if os.path.isfile(tmp):
                    return tmp
        except Exception as e:
            self._log(f"保存下载失败: {str(e)[:80]}", "warning")
        return None

    def wait_download(self, timeout=120):
        """等待下载完成,返回最终文件路径(失败返回None)
        1) 优先消费浏览器下载事件队列: 用 save_as 把下载保存到临时目录,再移动到正式位置
        2) 兜底: 监控 downloads 根目录,检测新出现的文件(兼容未触发download事件的场景)
        """
        root = os.path.abspath(DOWNLOAD_DIR)
        # 开启捕获模式(如果还没开,从此刻起记录新触发的事件)
        if not self._dl_capture_on:
            self._dl_queue = []
            self._dl_capture_on = True
        deadline = time.time() + timeout
        known = self._dir_snapshot(root)
        while time.time() < deadline:
            # 1) 事件队列出队: 保存到临时目录 → 归档到正式位置
            if self._dl_queue:
                item = self._dl_queue.pop(0)
                tmp_path = self._save_download_to_temp(item.get("dl"))
                if not tmp_path:
                    tmp_path = self._take_new_download(root, deadline)
                if tmp_path:
                    final = self._accept_download(tmp_path)
                    if final:
                        self.end_wait_download()
                        return final
                # 文件还未就绪则继续轮询等待
            # 2) 兜底: 目录中"等待开始后"新出现且已写完整的文件
            for fp in sorted(self._dir_snapshot(root) - known, key=os.path.getmtime, reverse=True):
                name = os.path.basename(fp)
                if name.endswith((".crdownload", ".tmp", ".part", ".download", ".dat")):
                    continue
                if self._is_stable(fp):
                    final = self._accept_download(fp)
                    if final:
                        self.end_wait_download()
                        return final
            time.sleep(1.5)
        self.end_wait_download()
        self._log("等待下载超时", "warning")
        self._cleanup_stale_files(root)
        return None

    def _accept_download(self, final):
        """把下载文件归档到正式位置后再做完整性校验。
        校验通过返回最终路径,失败则删除并返回 None。
        必须先归档——否则文件只留临时目录,随后 end_wait_download 清临时目录会把文件一起删掉,
        造成"日志显示下载完成但磁盘上找不到文件"。且避免"日志写 success 实为空表"这类问题。"""
        if not final or not os.path.isfile(final):
            return None
        final = self._finalize_download(final)
        if not final or not os.path.isfile(final):
            return None
        ok = self._validate_download(final)
        if ok:
            self._log(f"下载完成并校验通过: {os.path.basename(final)}")
            return final
        self._log(f"下载文件校验未通过,已清除: {os.path.basename(final)}", "warning")
        try:
            os.remove(final)
        except Exception:
            pass
        return None

    # ===== 下载文件完整性/内容校验(避免"下完了但实为空表") =====

    # 归属不明的残留下载文件放这儿, 不冒充任何商户的账单
    ORPHAN_DIR_NAME = "待确认"

    # ===== 下载文件类型识别(按文件头, 不看扩展名) =====
    # 兜底扫描会把 downloads/ 根目录里"最新出现的文件"当成本次下载认领, 而
    # screenshot() 恰好把 PNG 写在同一个根目录: 之前一张页面截图会被改名成
    # 平台_商户_起_止.xlsx 并通过校验上报 success —— 交出去的是截图。
    _IMAGE_MAGICS = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a",
                     b"BM", b"RIFF", b"IDNA", b"\x00\x00\x01\x00")

    @staticmethod
    def _file_head(path, n=12):
        try:
            with open(path, "rb") as f:
                return f.read(n)
        except Exception:
            return b""

    @classmethod
    def _is_image_file(cls, path):
        head = cls._file_head(path)
        return any(head.startswith(m) for m in cls._IMAGE_MAGICS)

    # 命中即判定为 manual/无效的错误文案
    _VALIDATE_ERROR_KEYWORDS = [
        "登录失效", "重新登录", "请登录", "系统繁忙", "操作失败", "请求超时",
        "出错了", "无权限", "参数错误", "<html", "<!doctype",
    ]

    def _validate_download(self, path):
        """校验下载文件: 非错误页文案、表格可读且有数据行;极小文件需能解析出数据。
        返回 True(正常) / False(需人工确认)。"""
        if not path or not os.path.isfile(path):
            return False
        try:
            size = os.path.getsize(path)
        except Exception:
            return False
        # 0) 文件头是图片 → 一定不是账单; 归档时扩展名已被改成 .xlsx, 只能看内容
        if self._is_image_file(path):
            self._log(f"[校验] 文件头是图片, 不可能是对账单: {os.path.basename(path)}", "warning")
            return False
        low = (os.path.basename(path) or "").lower()
        if low.endswith(".xlsx") and not self._file_head(path, 2).startswith(b"PK"):
            self._log(f"[校验] 扩展名是 .xlsx 但内容不是 zip: {os.path.basename(path)}", "warning")
            return False
        # 1) 先数数据行: 能读出数据行的表格就是真账单, 不再拿文案判生死。
        #    以前是"整个文件文本 子串匹配 错误文案"优先 —— 账单里退款备注写一句
        #    "客户申请操作失败"或"请登录后台查看明细", 真账单就会被判无效并被
        #    _accept_download 删掉, 用户看到的是"下载成功但文件没了"。
        rows = self._count_rows(path)
        if rows is not None and rows > 0:
            self._log(f"[校验] 文件通过完整性校验({size}B, {rows} 个数据行)")
            return True
        # 2) 读不出数据行时才用错误文案区分"空表"和"错误页/未登录页"
        hit = self._match_error_keyword(self._read_text_content(path))
        if hit:
            self._log(f"[校验] 无有效数据行且内容命中错误文案[{hit}]: "
                      f"{os.path.basename(path)}", "warning")
            return False
        if size < 1024:
            # 极小文件: 仅当能解析出有效数据行才算正常(空表/错误页无数据)
            self._log(f"[校验] 文件过小({size}B)且无有效数据: {os.path.basename(path)}", "warning")
            return False
        if rows == 0:
            self._log(f"[校验] 表格无有效数据行: {os.path.basename(path)}", "warning")
            return False
        # rows is None: 这种格式读不出行(如老 .xls), 维持原有的宽松判定
        self._log(f"[校验] 文件通过完整性校验({size}B, 无法解析行数)")
        return True

    def _read_text_content(self, path):
        """读取文件文本内容(宽容解码);二进制/xlsx 等会得到近似文本,不影响关键字检查。"""
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except Exception:
            return ""
        for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030", "latin-1"):
            try:
                return raw.decode(enc)
            except Exception:
                continue
        return ""

    def _match_error_keyword(self, text):
        for kw in self._VALIDATE_ERROR_KEYWORDS:
            if kw in text:
                return kw
        return None

    def _count_rows(self, path):
        """统计表格有效数据行数;无法解析返回 None(跳过行数校验)。"""
        low = (os.path.basename(path) or "").lower()
        try:
            if low.endswith(".csv"):
                with open(path, "r", encoding="utf-8-sig", errors="ignore") as f:
                    rows = sum(1 for _ in csv.reader(f))
                # 去掉表头: 仅有表头(rows==1)视为空表
                return max(0, rows - 1) if rows >= 1 else 0
            if low.endswith(".xlsx"):
                return self._xlsx_row_count(path)
        except Exception:
            return None
        return None

    def _xlsx_row_count(self, path):
        """用 zipfile 读 xlsx 首个工作表统计<row>近似行数(不引入 openpyxl 依赖)。
        仅用于"是否为空表"判断,返回近似非空行数;解析失败返回 None。"""
        try:
            sheet_pat = re.compile(r"xl/worksheets/sheet\d+\.xml")
            with zipfile.ZipFile(path) as z:
                names = [n for n in z.namelist() if sheet_pat.match(n)]
                if not names:
                    return None
                data = z.read(names[0]).decode("utf-8", errors="ignore")
            rows = len(re.findall(r"<row[ >]", data))
            return rows - 1 if rows >= 1 else 0
        except Exception:
            return None

    def _take_new_download(self, root, deadline):
        """取走队列中最新一次下载对应文件(等待它写完并移动到目标位置)"""
        while time.time() < deadline:
            new = self._find_new_candidate(root)
            if new:
                final = self._finalize_download(new)
                if final:
                    return final
            time.sleep(1.5)
        return None

    def _find_new_candidate(self, root):
        """在下载目录(仅根目录,不含平台子文件夹)找最新产生的候选文件"""
        now = time.time()
        best = None
        best_score = -1
        try:
            for f in os.listdir(root):
                fp = os.path.join(root, f)
                if not os.path.isfile(fp):
                    continue
                # 跳过下载中间态
                if f.endswith((".crdownload", ".tmp", ".part", ".download", ".dat")):
                    continue
                # 截图和其他图片不是账单: 兜底扫描不能把它们认领成本次下载
                if self._is_image_file(fp):
                    self._log(f"[兜底] 忽略图片文件: {f}", "debug")
                    continue
                try:
                    st = os.stat(fp)
                except Exception:
                    continue
                # 只认捕获开始后出现的文件,避免误取上次残留
                if st.st_mtime < self._dl_capture_t0:
                    continue
                # 文件还在写(刚修改过) → 不选
                if now - st.st_mtime < 2:
                    continue
                if st.st_mtime > best_score:
                    best_score = st.st_mtime
                    best = fp
        except Exception:
            pass
        return best

    def _is_stable(self, path):
        """两次采样文件大小一致认为写完(用于兜底检测)"""
        try:
            s1 = os.path.getsize(path)
            time.sleep(1)
            s2 = os.path.getsize(path)
            return s1 == s2
        except Exception:
            return False

    def _finalize_download(self, path):
        """将已下载文件移动/重命名并归入 平台/日期范围 文件夹
        顶层始终保留本次最新文件;若同日期范围已有同名文件,旧版先归档到 his/ 子目录(加时间戳)
        返回最终路径; 失败返回 None
        """
        suggested = os.path.basename(path)
        save_name = self._normalize_download_name(
            suggested, self._export_platform, self._export_start, self._export_end)
        base_dir = self._task_base_dir()
        os.makedirs(base_dir, exist_ok=True)
        final_path = os.path.join(base_dir, save_name)
        # 顶层的"旧文件"记录: 若同名文件已存在且不是本次下载本体,先归档到 历史/
        if os.path.isfile(final_path) and os.path.abspath(path) != os.path.abspath(final_path):
            try:
                his_dir = os.path.join(base_dir, "历史")
                os.makedirs(his_dir, exist_ok=True)
                # 旧文件时间戳 → 归档名: 原名_YYYYMMDD_HHMMSS.扩展名
                ts = datetime.fromtimestamp(os.path.getmtime(final_path)).strftime("%Y%m%d_%H%M%S")
                old_name, old_ext = os.path.splitext(save_name)
                his_path = os.path.join(his_dir, f"{old_name}_{ts}{old_ext}")
                shutil.copy2(final_path, his_path)
            except Exception:
                pass
            try:
                os.remove(final_path)   # 移除顶层旧文件,为本次最新文件腾位
            except Exception:
                shutil.copy2(path, final_path)
                return final_path
        # 多线程/浏览器占用时重试移动
        for attempt in range(10):
            try:
                if os.path.abspath(path) != os.path.abspath(final_path):
                    os.replace(path, final_path)
                return final_path
            except OSError:
                time.sleep(1.5)
                # 若文件已写完但被占用,尝试拷贝而不是移动(避免丢失文件)
                try:
                    shutil.copy2(path, final_path)
                    os.remove(path)
                    return final_path
                except Exception:
                    continue
        return None

    def _cleanup_stale_files(self, root, keep=None):
        """清理下载目录中残留的半成品文件(避免长期占用/堆积)"""
        try:
            for dirpath, _, files in os.walk(root):
                for f in files:
                    if f.startswith(".~") or f.endswith((".crdownload", ".tmp", ".part")):
                        try:
                            os.remove(os.path.join(dirpath, f))
                        except Exception:
                            pass
        except Exception:
            pass

    def latest_export_dir(self):
        """返回当前任务(最近一次导出)的平台/商户/日期范围文件夹路径,便于用户定位文件"""
        return self._task_base_dir()

    def _dir_snapshot(self, root):
        """获取目录下所有文件路径集合"""
        result = set()
        for dirpath, _, files in os.walk(root):
            for f in files:
                result.add(os.path.join(dirpath, f))
        return result

    def _normalize_download_name(self, suggested, platform, start_date, end_date, merchant=None):
        """按设置生成下载文件名(文件名中体现商户)
        - download_name_mode =
            "unified":   统一命名 平台名_商户_日期区间.扩展名
            "original":  保留原始名称,加 商户_ 前缀(UUID/无名称用统一命名兜底)
        扩展名从原始文件名推断,默认 .xlsx
        """
        from core.config import DEFAULT_SETTINGS, load_settings
        mode = load_settings().get("download_name_mode", DEFAULT_SETTINGS["download_name_mode"])
        merchant = merchant or self._export_merchant
        raw = os.path.basename(suggested or "")
        rn, rx = os.path.splitext(raw)
        if mode == "original":
            # 非UUID且有名称: 保留原始文件名,加商户前缀便于区分
            is_uuid = bool(re.match(
                r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
                rn))
            if rn and not is_uuid:
                return f"{merchant}_{raw}" if merchant else raw
        # 统一命名(或UUID兜底)
        name, ext = os.path.splitext(raw)
        ext = (ext or "").lower()
        if ext not in (".xlsx", ".xls", ".csv", ".txt"):
            ext = ".xlsx"
        base = platform or "download"
        if merchant:
            base = f"{base}_{merchant}"
        date_part = f"_{start_date}_{end_date}" if start_date and end_date else ""
        return f"{base}{date_part}{ext}"

    def screenshot(self, name="screenshot"):
        """截图保存到下载根目录(通用入口)"""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(os.path.abspath(DOWNLOAD_DIR), f"{name}_{ts}.png")
        try:
            self.page.screenshot(path=path, full_page=False)
            self._log(f"截图已保存: {path}")
        except Exception:
            pass
        return path

    def snapshot(self, step_name="step"):
        """步骤快照: 按当前任务上下文(平台/商户/日期)存到 snapshots/ 子目录。
        平台脚本在关键步骤(设日期/查询/点下载/等弹窗)调用,失败时一眼看到当时页面状态。
        返回截图完整路径(失败返回None)。"""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        # 存到 downloads/平台/商户/日期/snapshots/步骤_时间.png
        snap_dir = os.path.join(self._task_base_dir(), "snapshots")
        try:
            os.makedirs(snap_dir, exist_ok=True)
        except Exception:
            snap_dir = os.path.abspath(DOWNLOAD_DIR)   # 回退到下载根目录
        # 清理非法文件名字符
        safe_step = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(step_name or "step")).strip() or "step"
        path = os.path.join(snap_dir, f"{safe_step}_{ts}.png")
        try:
            self.page.screenshot(path=path, full_page=False)
        except Exception as e:
            self._log(f"步骤快照失败[{step_name}]: {str(e)[:80]}", "warning")
            return None
        return path

    def snapshot_on_failure(self, step_name="failure"):
        """失败现场截图: 包含页面URL/标题,便于排查"""
        try:
            info = self.get_page_info()
            self._log(f"失败现场: url={info.get('url','')[:80]} title={info.get('title','')[:40]}",
                      "warning")
        except Exception:
            pass
        return self.snapshot(f"FAIL_{step_name}")

    def get_page_info(self):
        """获取当前页面信息"""
        try:
            return {"url": self.page.url, "title": self.page.title()}
        except Exception:
            return {"url": "", "title": ""}

    # ===== 用户友好的辅助方法(供平台脚本调用) =====

    def sleep(self, seconds):
        """等待指定秒数(页面加载/动画过渡)"""
        time.sleep(seconds)

    def wait_for(self, selector=None, text=None, timeout=15, stable_for=1.0,
                 network_idle=True):
        """语义化等待,替代固定 sleep(机器快省时间,慢机器不误判)。
        条件满足即返回:
        - selector: 指定 CSS 选择器的元素出现
        - text:     指定页面文字出现(忽略空白,如"确 定")
        条件满足后可再等待网络空闲稳定(stable_for),避免"刚出现就操作"。
        返回 True(满足) / False(超时,不抛异常)。"""
        end = time.time() + timeout
        while time.time() < end:
            met = False
            if selector:
                try:
                    if self.page.locator(selector).count() > 0:
                        met = True
                except Exception:
                    pass
            if text and not met:
                met = self.is_visible_text(text, timeout=1)
            if met:
                if network_idle and stable_for and stable_for > 0:
                    try:
                        self.page.wait_for_load_state("networkidle",
                                                      timeout=int(stable_for * 1000))
                    except Exception:
                        pass
                return True
            time.sleep(0.3)
        self._log(f"等待超时: selector={selector or ''} text={text or ''} ({timeout}s)", "warning")
        return False

    def set_step_debug(self, enabled, callback=None):
        """开启/关闭单步调试模式。
        enabled=True 时,平台脚本调用 step_pause() 会在每步后暂停,
        等待 callback 返回真值(由 GUI 弹"继续/中止"对话框触发)再继续。
        callback(step_name) -> True 继续 / False 中止导出"""
        self._step_debug = bool(enabled)
        self._step_callback = callback if enabled else None
        self._log(f"单步调试模式: {'开' if enabled else '关'}")

    def step_pause(self, step_name="step"):
        """单步暂停: 在关键步骤处调用,开启调试模式时弹出"继续"确认,关闭时直接跳过。
        平台脚本可在 export 流程任意位置插入,排查"到底哪一步开始不对"。"""
        if not self._step_debug:
            return True
        # 自动截图,便于看当前页面状态
        self.snapshot(f"DEBUG_{step_name}")
        self._log(f"[单步] 已暂停: {step_name}(确认后继续)")
        if self._step_callback:
            try:
                cont = self._step_callback(step_name)
                if not cont:
                    self._log(f"[单步] 用户中止于: {step_name}", "warning")
                    raise RuntimeError(f"用户中止单步调试: {step_name}")
                return True
            except RuntimeError:
                raise
            except Exception as e:
                self._log(f"[单步] 回调异常,继续执行: {str(e)[:60]}")
                return True
        # 无回调时只截一张图、不阻塞(开发环境用)
        return True

    def set_user_wait_callback(self, callback):
        """注入"等待用户手动操作"回调(由 GUI 在 UI 线程弹窗提醒并阻塞,等用户完成)。
        用于资金流水导出需要微信扫码确认等场景。callback(prompt) 在用户确认后返回。"""
        self._user_wait_callback = callback

    def wait_user(self, prompt, timeout=600):
        """停留等待用户手动操作(如微信扫码确认资金流水)。
        提醒用户 + 阻塞等待,直到用户完成确认才能继续后续下载。
        有注入回调时: 由 GUI 弹窗提醒并阻塞; 无回调时: 打日志并按 timeout 停留兜底。
        返回 True(继续导出)。"""
        self.snapshot("等待用户确认")
        self._log(f"[待处理] {prompt}", "warning")
        if self._user_wait_callback:
            try:
                self._user_wait_callback(prompt)
                self._log("[待处理] 用户已确认,继续")
                return True
            except Exception as e:
                self._log(f"[待处理] 用户确认回调异常: {str(e)[:60]}", "warning")
        # 无回调兜底: 静默停留,给用户手动操作时间
        deadline = time.time() + max(1, int(timeout))
        while time.time() < deadline:
            time.sleep(2)
        return True

    @staticmethod
    def _text_pattern(text):
        """把文本转成可忽略任意空白的正则,解决"确 定"这类按钮文案匹配不到的问题"""
        return re.compile(r"\s*".join(re.escape(ch) for ch in text))

    def click_text(self, text, exact=False, retries=3):
        """点击包含指定文字的按钮/链接
        匹配优先级: 普通文本 → 忽略空白(确 定)→ JS 直接触发。
        后两种可绕过遮挡/视口外/不可见及文案含空格导致的点击失败。"""
        for attempt in range(1, retries + 1):
            try:
                loc = self.page.get_by_text(text, exact=exact)
                if loc.count() > 0:
                    loc.first.click(timeout=5000)
                    self._log(f"点击文字成功: {text}")
                    return True
            except Exception as e:
                self._log(f"点击文字失败(第{attempt}次): {text} - {str(e)[:60]}", "warning")
                # 回退1: 忽略空白匹配("确 定" → /确\s*定/)
                try:
                    loc = self.page.get_by_text(self._text_pattern(text), exact=False)
                    if loc.count() > 0:
                        loc.first.click(timeout=5000)
                        self._log(f"点击文字成功(忽略空格): {text}")
                        return True
                except Exception as e2:
                    self._log(f"点击文字忽略空格失败: {text} - {str(e2)[:60]}", "warning")
                # 回退2: JS 直接触发点击
                try:
                    loc = self.page.get_by_text(text, exact=exact).first
                    loc.evaluate("el => el.click()")
                    self._log(f"点击文字成功(JS回退): {text}")
                    return True
                except Exception as e3:
                    self._log(f"点击文字JS回退失败: {text} - {str(e3)[:60]}", "warning")
                if attempt < retries:
                    time.sleep(2)
        self._log(f"点击文字最终失败: {text}", "error")
        return False

    def click_selector(self, selector, retries=3):
        """点击指定CSS选择器元素"""
        return self.safe_click(selector, description=selector, retries=retries)

    def fill_placeholder(self, placeholder, value, retries=2):
        """向placeholder匹配的输入框填写内容"""
        for attempt in range(1, retries + 1):
            try:
                loc = self.page.get_by_placeholder(placeholder)
                if loc.count() > 0:
                    loc.first.click(timeout=3000)
                    loc.first.fill(value)
                    self._log(f"填写成功: {placeholder} = {value}")
                    return True
            except Exception as e:
                self._log(f"填写失败(第{attempt}次): {placeholder} - {str(e)[:60]}", "warning")
                if attempt < retries:
                    time.sleep(2)
        return False

    def fill_selector(self, selector, value, retries=2):
        """向CSS选择器匹配的输入框填写内容"""
        return self.safe_fill(selector, value, description=selector, retries=retries)

    def is_visible_text(self, text, timeout=3):
        """判断页面上是否出现指定文字(支持忽略空白,如"确 定")。
        先按忽略空白正则匹配(能命中"确 定"),失败再按普通文本匹配,避免白等。"""
        try:
            self.page.get_by_text(self._text_pattern(text), exact=False).first.wait_for(
                timeout=timeout * 1000)
            return True
        except Exception:
            pass
        try:
            self.page.get_by_text(text, exact=False).first.wait_for(timeout=timeout * 1000)
            return True
        except Exception:
            return False

    def close_popup(self, retries=2):
        """
        关闭页面上的弹窗(广告/公告/引导弹窗)
        没有弹窗时自动跳过,不会误操作
        返回: True(关闭了弹窗) / False(没有弹窗)

        注意: 当前各平台导出流程均无引导/公告弹窗,为使每次导出不再空扫描,
        此特性先暂停(直接返回 False)。**后续若出现弹窗需关闭,删掉下面一行
        `return False` 即可恢复下方扫描逻辑。**
        """
        return False  # [已暂停] 无弹窗,跳过 close_popup 扫描;需要时删除本行恢复
        # 常见弹窗关闭按钮选择器(按优先级)
        popup_selectors = [
            ".zent-dialog__close",          # 有赞 zent 对话框
            ".zent-dialog__header .zenticon-close",
            ".el-dialog__headerbtn",        # element-ui
            ".ant-modal-close",             # antd
            ".el-icon-close",
            ".modal-close",
            ".popup-close",
            ".close-btn",
            ".dialog-close",
        ]
        # 通用关闭图标(可能出现在非弹窗位置,放后面)
        icon_selectors = [
            ".zenticon-close",
            ".icon-close",
            ".close",
        ]
        # 文字关闭按钮
        text_buttons = ["我知道了", "知道了", "不再提示", "跳过", "关闭"]

        for attempt in range(1, retries + 1):
            closed = False
            # 1. 优先点击弹窗容器内的关闭按钮
            for sel in popup_selectors:
                try:
                    loc = self.page.locator(sel)
                    el = loc.first
                    # 只点"可见"的关闭按钮: DOM 中常驻但隐藏的图标(.el-icon-close 等)
                    # 会占用 click 的 2s 超时等待,逐个累积成十几秒空转,这里用 is_visible 快速过滤
                    if loc.count() == 0 or not el.is_visible():
                        continue
                    el.click(timeout=1500)
                    self._log(f"已关闭弹窗: {sel}")
                    self.sleep(1)
                    closed = True
                    break
                except Exception:
                    continue
            if closed:
                return True
            # 2. 尝试通用关闭图标
            for sel in icon_selectors:
                try:
                    loc = self.page.locator(sel)
                    el = loc.first
                    if loc.count() == 0 or not el.is_visible():
                        continue
                    el.click(timeout=1500)
                    self._log(f"已关闭弹窗: {sel}")
                    self.sleep(1)
                    return True
                except Exception:
                    continue
            # 3. 尝试文字按钮
            for t in text_buttons:
                try:
                    loc = self.page.get_by_text(t, exact=True)
                    el = loc.first
                    if loc.count() == 0 or not el.is_visible():
                        continue
                    el.click(timeout=1500)
                    self._log(f"已关闭弹窗: {t}")
                    self.sleep(1)
                    return True
                except Exception:
                    continue
            if attempt < retries:
                self.sleep(1)
        self._log("未检测到弹窗,跳过")
        return False

    def close(self):
        try:
            if self.context:
                self.context.close()
        except Exception:
            pass
        try:
            if self.playwright:
                self.playwright.stop()
        except Exception:
            pass

    # ===== 操作追踪(辅助编写平台脚本: 记录测试浏览器中的点击/输入元素信息) =====

    TRACE_SCRIPT = r"""
    (function() {
      if (window.__trace_injected__) return;
      window.__trace_injected__ = true;
      function brief(el) {
        if (!el) return {};
        return {
          tag: (el.tagName || '').toLowerCase(),
          text: (el.innerText || el.textContent || '').trim().slice(0, 50),
          cls: (typeof el.className === 'string' ? el.className : '').slice(0, 120),
          id: el.id || '',
          ph: el.getAttribute && el.getAttribute('placeholder') || '',
          name: el.getAttribute && el.getAttribute('name') || '',
          href: el.getAttribute && el.getAttribute('href') || ''
        };
      }
      document.addEventListener('click', function(e) {
        var el = e.target.closest('a,button,input,select,textarea,[role=button],[class*=btn],[class*=button]') || e.target;
        console.log('[TRACE_CLICK] ' + JSON.stringify(brief(el)));
      }, true);
      document.addEventListener('change', function(e) {
        var el = e.target;
        if (el && (el.tagName === 'INPUT' || el.tagName === 'SELECT' || el.tagName === 'TEXTAREA')) {
          console.log('[TRACE_CHANGE] ' + JSON.stringify(brief(el)));
        }
      }, true);
    })();
    """

    def enable_action_trace(self, record_to_file=True):
        """开启操作追踪: 向浏览器注入脚本,把用户在页面上的点击/输入元素信息
        通过 console 消息回传,记录到日志,辅助编写平台脚本。
        仅用于"打开商户"手工测试模式。
        record_to_file=True 时同时把结构化步骤追加到 recordings/*.jsonl,
        可用 tools/recording_to_script.py 转成 export.py 骨架。"""
        try:
            if not self.context:
                return False
            self.context.add_init_script(self.TRACE_SCRIPT)
            if self.page and not self.page.is_closed():
                self.page.on("console", self._on_trace_console)
            # 准备录制文件路径(用 平台_商户_时间戳 命名)
            if record_to_file:
                rec_dir = os.path.join(ROOT_DIR, "recordings")
                try:
                    os.makedirs(rec_dir, exist_ok=True)
                except Exception:
                    rec_dir = os.path.abspath(DOWNLOAD_DIR)
                plat = self._safe_name(self._export_platform or "unknown")
                merch = self._safe_name(self._export_merchant or "default")
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                self._recording_file = os.path.join(rec_dir, f"{plat}_{merch}_{ts}.jsonl")
            self._log("已开启操作追踪: 页面上的点击/输入会记录到日志"
                      + (f" 并录制到 {os.path.basename(self._recording_file)}" if self._recording_file else ""))
            return True
        except Exception as e:
            self._log(f"开启操作追踪失败: {e}", "warning")
            return False

    def _on_trace_console(self, msg):
        """接收页面 console 中的操作追踪消息: 写入日志 + 追加到结构化录制文件"""
        try:
            text = msg.text or ""
            if not text.startswith("[TRACE_"):
                return
            self._log(text)
            # 追加到 JSONL 录制文件,供后续生成脚本骨架
            if not self._recording_file:
                return
            # 解析 "[TRACE_CLICK] {json}" / "[TRACE_CHANGE] {json}"
            try:
                tag_end = text.index("]") + 1
                tag = text[:tag_end]   # [TRACE_CLICK]
                payload = text[tag_end:].strip()
                info = json.loads(payload) if payload else {}
            except Exception:
                tag, info = text, {}
            record = {
                "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "type": tag.replace("[", "").replace("]", "").replace("TRACE_", "").lower(),
                "url": (self.page.url if self.page else "")[:200],
                "element": info,
            }
            try:
                with open(self._recording_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
            except Exception:
                pass
        except Exception:
            pass

    # ===== 登录态导出/恢复(解决 session cookie 关闭即丢,如微信支付) =====

    LOGIN_STATE_FILE = "login_state.json"

    def save_login_state(self):
        """导出当前登录态(cookie + localStorage)到 profile 目录,供下次启动恢复。
        context.storage_state() 会包含 session cookie(无过期时间),正好解决
        Playwright persistent context 关闭时不保留 session cookie 的问题。"""
        try:
            if not self.context:
                return False
            state = self.context.storage_state()
            path = os.path.join(self._profile_dir, self.LOGIN_STATE_FILE)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False)
            self._log(f"登录态已保存({len(state.get('cookies', []))} 个 cookie)")
            return True
        except Exception as e:
            self._log(f"保存登录态失败: {e}", "warning")
            return False

    def _restore_login_state(self):
        """启动后恢复上次保存的登录态: 注入 cookie + 用初始化脚本恢复 localStorage。"""
        try:
            path = os.path.join(self._profile_dir, self.LOGIN_STATE_FILE)
            if not os.path.isfile(path):
                return
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
            cookies = state.get("cookies") or []
            if cookies:
                self.context.add_cookies(cookies)
            # 恢复 localStorage(通过 add_init_script 在页面加载前写入)
            origins = state.get("origins") or []
            ls_map = {}
            for origin in origins:
                origin_url = origin.get("origin", "")
                items = {it.get("name", ""): it.get("value", "")
                         for it in origin.get("localStorage", []) if it.get("name")}
                if items:
                    ls_map[origin_url] = items
            if ls_map:
                init = ("window.addEventListener('DOMContentLoaded', function(){"
                        "try{var m=" + json.dumps(ls_map, ensure_ascii=False) +
                        ";for(var o in m){if(location.origin===o){"
                        "for(var k in m[o]){localStorage.setItem(k,m[o][k]);}}}}catch(e){}});")
                self.context.add_init_script(init)
            self._log(f"已恢复登录态({len(cookies)} 个 cookie)")
        except Exception as e:
            self._log(f"恢复登录态失败: {e}", "warning")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
