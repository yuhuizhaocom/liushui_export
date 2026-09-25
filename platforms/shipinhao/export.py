"""
视频号平台导出脚本

操作流程(基于截图 - 微信小店商家后台):
    1. 打开微信小店后台 → 资金结算 → 流水与账单
    2. 确认在"资金流水"标签页
    3. 设置动账时间范围
    4. 点击"查询"
    5. 点击"全部导出"
    6. 等待导出完成,页面提示"资金流水已导出"
    7. 如未自动下载,点击提示中的"下载数据"链接

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


class ShipinhaoExporter(PlatformBase):
    key = "shipinhao"
    name = "视频号"
    # 微信小店商家后台 - 资金结算 → 流水与账单
    login_url = "https://store.weixin.qq.com/"
    export_url = "https://store.weixin.qq.com/shop/funds/moneyManage?section=1"
    guide = "资金结算 → 流水与账单 → 资金流水 → 选动账时间 → 查询 → 全部导出 → 下载数据"
    # 资金流水导出需在手机上微信扫码确认,属人工操作,导出时统一排到最后
    manual_intervention = True
    intervention_hint = "资金流水导出需要您手机微信扫码确认"

    def _set_time(self, browser, start_date, end_date):
        """设置"动账时间"范围(weui date-range,输入框 placeholder=动账开始/结束时间)。
        优先用 fill 赋值的直接方式;若回读 input 值未生效(受控组件),再退到
        点击->全选->键盘输入->Enter,最后 Esc 关闭日期面板。"""
        try:
            import re
            st = browser.page.get_by_placeholder(re.compile("开始"))   # 动账开始时间
            en = browser.page.get_by_placeholder(re.compile("结束"))   # 动账结束时间
            if st.count() == 0 or en.count() == 0:
                return False

            def set_one(loc, value):
                filled = False
                try:
                    loc.fill(value)
                    filled = True
                except Exception:
                    filled = False
                # 校验: 组件未接收则改用键盘方式
                try:
                    if value[:10] not in (loc.input_value() or ""):
                        filled = False
                except Exception:
                    filled = False
                if not filled:
                    loc.click()
                    browser.page.keyboard.press("Control+a")
                    browser.page.keyboard.type(value)
                    browser.page.keyboard.press("\n")

            set_one(st.first, f"{start_date[:10]} 00:00:00")
            browser.sleep(0.5)
            set_one(en.first, f"{end_date[:10]} 23:59:59")
            browser.page.keyboard.press("Escape")  # 关闭日期面板
            browser.sleep(0.5)
            return True
        except Exception:
            return False

    def export(self, browser, start_date, end_date):
        # ===== 1. 打开流水与账单页面 =====
        browser.navigate(self.export_url)
        browser.sleep(3)
        browser.close_popup()

        # ===== 2. 确认在"资金流水"标签页 =====
        browser.click_text("资金流水")
        browser.sleep(1)

        # ===== 3. 设置动账时间范围 =====
        # 日期没设成功就必须停: 继续点"查询/全部导出"会得到页面默认区间的账单,
        # 而归档文件名用的是本次请求的区间 —— 从产物上根本看不出区间错了。
        if not self._set_time(browser, start_date, end_date):
            browser._log("[中止] 视频号: 动账时间未能设置(找不到\"动账开始/结束时间\""
                         "输入框或设置过程报错), 已停止自动导出。若反复出现, 说明该平台"
                         "日期组件又变了, 需要复核 _set_time。", "warning")
            browser.snapshot("日期未填入")
            return "manual"
        browser.sleep(1)

        # ===== 4. 点击"查询" =====
        browser.click_text("查询")
        browser.sleep(3)

        # ===== 5. 点击"全部导出" =====
        browser.click_text("全部导出")
        browser.sleep(10)

        # ===== 6. 资金流水导出需微信扫码确认: 停留并提醒用户扫码 =====
        # 平台会弹出"需在手机上微信扫码确认",此处阻塞等待用户扫码完成后点击确定继续
        browser.wait_user("资金流水导出需要您在手机上使用微信扫码确认。\n请完成扫码后回到本界面点击\"确定\"继续下载。")

        # ===== 7. 等待导出完成,页面会提示"资金流水已导出" =====
        # 如未自动下载,点击提示中的"下载数据"链接
        browser.begin_wait_download()
        if browser.is_visible_text("下载数据", timeout=30):
            browser.click_text("下载数据")
            browser.wait_for(timeout=1)

        # ===== 8. 等待下载完成 =====
        path = browser.wait_download(timeout=60)
        if path:
            return "success"
        return "manual"
