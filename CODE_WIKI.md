# 流水自动导出工具 v1.0 — Code Wiki

> 本文档是对项目代码的完整解读，涵盖整体架构、模块职责、关键类与函数、依赖关系与运行方式，供二次开发与维护参考。

***

## 目录

- [1. 项目概述](#1-项目概述)

- [2. 整体架构](#2-整体架构)

- [3. 目录结构](#3-目录结构)

- [4. 主要模块职责](#4-主要模块职责)

- [5. 关键类与函数](#5-关键类与函数)

- [6. 依赖关系](#6-依赖关系)

- [7. 核心流程解析](#7-核心流程解析)

- [8. 平台插件体系](#8-平台插件体系)

- [9. 项目运行方式](#9-项目运行方式)

- [10. 数据目录与文件约定](#10-数据目录与文件约定)

- [11. 附录：文件清单](#11-附录文件清单)

***

## 1. 项目概述

| 项目   | 说明                                                                                |
| ---- | --------------------------------------------------------------------------------- |
| 名称   | 流水自动导出工具（Liushui Export Tool）v1.0                                                 |
| 定位 | 面向**非技术业务用户**的可视化桌面工具，自动批量导出电商/支付平台的交易流水（**当前已实现：有赞**；其余平台为待实现的开发模板，可通过插件体系补充） |
| 技术栈  | Python 3.10+、Tkinter（GUI）、Playwright / Chromium（浏览器自动化）                           |
| 架构风格 | **插件化**：每个平台一个独立文件夹，程序启动时自动发现加载，新增平台无需修改主程序                                       |

核心特性：

- **智能定位导出**：按 placeholder 文字/按钮文本自动识别页面上的日期输入框、查询按钮、导出按钮，不依赖平台特定选择器（`core/exporters.py` 的 `SmartExporter`）；

- **登录态隔离**：为每个「平台 + 商户」建立独立浏览器数据目录，登录状态按商户持久化保存（1\~7 天）；

- **下载兜底机制**：事件队列 + 目录轮询双通道捕获浏览器下载，防漏、防残留；无人认领的 UUID 残留文件移入 `downloads/待确认/`（**不再**按当前上下文改名归到某个商户目录，避免上一商户的文件冒充本商户账单）；候选与归档校验按**文件头**判类型，截图、假 `.xlsx` 一律不算账单；内容校验**先数数据行**——读得出数据行的表格直接通过，"操作失败/请登录"等错误文案只在读不出行时才用来区分空表与错误页（否则备注里出现这些字样的真账单会被判无效并删除）；

- **文件归档**：下载文件按 `平台/商户/日期范围` 组织，同名旧文件自动归档至 `历史/` 子目录；

- **高容错**：浏览器启动失败自动清理 Chrome 锁文件并重试 3 次；任何异常均写入日志并以弹窗提示，不静默崩溃。

***

## 2. 整体架构

项目采用三层结构，从上到下依次为 **GUI 表现层 → 核心框架层 → 平台插件层**：

```
┌─────────────────────────────────────────────────────────────────────────┐
│  GUI 表现层                                                            │
│  core/main_gui.py  (LiushuiApp, Tkinter)                               │
│  └─ 平台/商户勾选 · 日期选择 · 操作历史 · 实时日志 · 进度条 · 状态标识      │
├─────────────────────────────────────────────────────────────────────────┤
│  核心框架层  (全部位于 core/ 包内)                                       │
│  ├─ core/browser.py     BrowserManager: 浏览器生命周期/导航/点击/填表/   │
│  │                     下载捕获/文件归档/弹窗处理/日期选择器/重试容错      │
│  ├─ core/exporters.py   SmartExporter: 智能定位自动导出(通用兜底导出器)    │
│  ├─ core/loader.py      discover_platforms: 扫描加载 platforms/ 插件     │
│  ├─ core/platform_base.py  PlatformBase: 平台抽象基类(元信息+接口约定)    │
│  ├─ core/config.py      全局配置常量 + 用户设置(settings.json)读写         │
│  └─ core/logger.py      日志(文件 + GUI 回调双通道)                       │
├─────────────────────────────────────────────────────────────────────────┤
│  平台插件层  platforms/<platform>/export.py  (每个平台一个文件夹)         │
│  当前已实现: youzan(有赞)  其余平台为待实现模板                           │
└─────────────────────────────────────────────────────────────────────────┘
```

关键设计决策：

1. **平台即插即用**：`core/loader.py` 扫描 `platforms/` 下含 `export.py` 的文件夹，导入模块并找出继承 `PlatformBase` 的子类进行实例化，以 `key` 为索引注册。新增平台 = 新建文件夹 + 一个类，主程序零改动。
2. **浏览器能力下沉**：平台脚本不直接操作 Playwright，而是调用 `BrowserManager` 提供的 10+ 个用户友好辅助方法（`click_text`、`fill_placeholder`、`wait_download` 等），平台改版只需改脚本，浏览器层稳定复用。
3. **登录态按商户隔离**：`BrowserManager.set_browser_profile(platform_key, merchant)` 生成 `browser_data/平台key/商户/` 独立 profile，同一商户的登录状态不互相污染。
4. **下载双通道捕获**：`context.on("download")` 事件队列 + downloads 根目录文件轮询兜底，保证"点了下载但事件丢失"的场景仍能拿到文件。

***

## 3. 目录结构

```
liushui_export/
├── requirements.txt         # Python 依赖清单（playwright==1.62.0）
├── settings.json            # 用户设置（运行时自动生成/更新）
├── start.bat                # 命令行启动脚本（控制台常驻，错误可见）
├── 启动工具.vbs             # 免控制台启动脚本（pythonw，首次自动装依赖）
├── 使用说明.md              # 面向最终用户的使用文档
├── 脚本编写指南.md          # 面向开发者的平台插件编写指南
├── core/                    # ★ 核心框架包（GUI + 浏览器 + 导出器 + 配置 + 日志）
│   ├── __init__.py
│   ├── main_gui.py          # 主程序入口（Tkinter GUI）
│   ├── browser.py           # 浏览器管理（BrowserManager）
│   ├── exporters.py         # 智能导出器（SmartExporter）
│   ├── config.py            # 全局配置与用户设置读写
│   ├── cleanup.py           # 过期日志与汇总副本的按保留期清理
│   ├── crashguard.py        # 启动期崩溃兜底（sys/threading.excepthook）
│   ├── logger.py            # 日志模块
│   ├── loader.py            # 平台加载器（自动发现）
│   └── platform_base.py     # 平台基类
├── platforms/               # ★ 平台插件目录
│   ├── __init__.py
│   └── youzan/        (export.py + __init__.py)   # 当前唯一已实现平台
├── downloads/               # 运行时自动创建：导出文件归档目录
├── browser_data/            # 运行时自动创建：浏览器登录态数据
│   └── <平台key>/<商户名>/  # 每商户一个 profile
└── logs/                    # 运行时自动创建：运行日志 + 操作历史
```

***

## 4. 主要模块职责

| 模块/文件                    | 职责                                                                                                                                                                    |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `core/main_gui.py`       | **GUI 主程序**。构建三栏界面（左平台/商户选择、中设置+操作历史+按钮、右日志+进度），管理任务线程、浏览器实例生命周期，编排「首次登录 / 导出流水 / 检查状态」三大业务流程，导出后自动汇总文件并打开文件夹。                                                        |
| `core/browser.py`        | **浏览器管理**。封装 Playwright 持久化上下文（含登录态），提供导航/点击/填表/等待/下载捕获/文件归档/弹窗关闭/日期选择器辅助方法；内置浏览器启动重试与 Chrome 锁文件清理。                                                                  |
| `core/exporters.py`      | **智能导出器**。`SmartExporter` 通过 placeholder 匹配日期输入框、按文本匹配查询/导出按钮，全自动完成「填日期→查询→导出→等待下载」，失败时截图并返回提示，是平台未自定义 `export()` 时的默认实现。                                             |
| `core/loader.py`         | **平台加载器**。扫描 `platforms/` 自动发现并实例化所有平台，返回 `{key: 平台实例}` 字典：注册模块内**所有**带 key 的 `PlatformBase` 子类（用 `__module__` 排除从别处 import 进来的），key 重复/模块里没有平台类/导入失败都会写日志（导入失败以前只 print，pythonw 下界面完全看不到）。`reload_platforms()` 供平台管理改完脚本后免重启生效，需同时清 `sys.modules`、importlib 的 stat 缓存和磁盘上的 `.pyc`。                                                                                                           |
| `core/outputs.py` | **导出文件汇总**。`find_output_files`（认 `平台/日期` 与 `平台/商户/日期` 两种结构、跳过 `.crdownload/.tmp/.part`）、`copy_to_summary_dir`（复制到 `downloads/开始_结束_时间戳/`，无文件返回 None 因此不再打开文件夹）、`sanitize_name`（路径非法字符清理，商户名与 profile 目录共用）。纯文件操作，可脱离界面测试。 |
| `core/dialogs.py` | **查看类对话框**。`show_log_history(root)` 历史日志窗口、`show_stats_dashboard(app)` 稳定性看板；只读根窗口/状态栏，与导出流程无关。 |
| `core/theme.py` | **界面配色常量**。单独成模块供 `dialogs.py` 复用，避免反向 import `main_gui` 成环。 |
| `core/platform_base.py`  | **平台抽象基类**。定义平台元信息（`key/name/login_url/export_url/guide/enabled`）与接口约定（`login()`、`export()`），默认 `login()` 打开登录页，默认 `export()` 走 `SmartExporter`；另提供通用导出骨架 `open_export_page/set_date_range/trigger_export/download_export_file` + `run_standard_flow`，平台只覆盖有差异的钩子。                      |
| `core/config.py`         | **全局配置**。`ROOT_DIR`（项目根）、`DOWNLOAD_DIR`（下载目录）、`BROWSER_DATA_DIR`（浏览器数据目录）、`SETTINGS_FILE`、`DEFAULT_SETTINGS`；`load_settings()` 读设置（缺字段回默认），`save_settings(部分字典)` **以现有文件为底只覆盖传进来的键**——以前是以 `DEFAULT_SETTINGS` 为底，界面各处"点一下只写自己那一两个键"于是每次点勾都把别的设置抹回默认值；另有 `write_text_atomic`/`write_json_atomic`（临时文件 + `os.replace` 原子替换）——`settings.json`/`scheduled_tasks.json`/`selection_state.json` 三处整体重写都用它，避免写一半崩溃后被读取端"except 用默认值"静默清空。 |
| `core/cleanup.py` | **过期文件清理**。工具长期跑会累积"每次启动一个 run_日期.log"+"每次导出一个 起_止_时间戳/ 汇总副本目录"。`prune_logs`/`prune_summary_dirs` 按 mtime 删超过保留天数的这两类，**按文件名正则白名单认领**（不合规矩的名字原样留着），`stats.jsonl`（看板唯一历史）、`downloads/待确认/`（无人认领的账单）、`downloads/<平台>/…`（账单原件）任何情况下都不碰；`keep_days<=0` 表示关闭。清理失败只写日志，绝不影响启动。 |
| `core/logger.py` | **合并后的唯一日志通道**。统一输出控制台、`logs/run_YYYYMMDD.log` 与 GUI 回调；`log(msg, level, callback)` 支持级别（warning/error 界面行带 `[WARN]/[ERROR]` 前缀）；`record_stat`/`load_stats`/`summarize_stats` 管稳定性统计，其中 `load_stats(limit)` 用尾部反向分块读（`_read_tail_lines`），不再把整个 `stats.jsonl` 读进内存。 |
| `core/keepalive.py` | **登录保活服务**。`KeepAliveService`（后台线程）按可配置间隔周期巡检已选商户的登录态（打开 `login_url` 判断会话），任务执行中自动跳过本轮；浏览器工厂可注入便于测试。 |
| `core/crashguard.py` | **启动期崩溃兜底**。工具用 `pythonw` + 隐藏窗口启动，没有控制台；以前导入阶段抛错（缺 tkinter、`logs` 建不出来）的表现就是"图标闪一下，什么也没有"。`install()` 把 `sys.excepthook`/`threading.excepthook` 指向 `report_uncaught`：堆栈写进 `logs/crash_日期_时间_微秒.txt`，主线程再弹窗告知文件位置（一个进程只弹一次）。子线程那条**只落文件不弹窗**——在非主线程里拉 Tk 窗口可能把界面吊住。`main_gui` 在其余 import 之前装好它，所以模块自身的导入失败也覆盖得到。 |
| `platforms/*/export.py`  | **平台插件**。每个文件定义一个继承 `PlatformBase` 的导出类，实现该平台的登录与导出流程；平台专项逻辑（如有赞的 URL 日期参数）直接写在平台脚本内，不放入 `core/`（见 [第 8 章](#8-平台插件体系)）。 |
| `core/platform_admin.py` | **平台管理/脚本调试**。`render_platform_skeleton()` 纯函数生成骨架（输入按字面量转义，key 须字母/下划线开头）、`save_platform_script()` 先验语法再原子写（语法错误不覆盖磁盘上的好脚本）、`DebugProbe` 用 `__getattr__` 通用透传并如实抛异常、`PlatformManagerDialog`（向导+内置编辑器+列表）与 `DebugDialog`（试运行）三个 Tkinter 弹窗。 |
| `core/scheduler.py` | **定时任务**。`CronExpr`（5 字段 cron 轻量解析/匹配/next-run）、`CronJob`/`TaskStore`（`scheduled_tasks.json` 持久化）、`CronScheduler`（后台线程到期触发 `app.trigger_job`）、`SchedulerDialog`/`JobEditDialog`（任务管理界面）；导出失败自动重试（`run_with_retry`）亦由本批提供。 |
| `start.bat` / `启动工具.vbs` | **启动脚本**。以 `python -m core.main_gui` 方式启动：前者用控制台 Python（错误可见）；后者用 `pythonw` 免控制台，并在首次运行时自动 `pip install playwright` + 安装 Chromium。找 Python 分五档：① 历史写死路径（`E:\Python\Python314`、`C:\Python31x`，**排第一是有意的**——现在能用的机器不许换解释器）② `%WINDIR%` 下的 `py`/`pyw` 启动器（注意它不叫 `python.exe`，得按各自名字找）③ 用户级默认目录 `%LocalAppData%\Programs\Python\Python3xx` ④ 项目自带的 `.venv\Scripts` ⑤ `C:\Python3xx`。目录枚举带 `On Error Resume Next`，没权限读 `C:\` 也只跳过不中止。诊断：`cscript //nologo 启动工具.vbs --print-python` 只打印选中的解释器，不装依赖也不起界面。 |

***

## 5. 关键类与函数

### 5.1 `LiushuiApp`（core/main\_gui.py）— GUI 主应用类

构造时依次执行：发现平台（`discover_platforms`）→ 读取设置 → 发现商户（`discover_merchants`）→ 建窗 → 构建右/左/中三栏 → 刷新概览条。维护关键状态：`browser`（当前浏览器实例）、`running`（任务锁）、`platform_vars/merchant_vars`（勾选状态）、`_login_confirm`（登录确认事件）。

#### 界面构建方法

| 方法                      | 说明                                                                                                              |
| ----------------------- | --------------------------------------------------------------------------------------------------------------- |
| `_setup_window()`       | 设置 1100x700、最小 900x550、默认最大化。                                                                                   |
| `_build_left_panel()`   | 左侧面板：平台勾选（联动商户）、商户数徽标、状态指示灯、`+` 添加商户按钮；商户子勾选框容器（可滚动 Canvas）。                                                    |
| `_build_right_panel()`  | 右侧面板：操作日志 `ScrolledText`（深色主题）+ 状态栏 + 确定性进度条。                                                                   |
| `_build_middle_panel()` | 中间面板：操作历史（跨启动累积）、导出日期（起止 + 昨天/近7天/近30天快捷）、显示浏览器开关、文件名模式单选（统一命名/保留原文件名）、概览条、按钮区（首次登录/检查状态/打开下载/使用说明 + 大号「开始导出」）。 |

#### 用户操作入口

| 方法                                     | 说明                                                           |
| -------------------------------------- | ------------------------------------------------------------ |
| `_action_login_all()`                  | 校验日期/选择后，在主线程用 `_collect_tasks` 备好任务列表，启动线程执行 `_do_login(tasks)`，并记录操作历史。                          |
| `_action_export_all()`                 | 校验后弹出任务确认框（平台·商户明细 + 日期），确认后**在主线程取好日期与单步开关**再启动 `_do_export(tasks, start, end, step_debug)`。                 |
| `_action_check_status()`               | 启动线程执行 `_do_check(tasks)`，逐个商户调用平台自身的 `check_login` 判断登录态。                       |
| `_action_open_folder()`                | 打开 `downloads/` 目录。                                          |
| `_request_abort()`                     | 「中止本次任务」按钮：置 `_abort` 事件并记日志。只在**商户边界**生效（`_execute_export_tasks`/`_do_login`/`_do_check` 每轮开头查 `_aborted()`），不会中途掐浏览器留下半截下载；按钮由 `_run_async` 启用、`_thread_wrapper` 结束时置灰。 |
| `_action_help()`                       | 弹出简化使用说明。                                                    |
| `_prompt_add_merchant(key, plat_name)` | 弹窗输入商户名 → 调 `_add_merchant`；失败(名字空/重名)时弹窗说明并**留着窗口让用户改**。 |
| `_add_merchant(key, raw_name)`         | 建 profile 目录 + 加一行勾选，返回 `(是否成功, 文案)`。先查重再动手：按 Windows 目录规矩比（忽略首尾空格、不分大小写），重名直接拒绝——以前重名会 `makedirs(exist_ok=True)` 照样建、界面照样塞一行，而 `merchant_vars[平台][名字]` 是字典，新 Var 顶掉旧的，于是两行同名共用一个勾选框（勾上面那行等于没勾）。也拒绝 `..`/`.`/`___` 这类"清洗后没内容"的名字（`sanitize_name` 只换 `<>:"/\`，`..` 原样通过，拼进路径就指到 `browser_data` 本身）。 |
| `_prompt_delete_merchant(key, merchant)` | 商户行右侧「删」：任务进行中先拒绝；二次确认里写明要删哪个目录；只删 `browser_data/<key>/<商户>` 这一层（`delete_merchant_profile` 按 normpath 复核深度），**已导出的账单与 `downloads/待确认/` 不动**；删不成(目录被浏览器占着)如实报错且不改列表。成功后摘掉那一行并刷新商户数徽标。 |
| `_refresh_merchants()`                 | 左栏「刷新商户」：重新扫 `browser_data` 并走 `_rebuild_platform_list`（勾选按 `selection` 还原，不会被洗掉），日志写明新增了谁、外部删了谁，没变化也报总数。以前手工放进目录或从别的电脑拷来的 profile 要重启才看得见。 |

#### 核心业务流程方法

| 方法                                 | 说明                                                                                                                                                                                                                    |
| ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_do_login(tasks)`                   | **首次登录**：逐家商户跑 `_login_one_round`（可见浏览器 → 用户**自己关掉窗口**表示登完 → 释放 profile → 后台核实）。未登录时 `_ask_login_retry` 让用户选「回去继续登录 / 放弃这家」，最多 `LOGIN_RETRY_LIMIT` 轮。**界面上的「确定」按钮已经去掉**：以前用户常在短信/扫码还没完成时就点确定，程序据此存下一份没登录的 profile。收尾日志：`已核实登录成功 X / 未登录 Y / 没能核实 Z / 未完成 N`。 |
| `_wait_login_window_closed(browser)` | 轮询 `browser.window_closed()`；期间只要 `login_signature()`（cookie 条数+内容指纹，不触发导航）变了就 `save_login_state(quiet=True)` 当场导出。原因：persistent context 在 Chromium 退出时不保留 session cookie，等窗口关完再存就来不及了。**刻意不调用 `plat.check_login`** —— 它会把页面导航到导出地址，用户正扫码会被拽走。返回 `("closed"/"aborted"/"timeout", 是否保存过)`。 |
| `_login_one_round(plat, key, merchant)` | 一轮完整登录：可见浏览器 → `plat.login` → 提示窗 → `_wait_login_window_closed` → `_close_browser()` 释放 profile → `_verify_login_after_close`；`finally` 再收一次浏览器。返回 `True/False/None/"skip"`（skip = 中止/超时/登录页没打开，这一轮不核实）。重试就是再跑一轮。 |
| `_verify_login_after_close(plat, key, merchant, saved_seen)` | 用 `force_headless=True` 重开浏览器跑 `plat.check_login`（此时导航不打扰任何人）。已登录→绿灯 `True`；未登录→红灯 `False` 并把原因存进 `_last_verify_reason`；核实异常→橙灯 + "没能核实" `None`，**不冒充未登录**。**只报结果，要不要再给一次机会由 `_do_login` 问用户。** |
| `_ask_login_retry(plat, merchant, attempt)` | 未登录时 `askretrycancel` 二选一：「重试」=重开这家的登录页继续登，「取消」=先放弃、稍后单独重登；文案带上 `_last_verify_reason`。为什么交给用户：微信支付那类同域平台靠页面文案判断，存在"其实登上了但被判未登录"的可能。弹窗起不来/一小时无人应答都按放弃返回 False，绝不把整批卡住。每家最多 `LOGIN_RETRY_LIMIT`（3）轮，到上限不再问。 |
| `_close_browser()`                   | 统一关闭点：`self.browser.close()` 后置 None，异常只写一句日志。因为 `BrowserManager.close()` 内部先导出登录态，导出收尾/保活/手工测试窗口/退出这些路径都会落盘。 |
| `_do_export(tasks, start, end, step_debug)` | **导出流水**：转交 `_execute_export_tasks`；结束后调用 `_copy_export_outputs` 汇总并自动打开文件夹。任务列表/日期/单步开关均由主线程取好传入，工作线程不回读界面。 |
| `_do_check(tasks)`                   | **检查登录态**：逐个商户启动浏览器并调用 `plat.check_login(browser)`（多数平台靠 `export_url` 是否被重定向到登录路径判断），结果反映到平台状态灯。                                                                                                                    |
| `_copy_export_outputs(start, end)` | 导出后把各平台/商户日期目录下的最新文件去重复制到 `downloads/开始日期_结束日期_时间戳/` 汇总文件夹，兼容新旧两种目录结构（平台/日期 与 平台/商户/日期）。                                                                                                                              |

#### 任务线程管理

| 方法                                               | 说明                                                                                                        |
| ------------------------------------------------ | --------------------------------------------------------------------------------------------------------- |
| `_run_async(target)`                             | 在 `_task_lock` 内"检查 + 置位" `running`（运行中则投递警告弹窗），复位平台状态灯后启动后台守护线程。调用方可能是界面按钮，也可能是 CronScheduler 线程。                                                                 |
| `_thread_wrapper(target)`                        | 线程入口：执行任务 → 捕获异常写入日志 → `finally` 在锁内复位 `running`、重置进度条并**自动关闭浏览器**（避免残留进程）。                                           |
| `_ensure_browser(plat, merchant, force_visible)` | 关闭旧浏览器 → 按 headless 配置创建 `BrowserManager` → 设置平台+商户 profile → `start()`。登录流程 `force_visible=True` 强制显示窗口；是否显示读 `settings.json` 的 `show_browser`（复选框 trace 已同步），不跨线程读 Tk 变量。 |
| `_ui(fn)` / `_pump_ui()`                         | 界面更新的唯一通道：非主线程调用 `_ui` 只把回调投入队列，主线程 `_pump_ui` 每 60ms 批量消费并重新排程；已在主线程则就地执行。单条回调抛错只丢这一条，不会停泵。 |
| `_set_progress(**kwargs)`                        | 进度条更新的线程安全包装。                        |
| `_prompt_manual_leftovers(items, date_str)`      | 需要人工完成的平台**在整批结束后一次性列出一个窗**（以前每个 manual 各弹一个，多个平台会叠好几个窗）；全自动成功时完全不打扰。 |

#### 状态/日志

| 方法                                  | 说明                                                                                 |
| ----------------------------------- | ---------------------------------------------------------------------------------- |
| `_append_log(line)`                 | 追加日志：文本缓冲 `log_lines` 就地追加（最多保留 300 条），控件刷新经 `_ui` 投递主线程。任意线程可调。                                                             |
| `_set_status(text)`                 | 更新右侧状态栏（经 `_ui`）。                                                                           |
| `set_platform_status(key, status)`  | 平台状态灯的**线程安全入口**：`ok`绿 / `warn`橙 / `error`红 / `idle`灰。保活服务 `KeepAliveService._mark` 按这个名字查找回调，改名需同步。内部实现 `_paint_platform_status` 只能在主线程调用。                                     |
| `_refresh_summary()`                | 实时刷新概览条「已选 X 个商户 · Y 个平台 \[日期]」。                                                   |

#### 其他

- `_validate_dates(show_warning=True)`：日期区间的四道校验（格式、起止顺序、**不晚于今天**、**超过 92 天先问一句**），见 `check_date_range`。超长不拦只问（有人确实要一次导一年）；未来日期直接拦（后台只会给空列表，等满超时+两轮重试等于白等半天）。校验通过时把 `2026-9-2` 回写成 `2026-09-02`——这串字符同时进日期框填写、归档目录名和汇总文件夹名，两种写法会分成两个目录。`show_warning=False`（检查登录态）时不弹窗。

- `_on_platform_toggled(key)`：平台勾选框联动商户。**取消勾选时先记住每家商户的样子，再勾回来按原样还原**；从没记过的（第一次勾平台）仍按旧行为整平台全选，取消之后新建的商户默认勾上。以前是"取消=全不勾、勾回=全勾"，常年不勾的那几家只要手滑点一次平台框就悄悄回到任务里。

- `_run_cleanup_once()` / `_start_cleanup()`：启动后在**后台守护线程**跑一次 `core.cleanup.run_cleanup`，结果写成一行 `[清理] …`；目录不存在/文件被占用/设置写了怪值都只在日志上记一句，不影响启动。设置区新增「日志/汇总保留(天,0=不清理)」，默认 365。

- `_get_selected()` / `_get_selected_merchants(key)`：取勾选的平台 key / 商户名列表。**只能在主线程调用**（读 Tk 变量），任务用 `_collect_tasks(selected)` 一次性展成 `(key, plat, merchant)` 列表再交给后台线程。

- `pair_job_targets(job_platforms, job_merchants, merchants_by_key)`：定时任务的平台/商户配对。任务里的商户是一整条与平台无关的逗号文本，只保留 `browser_data/<平台key>/<商户>` 下确实建档的配对，不做笛卡尔积。

- `iter_selected_merchants`（= `__iter_merchants`）：保活服务用的 `(平台key, 商户)` 迭代器，读 `self.selection` 纯数据镜像而非勾选框变量。

- `_sanitize_name(name)`：正则清洗路径非法字符 `[<>:"/\\|?*]` 及控制字符。

- 模块级函数 `check_dependencies()`：检测 `playwright`，缺失时询问并自动 `pip install playwright` + `playwright install chromium`，完成后重启进程。

- 模块级函数 `main()`：入口；全局异常兜底（写日志 + 弹窗提示），防止程序静默崩溃。

### 5.2 `BrowserManager`（core/browser.py）— 浏览器管理类

封装 Playwright 的**持久化上下文**（`launch_persistent_context`，`user_data_dir` 按平台/商户隔离），是平台脚本与浏览器之间的唯一桥梁。构造时初始化下载捕获状态机（`_dl_queue` 事件队列、`_dl_capture_on` 捕获开关、`_dl_temp_dir` 临时目录）。

#### 生命周期与重试

| 方法                                   | 说明                                                                                                                                               |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `start()`                            | 启动前清理 Chrome 锁文件（`SingletonLock` 等）；`launch_persistent_context` 带 `accept_downloads=True`、`downloads_path` 指向 downloads 根目录；失败自动清理锁文件并重试，最多 3 次。 |
| `close()` / `__enter__` / `__exit__` | 关闭 context 与 playwright 实例，支持上下文管理器用法。**关之前先 `save_login_state_on_close()`**：Chromium 退出时不保留 session cookie，而关闭点远不止首次登录（导出收尾、保活巡检、手工测试窗口、程序退出）。守卫：当前 cookie 数比已存的登录态**少**时不覆盖（防页面清 cookie 把上次的登录态换成空的），条数一样但值变了(续期/换 token)照存；用户自己关掉窗口时存不上是常态，静默跳过不刷日志。 |
| `save_login_state_on_close()`        | 上面那层守卫的实现，返回是否真的写了文件；`_saved_cookie_count()` 读已落盘的 `login_state.json` 条数。 |
| `_cleanup_lock_files()`              | 删除 profile 目录中的 `Singleton*/lockfile/*.lock` 残留文件，防止 Chrome 启动失败。                                                                                |

#### 导航与元素操作

| 方法                                    | 说明                                                                                            |
| ------------------------------------- | --------------------------------------------------------------------------------------------- |
| `navigate(url, retries=3)`            | `goto` 带 3 次重试（`domcontentloaded`，60s 超时）。                                                    |
| `safe_click(selector, desc, retries)` | 等待选择器出现后点击，带重试。                                                                               |
| `safe_fill(selector, value, desc)`    | 等待选择器后 `fill`。                                                                                |
| `sleep(seconds)`                      | 固定等待（页面加载/动画）。                                                                                |
| `click_text(text, exact, retries)`    | 点击包含指定文字的按钮/链接（`get_by_text`）。匹配链：普通文本 → 忽略空白（`确 定`）→ JS 直接 `el.click()`。**`exact=True` 时第二路的正则两端锚定**（`^\s*导\s*出\s*$`），否则"导出"会被 DOM 更靠前的"导出历史"抢走——精确匹配不能被自己的回退链削弱。命中后若页面有 ≥2 处同样文字，另起一行 `[提醒] 页面有 N 处含"X"的文字, 已点中第一处: <该行文字>`，把"点的是哪一行"变得可见（导出历史列表点的永远是第一条）。 |
| `click_selector(selector)`            | 点击 CSS 选择器元素。                                                                                 |
| `fill_placeholder(ph, value)`         | 按 placeholder 填输入框，**返回是否真的填进去**（找不到框会写一条 warning 日志，不再静默返回 None）。                            |
| `fill_selector(sel, value)`           | 按 CSS 选择器填输入框。                                                                                |
| `is_visible_text(text, timeout)`      | 页面是否出现指定文字。先按"忽略内部空白"的**非锚定**正则匹配（`确 定` 这类文案命中得了，`导出` 也算出现在"导出历史"里），再退回普通文本；等待语义上宁可宽松。 |
| `wait_for(selector, text, timeout, stable_for)` | 语义化等待，替代固定 `sleep`：条件满足即返回 True，超时返回 False 并写一条 warning（不抛异常）。平台脚本用它等"查询结果里出现导出按钮"。 |
| `close_popup(retries)`                | 按优先级关闭广告/引导弹窗：弹窗容器关闭按钮（zent/element-ui/antd 等 8 种）→ 通用关闭图标 → 文字按钮（"我知道了/跳过/关闭"）；无弹窗自动跳过，不误操作。 |

#### 下载捕获与文件归档（核心机制）

| 方法                                                   | 说明                                                                                                                        |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `set_export_context(platform, start, end, merchant)` | 设置导出上下文；任务 ID = `开始日期_结束日期`，保证同平台/商户/区间落同一文件夹。                                                                            |
| `begin_wait_download()`                              | **开启捕获模式**（点击"下载"前调用）：清空事件队列 → 记录捕获起点时间 → 归位根目录残留 UUID 文件 → 清空并重建 `下载目录/平台/商户/日期/临时` 目录。                                  |
| `end_wait_download()`                                | 结束捕获：清队列、关开关、归位根目录残留、清理临时目录（空目录 rmdir，带 8 次重试防瞬时文件锁）。                                                                     |
| `_on_download(download)`                             | 浏览器下载事件回调：捕获模式下把 `{dl, name}` 推入队列，否则忽略。                                                                                  |
| `wait_download(timeout=120)`                         | **等待下载完成**：优先消费事件队列（`_save_download_to_temp` 保存到临时目录 → `_finalize_download` 归档）；兜底轮询根目录新出现的稳定文件；超时返回 None 并清理残留。          |
| `_finalize_download(path)`                           | 将文件归入 `平台/商户/日期范围/`：顶层始终保留本次最新文件，同区间已存在同名旧文件则先复制到 `历史/原名_时间戳.扩展名` 再替换；移动失败有 10 次重试 + 拷贝兜底。                                |
| `_normalize_download_name(...)`                      | 按设置生成文件名：`unified`=统一命名 `平台_商户_日期区间.扩展名`；`original`=保留原始名+商户前缀（UUID/无名文件回退统一命名）。扩展名白名单 `.xlsx/.xls/.csv/.txt`，默认 `.xlsx`。 |
| `_carry_over_orphans()`                              | 把下载根目录残留的 UUID 无扩展名文件归位（`_finalize_download`）。                                                                            |
| `_cleanup_stale_files(root)`                         | 清理 `.~`/`.crdownload/.tmp/.part` 半成品残留。                                                                                   |

#### 信息与辅助

| 方法                    | 说明                                        |
| --------------------- | ----------------------------------------- |
| `screenshot(name)`    | 截图保存到 downloads 根目录（`名字_时间戳.png`），用于失败排查。 |
| `get_page_info()`     | 返回当前页 `{url, title}`，用于登录态判断。             |
| `latest_export_dir()` | 返回当前任务归档文件夹路径。                            |
| `window_closed()`     | 浏览器窗口是否已被用户关掉：`context` 的 close 事件 / 已无页面 / 与 Chromium 通讯断开，任一成立即算；本身不抛异常。首次登录用它当"我登好了"的信号。 |
| `login_signature()`   | `(cookie 条数, 内容指纹)`，**不触发导航**；窗口没了返回 None。登录等待期间用它判断"有登录写入"，因为 `check_login` 会把页面跳走。 |
| `save_login_state(quiet=False)` | `context.storage_state()` 导出 cookie+localStorage（含 session cookie）到 profile 目录。`quiet=True` 不写日志（登录等待期间是轮询保存的）。 |
| `_safe_name(name)`    | 静态方法：清洗路径非法字符，防目录逃逸。                      |

#### 有赞专项（位于 `platforms/youzan/export.py`）

有赞平台独有的逻辑（带日期参数的流水页 URL 构造）直接实现在 `YouzanExporter` 类内，不放入 `core/` 通用模块：

| 方法 | 说明 |
|------|------|
| `_build_youzan_url(url, start, end)` | 构造带 `startTime/endTime`（UTC+8 毫秒时间戳）与 `dateType=SETTLE_TIME` 的有赞流水页 URL，不执行跳转。 |
| `open_youzan_record(browser, url, start, end)` | 一次性打开已带日期参数的流水页（仅导航一次，避免二次跳转）。 |

> 早期版本曾将有赞 zent 日期选择器操作（`fill_zent_*` 等）提取为独立模块，但当前 URL 参数方案已无需操作页面日期组件，相关未使用的方法已删除。

### 5.3 `PlatformBase`（core/platform\_base.py）— 平台基类

全平台脚本必须继承的抽象基类，接口约定如下：

| 成员                                      | 类型  | 说明                                                                                            |
| --------------------------------------- | --- | --------------------------------------------------------------------------------------------- |
| `key`                                   | 类属性 | 唯一标识（与文件夹名一致，如 `"youzan"`），加载器以此为索引。                                                          |
| `name`                                  | 类属性 | 界面显示名（如 `"有赞"`）。                                                                              |
| `login_url` / `export_url`              | 类属性 | 登录页 / 流水导出页地址。                                                                                |
| `guide`                                 | 类属性 | 给用户看的操作指引文字。                                                                                  |
| `enabled`                               | 类属性 | 是否在界面中显示（默认 True）。                                                                            |
| `login(browser)`                        | 方法  | 默认实现：`navigate(login_url)` 等待用户手动登录；可覆盖做特殊处理。                                                 |
| `export(browser, start_date, end_date)` | 方法  | **核心接口**。返回 `"success"`（成功）/ `"manual"`（需手动）/ `"failed"`（失败）。默认实现：懒加载 `SmartExporter` 执行智能导出。 |
| `open_export_page(browser)`            | 方法  | 骨架钩子①：`navigate(export_url)` + `sleep(PAGE_SETTLE_S)` + `close_popup()`。 |
| `set_date_range(browser, start, end)`  | 方法  | 骨架钩子②：按 placeholder 填「开始日期/结束日期」（各取 `[:10]`）。**返回两个框是否都填成功**；页面改版/框名不同导致填不进去时不能继续——否则后台按自己的默认区间出账单，文件名却是本次请求的区间。 |
| `trigger_export(browser, start, end)`  | 方法  | 骨架钩子③：点「查询」再点「导出」。需先切标签、或导出后还要去历史报表页的平台覆盖此方法。 |
| `download_export_file(browser, label, settle_s)` | 方法 | 骨架钩子④：`begin_wait_download` → 点 `label`（默认 `DOWNLOAD_LABEL`「下载」）→ `sleep(DOWNLOAD_SETTLE_S)` → `wait_download(timeout=DOWNLOAD_TIMEOUT_S)`，返回 `"success"`/`"manual"`。 |
| `PAGE_SETTLE_S` / `DOWNLOAD_TIMEOUT_S` / `DOWNLOAD_LABEL` / `DOWNLOAD_SETTLE_S` / `DATE_VALUE_SLICE` | 类属性 | 骨架的三个可声明差异点：打开页面后等几秒（3）、下载最多等多久（60）、下载按钮的真实文字（有的后台叫「下载全部」「下载明细」，小红书干脆就是「导出」）、点完给几秒落地、以及填进日期框的字符串长度（天猫「月汇总」只吃 `2026-09`）。 |
| `run_standard_flow(browser, start, end)` | 方法 | 串起上述四步；日期没全部填进去时**在点导出之前停下**（截图 + `[中止]` 日志 + 返回 `"manual"`），不会拿页面默认区间的账单冒充本次区间。平台脚本 `export()` 里 `return self.run_standard_flow(...)` 即采用骨架。**不调用则行为完全不变**（SmartExporter 默认实现保留）。手写流程的京东/拼多多各自做了同样的日期检查。 |
| `SELECTORS`                             | 类属性 | 关键元素的选择器表（`{"export_btn": "button.export"}` 之类）。声明后导出前会被自动校验；目前只有微信支付声明了它。 |
| `check_selectors(browser)`              | 方法  | 自检 `SELECTORS` 是否都在页面上，返回 `(ok, missing)`。**由 `_run_single_export` 在导出前自动调用一次**，缺失只写日志不拦截导出；未声明的平台直接跳过。 |
| `assert_selector(browser, key)`         | 方法  | 步骤级断言：关键元素不在就抛（附失败截图），用于脚本内部确认走到正确页面。 |

### 5.4 `SmartExporter`（core/exporters.py）— 智能导出器

当平台不自定义 `export()` 时由基类调用，自动执行通用导出流程：

| 方法                                  | 说明                                                                                                                        |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `_find_date_inputs()`               | 遍历页面 input，按 placeholder 关键词（开始/起始/start… 与 结束/截止/end…）识别起止日期框。                                                           |
| `_find_button(texts, exact)`        | 按文本匹配按钮，返回第一个命中的 locator。                                                                                                 |
| `_fill_dates(start, end)`           | 点击并填入日期，返回成功填写数量。                                                                                                         |
| `_click_query()`                    | 依次尝试「查询/搜索/确定/筛选/查找」。                                                                                                     |
| `_click_export()`                   | 两轮匹配：**先精确后子串**，且长标签在前（「导出报表/下载报表/导出账单/下载账单/生成报表」再「导出/下载」）。旧实现整表子串匹配且「导出」在最前，会先点中"导出记录"这类导航文字。 |
| `_click_confirm()`                  | 点击弹窗确认按钮（确定/确认/下载/导出/保存）。                                                                                                 |
| `_setup_dialog_handler()` / `_handle_dialog(dialog)` | 只自动接受文案含「导出/下载/生成报表/账单」的 JS 弹窗，其余 `dismiss` 并写日志；处理弹窗自身出错也只记一行（以前是无条件 `accept`，作废/删除类确认会被自动点掉）。 |
| `export(start, end, timeout)`       | 主流程：填日期 → 点查询 → `begin_wait_download` → 点导出 → `wait_download`；未捕获到下载则尝试确认弹窗再等一次；仍失败则截图 `manual_<时间戳>.png` 并返回 `"manual"`。**起止只填进一个日期框时直接转 `manual`**（另一端仍是页面默认区间，硬导会得到错区间的账单）。 |
| `quick_export(start, end, timeout)` | 快速导出：不填日期，直接点导出（用于页面已有默认日期的场景）。                                                                                           |

### 5.5 KeepAliveService（core/keepalive.py）— 登录保活服务

| 方法/属性 | 说明 |
|-----------|------|
| `start()` | 启动后台守护线程，按间隔周期巡检（`stop` 事件驱动退出）。 |
| `stop()` | 置停止标志并等待线程退出（窗口关闭时由主程序调用）。 |
| `run_once()` | 巡检一轮：遍历 `app.iter_selected_merchants()`，逐个用独立 `BrowserManager` 打开 `login_url` 刷新会话，URL/标题含 login 或「登录」即标红（`app.set_platform_status(key,"error")`），否则标绿。 |
| `enabled` / `interval_min` | 可运行时调整；间隔改动即时生效。 |

> 并发约定：巡检轮询时若 `app.running == True`（正在导出/调试）立即跳过本轮，绝不与任务并发；`make_browser` 工厂注入便于单元测试。

### 5.6 PlatformAdmin（core/platform_admin.py）— 平台管理与调试

- `generate_platform_skeleton(key, name, login_url, export_url, guide)`：按模板生成 `platforms/<key>/`（`__init__.py` + `export.py`），重名/非法 key 抛 `ValueError`。
- `validate_platform_key(key)`：小写字母/数字/下划线校验。
- `DebugProbe`：包装 `BrowserManager`，拦截 `navigate`/`click_text`/`fill_*`/`wait_download` 等 13 个方法，每步记录 `{i, action, args, ok, error}`；以 browser 参数传入平台 `export()` 即可逐步调试，平台脚本零改动。
- `PlatformManagerDialog`：平台列表 + 新增（`PlatformWizard` 表单）+ 编辑（`PlatformEditor` 内置编辑器，保存即 py_compile 语法检查）。
- `DebugDialog`：平台/日期选择 + 试运行，经 `_run_async` 线程执行，步骤流经统一日志视图输出。

### 5.7 CronScheduler（core/scheduler.py）— 定时任务

- `CronExpr(expr)`：支持 `*`/`*/n`/`a-b`/`a,b`/`?`；`match(dt)` 按分时日月周匹配（dom 与 dow 同时受限为 OR）；`next_run(after)` 返回下一个匹配分钟（一年内）。
- `CronJob`/`TaskStore`：任务模型与 `scheduled_tasks.json`（version 1）读写；损坏条目跳过。**新建任务走 `CronJob.new(...)`**，它把 `last_run` 记为创建时间。
- `CronScheduler(app, store, poll_interval=30)`：后台线程轮询；`should_trigger` 基于「上次运行后的 next_run <= now」（`last_run` 为空视为已到期，故新建任务必须用 `CronJob.new` 记起算点，否则保存后 30s 内就会执行一次）；触发经 `app.trigger_job(job)` 后更新 `last_run` 并保存；错过不补跑。
- `app.trigger_job(job)`：用 `pair_job_targets` 把任务的平台/商户配成实际可跑的组合（商户只与其所属平台配对），无匹配时在日志里提示而不是静默返回；返回 `_run_async` 是否真的开始（正忙时 False，且不弹"已有任务在执行"的窗打扰人）。
- `CronScheduler.trigger(job, now)`：**只有真的开始执行才写 `last_run`**；到点时若被手动导出占着，本次不记，下个轮询周期自动重试（"程序关闭期间错过不补跑"的语义保持不变）。
- 界面：`SchedulerDialog` 列表的「对象」列显示 `pair_job_targets` 算出的**实际项数**（配不出来标「0 项(商户与平台不匹配)」）；`JobEditDialog` 保存时对空商户/不匹配商户先问一句再存。
- `SchedulerDialog`/`JobEditDialog`：任务新增/编辑/删除/启停；cron 带常用模板与校验。编辑走 `CronJob.apply_edit(...)`：**改了 cron 就把起算点挪到编辑时刻**，否则老 `last_run` 配新 cron 早已"到期"，保存后 30 秒内会立刻跑一次。
- 失败重试：`run_with_retry(fn, retry_times, retry_interval_s, log)` 与 `_execute_export_tasks`（自 `_do_export` 提炼）；settings `retry_times`(默认 2)/`retry_interval_s`(默认 30，递增 ×2)；`retry_times=0` 关闭。`_run_single_export` 对任何异常都在内部消化成返回值 `"failed"`（含浏览器启动阶段），否则抛出会让重试整批失效；导出前登录预检失败返回 `"manual"` 且同样落 `stats.jsonl`（`error` 写明「登录已失效」）。

### 5.8 模块级关键函数

| 函数                                    | 位置                 | 说明                                                                                                           |
| ------------------------------------- | ------------------ | ------------------------------------------------------------------------------------------------------------ |
| `discover_platforms()`                | `core/loader.py`   | 扫描 `platforms/` 下含 `export.py` 的文件夹（跳过 `_` 开头的目录），动态导入，找出继承 `PlatformBase` 且定义 `key` 的子类并实例化，返回 `{key: 实例}`。 |
| `discover_merchants(platform_keys)`   | `core/main_gui.py` | 扫描 `browser_data/<平台key>/` 下的子目录，发现已建档商户，返回 `{key: [商户名]}`。                                                  |
| `load_settings()` / `save_settings()` | `core/config.py`   | 读写 `settings.json`，缺失字段回退 `DEFAULT_SETTINGS`（`show_browser`、`download_name_mode`）。                           |
| `log(message, level, callback)`       | `core/logger.py`   | 统一日志：输出控制台（容错 `pythonw` 无 stdout 场景）→ 写入 `logs/run_YYYYMMDD.log` → 转发 GUI 回调。                                |
| `check_dependencies()` / `main()`     | `core/main_gui.py` | 依赖检测与自动安装；程序入口。 |
| `report_crash(summary, detail)`       | `core/main_gui.py` | `main()` 的异常兜底：先用模块顶部已导入的 `log` 记 ERROR，再弹窗。**返回值=日志是否真写成功**，弹窗文案跟着变（写不成功就不谎称"已记录到 logs 文件夹"）。与 crashguard 的区别：这里兜的是 `main()` 内部已起来之后的异常。 |
| `find_duplicate_merchant(name, existing)` | `core/main_gui.py` | 返回 `existing` 中与该名字视为同一家商户的那个名字（忽略首尾空格、不分大小写），没有则空串。 |
| `merchant_profile_dir(base, key, merchant)` / `delete_merchant_profile(...)` | `core/main_gui.py` | 前者按 `BrowserManager.set_browser_profile` 同一套算法算出 profile 路径（有测试钉住两者一致）；后者只删这一层目录，路径深度不对/名字为空一律拒绝，被占用时重试 4 次后如实返回失败。 |
| `merchant_changes(before, after)`    | `core/main_gui.py` | 两次扫目录结果的差集，返回 `(新增, 消失)`，顺序变化不算改动。 |
| `check_date_range(start, end, today=None)` | `core/main_gui.py` | 返回 `DateCheck(verdict, title, message, start, end)`，`verdict` ∈ `ok`/`ask`/`bad`；`start/end` 是补零规范化后的日期。纯函数，`today` 可注入以便测试。 |
| `run_cleanup(log_dir, downloads_dir, keep_days, dry_run=False)` | `core/cleanup.py` | 删除过期 `run_*.log`/`crash_*.txt` 与过期汇总副本目录，返回 `{"logs": [...], "summary_dirs": [...]}`；`keep_days<=0` 完全不动，`dry_run` 只列不删。 |

***

## 6. 依赖关系

### 6.1 模块依赖图

```
                     ┌──────────────┐
                     │ start.bat /  │
                     │ 启动工具.vbs   │
                     └──────┬───────┘
                            │ 启动
                            ▼
                     ┌──────────────┐
         ┌──────────►│core/main_gui │◄───────────┐
         │           │    .py       │            │
         │           └──┬──┬──┬────┘            │
         │              │  │  │                 │
         ▼              ▼  │  ▼                 │
 ┌─────────────┐  ┌────────┐  ┌──────────────┐  │
 │core/config  │  │core/   │  │core/loader   │  │
 │    .py      │  │logger  │  │ .py          │  │
 │(常量/设置)   │  │.py     │  └──────┬───────┘  │
 └─────────────┘  └────────┘         │          │
                                     ▼          │
         ┌───────────────────┐  ┌───────────────────────┐
         │core/browser.py    │  │core/platform_base.py  │
         │BrowserManager     │  │PlatformBase           │
         │(依赖 config,      │  └──────┬────────────────┘
         │ logger)           │         │ 继承
         └─────────┬─────────┘         ▼
                   │ 调用    ┌──────────────────────┐
                   │         │ platforms/*/export.py│
                   │         │ (平台插件)               │
                   │         └──────────────────────┘
                   │ 默认导出实现(懒加载)
                   ▼
         ┌─────────────────┐
         │core/exporters.py│
         │ SmartExporter   │
         │ (依赖 browser)   │
         └─────────────────┘
```

### 6.2 依赖明细

| 依赖方                     | 被依赖方                           | 说明                                                                      |
| ----------------------- | ------------------------------ | ----------------------------------------------------------------------- |
| `core/main_gui.py`      | `core.config`                  | 导入 `DOWNLOAD_DIR/BROWSER_DATA_DIR/ROOT_DIR/load_settings/save_settings` |
| `core/main_gui.py`      | `core.loader`                  | `discover_platforms()`                                                  |
| `core/main_gui.py`      | `core.logger`                  | `log()`，日志回调兼作 GUI 日志源                                                  |
| `core/main_gui.py`      | `core.browser.BrowserManager`  | 延迟导入（方法内），管理浏览器实例                                                       |
| `core/browser.py`       | `core.config`                  | `BROWSER_DATA_DIR/DOWNLOAD_DIR`                                         |
| `core/browser.py`       | `core.logger`                  | `log()`                                                                 |
| `core/loader.py`        | `core.platform_base`           | `PlatformBase` 判定                                                       |
| `core/platform_base.py` | `core.exporters.SmartExporter` | 方法内懒加载（默认导出实现）                                                          |
| `core/exporters.py`     | `browser`（鸭子类型）                | 使用 `begin_wait_download/wait_download/screenshot` 等                     |
| `platforms/youzan/export.py` | 标准库 | `datetime/urllib.parse`（`_build_youzan_url` 构造带日期参数的流水页 URL） |
| `platforms/*/export.py` | `core.platform_base`           | `from core.platform_base import PlatformBase`                           |
| `core/logger.py`        | 标准库                            | `logging`                                                               |
| 全部                      | Playwright                     | 外部依赖 `playwright==1.62.0`（`requirements.txt`）                           |

### 6.3 外部依赖

- **Python 3.10+**（启动脚本探测 Python3.10\~3.14 常见路径）

- **playwright==1.62.0** + Chromium 浏览器（首次运行由 `启动工具.vbs` 或 `check_dependencies()` 自动安装）

***

## 7. 核心流程解析

### 7.1 程序启动流程

```
启动脚本(start.bat / 启动工具.vbs)
  → 探测 Python 解释器(vbs 按五档找: 写死路径 → py 启动器 → 用户级目录 → .venv → C:\Python3xx)
  → (vbs 专用)检查 playwright，缺失则自动安装
  → 运行 python -m core.main_gui
      ├─ crashguard.install()         # 先装兜底，之后任何导入失败都会落 logs/crash_*.txt
      ├─ check_dependencies()         # 兜底依赖检测
      ├─ LiushuiApp.__init__
      │    ├─ discover_platforms()    # 加载已实现平台插件
      │    ├─ load_settings()         # 读取 settings.json
      │    ├─ discover_merchants()    # 扫描已建档商户
      │    ├─ 构建三栏界面
      │    └─ _start_cleanup()        # 后台线程清一次很旧的日志/汇总副本(失败只写日志)
      └─ root.mainloop()              # 进入事件循环
          └─ 逃逸出 main() 的异常 → report_crash(): 记 ERROR 日志 + 弹窗(文案如实说明日志是否写成)
```

### 7.2 首次登录流程（`_do_login`）

```
主线程 _collect_tasks 展开 选中平台 × 勾选商户 → [(key, plat, merchant), ...]
后台线程遍历该列表（不再回读界面控件）:
  _ensure_browser(plat, merchant, force_visible=True)   # 独立 profile，强制可见
  plat.login(browser)                                    # 打开登录页
  主线程弹非模态提示窗「登录好之后直接关掉浏览器窗口即可」（不挡事，不需要点任何按钮）
  后台线程轮询（2s 一轮，最长 30 分钟，可被「中止」打断）:
      browser.window_closed()?  → 是 → 本商户结束
      browser.login_signature() 变了? → save_login_state(quiet=True) 当场导出登录态
  销毁提示窗；`_close_browser()` 释放这个 profile（close 内部顺手导出登录态）
  _verify_login_after_close: force_headless=True 重开浏览器 → plat.check_login 真核实
      已登录 → 绿灯 → 进下一个商户
      未登录 → 红灯 + 「重试 / 取消」二选一(_ask_login_retry)
                 重试 → 重新走一轮(最多 LOGIN_RETRY_LIMIT=3 次, 到上限不再问)
                 取消 → 记"未登录", 进下一个商户
      核实异常 → 橙灯 + "没能核实"（不冒充未登录、也不弹窗）
  进入下一个商户；收尾汇总 "已核实登录成功 / 未登录 / 没能核实 / 未完成"
```

> 一整个"开浏览器→等关窗→核实"打包在 `_login_one_round` 里，所以"重试"就是再跑一轮，
> 进度条只在商户边界推进。中止或 30 分钟超时的那一轮不核实、也不问。

> 核实必须排在 `_close_browser()` 之后：Chromium 的 profile 目录同时只能被一个进程占用，
> 登录用的窗口不先释放，后台这次重开就起不来。

> 旧流程是"弹窗→用户点确定→再 `check_login` 轮询确认→存登录态"。去掉确定按钮是因为
> 业务用户会在短信/扫码还没完成时就点它，程序据此存下一份未登录的 profile，之后每次
> 导出都失败。等待期间也不能用 `check_login` 探测——它会把页面导航到导出地址，把正在
> 扫码的用户拽走，所以改用不触发导航的 cookie 指纹变化；真正的核实留到窗口关掉之后做。

### 7.3 导出流水流程（`_do_export`）

```
主线程取好 日期/单步开关 + _collect_tasks 后 _run_async(_do_export)
每个商户一次尝试 = _run_single_export，失败按 run_with_retry 递增间隔重试:
  _ensure_browser(plat, merchant)                        # headless 取决于"显示浏览器"设置
  browser.set_export_context(平台名, 起, 止, 商户)        # 下载归档上下文
  注入 wait_user/单步回调（弹窗经 _ui 回主线程执行）
  登录预检 plat.check_login：失效则记 "manual"（error 写明原因），不执行导出
  自检 plat.check_selectors（声明了 SELECTORS 才跑）：缺元素只写日志，不拦截本次导出
  result = plat.export(browser, start_date, end_date)    # 平台插件逻辑
  └─ 平台自定义实现 / 采用 run_standard_flow 骨架 / PlatformBase 默认走 SmartExporter
       ├─ 填日期 → 点查询 → begin_wait_download → 点导出
       └─ wait_download: 事件队列优先 + 目录轮询兜底
  任何阶段抛错（含浏览器启动）都在内部转成 "failed"，每条结果落 stats.jsonl
  result: "success"→绿 | "manual"→橙(弹窗指引手动) | 其他→红
结算统计 → _copy_export_outputs 汇总文件 → 自动打开汇总文件夹
```

### 7.4 下载捕获与文件归档时序

```
点击"下载"前:  begin_wait_download()
               ├─ 清空事件队列, 打开捕获开关
               ├─ _carry_over_orphans()  # 归位上次残留 UUID 文件
               └─ 重建 <平台/商户/日期>/临时 目录
点击"下载"后:  context "download" 事件 → _on_download → 入队
wait_download(): 出队 → _save_download_to_temp(写入临时目录)
                 → _finalize_download(归档到 平台/商户/日期/)
                     ├─ 顶层同名旧文件 → 复制到 历史/原名_时间戳.ext
                     └─ os.replace 移动(10 次重试 + 拷贝兜底)
兜底: 轮询 downloads 根目录新出现的稳定文件(两份采样大小一致)
end_wait_download(): 清队列、归位残留、清理临时目录
```

### 7.5 登录态检查流程（`_do_check`）

```
遍历任务: _ensure_browser → navigate(login_url) → sleep(3)
  get_page_info() → URL 含 "login" 或标题含 "登录" → 未登录(红)
                     否则 → 已登录(绿)，显示页面标题前 20 字符
```

### 7.6 平台新增与调试流程

① **新增**：`PlatformWizard` 表单填写平台元信息 → `generate_platform_skeleton()` 生成 `platforms/<key>/`（`__init__.py` + `export.py`）→ `discover_platforms()` 刷新左侧列表立即出现。

② **编辑**：`PlatformEditor` 内置编辑器修改 `export.py` → 保存即 `py_compile` 语法检查（错误不离屏）→ 重新 `discover_platforms()` 载入最新脚本。

③ **调试**：`DebugProbe` 包装 browser → `plat.export(probe, ...)` 试运行 → 每步记录 `{i, action, args, ok, error}` → 日志视图逐条展示 `#N 动作`，失败步骤标红并显示异常。

***

## 8. 平台插件体系

### 8.1 开发模型

每个平台 = `platforms/<key>/` 文件夹下的 `export.py`，定义一个继承 `PlatformBase` 的类并实现 `export()`。加载器按 `key` 注册，GUI 自动展示。详细编写规范见《脚本编写指南.md》，要点：

- `export()` 必须返回 `"success" / "manual" / "failed"` 三选一；

- 优先使用 `BrowserManager` 辅助方法（表格见 5.2）；

- 通用导出场景可直接不写 `export()`，走默认 `SmartExporter`；

- 复杂场景（iframe、特殊日期组件如 zent）可参考《脚本编写指南.md》中关于 iframe 切换的写法，以及有赞（URL 参数日期 + zent 日历）的实现。

### 8.2 已实现平台明细

**当前仓库仅实现 1 个平台：**

| key | 名称 | 登录/导出地址域 | 亮点实现 |
|-----|------|----------------|----------|
| `youzan` | 有赞 | youzan.com | **最完整示例**：URL 带日期参数直开流水页、`close_popup` 弹窗处理、zent 日期选择器、下载前 `begin_wait_download`、确认弹窗兜底 |

> 其余平台（微信支付、拼多多、抖音/抖店、小红书、京东、快手、视频号、银联、天猫/淘宝）为**待实现的开发模板**，用户已从 `platforms/` 移除；后续按 [8.1 开发模型](#81-开发模型) 补充各自的 `export.py` 即可自动生效。

所有平台脚本统一遵循：`navigate → sleep → fill 日期 → 查询 → begin_wait_download → 点导出 → 确认弹窗 → wait_download → 返回状态` 模板。

***

## 9. 项目运行方式

### 9.1 环境要求

| 依赖       | 要求                                     |
| -------- | -------------------------------------- |
| 操作系统     | Windows                                |
| Python   | 3.10+（`启动工具.vbs` 五档探测：写死路径 → `py`/`pyw` 启动器 → 用户级安装目录 → 项目 `.venv` → `C:\Python3xx`；见 [4 章](#4-主要模块职责)） |
| Python 包 | `playwright==1.62.0` + Chromium 内核     |

### 9.2 启动方式（三选一）

| 方式       | 命令/操作                              | 特点                                                |
| -------- | ---------------------------------- | ------------------------------------------------- |
| 推荐（免控制台） | 双击 `启动工具.vbs`                      | 用 `pythonw` 运行无黑窗；首次自动检测并安装 playwright + Chromium |
| 命令行      | 双击 `start.bat`                     | 控制台常驻，错误信息直接可见，适合排查问题                             |
| 手动       | 在项目根目录执行 `python -m core.main_gui` | 需自行确保依赖已安装                                        |

手动安装依赖：

```bat
pip install playwright==1.62.0
python -m playwright install chromium
```

> 注：`core/browser.py` 检测到 `C:\pw_browsers` 目录存在时会设置 `PLAYWRIGHT_BROWSERS_PATH`，即支持自定义浏览器内核安装目录。

> 起不来时：双击 `启动工具.vbs` 后"图标闪一下什么也没有"，先翻 `logs/crash_*.txt`（未捕获异常的堆栈，主线程崩溃会另外弹窗告知路径）；想确认启动器挑中了哪个解释器，执行 `cscript //nologo 启动工具.vbs --print-python`（只打印，不装依赖、不起界面）；`start.bat` 让控制台常驻，错误直接可见。

### 9.3 典型使用路径

1. 双击 `启动工具.vbs` 启动；
2. 左侧勾选平台，点平台右侧 `+` 添加商户（生成登录态目录）；
3. 勾选商户 → 点「首次登录」→ 浏览器中完成各平台登录；
4. 选择日期范围（或点「昨天/近7天/近30天」）→ 点「开始导出」→ 确认任务；
5. 完成后自动打开 `downloads/起_止_时间戳/` 汇总文件夹。

### 9.4 二次开发入口

- **新增/修改平台**：`platforms/<key>/export.py`，保存重启即生效；

- **修改平台 URL**：编辑对应平台类的 `export_url/login_url`；

- **扩展浏览器能力**：在 `BrowserManager` 增加辅助方法供全平台复用；

- **调整默认导出策略**：修改 `SmartExporter` 或 `PlatformBase.export` 默认实现。

***

## 10. 数据目录与文件约定

| 目录/文件                            | 自动创建           | 约定                                                                                     |
| -------------------------------- | -------------- | -------------------------------------------------------------------------------------- |
| `downloads/`                     | 是              | 导出文件根目录；运行时还有根目录临时落盘（UUID 文件名，随后被归位）                                                   |
| `downloads/<平台名>/<商户>/<起_止>/`    | 是              | 导出文件正式归档位置（无商户时省略商户层）                                                                  |
| `downloads/<平台名>/<商户>/<起_止>/历史/` | 是              | 同区间同名旧文件归档（`原名_时间戳.ext`）                                                               |
| `downloads/<平台名>/<商户>/<起_止>/临时/` | 是              | 单次任务下载临时暂存，结束后清空                                                                       |
| `downloads/<起_止>_<时间戳>/`         | 是              | 导出完成后全平台汇总目录（`_copy_export_outputs` 生成）                                                |
| `browser_data/<平台key>/<商户>/`     | 是（点 `+` 添加商户时） | Chromium 持久化 profile，保存登录态                                                             |
| `scheduled_tasks.json`            | 是              | 定时任务持久化（新增/编辑后自动保存）                                                                        |
| `logs/run_YYYYMMDD.log`          | 是              | 当日统一日志（系统+操作，DEBUG 级）                                                                        |
| `logs/crash_YYYYMMDD_HHMMSS_微秒.txt` | 崩溃时才生成 | 未捕获异常的完整堆栈（含子线程里的）。**双击没反应/闪一下就退时先看这里**，弹窗上写的是它的路径；写不进 `logs` 时退回项目根同名文件。                        |
| `settings.json`                  | 是              | 用户设置：`show_browser`（显示/隐藏浏览器）、`download_name_mode`（`unified` 统一命名 / `original` 保留原文件名）、`enable_keepalive`（登录保活开关）、`keepalive_interval_min`（保活间隔分钟）、`retry_times`/`retry_interval_s`（失败重试次数与首次间隔）、`cleanup_keep_days`（日志/汇总副本保留天数，0=不清理，默认 365） |

***

## 11. 附录：文件清单

| 文件                       | 规模（约）         | 作用                              |
| ------------------------ | ------------- | ------------------------------- |
| `core/main_gui.py`       | 约 1850 行    | GUI 主程序与三大业务流程、界面更新队列、中止控制、商户增删改与刷新、日期区间校验 |
| `core/browser.py`        | 约 1240 行      | 浏览器管理与下载归档（项目体量最大的核心模块）         |
| `core/exporters.py`      | 约 190 行       | 智能导出器（默认兜底导出）                   |
| `core/platform_base.py`  | 约 190 行       | 平台基类（元信息 + 登录态自检 + 通用导出骨架钩子）  |
| `core/loader.py`         | 约 90 行        | 平台加载器与免重启 reload                 |
| `core/config.py`         | 约 60 行        | 配置与设置读写                         |
| `core/logger.py`         | 约 130 行       | 日志通道 + 稳定性统计（stats.jsonl 读写与汇总） |
| `core/keepalive.py` | 约 100 行 | 登录保活服务（后台线程周期巡检） |
| `core/crashguard.py` | 约 96 行 | 启动期崩溃兜底（堆栈落 `logs/crash_*.txt`，主线程才弹窗） |
| `core/cleanup.py` | 约 120 行 | 过期运行日志与汇总副本的按保留期清理（白名单认领文件名，原件/统计/待确认不碰） |
| `core/platform_admin.py` | 约 336 行 | 平台管理/脚本调试（骨架纯函数生成 + 先验语法再原子写 + DebugProbe 通用透传 + 三个弹窗） |
| `core/scheduler.py` | 约 380 行 | 定时任务（cron 解析/持久化/调度/管理界面） |
| `tests/` | 33 个文件约 4600 行 | pytest 测试（322 项）：日志与统计尾部读、加载器、保活、平台管理、重试、调度、导出结果落库、界面线程模型、平台调用序列与骨架迁移、有赞日期、录制生成器、文件汇总与原子写、对话框构造、商户测试窗口、文字点击的精确性与歧义提醒、崩溃兜底与启动器找 Python 的五档顺序、商户增删改与查重、平台勾选联动、日期区间校验、过期文件清理、首次登录"关窗口即完成"的等待与核实、关浏览器前保存登录态（含"更空的一份不覆盖"守卫） |
| `requirements-dev.txt` | — | 开发依赖（pytest，已装入 `.venv`；`python -m pytest -q` 或全局 `py -m pytest -q` 均可，全套约 0.5 秒） |
| `tools/recording_to_script.py` | 约 270 行 | 录制 JSONL → 脚本骨架生成器：输出基类钩子形状（`set_date_range`/`trigger_export` 覆盖 + `run_standard_flow`），目标文件已存在时默认拒绝覆盖（`--force` 才写） |
| `platforms/*/export.py` | 11 个平台约 950 行 | 平台导出脚本（有赞、快手、小红书、抖音、天猫、京东、拼多多、视频号、微信支付、银联、支付宝）。**6 个已用 `run_standard_flow` 骨架**（快手、支付宝、天猫、抖音、小红书、银联）；京东/拼多多用 `wait_for` 驱动、视频号与微信支付日期控件特殊、有赞走 URL 带日期参数，这 5 个保留逐步写法（强套骨架会改变操作）。 |
| `start.bat` / `启动工具.vbs` | —             | 启动脚本（`python -m core.main_gui`） |
| `使用说明.md` / `脚本编写指南.md`  | —             | 用户文档 / 开发文档                     |

***

*文档生成基于当前仓库代码（无 git 提交历史，为首版代码）。如代码更新，请同步维护本文档。*
