"""
小红书平台导出脚本

操作流程(基于截图 - 小红书千帆商家后台):
    方式A - 货款资金动账明细: 【当前实现走这条】
    1. 打开 ark.xiaohongshu.com → 资金 → 账户管理(货款资金)
    2. 确认在"动账明细"标签 → "店铺余额"子标签
    3. 设置日期范围
    4. 点击"查询"
    5. 点击"导出"
    6. 浏览器触发下载(新建下载任务弹窗)

    方式B - 订单结算明细: 【本脚本未实现, 记着备用】
    1. 财务管理 → 订单结算明细
    2. 设置结算时间范围
    3. 点击"查询"
    4. 点击"导出订单"

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


class XiaohongshuExporter(PlatformBase):
    key = "xiaohongshu"
    name = "小红书"
    # 小红书千帆 - 资金 → 货款资金(动账明细)
    login_url = "https://ark.xiaohongshu.com/"
    export_url = "https://ark.xiaohongshu.com/app-merchant/third-settle/account"
    guide = "资金 → 账户管理 → 动账明细 → 选日期 → 查询 → 导出"

    # 这个后台点"导出"就直接产出文件, 没有单独的"下载"按钮
    DOWNLOAD_LABEL = "导出"
    DOWNLOAD_SETTLE_S = 5

    def open_export_page(self, browser):
        super().open_export_page(browser)
        browser.click_text("动账明细")   # 默认停在"账户余额"一类标签
        browser.sleep(1)

    def trigger_export(self, browser, start_date, end_date):
        browser.click_text("查询")
        browser.sleep(3)

    def export(self, browser, start_date, end_date):
        # 打开货款资金 → 切动账明细 → 填日期 → 查询 → 点"导出"并等文件
        return self.run_standard_flow(browser, start_date, end_date)
