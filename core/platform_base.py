"""
平台基类 - 所有平台脚本必须继承此类

使用方式:
    1. 在 platforms/ 目录下创建平台文件夹(如 platforms/youzan/)
    2. 在文件夹中创建 export.py
    3. 继承 PlatformBase,填写平台信息,实现 export 方法
    4. 程序启动时自动发现并加载

示例:
    from core.platform_base import PlatformBase

    class YouzanExporter(PlatformBase):
        key = "youzan"
        name = "有赞"
        login_url = "https://..."
        export_url = "https://..."
        guide = "进入后台 → 数据 → 交易明细 → 导出"

        def export(self, browser, start_date, end_date):
            browser.navigate(self.export_url)
            browser.sleep(2)
            browser.fill_placeholder("开始日期", start_date)
            browser.fill_placeholder("结束日期", end_date)
            browser.click_text("查询")
            browser.sleep(2)
            browser.click_text("导出")
            path = browser.wait_download(timeout=120)
            return "success" if path else "manual"
"""


class PlatformBase:
    # ===== 平台元信息(必须填写) =====
    key = ""            # 唯一标识,建议与文件夹名一致,如 "youzan"
    name = ""           # 显示名称,如 "有赞"
    login_url = ""      # 登录页面地址
    export_url = ""     # 流水导出页面地址
    guide = ""          # 操作指引(给用户看的提示文字)
    enabled = True      # 是否在界面中显示
    manual_intervention = False  # 是否需要人工操作(如扫码/手动确认)。True 时导出排到最后,先自动跑完自动化平台
    intervention_hint = ""       # 人工操作说明(选填,便于日志/提示)

    # 关键元素选择器(可选,声明后可在导出前自检页面结构是否变更)。
    # 形如: {"date_start": ".el-range-input:nth-child(1)", "query_btn": "button.el-button--primary"}
    # 自检失败的元素会记日志/返回缺失清单,不必等下载失败才发现页面改版。
    SELECTORS = {}

    def login(self, browser):
        """
        登录流程(可选覆盖)

        默认: 打开登录页,等待用户手动登录
        如需特殊处理(如点击"登录"按钮跳转),可覆盖此方法
        """
        browser.navigate(self.login_url)

    def check_login(self, browser):
        """
        检查当前平台是否已登录(可选覆盖)。

        思路: 直接访问受保护的导出地址(export_url)。
            - 已登录: 能停留在导出页,URL 不含登录路径 → True
            - 未登录: 被重定向到独立的登录路径(URL 含 /login|sso|passport|cas 等)→ False

        相比"访问 login_url + URL/标题含 login/登录 就判未登录"的旧逻辑,
        不会把"已登录但登录页标题仍带登录字样"的情形误判为未登录。

        注意: "扫码页与后台同域、访问导出页不重定向"的平台(如微信支付),
        URL 保持不变无法区分,必须覆盖本方法改用页面文案/元素判断。
        """
        export_url = (self.export_url or "").lower()
        if "/login" in export_url or "login." in export_url:
            # 导出地址本身落在登录路径时,退化为旧判断(一般不会发生)
            browser.navigate(self.login_url or self.export_url)
            browser.sleep(2)
            info = browser.get_page_info()
            url = info.get("url", "").lower()
            return not self._is_login_url(url)
        browser.navigate(self.export_url)
        browser.wait_for(timeout=3)  # 等未登录重定向落地(服务端302/前端路由跳转)
        url = (browser.get_page_info().get("url", "") or "").lower()
        if self._is_login_url(url):
            return False
        return True

    @staticmethod
    def _is_login_url(url):
        """判定 URL 是否位于"未登录重定向目的地"(独立登录路径)。"""
        for token in ("/login", "login.", "login.cgi", "passport",
                      "/signin", "sign_in", "sso", "cas", "auth."):
            if token in url:
                return True
        return False

    def check_selectors(self, browser, timeout=2):
        """自检声明的 SELECTORS 是否都在页面上找到。
        返回 (ok: bool, missing: [缺失的键名列表])。
        平台改版导致选择器失效时会前置暴露,避免静默失败到下载阶段才察觉。"""
        missing = []
        if not self.SELECTORS:
            return True, []
        for key, sel in self.SELECTORS.items():
            try:
                loc = browser.page.locator(sel)
                if loc.count() == 0:
                    missing.append(key)
                    browser._log(f"[自检] 缺失元素 {key}: {sel}", "warning")
            except Exception as e:
                missing.append(key)
                browser._log(f"[自检] 选择器非法 {key}: {sel} - {str(e)[:60]}", "warning")
        ok = not missing
        if ok:
            browser._log(f"[自检] {len(self.SELECTORS)} 个关键元素全部就位")
        return ok, missing

    def assert_selector(self, browser, key, timeout=5):
        """步骤级断言: 确认某个关键元素存在再继续,否则抛异常。
        用于在 export 关键节点(如点查询前)显式校验,避免错上加错。"""
        sel = self.SELECTORS.get(key)
        if not sel:
            browser._log(f"[断言] 未声明选择器 {key},跳过", "warning")
            return True
        try:
            loc = browser.page.locator(sel)
            loc.first.wait_for(timeout=timeout * 1000)
            return True
        except Exception as e:
            browser.snapshot_on_failure(f"断言失败_{key}")
            raise RuntimeError(f"关键元素 [{key}] 未找到: {sel} ({str(e)[:60]})")

    def export(self, browser, start_date, end_date):
        """
        导出流水流程(核心方法,建议覆盖)

        参数:
            browser: 浏览器对象,提供辅助方法(见 browser.py)
            start_date: 开始日期,格式 "2026-09-01"
            end_date: 结束日期,格式 "2026-09-01"

        返回:
            "success" - 导出成功
            "manual"  - 需要用户手动完成
            "failed"  - 导出失败

        默认实现: 使用智能导出器自动识别页面上的
        日期输入框、查询按钮、导出按钮并自动操作
        """
        from .exporters import SmartExporter
        exporter = SmartExporter(browser)
        return exporter.export(start_date, end_date)
