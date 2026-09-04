"""
浏览器管理模块 - 持久化上下文 + 容错重试
"""

import os
import re
import shutil
import time
import glob
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

PW_BROWSERS_PATH = r"C:\pw_browsers"
if os.path.isdir(PW_BROWSERS_PATH):
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = PW_BROWSERS_PATH

from playwright.sync_api import sync_playwright
from config import BROWSER_DATA_DIR, DOWNLOAD_DIR
from logger import log

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
        self._setup_dirs()

    @staticmethod
    def _safe_name(name):
        """清理名称中的路径非法字符,避免目录逃逸"""
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name or "")).strip()
        return name or "default"

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
                        "--start-fullscreen",   # 全屏显示,便于操作查看
                        "--disable-features=Translate",
                    ],
                )
                if len(self.context.pages) > 0:
                    self.page = self.context.pages[0]
                else:
                    self.page = self.context.new_page()
                # 监听浏览器下载事件,推入队列(即使文件先写完也不丢事件)
                self.context.on("download", self._on_download)
                self._log("浏览器启动成功")
                return self.page
            except Exception as e:
                self._log(f"浏览器启动失败(第{attempt}次): {str(e)[:100]}", "warning")
                self._cleanup_lock_files()
                if attempt < 3:
                    time.sleep(3)
                else:
                    self._log("浏览器启动最终失败", "error")
                    raise

    def navigate(self, url, retries=3):
        """带重试的页面导航"""
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

    def safe_click(self, selector, description="", retries=3):
        """安全点击元素,带重试和等待"""
        for attempt in range(1, retries + 1):
            try:
                el = self.page.wait_for_selector(selector, timeout=10000)
                if el:
                    el.click(timeout=5000)
                    self._log(f"点击成功: {description or selector}")
                    return True
            except Exception as e:
                self._log(f"点击失败(第{attempt}次): {description or selector} - {e}", "warning")
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
        self._carry_over_orphans()  # 把上次残留的已下载文件(如UUID)归位
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
        """把下载根目录下上次残留的已下载文件(如UUID无扩展名)移动到平台文件夹"""
        try:
            root = os.path.abspath(DOWNLOAD_DIR)
            orphan_ok = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                                   r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
            for f in os.listdir(root):
                fp = os.path.join(root, f)
                if not os.path.isfile(fp):
                    continue
                name, ext = os.path.splitext(f)
                # 残留UUID文件(无扩展名或为已有内容)归入平台文件夹
                if orphan_ok.match(name):
                    final = self._finalize_download(fp)
                    if final:
                        self._log(f"已归位残留下载: {os.path.basename(final)}")
        except Exception:
            pass

    def end_wait_download(self):
        """结束捕获模式(一次等待结束,清空队列、收走根目录残留、清理临时目录)"""
        self._dl_queue = []
        self._dl_capture_on = False
        self._carry_over_orphans()  # 收走根目录可能残留的原始下载文件(UUID)
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
                    final = self._finalize_download(tmp_path)
                    if final:
                        self.end_wait_download()
                        self._log(f"下载完成: {os.path.basename(final)}")
                        return final
                # 文件还未就绪则继续轮询等待
            # 2) 兜底: 目录中"等待开始后"新出现且已写完整的文件
            for fp in sorted(self._dir_snapshot(root) - known, key=os.path.getmtime, reverse=True):
                name = os.path.basename(fp)
                if name.endswith((".crdownload", ".tmp", ".part", ".download", ".dat")):
                    continue
                if self._is_stable(fp):
                    final = self._finalize_download(fp)
                    if final:
                        self.end_wait_download()
                        self._log(f"下载完成: {os.path.basename(final)}")
                        return final
            time.sleep(1.5)
        self.end_wait_download()
        self._log("等待下载超时", "warning")
        self._cleanup_stale_files(root)
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
        from config import DEFAULT_SETTINGS, load_settings
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
        """截图保存"""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(os.path.abspath(DOWNLOAD_DIR), f"{name}_{ts}.png")
        try:
            self.page.screenshot(path=path, full_page=False)
            self._log(f"截图已保存: {path}")
        except Exception:
            pass
        return path

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

    def click_text(self, text, exact=False, retries=3):
        """点击包含指定文字的按钮/链接"""
        for attempt in range(1, retries + 1):
            try:
                loc = self.page.get_by_text(text, exact=exact)
                if loc.count() > 0:
                    loc.first.click(timeout=5000)
                    self._log(f"点击文字成功: {text}")
                    return True
            except Exception as e:
                self._log(f"点击文字失败(第{attempt}次): {text} - {str(e)[:60]}", "warning")
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
        """判断页面上是否出现指定文字"""
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
        """
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
                    if loc.count() > 0:
                        loc.first.click(timeout=2000)
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
                    if loc.count() > 0:
                        loc.first.click(timeout=2000)
                        self._log(f"已关闭弹窗: {sel}")
                        self.sleep(1)
                        return True
                except Exception:
                    continue
            # 3. 尝试文字按钮
            for t in text_buttons:
                try:
                    loc = self.page.get_by_text(t, exact=True)
                    if loc.count() > 0:
                        loc.first.click(timeout=2000)
                        self._log(f"已关闭弹窗: {t}")
                        self.sleep(1)
                        return True
                except Exception:
                    continue
            if attempt < retries:
                self.sleep(1)
        self._log("未检测到弹窗,跳过")
        return False

    def fill_zent_date_range(self, start_date, end_date):
        """
        填写有赞 zent 日期范围选择器
        开始时间自动补 00:00:00,结束时间自动补 23:59:59
        方式: 点击触发区域 → input原生setter+input事件(React真正更新) → 回车
        失败: 日历面板点击日期
        返回: True(成功) / False(失败)
        """
        triggers = self.page.locator(".zent-datepicker-trigger")
        if triggers.count() < 2:
            self._log("未找到 zent 日期范围选择器", "warning")
            return False
        # 纯日期自动补时间(有赞格式: 开始00:00:00, 结束23:59:59)
        start_val = start_date if len(start_date) > 10 else start_date + " 00:00:00"
        end_val = end_date if len(end_date) > 10 else end_date + " 23:59:59"
        ok1 = self._fill_zent_trigger(triggers.nth(0), start_val)
        ok2 = self._fill_zent_trigger(triggers.nth(1), end_val)
        if ok1 and ok2:
            self._log(f"已设置日期范围: {start_val} ~ {end_val}")
            return True
        self._log("日期范围设置未完全成功", "warning")
        return False

    def fill_zent_date(self, value, trigger_index=0):
        """
        填写单个 zent 日期选择器
        trigger_index: 0=第一个(开始), 1=第二个(结束)
        """
        triggers = self.page.locator(".zent-datepicker-trigger")
        if trigger_index >= triggers.count():
            return False
        return self._fill_zent_trigger(triggers.nth(trigger_index), value)

    def build_youzan_url(self, url, start_date, end_date):
        """构造带日期参数的有赞流水页 URL(UTC+8 毫秒时间戳),不执行跳转"""
        try:
            cst = timezone(timedelta(hours=8))
            start_ts = int(datetime.strptime(start_date[:10], "%Y-%m-%d")
                           .replace(tzinfo=cst).timestamp() * 1000)
            end_ts = int(datetime.strptime(end_date[:10], "%Y-%m-%d")
                         .replace(hour=23, minute=59, second=59,
                                  microsecond=999000, tzinfo=cst).timestamp() * 1000)
            parsed = urlparse(url)
            qs = parse_qs(parsed.query)
            qs["startTime"] = [str(start_ts)]
            qs["endTime"] = [str(end_ts)]
            qs["dateType"] = ["SETTLE_TIME"]
            new_query = urlencode(qs, doseq=True)
            return urlunparse(parsed._replace(query=new_query))
        except Exception as e:
            self._log(f"构造有赞URL失败: {e}", "warning")
            return url

    def open_youzan_record(self, url, start_date, end_date):
        """一次性打开有赞流水页并设置日期(仅导航一次,不再二次跳转)"""
        target = self.build_youzan_url(url, start_date, end_date)
        self._log(f"打开有赞流水页(日期 {start_date} ~ {end_date})")
        return self.navigate(target)

    def set_youzan_date_range_by_url(self, start_date, end_date):
        """
        通过URL参数设置有赞日期范围(绕开日期选择器)
        有赞页面把日期同步到URL的 startTime/endTime(毫秒时间戳, UTC+8)
        返回: True(成功) / False(失败)
        """
        new_url = self.build_youzan_url(self.page.url, start_date, end_date)
        self._log(f"通过URL设置日期: {start_date} ~ {end_date}")
        if not new_url:
            return False
        self.navigate(new_url)
        self.sleep(3)
        return True

    def _fill_zent_trigger(self, trigger, value):
        """点击 zent 日期触发区域,用原生value setter+input事件设置值(React真正更新)"""
        for attempt in range(1, 4):
            try:
                trigger.click(timeout=5000)
                # 等待 input 出现(trigger内或弹出面板内,最多5秒)
                inp = None
                for _ in range(10):
                    inp = trigger.locator("input")
                    if inp.count() > 0:
                        break
                    inp = self.page.locator(
                        ".zent-datepicker-panel input, .zent-datepicker-popup input, "
                        ".zent-popover input, .zent-datepicker-popper input"
                    )
                    if inp.count() > 0:
                        break
                    self.sleep(0.5)
                if inp is not None and inp.count() > 0:
                    # React受控组件标准更新: 原生value setter + input事件
                    for fmt in [value, value[:10]]:
                        try:
                            self.page.evaluate("""(el, v) => {
                                const setter = Object.getOwnPropertyDescriptor(
                                    window.HTMLInputElement.prototype, 'value').set;
                                setter.call(el, v);
                                el.dispatchEvent(new Event('input', { bubbles: true }));
                                el.dispatchEvent(new Event('change', { bubbles: true }));
                            }""", inp.first, fmt)
                            self.page.keyboard.press("Enter")
                            self.sleep(1)
                            return True
                        except Exception:
                            continue
                # input方式失败,尝试日历面板点击
                if self._fill_zent_by_calendar(trigger, value):
                    return True
            except Exception:
                pass
            if attempt < 3:
                self.sleep(1)
        self.screenshot(f"zent_date_fail_{int(time.time())}")
        self._log(f"zent日期设置失败: {value}", "warning")
        return False

    def _fill_zent_by_calendar(self, trigger, value):
        """点击触发区域,在日历面板中点击具体日期(组件自己更新状态)"""
        try:
            target = datetime.strptime(value[:10], "%Y-%m-%d")
        except Exception:
            return False
        for attempt in range(1, 4):
            try:
                trigger.click(timeout=5000)
                self.sleep(1)
                # 确认日历面板弹出(宽松选择器)
                panel = self.page.locator(
                    ".zent-datepicker-panel, [class*='zent-datepicker-panel'], "
                    "[class*='zent-datepicker'] [class*='panel']"
                )
                if panel.count() == 0:
                    self._log(f"未找到日历面板, 可见日期元素: {self._visible_datepicker_classes()}", "warning")
                    self.sleep(1)
                    continue
                self._log(f"找到日历面板: {panel.first.get_attribute('class')}")
                # 翻月到目标年月
                if not self._navigate_calendar_to(target.year, target.month):
                    self._log("翻月失败", "warning")
                    continue
                # 点击目标日期
                if self._click_calendar_cell(target.year, target.month, target.day):
                    self.sleep(1)
                    return True
            except Exception:
                pass
        return False

    def _visible_datepicker_classes(self):
        """输出可见的日期选择器相关元素的class列表(诊断用)"""
        try:
            return self.page.evaluate("""() => {
                const out = [];
                for (const el of document.querySelectorAll(
                    '[class*="datepicker"],[class*="DatePicker"],' +
                    '[class*="calendar"],[class*="Calendar"],[class*="picker"]')) {
                    if (el.offsetParent !== null) out.push(el.className);
                }
                return out.slice(0, 30).join(' | ');
            }""")
        except Exception:
            return "?"

    def _navigate_calendar_to(self, year, month):
        """翻月到目标年月,返回是否成功"""
        for _ in range(24):  # 最多翻24个月
            current = self._get_calendar_month()
            if not current:
                return False
            if current[0] == year and current[1] == month:
                return True
            if (year, month) > (current[0], current[1]):
                btn = self.page.locator(
                    ".zent-datepicker-panel-header-btn--right, "
                    ".zent-datepicker-panel-header-btn:last-child, "
                    ".zent-datepicker-panel-header .zenticon-right, "
                    ".zent-datepicker-panel-header-right, "
                    ".zent-datepicker-panel-header .zenticon-chevron-right"
                )
            else:
                btn = self.page.locator(
                    ".zent-datepicker-panel-header-btn--left, "
                    ".zent-datepicker-panel-header-btn:first-child, "
                    ".zent-datepicker-panel-header .zenticon-left, "
                    ".zent-datepicker-panel-header-left, "
                    ".zent-datepicker-panel-header .zenticon-chevron-left"
                )
            if btn.count() == 0:
                return False
            try:
                btn.first.click(timeout=3000)
            except Exception:
                return False
            self.sleep(0.5)
        return False

    def _click_calendar_cell(self, year, month, day):
        """点击日历面板中指定日期的单元格"""
        date_str = f"{year:04d}-{month:02d}-{day:02d}"
        # 1. 按 data-date 属性精确匹配(多种属性名)
        for attr in ["data-date", "data-date-value", "data-value", "data-day"]:
            try:
                loc = self.page.locator(f'[{attr}="{date_str}"]')
                if loc.count() > 0:
                    loc.first.click(timeout=3000)
                    self.sleep(1)
                    return True
            except Exception:
                continue
        # 2. 用JS在面板内精确匹配日期数字文本(避免误点其他元素)
        try:
            ok = self.page.evaluate("""(day) => {
                const panel = document.querySelector(
                    '.zent-datepicker-panel, [class*="zent-datepicker-panel"]');
                if (!panel) return false;
                const cells = panel.querySelectorAll('[class*="cell"], td, [class*="date"]');
                for (const c of cells) {
                    const t = (c.textContent || '').trim();
                    if (t === String(day) && c.offsetParent !== null) {
                        c.click();
                        return true;
                    }
                }
                return false;
            }""", day)
            if ok:
                self.sleep(1)
                return True
        except Exception:
            pass
        # 3. 失败时输出面板内单元格真实HTML(诊断用)
        try:
            diag = self.page.evaluate("""() => {
                const panel = document.querySelector(
                    '.zent-datepicker-panel, [class*="zent-datepicker-panel"]');
                if (!panel) return 'no panel';
                const cells = panel.querySelectorAll('[class*="cell"], td');
                const out = [];
                for (const c of cells) {
                    if (c.offsetParent !== null) {
                        out.push(c.outerHTML);
                        if (out.length >= 8) break;
                    }
                }
                return out.join(' || ');
            }""")
            self._log(f"日期单元格HTML: {diag}", "warning")
        except Exception:
            pass
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

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
