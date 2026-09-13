"""
银联平台导出脚本

操作流程(基于截图 - 中国银联条码支付综合前置平台):
    1. 打开银联后台 → 文件管理 → 商户对账单下载
    2. 设置账单日期范围
    3. 点击"查询"
    4. 点击"商户对账单下载"按钮(带下拉箭头)
    5. 弹窗: 选择对账单类型(所有交易账单),设置开始/结束时间
    6. 点击"导出"
    7. 等待文件生成,在列表中点击"下载"

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


class YinlianExporter(PlatformBase):
    key = "yinlian"
    name = "银联"
    # 中国银联条码支付综合前置平台 - 文件管理 → 商户对账单下载
    login_url = "https://up.95516.com/qmpmgm/"
    export_url = "https://up.95516.com/qmpmgm/system/guide"
    guide = "文件管理 → 商户对账单下载 → 选日期 → 查询 → 商户对账单下载 → 选类型/时间 → 导出 → 下载"

    def export(self, browser, start_date, end_date):
        # ===== 1. 打开商户对账单下载页面 =====
        browser.navigate(self.export_url)
        browser.sleep(3)
        browser.close_popup()

        # ===== 2. 设置账单日期范围 =====
        browser.fill_placeholder("开始日期", start_date[:10])
        browser.fill_placeholder("结束日期", end_date[:10])
        browser.sleep(1)

        # ===== 3. 点击"查询" =====
        browser.click_text("查询")
        browser.sleep(3)

        # ===== 4. 点击"商户对账单下载"按钮(打开导出弹窗) =====
        browser.click_text("商户对账单下载")
        browser.sleep(2)

        # ===== 5. 在弹窗中选择对账单类型和日期 =====
        # 默认选"所有交易账单"
        browser.click_text("所有交易账单")
        browser.sleep(1)

        # 填写对账单开始/结束时间(与查询日期一致)
        browser.fill_placeholder("对账单开始时间", start_date[:10])
        browser.fill_placeholder("对账单结束时间", end_date[:10])
        browser.sleep(1)

        # ===== 6. 点击"导出"按钮 =====
        browser.click_text("导出")
        browser.sleep(10)

        # ===== 7. 等待文件生成,在列表中点击"下载" =====
        browser.begin_wait_download()
        browser.click_text("下载")
        browser.sleep(3)

        # ===== 8. 等待下载完成 =====
        path = browser.wait_download(timeout=60)
        if path:
            return "success"
        return "manual"
