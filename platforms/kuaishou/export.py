"""
快手平台导出脚本

操作流程(基于截图 - 快手小店商家后台):
    方式A - 货款账单(按结算时间):
    1. 打开资金 → 货款账单
    2. 设置结算时间范围(红字标注"时间一般选半年")
    3. 选择资金渠道(全选)
    4. 点击"查询"
    5. 点击"导出"
    6. 去"查看导出记录" → 点击"下载"

    方式B - 收支明细(月账单):
    1. 打开资金 → 账户中心 → 收支明细
    2. 选择"月账单"标签
    3. 设置到账日期范围
    4. 点击"查询"
    5. 点击目标行的"导出账单"
    6. 去"导出记录" → 点击"下载"

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


class KuaishouExporter(PlatformBase):
    key = "kuaishou"
    name = "快手"
    # 快手小店商家后台 - 资金 → 货款账单
    login_url = "https://s.kwaixiaodian.com/"
    export_url = "https://s.kwaixiaodian.com/zone/fund/payment/bill"
    guide = "资金 → 货款账单 → 选结算时间(建议半年) → 查询 → 导出 → 查看导出记录 → 下载"

    def trigger_export(self, browser, start_date, end_date):
        """差异步骤: 快手点完"导出"要先进"查看导出记录"列表, 才有可点的"下载"。"""
        browser.click_text("查询")            # 应用结算时间范围
        browser.sleep(3)
        browser.click_text("导出")
        browser.sleep(5)
        browser.click_text("查看导出记录")
        browser.sleep(3)

    def export(self, browser, start_date, end_date):
        # 打开货款账单页 → 填结算时间 → 查询/导出/查看导出记录 → 点下载并等文件
        return self.run_standard_flow(browser, start_date, end_date)
