"""
支付宝商家平台导出脚本

操作流程(基于截图 - 支付宝商家平台):
    1. 打开 b.alipay.com → 对账中心 → 资金流水/账单
    2. 选择商家账户(如有多个)
    3. 设置时间范围(精确到秒: 2026-08-24 00:00:00 至 23:59:59)
    4. 选择收支类型(全部/收入/支出)
    5. 点击"查询"
    6. 点击"下载全部"

    注意:
    - 目前仅支持查询2025年08月24日及以后的资金流水
    - 支持快捷选项: 今日/昨日/7日/30日

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


class AlipayExporter(PlatformBase):
    key = "alipay"
    name = "支付宝"
    # 支付宝商家平台 - 对账中心 → 资金流水/账单
    login_url = "https://b.alipay.com/"
    export_url = "https://b.alipay.com/page/mbillprod/account/detail"
    guide = "对账中心 → 资金流水/账单 → 选账户/日期/收支类型 → 查询 → 下载全部"

    # 查询之后没有"导出"这一步, 直接点"下载全部"(给 5 秒落地)
    DOWNLOAD_LABEL = "下载全部"
    DOWNLOAD_SETTLE_S = 5

    def trigger_export(self, browser, start_date, end_date):
        browser.click_text("查询")
        browser.sleep(3)

    def export(self, browser, start_date, end_date):
        # 打开资金流水页 → 填时间范围 → 查询 → 点"下载全部"并等文件
        return self.run_standard_flow(browser, start_date, end_date)
