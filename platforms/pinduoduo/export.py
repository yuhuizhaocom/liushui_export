"""
拼多多平台导出脚本

操作流程(基于截图):
    1. 打开对账中心页面(资金中心 → 对账中心)
    2. 选择"货款账户"标签 → "货款明细"子标签
    3. 设置日期范围
    4. 点击"查询"
    5. 点击"导出" → 等待生成
    6. 点击"导出历史" → 在历史列表中点击"下载账单"

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


class PinduoduoExporter(PlatformBase):
    key = "pinduoduo"
    name = "拼多多"
    # 拼多多商家后台 - 资金中心 → 对账中心
    login_url = "https://mms.pinduoduo.com/main/"
    export_url = "https://mms.pinduoduo.com/bill_record/bill_list"
    guide = "资金中心 → 对账中心 → 货款账户 → 货款明细 → 选日期 → 查询 → 导出 → 导出历史 → 下载账单"

    def export(self, browser, start_date, end_date):
        # ===== 1. 打开对账中心页面 =====
        browser.navigate(self.export_url)
        browser.wait_for(text="货款明细", timeout=15)
        browser.close_popup()

        # ===== 2. 确认在"货款明细"标签页 =====
        browser.click_text("货款明细")
        browser.wait_for(text="查询", timeout=10)

        # ===== 3. 设置日期范围 =====
        # 拼多多日期选择器: 需要点击日期框,然后输入起止日期
        ok_start = browser.fill_placeholder("开始日期", start_date[:10])
        ok_end = browser.fill_placeholder("结束日期", end_date[:10])
        if not (ok_start and ok_end):
            # 日期没填进去就不能继续: 页面按默认区间出账单, 归档名却是本次请求区间
            browser._log("[中止] 拼多多: 起止日期未能全部填入日期框, 已停止自动导出",
                         "warning")
            return "manual"
        browser.wait_for(timeout=1)

        # ===== 4. 点击"查询" =====
        browser.click_text("查询")
        # 等待查询结果加载出"导出"按钮(替代固定 sleep)
        browser.wait_for(text="导出", timeout=15)

        # ===== 5. 点击"导出"按钮 =====
        # 同一页上"导出"和"导出历史"都含"导出"三个字, 子串匹配下点中哪个由 DOM
        # 顺序决定。先精确匹配按钮文字, 确实找不到再退回子串匹配。
        if not browser.click_text("导出", exact=True):
            browser.click_text("导出")
        # 等待导出完成生成"导出历史"入口
        browser.wait_for(text="导出历史", timeout=15)

        # ===== 6. 等待导出完成,然后去"导出历史"下载 =====
        # 点击"导出历史"链接
        browser.click_text("导出历史")
        browser.wait_for(text="下载账单", timeout=15)

        # ===== 7. 在历史列表中点击最新一条"下载账单" =====
        browser.begin_wait_download()
        browser.click_text("下载账单")
        browser.wait_for(timeout=1)

        # 处理可能的确认弹窗
        if browser.is_visible_text("确定", timeout=5):
            browser.click_text("确定")
            browser.wait_for(timeout=1)

        # ===== 8. 等待下载完成 =====
        path = browser.wait_download(timeout=60)
        if path:
            return "success"
        return "manual"
