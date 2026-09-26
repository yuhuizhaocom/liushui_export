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

一个主账号下要切子商户导多份时:
    在界面这一家商户行上点「子商户」, 一行一个把子商户号/名录进去(顺序就是导出顺序),
    导出时程序会在同一次登录里逐个切过去、每份各归各的目录(`downloads/银联/主账号/子商户/日期/`)。
    切换与读回由下面那几个 SUB_* 字面量决定, **它们还没在真实页面上核过**: 第一次用请开
    「平台管理 → 打开录制」照着切一遍, 再照录制改字面量。核之前会发生的事是"整家停手转人工"
    或"日志里提示归属未经校验", 不会把 A 的账单记到 B 名下。
"""

from core.platform_base import PlatformBase


class YinlianExporter(PlatformBase):
    key = "yinlian"
    name = "银联"
    # 中国银联条码支付综合前置平台 - 文件管理 → 商户对账单下载
    login_url = "https://up.95516.com/qmpmgm/"
    export_url = "https://up.95516.com/qmpmgm/system/guide"
    guide = "文件管理 → 商户对账单下载 → 选日期 → 查询 → 商户对账单下载 → 选类型/时间 → 导出 → 下载"

    # ===== 子商户: 主账号登录一次, 切着导多份 =====
    # 银联是"一个主商户下挂多个子商户、要一份一份导"的典型, 所以这里声明支持:
    # 导出流程会在同一份 profile 里按界面「子商户」里录的顺序逐个切换并各导一份。
    supports_sub_merchants = True

    # ⚠ 下面这几条是**按常见后台的样子先写的, 银联真实页面还没核实过** —— 第一次真机跑
    #   请用「平台管理 → 打开录制」照着切一遍子商户, 再照录制结果改这些字面量。
    #   猜错的后果是可控的: 入口点不到 → 返回 False → 这一家整家停手转人工, 不会拿别人
    #   的账单冒充; 读回选择器没填/没对上 → 返回空串 → 照常导出但日志写明"归属未经校验"。
    SUB_SWITCH_ENTRY = "切换商户"                       # 进入子商户选择的入口文字
    SUB_SEARCH_LABELS = ("商户号", "子商户号", "商户编号")  # 弹窗里的搜索/输入框 placeholder
    SUB_CONFIRM_TEXTS = ("确定", "确认")                 # 弹窗里的提交按钮
    SUB_ID_SELECTOR = ""                                # 页面显示"当前商户号"的元素(待真机填)
    SUB_SWITCH_SETTLE_S = 3                             # 切完给页面的落地时间

    def switch_sub_merchant(self, browser, sub_merchant):
        """进导出页 → 打开商户切换入口 → 找到那一家 → 提交。切换是否真成功由流程读回核对。"""
        browser.navigate(self.export_url)
        browser.sleep(self.PAGE_SETTLE_S)
        if not browser.click_text(self.SUB_SWITCH_ENTRY):
            browser._log(f"[子商户] 页面上找不到「{self.SUB_SWITCH_ENTRY}」入口, 没能切到"
                         f"「{sub_merchant}」(第一次跑请照录制核这几个字面量)", "warning")
            browser.snapshot("子商户入口未找到")
            return False
        browser.sleep(1)
        found = False
        for label in self.SUB_SEARCH_LABELS:
            if browser.fill_placeholder(label, sub_merchant):
                found = True
                break
        if not found:
            # 也有后台不是搜索框, 而是列表里直接列着子商户名/号: 那就按文字点它
            found = browser.click_text(sub_merchant, exact=True)
        if not found:
            browser._log(f"[子商户] 打开了切换入口但没找到「{sub_merchant}」这一家 —— "
                         f"清单里的名字要和页面上显示的写法一致", "warning")
            browser.snapshot(f"子商户未找到_{sub_merchant}")
            return False
        for text in self.SUB_CONFIRM_TEXTS:
            if browser.click_text(text):
                break
        browser.sleep(self.SUB_SWITCH_SETTLE_S)
        return True

    def current_sub_merchant(self, browser):
        """读回页面上显示的当前商户号。选择器还没核实过, 读不到就返回空串(不猜)。"""
        if not self.SUB_ID_SELECTOR:
            return ""
        try:
            return (browser.page.locator(self.SUB_ID_SELECTOR).first.inner_text() or "").strip()
        except Exception:
            return ""

    def trigger_export(self, browser, start_date, end_date):
        """查询后要再开一层"商户对账单下载"弹窗, 在弹窗里选类型/时间再导出。"""
        browser.click_text("查询")
        browser.sleep(3)
        browser.click_text("商户对账单下载")
        browser.sleep(2)
        browser.click_text("所有交易账单")
        browser.sleep(1)
        browser.fill_placeholder("对账单开始时间", start_date[:10])
        browser.fill_placeholder("对账单结束时间", end_date[:10])
        browser.sleep(1)
        browser.click_text("导出")
        browser.sleep(10)      # 银联生成对账单文件明显偏慢

    def export(self, browser, start_date, end_date):
        # 打开商户对账单下载 → 填日期 → 查询/弹窗导出 → 点列表里的"下载"
        return self.run_standard_flow(browser, start_date, end_date)
