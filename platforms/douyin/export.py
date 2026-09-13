"""
抖音平台导出脚本

操作流程(基于截图):
    1. 打开抖店后台 → 资金 → 账单管理
    2. 确认在"资金账单"标签 → 选择"资金流水明细"或"日汇总"
    3. 设置账单日期范围
    4. 点击"查询"
    5. 点击"生成报表明细"或"导出报表"
    6. 去"历史报表"页面下载已生成的报表

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


class DouyinExporter(PlatformBase):
    key = "douyin"
    name = "抖音"
    # 抖店后台 - 资金 → 账单管理
    login_url = "https://fxg.jinritemai.com/login"
    export_url = "https://fxg.jinritemai.com/ffa/fxg-bill/fund-detail-bill"
    guide = "资金 → 账单管理 → 资金账单 → 资金流水明细/日汇总 → 选日期 → 查询 → 生成报表明细 → 历史报表 → 下载"

    def export(self, browser, start_date, end_date):
        # ===== 1. 打开账单管理页面 =====
        browser.navigate(self.export_url)
        browser.sleep(3)
        browser.close_popup()

        # ===== 2. 确认在"资金流水明细"或"日汇总"标签 =====
        browser.click_text("日汇总")
        browser.sleep(1)

        # ===== 3. 设置账单日期范围 =====
        # 抖音日期选择器格式: 2026-08-21 00:00:00 ~ 2026-08-23 23:59:59
        browser.fill_placeholder("开始日期", start_date[:10])
        browser.fill_placeholder("结束日期", end_date[:10])
        browser.sleep(1)

        # ===== 4. 点击"查询" =====
        browser.click_text("查询")
        browser.sleep(3)

        # ===== 5. 点击"生成报表明细" =====
        browser.click_text("生成报表")
        browser.sleep(10)

        # ===== 6. 去历史报表页面下载 =====
        browser.click_text("历史报表")
        browser.sleep(3)

        # ===== 7. 点击最新一条的"下载"按钮 =====
        browser.begin_wait_download()
        browser.click_text("下载")
        browser.sleep(3)

        # ===== 8. 等待下载完成 =====
        path = browser.wait_download(timeout=60)
        if path:
            return "success"
        return "manual"
