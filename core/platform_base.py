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

    # 一个主账号下要切着导多个子商户的平台(银联这类)把它置 True: 导出流程会在**同一份
    # 浏览器 profile、同一次登录**里按清单逐个切子商户跑 N 轮, 每轮的成品多一层目录。
    # 默认 False —— 现有平台的行为与今天逐字相同, 不声明就完全不会被循环。
    supports_sub_merchants = False

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

    # ===== 子商户(仅 supports_sub_merchants=True 的平台会被调用) =====
    # 调用顺序由导出流程保证: switch_sub_merchant → current_sub_merchant(比对归属)
    # → export。这两个钩子的**返回值语义是安全相关的**, 别为了"看起来能用"而放宽。

    def switch_sub_merchant(self, browser, sub_merchant):
        """把页面切到指定子商户。返回 True 只表示"切换动作点完了"。

        覆盖时的两条底线:
          1. 切不过去(找不到入口、点了没反应)必须返回 False —— 流程会就此**停手**转人工,
             绝不带着没确认过的归属去点导出;
          2. 不要在这里点导出/下载, 导出是 `export()` 的事, 混在一起会让重试语义说不清。
        """
        browser._log(f"[子商户] {self.name}: 没有实现切换子商户的动作, 无法安全地留在同一"
                     f"次登录里切到「{sub_merchant}」, 这一家转人工。", "warning")
        return False

    def current_sub_merchant(self, browser):
        """从页面上读回"现在是谁"(商户号/商户名任意一种稳定标识)。

        返回空串表示读不到 —— 流程不会因此停手, 但会记一条"归属未经校验"的提醒:
        读回是这整套机制里唯一能防住"A 的账单记到 B 名下"的东西, 平台脚本能实现尽量实现。
        """
        return ""

    def verifies_sub_merchant_identity(self):
        """这个平台的脚本实现了「读回当前子商户」吗 —— 决定导出时归属能不能被校验。

        界面上拿它决定要不要提示"归属不会被校验", 免得用户以为切错了会有人拦。
        """
        return type(self).current_sub_merchant is not PlatformBase.current_sub_merchant

    # ===== 通用导出骨架(平台脚本按需改用;不调用则完全不受影响) =====
    # 各平台脚本里"打开页面 → 填日期 → 点查询/导出 → 等下载"这段几乎逐字相同,
    # 差异只有中间点哪些按钮、下载等多久。收在这里后平台只需覆盖有差异的钩子,
    # 收尾的 begin_wait_download/wait_download 配对与超时也不会再各写一份。
    PAGE_SETTLE_S = 3          # 打开页面后的等待秒数
    DOWNLOAD_TIMEOUT_S = 60    # 等待下载完成的秒数
    DOWNLOAD_LABEL = "下载"     # 收尾要点的按钮文字(有的平台就是"导出"/"下载全部")
    DOWNLOAD_SETTLE_S = 3      # 点完那个按钮后给它的落地时间
    DATE_VALUE_SLICE = 10      # 填进日期框的字符串长度(天猫"月汇总"只要 2026-09)

    def open_export_page(self, browser):
        """打开导出页并等页面稳定(close_popup 当前在各平台是空转, 保留调用点)。"""
        browser.navigate(self.export_url)
        browser.sleep(self.PAGE_SETTLE_S)
        browser.close_popup()

    def set_date_range(self, browser, start_date, end_date):
        """按 placeholder 填起止日期(多数后台的日期框都叫"开始日期/结束日期")。

        返回 False 表示至少有一个框没填进去 —— 调用方必须据此停止: 否则会拿页面
        默认区间的账单, 却按"请求区间"命名归档, 从文件名上根本看不出错了。
        """
        n = self.DATE_VALUE_SLICE
        ok_start = browser.fill_placeholder("开始日期", (start_date or "")[:n])
        ok_end = browser.fill_placeholder("结束日期", (end_date or "")[:n])
        browser.sleep(1)
        return bool(ok_start) and bool(ok_end)

    def trigger_export(self, browser, start_date, end_date):
        """查询 + 点导出。需要额外步骤(如先切标签、再去历史报表页)的平台覆盖本方法。"""
        browser.click_text("查询")
        browser.sleep(3)
        browser.click_text("导出")
        browser.sleep(5)

    def download_export_file(self, browser, label=None, settle_s=None):
        """开启下载捕获 → 点下载 → 等文件落地。返回 "success"/"manual"。"""
        label = label or self.DOWNLOAD_LABEL
        settle_s = self.DOWNLOAD_SETTLE_S if settle_s is None else settle_s
        browser.begin_wait_download()
        browser.click_text(label)
        browser.sleep(settle_s)
        path = browser.wait_download(timeout=self.DOWNLOAD_TIMEOUT_S)
        return "success" if path else "manual"

    def run_standard_flow(self, browser, start_date, end_date):
        """串起上述四步。平台脚本在 export() 里 return 本方法即可。

        日期没全部填进日期框时就中止并返回 manual: 继续点查询/导出会得到页面默认
        区间的账单, 而归档文件名用的是本次请求的区间, 从产物上完全看不出区间错了。
        """
        self.open_export_page(browser)
        if not self.set_date_range(browser, start_date, end_date):
            browser._log(f"[中止] {self.name}: 起止日期未能全部填入日期框, "
                         f"已停止自动导出(继续会得到错区间的账单)。"
                         f"若反复出现, 说明该平台日期框的 placeholder 不是"
                         f"\"开始日期/结束日期\", 需要在脚本里覆盖 set_date_range。",
                         "warning")
            browser.snapshot("日期未填入")
            return "manual"
        self.trigger_export(browser, start_date, end_date)
        return self.download_export_file(browser)

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
        # 日志接回 BrowserManager 的通道: 以前只传 browser, SmartExporter 的
        # log_callback 是 None, 于是它的 _log() 全成空操作 —— 默认导出到底认出了哪个
        # 日期框、点了哪个按钮、为什么转人工, 日志里一个字都看不到。
        exporter = SmartExporter(browser, log_callback=browser._log)
        return exporter.export(start_date, end_date)
