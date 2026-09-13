"""
微信支付商户平台导出脚本

操作流程(基于截图 - 微信支付商户平台):
    1. 打开 pay.weixin.qq.com → 交易中心 → 账单管理 → 资金账单
    2. 选择账户类型(基本账户/运营账户)
    3. 选择日期范围(最多31天)
    4. 选择Office版本(2007以上/以下)
    5. 点击"查询"
    6. 等待系统打包完成
    7. 弹窗提示"账单打包完成,请确认下载" → 点击"确定"

    下载方式:
    - 方式A: 表格行中的"业务明细"/"业务汇总"链接(单日)
    - 方式B: 底部"下载多日账单: 业务明细账单/业务汇总账单"链接(多日)

辅助方法速查(通过 browser 调用):
    browser.navigate(url)                    # 打开页面
    browser.sleep(秒)                        # 等待
    browser.click_text("导出")               # 点击包含文字的按钮
    browser.click_selector("css选择器")      # 点击CSS选择器元素
    browser.fill_placeholder("开始日期", "2026-09-01")  # 填输入框
    browser.fill_selector("css选择器", "值") # 填CSS选择器输入框
    browser.begin_wait_download()            # 开启下载捕获(点下载按钮前调用)
    browser.wait_download(timeout=120)       # 等待下载,返回文件路径
    browser.is_visible_text("导出成功")      # 判断页面是否出现文字
    browser.close_popup()                    # 关闭弹窗(无弹窗自动跳过)
    browser.screenshot("名字")               # 截图

返回结果:
    "success" - 导出成功
    "manual"  - 需要用户手动完成
    "failed"  - 导出失败
"""

import os

from core.platform_base import PlatformBase


