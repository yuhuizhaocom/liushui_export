"""
智能流水导出器 - 自动完成各平台流水导出
使用智能定位策略(文本/placeholder匹配),不依赖特定选择器
自动导出失败时,截图并提示用户手动完成
"""

import os
import re
import time


class SmartExporter:
    def __init__(self, browser, log_callback=None):
        self.browser = browser
        self.page = browser.page
        self.log_callback = log_callback

    def _log(self, msg):
        if self.log_callback:
            self.log_callback(msg)

    def _find_date_inputs(self):
        """查找日期输入框(通过placeholder匹配)"""
        inputs = self.page.locator("input")
        result = {"start": None, "end": None}
        for i in range(inputs.count()):
            try:
                el = inputs.nth(i)
                ph = (el.get_attribute("placeholder") or "").lower()
                if not ph:
                    continue
                if any(k in ph for k in ["开始", "起始", "起", "start", "from", "开始日期"]):
                    if not result["start"]:
                        result["start"] = el
                elif any(k in ph for k in ["结束", "截止", "止", "end", "to", "结束日期"]):
                    if not result["end"]:
                        result["end"] = el
            except Exception:
                continue
        return result

    def _find_button(self, texts, exact=False):
        """按文本查找按钮,返回第一个匹配"""
        for t in texts:
            try:
                loc = self.page.get_by_text(t, exact=exact)
                if loc.count() > 0:
                    return loc.first
            except Exception:
                continue
        return None

    def _fill_dates(self, start_date, end_date):
        """填入日期范围,返回成功填入数量"""
        filled = 0
        date_inputs = self._find_date_inputs()
        if date_inputs["start"]:
            try:
                date_inputs["start"].click(timeout=3000)
                date_inputs["start"].fill(start_date)
                filled += 1
            except Exception:
                pass
        if date_inputs["end"]:
            try:
                date_inputs["end"].click(timeout=3000)
                date_inputs["end"].fill(end_date)
                filled += 1
            except Exception:
                pass
        return filled

    def _click_query(self):
        """点击查询/搜索按钮"""
        for t in ["查询", "搜索", "确定", "筛选", "查找"]:
            btn = self._find_button([t], exact=True)
            if btn:
                try:
                    btn.click(timeout=3000)
                    return True
                except Exception:
                    continue
        return False

    def _click_export(self):
        """点击导出/下载按钮"""
        for t in ["导出", "下载", "生成报表", "导出报表", "下载账单", "下载报表", "导出账单"]:
            btn = self._find_button([t], exact=False)
            if btn:
                try:
                    btn.click(timeout=3000)
                    return True
                except Exception:
                    continue
        return False

    def _click_confirm(self):
        """点击弹窗中的确认按钮"""
        for t in ["确定", "确认", "下载", "导出", "保存"]:
            btn = self._find_button([t], exact=True)
            if btn:
                try:
                    btn.click(timeout=2000)
                    return True
                except Exception:
                    continue
        return False

    def _setup_dialog_handler(self):
        """自动接受所有JS弹窗"""
        try:
            self.page.on("dialog", lambda d: d.accept())
        except Exception:
            pass

    def export(self, start_date, end_date, timeout=120):
        """
        执行智能导出流程
        返回: "success"(自动完成) / "manual"(需手动) / "failed"(失败)
        """
        self._setup_dialog_handler()
        self._log("  [自动] 开始智能导出流程...")

        # 1. 填入日期范围
        filled = self._fill_dates(start_date, end_date)
        if filled > 0:
            self._log(f"  [自动] 已填入日期范围: {start_date} ~ {end_date}")
        else:
            self._log("  [提示] 未找到日期输入框,可能使用日期选择器组件")
            self._log("  [提示] 将尝试直接查找导出按钮")

        # 2. 点击查询
        time.sleep(1)
        if self._click_query():
            self._log("  [自动] 已点击查询按钮")
            time.sleep(2)

        # 3. 点击导出(点击前开启下载捕获,避免下载事件/文件漏掉)
        self.browser.begin_wait_download()
        if self._click_export():
            self._log("  [自动] 已点击导出按钮")
            time.sleep(2)

            # 4. 等待下载
            path = self.browser.wait_download(timeout=timeout)
            if path:
                self._log(f"  [成功] 下载完成: {os.path.basename(path)}")
                return "success"

            # 5. 可能弹窗需要确认,再试一次
            self._log("  [提示] 未检测到下载,尝试点击确认弹窗...")
            if self._click_confirm():
                time.sleep(1)
                path = self.browser.wait_download(timeout=timeout)
                if path:
                    self._log(f"  [成功] 下载完成: {os.path.basename(path)}")
                    return "success"
        else:
            self._log("  [提示] 未找到导出按钮")

        # 6. 自动导出失败,截图提示手动
        self._log("  [提示] 自动导出未完全成功,请手动完成导出")
        self.browser.screenshot(f"manual_{int(time.time())}")
        return "manual"

    def quick_export(self, start_date, end_date, timeout=120):
        """
        快速导出: 只尝试点击导出按钮(用于已设置好默认日期的页面)
        """
        self._setup_dialog_handler()
        self._log("  [自动] 尝试快速导出...")
        self.browser.begin_wait_download()
        if self._click_export():
            self._log("  [自动] 已点击导出按钮")
            time.sleep(2)
            path = self.browser.wait_download(timeout=timeout)
            if path:
                self._log(f"  [成功] 下载完成: {os.path.basename(path)}")
                return "success"
            if self._click_confirm():
                time.sleep(1)
                path = self.browser.wait_download(timeout=timeout)
                if path:
                    self._log(f"  [成功] 下载完成: {os.path.basename(path)}")
                    return "success"
        self._log("  [提示] 快速导出未成功,请手动完成")
        self.browser.screenshot(f"manual_{int(time.time())}")
        return "manual"
