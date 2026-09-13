"""
天猫/千牛平台导出脚本

操作流程(基于截图 - 天猫商家后台/千牛工作台):
    1. 打开千牛后台 → 财务 → 资金管理 → 聚合结算账户
    2. 选择"月汇总"标签页
    3. 设置入账日期范围(月份选择)
    4. 点击"搜索"
    5. 点击"下载明细"
    6. (备选)去"导出记录"查看历史导出

    备选路径 - 收支记录:
    - 财务 → 总览 → 货款账户 → 收支记录
    - 在收支明细页设置日期范围 → 搜索 → 下载明细

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


class TmallExporter(PlatformBase):
    key = "tmall"
    name = "天猫"
    # 千牛/天猫商家后台 - 财务 → 资金管理 → 聚合结算账户
    login_url = "https://myseller.taobao.com/home.htm"
    export_url = "https://myseller.taobao.com/home.htm/whale-accountant/index"
    guide = "财务 → 资金管理 → 聚合结算账户 → 月汇总 → 选月份 → 搜索 → 下载明细"

    def export(self, browser, start_date, end_date):
        # ===== 1. 打开聚合结算账户页面 =====
        browser.navigate(self.export_url)
        browser.sleep(3)
        browser.close_popup()

        # ===== 2. 选择"月汇总"标签页 =====
        browser.click_text("月汇总")
        browser.sleep(1)

        # ===== 3. 设置入账日期范围 =====
        browser.fill_placeholder("开始日期", start_date[:7])  # 月份格式 2026-09
        browser.fill_placeholder("结束日期", end_date[:7])
        browser.sleep(1)

        # ===== 4. 点击"搜索" =====
        browser.click_text("搜索")
        browser.sleep(3)

        # ===== 5. 点击"下载明细" =====
        browser.begin_wait_download()
        browser.click_text("下载明细")
        browser.sleep(3)

        # ===== 6. 等待下载完成 =====
        path = browser.wait_download(timeout=60)
        if path:
            return "success"
        return "manual"