class WechatpayExporter(PlatformBase):
    key = "wechatpay"
    name = "微信支付"
    # 微信支付商户平台 - 交易中心 → 账单管理 → 资金账单
    login_url = "https://pay.weixin.qq.com/"
    export_url = "https://pay.weixin.qq.com/index.php/xphp/cfund_bill_nc/funds_bill_nc#/"
    guide = "交易中心 → 账单管理 → 资金账单 → 选账户/日期 → 查询 → 业务明细账单/业务汇总账单 → 确定"

    # 关键元素选择器(平台改版自检用)
    SELECTORS = {
        "date_range_input": ".el-range-input",              # 日期范围输入框
        "query_btn": "button.el-button--primary",          # 查询按钮
        "bill_detail_link": 'a.popups.download:has-text("业务明细账单")',  # 业务明细账单链接
        "bill_summary_link": 'a.popups.download:has-text("业务汇总账单")', # 业务汇总账单链接
    }

    def check_login(self, browser):
        """
        微信支付特判: 扫码页与后台页同域(都是 bill_manage),不能用 URL 判断。

        打开资金账单页:
        - 未登录: 页面显示"扫码登录/请使用微信扫码"等引导,返回 False
        - 已登录: 出现顶部主导航"交易中心",返回 True
        """
        browser.navigate(self.export_url)
        # 已登录特征优先: 出现后台主导航"交易中心"即放行(快,避免逐个等扫码文案超时)
        if browser.is_visible_text("交易中心", timeout=5):
            return True
        # 未登录特征: 出现扫码相关引导文字
        for kw in ("扫码登录", "微信扫码", "扫一扫登录", "请使用微信"):
            if browser.is_visible_text(kw, timeout=1):
                return False
        # 都未命中: 保守判为未登录(让用户重新登录)
        return False

    def _set_dates(self, browser, start_date, end_date):
        """设置 element-ui 日期范围选择器(el-date-range-picker)的日期范围。
        fill() 对 Vue 受控组件不生效,改用: 点击输入框→全选→键盘输入→Enter 确认。"""
        try:
            inputs = browser.page.locator(".el-range-input")
            if inputs.count() >= 2:
                # 开始日期
                inputs.nth(0).click()
                browser.page.keyboard.press("Control+a")
                browser.page.keyboard.type(start_date[:10])
                browser.sleep(1)
                # 结束日期
                inputs.nth(1).click()
                browser.page.keyboard.press("Control+a")
                browser.page.keyboard.type(end_date[:10])
                browser.page.keyboard.press("Enter")   # 确认并关闭日期面板
                browser.sleep(1)
                return True
        except Exception:
            pass
        # 回退: 按 placeholder 填写
        browser.fill_placeholder("开始日期", start_date[:10])
        browser.fill_placeholder("结束日期", end_date[:10])
        browser.sleep(1)
        return False

    def _download_one_bill(self, browser, bill_text):
        """下载单个账单(业务明细账单/业务汇总账单)。
        流程: 开启捕获→点链接→等"账单打包完成"弹窗→点确定→等待下载完成。
        返回最终文件路径(失败返回None)。"""
        browser.begin_wait_download()
        # 优先用 CSS 选择器精确定位该账单链接(带文本回退)
        # 页面结构: <a href="javascript:;" class="popups download"><i class="ico-exp"></i>业务XX账单</a>
        if not browser.click_selector(f'a.popups.download:has-text("{bill_text}")'):
            browser.click_text(bill_text)
        # 替代固定 sleep: 等待"账单打包完成"弹窗出现(超时未出现继续走回退逻辑)
        browser.wait_for(text="账单打包完成", timeout=6, network_idle=False)

        # 等待"账单打包完成"弹窗→点确定
        # 按钮文本为"确 定"(中间含空格);页面可能有多个隐藏 el-dialog,用 :visible 只点当前可见弹窗
        if browser.is_visible_text("账单打包完成", timeout=30):
            browser.click_selector(".el-dialog:visible .el-button--primary")
            browser.wait_for(timeout=1)
        # 回退: 提示"文件生成需要时间,请稍后在下载列表查看"
        if browser.is_visible_text("下载列表", timeout=5):
            browser.click_text("下载列表")
            browser.wait_for(text="立即下载", timeout=10)
            if browser.is_visible_text("立即下载", timeout=10):
                browser.click_text("立即下载")
                browser.wait_for(timeout=1)

        path = browser.wait_download(timeout=90)
        if path:
            browser._log(f"{bill_text} 下载完成: {os.path.basename(path)}")
        else:
            browser._log(f"{bill_text} 下载失败(未收到下载文件)", "warning")
            browser.snapshot_on_failure(f"下载_{bill_text}")
        return path

    def export(self, browser, start_date, end_date):
        try:
            return self._export_inner(browser, start_date, end_date)
        except Exception as e:
            # 任意异常都截一张现场图,排查"明明登录了却点不动"这类问题
            browser._log(f"导出异常: {str(e)[:120]}", "error")
            browser.snapshot_on_failure("导出异常")
            return "failed"

    def _export_inner(self, browser, start_date, end_date):
        # ===== 1. 打开资金账单页面 =====
        browser.navigate(self.export_url)
        browser.wait_for(text="交易中心", timeout=15)
        browser.close_popup()
        # 自检关键元素是否就位(平台改版时前置暴露,避免静默失败)
        ok, missing = self.check_selectors(browser)
        if missing:
            browser._log(f"[自检] 微信支付页面缺失元素: {missing}(可能页面改版,需复核脚本)")

        # ===== 2. 设置日期范围(键盘输入方式,确保 Vue 组件真正更新) =====
        # 步骤级断言: 确认日期输入框存在再继续(不存在说明页面结构变了)
        self.assert_selector(browser, "date_range_input")
        self._set_dates(browser, start_date, end_date)
        browser.snapshot("设置日期后")
        browser.step_pause("设置日期后")
        # 兜底: 关闭可能残留的日期选择器弹层,避免遮挡按钮
        try:
            browser.page.keyboard.press("Escape")
            browser.sleep(1)
        except Exception:
            pass

        # ===== 3. 点击"查询" =====
        # 注意: 页面左侧菜单有"已结算查询"等链接,必须精确匹配"查询"按钮,
        # 否则会点到菜单链接导致跳转到其他页面
        self.assert_selector(browser, "query_btn")
        browser.click_text("查询", exact=True)
        # 等待查询结果与"下载多日账单"区块渲染完成(替代固定 sleep(8))
        browser.wait_for(selector=self.SELECTORS["bill_detail_link"], timeout=20)
        browser.snapshot("查询后")
        browser.step_pause("查询后")

        # ===== 4. 循环下载账单(登录/设日期/查询只做一次) =====
        # 目前只需"业务明细账单";若后续需要"业务汇总账单",在此追加即可,
        # 多个文件会落到同一 downloads/微信支付/商户/日期范围/ 目录
        bills = ["业务明细账单"]
        results = []
        for bill in bills:
            browser._log(f">>> 开始下载 {bill}")
            path = self._download_one_bill(browser, bill)
            results.append((bill, path))

        # ===== 5. 汇总结果 =====
        ok = [b for b, p in results if p]
        if len(ok) == len(bills):
            return "success"
        elif ok:
            missing = [b for b, p in results if not p]
            browser._log(f"部分账单下载失败: {missing}", "warning")
            return "manual"
        return "failed"
