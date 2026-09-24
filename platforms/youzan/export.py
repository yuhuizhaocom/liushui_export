"""
有赞平台导出脚本 - 完整示例

这个脚本演示了如何编写平台导出脚本。
有赞专项逻辑(带日期参数的流水页 URL、确认弹窗处理)直接在本平台脚本内实现,
不放入 core/ 通用模块。

辅助方法速查(通过 browser 调用):
    browser.navigate(url)                    # 打开页面
    browser.sleep(秒)                        # 等待
    browser.click_text("导出")               # 点击包含文字的按钮
    browser.click_selector("css选择器")      # 点击CSS选择器元素
    browser.fill_placeholder("开始日期", "2026-09-01")  # 填输入框
    browser.fill_selector("css选择器", "值") # 填CSS选择器输入框
    browser.wait_download(timeout=120)       # 等待下载,返回文件路径
    browser.is_visible_text("导出成功")      # 判断页面是否出现文字
    browser.screenshot("名字")               # 截图

返回结果:
    "success" - 导出成功
    "manual"  - 需要用户手动完成
    "failed"  - 导出失败
"""

from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from core.platform_base import PlatformBase


class YouzanExporter(PlatformBase):
    key = "youzan"
    name = "有赞"
    login_url = "https://www.youzan.com/v4/assets/dashboard?from=PC-SHARED-NAV"
    export_url = "https://www.youzan.com/v4/assets/record?accountType=&active=&bizType=&chooseDays=7&dateType=SETTLE_TIME&endTime=1788364799999&page=1&pageSize=10&payMethod=&startTime=1787760000000&tradeChannel=&waterNo="
    guide = "进入后台 → 数据 → 交易明细 → 选择日期 → 导出"

    # ===== 有赞专项: 带日期参数的流水页 URL =====

    def _build_youzan_url(self, url, start_date, end_date):
        """构造带日期参数的有赞流水页 URL(UTC+8 毫秒时间戳),不执行跳转。

        日期解析失败必须抛出去: self.export_url 里保留着录制当时的
        startTime/endTime, 以前异常时退回原 URL, 会静默导出那个死区间,
        用户拿到的账单看起来完全正常。
        """
        cst = timezone(timedelta(hours=8))
        start_ts = int(datetime.strptime(start_date[:10], "%Y-%m-%d")
                       .replace(tzinfo=cst).timestamp() * 1000)
        end_ts = int(datetime.strptime(end_date[:10], "%Y-%m-%d")
                     .replace(hour=23, minute=59, second=59,
                              microsecond=999000, tzinfo=cst).timestamp() * 1000)
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        qs["startTime"] = [str(start_ts)]
        qs["endTime"] = [str(end_ts)]
        qs["dateType"] = ["SETTLE_TIME"]
        new_query = urlencode(qs, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    def open_youzan_record(self, browser, url, start_date, end_date):
        """一次性打开已带日期参数的有赞流水页(仅导航一次,不再二次跳转)"""
        target = self._build_youzan_url(url, start_date, end_date)
        return browser.navigate(target)

    def export(self, browser, start_date, end_date):
        # ===== 一次性打开导出页面并带上日期参数(不再二次跳转) =====
        self.open_youzan_record(browser, self.export_url, start_date, end_date)
        browser.sleep(3)  # 等待页面加载

        # ===== 关闭弹窗(有就关,没有自动跳过) =====
        # 有赞有时会弹出公告/引导弹窗,需要点右上角关闭图标
        # 没有弹窗时 close_popup 会自动跳过,不会误操作
        browser.close_popup()
        browser.sleep(1)

        # browser.click_text("进入工作台")
        # browser.sleep(2)
        #
        # browser.click_text("对账单明细")
        # browser.sleep(2)

        browser.click_text("筛选")
        browser.sleep(3)

        browser.click_text("导出报表")
        browser.sleep(10)

        # 点击"下载报表"前开启下载捕获,保证拿到本次下载事件与文件
        browser.begin_wait_download()
        browser.click_text("下载报表")
        browser.sleep(3)

        # ===== 处理可能的确认弹窗(等弹窗出现再点确定) =====
        if browser.is_visible_text("确定", timeout=5):
            browser.click_text("确定")
            browser.sleep(2)

        # ===== 等待下载完成(监控下载文件夹,兼容异步下载) =====
        path = browser.wait_download(timeout=10)
        if path:
            return "success"
        return "manual"