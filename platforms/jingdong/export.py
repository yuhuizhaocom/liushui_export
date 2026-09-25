"""
京东平台导出脚本

操作流程(基于截图 - 京东金融企业版):
    1. 打开京东金融企业版 → 资金账户 → 账户流水 → 账单查询
    2. 选择账户类型(单个账户)
    3. 选择账单类型(日账单/月账单)
    4. 设置日期范围或年份
    5. 点击"查询"
    6. 在结果表格中点击"账单下载"
    7. 弹出"操作提示"弹窗 → 点击"去下载列表"
    8. 在下载列表中点击"立即下载"或"查看文件"

    备选路径(商城销售明细):
    - 在账单查询结果中点击"查看商城销售明细"
    - 在弹出的"商城销售明细下载列表"中点击"全部下载"或逐行"下载明细"

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

from core.platform_base import PlatformBase


class JingdongExporter(PlatformBase):
    key = "jingdong"
    name = "京东"
    # 京东金融企业版 - 资金账户 → 账户流水 → 账单查询
    login_url = "https://biz.jr.jd.com/"
    export_url = "https://biz.jr.jd.com/fund/account/flow/billQuery"
    guide = "资金账户 → 账户流水 → 账单查询 → 选账户/账单类型/日期 → 查询 → 账单下载 → 去下载列表 → 立即下载"

    def export(self, browser, start_date, end_date):
        # ===== 1. 打开账单查询页面 =====
        browser.navigate(self.export_url)
        browser.wait_for(text="日账单", timeout=15)
        browser.close_popup()

        # ===== 2. 选择账单类型(日账单,因为我们要按日期范围导出) =====
        browser.click_text("日账单")
        browser.wait_for(text="查询", timeout=10)

        # ===== 3. 设置日期范围 =====
        ok_start = browser.fill_placeholder("开始日期", start_date[:10])
        ok_end = browser.fill_placeholder("结束日期", end_date[:10])
        if not (ok_start and ok_end):
            # 日期没填进去就不能继续: 页面会按默认区间出账单, 而归档名用的是本次
            # 请求区间, 事后根本看不出区间错了。
            browser._log("[中止] 京东: 起止日期未能全部填入日期框, 已停止自动导出",
                         "warning")
            return "manual"
        browser.wait_for(timeout=1)

        # ===== 4. 点击"查询" =====
        browser.click_text("查询")
        # 等待查询结果中出现"账单下载"行
        browser.wait_for(text="账单下载", timeout=15)

        # ===== 5. 点击目标行的"账单下载" =====
        browser.click_text("账单下载")
        # 弹窗里的按钮是"去下载列表"。以前只匹配一个"去"字, 页面上任何含"去"的
        # 文案("去掉""过去 30 天"…)都会被判成"弹窗已出现"并被点掉。
        browser.wait_for(text="去下载列表", timeout=10)

        # ===== 6. 处理"操作提示"弹窗 → 点击"去下载列表" =====
        if browser.is_visible_text("去下载列表", timeout=5):
            browser.click_text("去下载列表")
            browser.wait_for(timeout=1)

        # ===== 7. 在下载列表中点击"查看文件"或"立即下载" =====
        browser.begin_wait_download()
        # 优先尝试"立即下载"
        if browser.is_visible_text("立即下载", timeout=3):
            browser.click_text("立即下载")
        else:
            browser.click_text("查看文件")
        browser.wait_for(timeout=1)

        # ===== 8. 等待下载完成 =====
        path = browser.wait_download(timeout=60)
        if path:
            return "success"
        return "manual"
