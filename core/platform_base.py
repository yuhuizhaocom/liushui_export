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

    def login(self, browser):
        """
        登录流程(可选覆盖)
        默认: 打开登录页,等待用户手动登录
        如需特殊处理(如点击"登录"按钮跳转),可覆盖此方法
        """
        browser.navigate(self.login_url)

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
        from exporters import SmartExporter
        exporter = SmartExporter(browser)
        return exporter.export(start_date, end_date)
