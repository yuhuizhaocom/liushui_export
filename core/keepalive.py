"""
登录保活服务 - 程序运行期后台周期刷新各商户登录态
- 独立后台线程, 间隔可配置
- 巡检时若 app.running 为真(正在导出/调试)则跳过本轮, 不与之并发
- 浏览器实例由 make_browser 工厂创建(便于测试注入), 每商户独立 profile
"""
import threading
import time
from datetime import datetime

from core.logger import log


class KeepAliveService:
    def __init__(self, app, interval_min=30, enabled=True, make_browser=None):
        self.app = app
        self.interval_min = max(1, int(interval_min))
        self.enabled = enabled
        self.make_browser = make_browser or self._default_browser
        self._stop = threading.Event()
        self._thread = None

    @staticmethod
    def _default_browser(key, merchant):
        from core.browser import BrowserManager
        b = BrowserManager(headless=True)
        b.set_browser_profile(key, merchant)
        return b

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        log("保活服务已启动(间隔 %d 分钟)" % self.interval_min)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        log("保活服务已停止")

    def _run(self):
        while not self._stop.wait(self.interval_min * 60):
            if not self.enabled:
                continue
            if getattr(self.app, "running", False):
                log("保活巡检跳过(有任务执行中)")
                continue
            try:
                self.run_once()
            except Exception as e:
                log(f"保活巡检异常: {e}", "error")

    def run_once(self):
        """巡检一轮: 逐个商户打开登录页刷新会话并判定状态。"""
        for key, merchant in self._iter_merchants():
            b = None
            try:
                b = self.make_browser(key, merchant)
                b.start()
                url = getattr(self.app, "login_urls", {}).get(key, "") or ""
                if url:
                    b.navigate(url)
                b.sleep(3)
                b.close_popup()
                info = b.get_page_info()
                cur_url = (info.get("url") or "").lower()
                title = info.get("title") or ""
                if "login" in cur_url or "登录" in title:
                    self._mark(key, "error")
                    log(f"保活: {key}/{merchant} 登录已失效, 建议重新登录", "warning")
                else:
                    self._mark(key, "ok")
                    log(f"保活: {key}/{merchant} 会话有效")
            except Exception as e:
                self._mark(key, "error")
                log(f"保活: {key}/{merchant} 巡检失败 - {e}", "error")
            finally:
                if b is not None:
                    try:
                        b.close()
                    except Exception:
                        pass

    def _iter_merchants(self):
        """迭代器: 取勾选且有商户的平台×商户。app 需提供该接口。"""
        method = getattr(self.app, "iter_selected_merchants", None)
        if method is None:
            return
        yield from method() or []

    def _mark(self, key, status):
        fn = getattr(self.app, "set_platform_status", None)
        if fn:
            fn(key, status)