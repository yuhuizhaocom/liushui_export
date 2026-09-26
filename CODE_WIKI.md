# 流水自动导出工具 v1.0 — Code Wiki

> 本文档是本项目**唯一一份代码逻辑文档**：按模块/类逐方法讲清"做什么、为什么这么写、边界与失败处理在哪"，并汇总第 12 章的硬约束与第 13 章的已知不一致。文中行号指向当前工作区代码，改完代码请同步维护（第 11 章的规模列同理）。面向最终用户的操作步骤见 `使用说明.md`，写平台脚本的规范见 `脚本编写指南.md`。

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

- [12. 全仓硬约束（改这些之前先读）](#12-全仓硬约束改这些之前先读)

- [13. 已知不一致、未接线与待议](#13-已知不一致未接线与待议)

- [14. 绿色版与工作空间](#14-绿色版与工作空间)

***

## 1. 项目概述

| 项目   | 说明                                                                                |
| ---- | --------------------------------------------------------------------------------- |
| 名称   | 流水自动导出工具（Liushui Export Tool）v1.0                                                 |
| 定位 | 面向**非技术业务用户**的可视化桌面工具，自动批量导出电商/支付平台的交易流水（**11 个平台均已内置脚本**：有赞、微信支付、支付宝、拼多多、抖音、快手、小红书、京东、天猫、视频号、银联；新平台仍按插件体系补 `platforms/<key>/export.py`） |
| 技术栈  | Python 3.10+、Tkinter（GUI）、Playwright / Chromium（浏览器自动化）                           |
| 架构风格 | **插件化**：每个平台一个独立文件夹，程序启动时自动发现加载，新增平台无需修改主程序                                       |

核心特性：

- **智能定位导出**：按 placeholder 文字/按钮文本自动识别页面上的日期输入框、查询按钮、导出按钮，不依赖平台特定选择器（`core/exporters.py` 的 `SmartExporter`）；

- **登录态隔离**：为每个「平台 + 商户」建立独立浏览器数据目录，登录状态按商户持久化保存（1\~7 天）；

- **下载兜底机制**：事件队列 + 目录轮询双通道捕获浏览器下载，防漏、防残留；无人认领的 UUID 残留文件移入 `downloads/待确认/`（**不再**按当前上下文改名归到某个商户目录，避免上一商户的文件冒充本商户账单）；候选与归档校验按**文件头**判类型，截图、假 `.xlsx` 一律不算账单；内容校验**先数数据行**——读得出数据行的表格直接通过，"操作失败/请登录"等错误文案只在读不出行时才用来区分空表与错误页（否则备注里出现这些字样的真账单会被判无效并删除）；

- **文件归档**：下载文件按 `平台/商户[/子商户]/日期范围` 组织，同名旧文件自动归档至 `历史/` 子目录；

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
│  ├─ main_gui.py     LiushuiApp: 三栏界面 · 批任务编排 · 线程/日志/状态灯  │
│  ├─ browser.py      BrowserManager: 生命周期/导航点击填表/下载捕获与归档/  │
│  │                  文件校验/登录态存取/录制/人工等待与单步调试            │
│  ├─ platform_base.py PlatformBase: 元信息 + 登录判据 + 通用导出骨架        │
│  ├─ exporters.py    SmartExporter: 无人写脚本时的兜底自动导出器            │
│  ├─ loader.py       discover_platforms / reload_platforms(含清 .pyc)     │
│  ├─ config.py       路径常量 + DEFAULT_SETTINGS + 原子读写 settings.json  │
│  ├─ logger.py       log 三通道 · stats.jsonl 统计与尾部读                │
│  ├─ outputs.py      导出成品查找与汇总副本复制(纯文件操作)                 │
│  ├─ cleanup.py      过期日志/汇总副本按保留期清理                          │
│  ├─ keepalive.py    登录保活后台巡检                                      │
│  ├─ scheduler.py    cron 解析 · 任务持久化 · 轮询触发 · 管理窗口           │
│  ├─ platform_admin.py 平台骨架生成/脚本保存/DebugProbe/管理+调试窗口       │
│  ├─ dialogs.py      历史日志窗口 · 稳定性看板                             │
│  ├─ crashguard.py   sys/threading.excepthook 崩溃落文件                  │
│  └─ theme.py        配色常量(让 dialogs 不必反向 import main_gui)         │
├─────────────────────────────────────────────────────────────────────────┤
│  平台插件层  platforms/<platform>/export.py  (每个平台一个文件夹)         │
│  11 个平台均有 export.py：6 个走 PlatformBase 骨架,5 个手写流程          │
│  (骨架: alipay/douyin/kuaishou/tmall/xiaohongshu/yinlian)              │
│  (手写: jingdong/pinduoduo/shipinhao/wechatpay/youzan)                 │
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
├── requirements-dev.txt     # 开发依赖（pytest）
├── settings.json            # 用户设置（8 个开关，见 10 章）
├── selection_state.json     # 平台/商户勾选状态（重启恢复）
├── sub_merchants.json       # 主账号下的子商户清单（键=平台key/商户名；见 core/submerchants.py，不进绿色包）
├── scheduled_tasks.json     # 定时任务（version 1 + jobs[]）
├── start.bat                # 命令行启动脚本（控制台常驻，错误可见）
├── 启动工具.vbs             # 免控制台启动脚本（pythonw，五档找 Python；装依赖交给程序的体检，见 14.2）
├── 使用说明.md              # 面向最终用户的使用文档
├── 脚本编写指南.md          # 面向开发者的平台插件编写指南
├── core/                    # ★ 核心框架包（15 个模块，见 2 章分层图）
│   ├── main_gui.py          # 2285 行 · 界面 + 批任务编排
│   ├── browser.py           # 1390 行 · BrowserManager
│   ├── scheduler.py         # 443 行 · cron 与定时任务
│   ├── platform_admin.py    # 336 行 · 平台管理与脚本调试
│   ├── exporters.py         # 229 行 · SmartExporter
│   ├── platform_base.py     # 214 行 · 平台基类与导出骨架
│   ├── dialogs.py           # 198 行 · 历史日志 / 稳定性看板
│   ├── logger.py            # 165 行 · 日志与统计
│   ├── cleanup.py           # 121 行 · 过期文件清理
│   ├── loader.py / config.py / crashguard.py / keepalive.py / outputs.py
│   │                        # 112 / 98 / 96 / 96 / 89 行
│   └── theme.py             # 17 行 · 配色常量
├── platforms/               # ★ 平台插件目录（11 个平台，每个一个文件夹）
│   ├── youzan/ wechatpay/ alipay/ pinduoduo/ jingdong/ douyin/
│   ├── kuaishou/ xiaohongshu/ tmall/ shipinhao/ yinlian/
│   └── <key>/__init__.py + export.py     # 详见 8.2
├── tools/
│   ├── recording_to_script.py # 272 行 · 录制 JSONL → 平台脚本骨架
│   └── build_portable.py    # 170 行 · 绿色包白名单组装 + 成品扫描/自检
├── packaging/               # 绿色包的启动器与随包说明（run-portable.vbs / .bat / README）
├── .github/workflows/build-portable.yml   # Actions 出包：自带 Python + playwright + Chromium
├── tests/                   # 38 个文件约 5900 行，410 项 pytest（离线，不碰真浏览器）
├── downloads/               # 运行时创建：账单归档 + 汇总副本 + 待确认/
├── browser_data/            # 运行时创建：<平台key>/<商户名>/ 每商户一个 profile
├── recordings/              # 「打开」手工测试窗口的点击录制 jsonl（已 gitignore）
├── logs/                    # 运行时创建：run_*.log + stats.jsonl + crash_*.txt
└── docs/superpowers/        # 历史设计/计划文档（plans/ 与 specs/）
```

***

## 4. 主要模块职责

| 模块/文件                    | 职责                                                                                                                                                                    |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `core/main_gui.py`       | **GUI 主程序**。构建三栏界面（左平台/商户选择、中设置+操作历史+按钮、右日志+进度），管理任务线程、浏览器实例生命周期，编排「首次登录 / 导出流水 / 检查状态」三大业务流程，导出后自动汇总文件并打开文件夹。                                                        |
| `core/browser.py`        | **浏览器管理**。封装 Playwright 持久化上下文（含登录态），提供导航/点击/填表/等待/下载捕获/文件归档/弹窗关闭/日期选择器辅助方法；内置浏览器启动重试与 Chrome 锁文件清理。                                                                  |
| `core/exporters.py`      | **智能导出器**。`SmartExporter` 通过 placeholder 匹配日期输入框、按文本匹配查询/导出按钮，全自动完成「填日期→查询→导出→等待下载」，失败时截图并返回提示，是平台未自定义 `export()` 时的默认实现。                                             |
| `core/loader.py`         | **平台加载器**。扫描 `platforms/` 自动发现并实例化所有平台，返回 `{key: 平台实例}` 字典：注册模块内**所有**带 key 的 `PlatformBase` 子类（用 `__module__` 排除从别处 import 进来的），key 重复/模块里没有平台类/导入失败都会写日志（导入失败以前只 print，pythonw 下界面完全看不到）。`reload_platforms()` 供平台管理改完脚本后免重启生效，需同时清 `sys.modules`、importlib 的 stat 缓存和磁盘上的 `.pyc`。                                                                                                           |
| `core/outputs.py` | **导出文件汇总**。`find_output_files`（认 `平台/日期`、`平台/商户/日期`、`平台/商户/子商户/日期` 三种结构，日期目录**最多下探两层**、找到就不再往下所以 `历史/` 不会被重复计数；跳过 `.crdownload/.tmp/.part`）、`copy_to_summary_dir`（复制到 `downloads/开始_结束_时间戳/`，无文件返回 None 因此不再打开文件夹；`summary_name` 处理撞名：两个子商户的原始文件名一模一样时加"上一级非日期目录名"前缀，绝不静默覆盖）、`sanitize_name`（路径非法字符清理，商户名与 profile 目录共用）。纯文件操作，可脱离界面测试。 |
| `core/dialogs.py` | **查看类对话框**。`show_log_history(root)` 历史日志窗口、`show_stats_dashboard(app)` 稳定性看板；只读根窗口/状态栏，与导出流程无关。 |
| `core/theme.py` | **界面配色常量**。单独成模块供 `dialogs.py` 复用，避免反向 import `main_gui` 成环。 |
| `core/platform_base.py`  | **平台抽象基类**。定义平台元信息（`key/name/login_url/export_url/guide/enabled`）与接口约定（`login()`、`export()`），默认 `login()` 打开登录页，默认 `export()` 走 `SmartExporter`；另提供通用导出骨架 `open_export_page/set_date_range/trigger_export/download_export_file` + `run_standard_flow`，平台只覆盖有差异的钩子。子商户那一档另有 `supports_sub_merchants` + `switch_sub_merchant`/`current_sub_merchant`/`verifies_sub_merchant_identity`，默认全关（见 5.3）。 |
| `core/submerchants.py` | **子商户清单与归属比对**（银联这类"主账号一次登录、切着导多份"）。清单存在工作空间 `sub_merchants.json`（键 = `平台key/商户名`，不放 settings.json：那个文件每次点勾都整体重写），坏文件回退成"按未配置处理"并把原因带出去。`clean_list` 拆分隔符 + 清洗 + 挡掉 `.`/`..`/纯点下划线名（这些会变成一级目录名）。`matches(expected, on_page)`：归一化后相等，或一方**独立出现**在另一方里（边界只认 ASCII 字母数字，所以 `8234` 不许蒙过 `8234000540`，而 `8234000540已激活` 算命中）；任何一侧为空都是"不匹配"。全角映射用逐对字典而不是 `zip` —— 长度对不齐会静默把数字翻译成别的数字。 |
| `core/config.py`         | **全局配置**。`ROOT_DIR`（项目根）、`DOWNLOAD_DIR`（下载目录）、`BROWSER_DATA_DIR`（浏览器数据目录）、`SETTINGS_FILE`、`DEFAULT_SETTINGS`；`load_settings()` 读设置（缺字段回默认），`save_settings(部分字典)` **以现有文件为底只覆盖传进来的键**——以前是以 `DEFAULT_SETTINGS` 为底，界面各处"点一下只写自己那一两个键"于是每次点勾都把别的设置抹回默认值；另有 `write_text_atomic`/`write_json_atomic`（临时文件 + `os.replace` 原子替换）——`settings.json`/`scheduled_tasks.json`/`selection_state.json` 三处整体重写都用它，避免写一半崩溃后被读取端"except 用默认值"静默清空。 |
| `core/cleanup.py` | **过期文件清理**。工具长期跑会累积"每次启动一个 run_日期.log"+"每次导出一个 起_止_时间戳/ 汇总副本目录"。`prune_logs`/`prune_summary_dirs` 按 mtime 删超过保留天数的这两类，**按文件名正则白名单认领**（不合规矩的名字原样留着），`stats.jsonl`（看板唯一历史）、`downloads/待确认/`（无人认领的账单）、`downloads/<平台>/…`（账单原件）任何情况下都不碰；`keep_days<=0` 表示关闭。清理失败只写日志，绝不影响启动。 |
| `core/logger.py` | **合并后的唯一日志通道**。`log(msg, level, callback)` 三通道输出：控制台（整句包 try，`pythonw` 下 `sys.stdout` 可能是 None）、`logs/run_<启动时刻>.log`（**每次启动一个文件**，不是按天）、GUI 回调；warning/error 在界面行前加 `[WARN]/[ERROR]`。`record_stat`/`load_stats`/`summarize_stats` 管稳定性统计，其中 `load_stats(limit)` 用尾部反向分块读（`_read_tail_lines`），不再把整个 `stats.jsonl` 读进内存。 |
| `core/keepalive.py` | **登录保活服务**。`KeepAliveService`（后台线程）按可配置间隔周期巡检已选商户的登录态（打开 `login_url` 判断会话），任务执行中自动跳过本轮；浏览器工厂可注入便于测试。 |
| `core/crashguard.py` | **启动期崩溃兜底**。工具用 `pythonw` + 隐藏窗口启动，没有控制台；以前导入阶段抛错（缺 tkinter、`logs` 建不出来）的表现就是"图标闪一下，什么也没有"。`install()` 把 `sys.excepthook`/`threading.excepthook` 指向 `report_uncaught`：堆栈写进 `logs/crash_日期_时间_微秒.txt`，主线程再弹窗告知文件位置（一个进程只弹一次）。子线程那条**只落文件不弹窗**——在非主线程里拉 Tk 窗口可能把界面吊住。`main_gui` 在其余 import 之前装好它，所以模块自身的导入失败也覆盖得到。 |
| `core/deps.py` | **依赖体检（三档）**。`probe()` 只回答"这台机器上缺什么"，**既不 import playwright 也不碰界面**（缺依赖时这两件事恰恰做不到，而它正是被 `core/browser.py` 顶层那句 import 挡住的），所以内核目录优先级用的是 `config.resolve_browsers_path`。三档结论：`no_playwright`（包都没有）／`bad_version`（版本与实测过的不一致）／`no_kernel`（有包没内核，**最常见**的半拉子状态）；齐活 `ok`，体检自己出错 `unknown`（=放行，原因写进 `notes`）。`find_chromium_dir()` 先认 Playwright 的 `INSTALLATION_COMPLETE` 标记、再逐个子目录找 `chrome.exe`——内核子目录名随版本变过（`chrome-win` → `chrome-win64`），写死就会在真机上集体误报"缺内核"；`chromium_headless_shell-*` 不算内核。`describe()`/`fix_commands()`/`show()` 只出结论和 argv 命令（argv 而非 shell 字符串：路径带空格不炸），弹窗由 `main_gui.check_dependencies()` 渲染。`REQUIRED_PLAYWRIGHT_VERSION` 是版本唯一真源。 |
| `platforms/*/export.py`  | **平台插件**。每个文件定义一个继承 `PlatformBase` 的导出类，实现该平台的登录与导出流程；平台专项逻辑（如有赞的 URL 日期参数）直接写在平台脚本内，不放入 `core/`（见 [第 8 章](#8-平台插件体系)）。 |
| `core/platform_admin.py` | **平台管理/脚本调试**。`render_platform_skeleton()` 纯函数生成骨架（输入按字面量转义，key 须字母/下划线开头）、`save_platform_script()` 先验语法再原子写（语法错误不覆盖磁盘上的好脚本）、`DebugProbe` 用 `__getattr__` 通用透传并如实抛异常、`PlatformManagerDialog`（向导+内置编辑器+列表）与 `DebugDialog`（试运行）三个 Tkinter 弹窗。 |
| `core/scheduler.py` | **定时任务**。`CronExpr`（5 字段 cron 轻量解析/匹配/next-run）、`CronJob`/`TaskStore`（`scheduled_tasks.json` 持久化）、`CronScheduler`（后台线程到期触发 `app.trigger_job`）、`SchedulerDialog`/`JobEditDialog`（任务管理界面）；导出失败自动重试（`run_with_retry`）亦由本批提供。 |
| `start.bat` / `启动工具.vbs` | **启动脚本**。以 `python -m core.main_gui` 方式启动：前者用控制台 Python（错误可见）；后者用 `pythonw` 免控制台，并在首次运行时自动 `pip install playwright` + 安装 Chromium。找 Python 分五档：① 历史写死路径（`E:\Python\Python314`、`C:\Python31x`，**排第一是有意的**——现在能用的机器不许换解释器）② `%WINDIR%` 下的 `py`/`pyw` 启动器（注意它不叫 `python.exe`，得按各自名字找）③ 用户级默认目录 `%LocalAppData%\Programs\Python\Python3xx` ④ 项目自带的 `.venv\Scripts` ⑤ `C:\Python3xx`。目录枚举带 `On Error Resume Next`，没权限读 `C:\` 也只跳过不中止。诊断：`cscript //nologo 启动工具.vbs --print-python` 只打印选中的解释器，不装依赖也不起界面。 |

***

## 5. 关键类与函数

### 5.1 `LiushuiApp`（core/main\_gui.py）— GUI 主应用类

#### 构造与初始化（`__init__`，199-243）

顺序：`discover_platforms()` → `load_settings()` → 组 `login_urls` → `discover_merchants()` → `_load_selection()` → `_setup_window()` → **右日志 → 左平台 → 中间设置+按钮** → `_refresh_summary()` → 起 `_pump_ui` 泵 → `KeepAliveService.start()` → `CronScheduler.start()` → `_start_cleanup()`。界面回调 `root.after` 必须在控件建好之后才启动（227）。

| 状态成员 | 含义与约束 |
| --- | --- |
| `browser` | 当前唯一的 `BrowserManager`；一个任务里换商户靠 `_ensure_browser` 先关旧再开新，任务结束由 `_thread_wrapper` 关掉 |
| `running` + `_task_lock` | 全应用一次只跑一个长任务；**检查+置位必须在同一把锁里**（注释 203-204：否则界面线程与调度线程可能同时进入，同一 profile 被两个 Chromium 占用） |
| `_ui_thread` + `_uiq` | 主线程 ident 与界面回调队列，`_ui` 据此判断"就地执行"还是"入队" |
| `platform_vars` / `merchant_vars` | 平台/商户勾选的 Tk 变量，**只能在主线程读** |
| `selection` | 勾选状态的纯数据镜像（`_save_selection` 顺手更新），供保活线程读 |
| `_merchant_memory` | 取消平台勾选那一刻各商户的样子，**只在内存里**，重启后第一次"取消→勾回"仍走整平台全选 |
| `login_urls` / `merchants` / `platforms` | 保活按 `login_urls` 名字取地址（keepalive.py:63），`merchants` 是定时任务配对的唯一事实来源 |
| `_abort` | 中止事件，只在商户边界生效 |
| `_login_hint` | 首次登录的非模态提示窗句柄（旧文档里的 `_login_confirm` 事件已随"确定按钮"一起取消） |

类常量：`LOGIN_WAIT_TIMEOUT_S = 30*60`、`LOGIN_POLL_S = 2.0`（等用户关登录窗口的上限与轮询步长）、`MERCHANT_PROBE_TIMEOUT_S = 30*60`（手工测试窗口最长占用）、`LOGIN_RETRY_LIMIT = 3`（每家登录总次数，含第一次）。测试与替身直接引用这些类属性，改成实例属性会让测试失效。

#### 界面构建与"设置项 ↔ settings.json 键"映射

| 方法 | 说明 |
| --- | --- |
| `_setup_window()` | 标题、1100x700、minsize 900x550、底色；`state("zoomed")` 默认最大化（注释：Windows 下这样仍保留标题栏与任务栏），失败静默。 |
| `_build_left_panel()` | 宽 340 且 `pack_propagate(False)`；「全选/取消全选/刷新商户」+ Canvas 滚动区放平台行与商户子行；`enabled=False` 的平台不建行。 |
| `_build_platform_row(key, plat)` | 一个平台一行：勾选框（初值取 `selection`）、商户数徽标、`●` 状态灯（**灯控件挂在平台对象上** `plat.status_label`）、紫色 `+` 添加商户、下方商户容器。收尾若该平台已有勾上商户则把平台勾回，保证界面与状态一致。 |
| `_build_middle_panel()` | 设置区 + 概览条 + 按钮栅格。**落盘项**：`show_browser`、`download_name_mode`(unified/original)、`enable_keepalive`+`keepalive_interval_min`、`retry_times`、`cleanup_keep_days`、`preflight_login_check`，全部 trace/`<FocusOut>` 即写；**不落盘**：日期起止（只刷概览）、「单步调试」（每次导出时从 Var 取）。⚠ Spinbox 没有 trace，靠 `<FocusOut>` 触发，所以**输入框未失焦时 settings.json 还是旧值**，而 `_ensure_browser`/`_execute_export_tasks` 读的正是文件。 |
| `_build_right_panel()` | 操作日志 `ScrolledText(state=DISABLED)` + 「清空屏/历史日志/复制日志」+ 状态栏 + `determinate` 进度条。 |
| `_rebuild_platform_list()` | **先 `_save_selection()` 再 `_load_selection()`**，重扫商户、销毁重建左栏、刷概览；`_refresh_merchants`、平台管理保存、新增商户都走它。 |
| `_refresh_summary()` | 「已选 N 个商户 · M 个平台 [起 ~ 止]」；整段吞异常（构建早期控件未就绪时不打扰）。 |

#### 用户操作入口

| 方法 | 说明 |
| --- | --- |
| `_action_login_all()` | 校验日期/选择后，在主线程用 `_collect_tasks` 备好任务列表，启动线程执行 `_do_login(tasks)`，并记录操作历史。 |
| `_action_export_all()` | 校验后弹出任务确认框（平台·商户明细 + 日期，最多列 12 项），确认后**在主线程取好日期、单步开关与"导出前检查登录"开关**再启动 `_do_export(tasks, start, end, step_debug, preflight)`。这是"工作线程不回读界面"契约最典型的落实点。 |
| `_action_check_status()` | 启动线程执行 `_do_check(tasks)`，逐个商户调用平台自身的 `check_login` 判断登录态（与导出前预检共用 `_probe_login` 这条探测路径）。日期校验用 `show_warning=False`：只规范化不打扰。 |
| `_action_open_folder()` | `makedirs(exist_ok=True)` + `os.startfile` 打开 `downloads/`（Windows 专有，无 try）。 |
| `_action_open_merchant(key, merchant)` → `_open_merchant_browser` | 商户行「打开」= 手工测试兼录制：`force_visible=True` 起浏览器 → **`enable_action_trace()`** 开始把点击/输入录成 `recordings/<平台>_<商户>_<时间>.jsonl` → 导航 `export_url` → 每 2 秒轮询页面标题。三条退出路径：点「中止」、超过 `MERCHANT_PROBE_TIMEOUT_S`(30 分钟)、页面被关掉。窗口开着期间 `running` 一直为真（导出被挡、定时任务被推迟），所以必须能中止且不能无限挂着。 |
| `_request_abort()` | 「中止本次任务」按钮：置 `_abort` 事件并记日志。只在**商户边界**生效（`_preflight_login_check`/`_execute_export_tasks`/`_do_login`/`_do_check`/`_open_merchant_browser` 每轮开头查 `_aborted()`），不会中途掐浏览器留下半截下载；`running` 为假时静默忽略；按钮由 `_run_async` 启用、`_thread_wrapper` 结束时置灰。 |
| `_action_help()` | 弹简化的使用说明。文案与现流程一致：登录以**关掉浏览器窗口**为完成标志（不是点『确定』），并提了导出前的登录预检与「中止」。`tests/test_action_help_text.py` 钉着这几句，改回旧说法即红。 |
| `_add_merchant(key, raw_name)` | 建 profile 目录 + 加一行勾选，返回 `(是否成功, 文案)`。先查重再动手：按 Windows 目录规矩比（忽略首尾空格、不分大小写），重名直接拒绝——以前重名会 `makedirs(exist_ok=True)` 照样建、界面照样塞一行，而 `merchant_vars[平台][名字]` 是字典，新 Var 顶掉旧的，于是两行同名共用一个勾选框（勾上面那行等于没勾）。也拒绝 `..`/`.`/`___` 这类"清洗后没内容"的名字（`sanitize_name` 只换 `<>:"/\`，`..` 原样通过，拼进路径就指到 `browser_data` 本身）。 |
| `_prompt_add_merchant` / `_prompt_delete_merchant` | 添加：模态小窗，失败时**留着窗口让用户改名字**、`messagebox(parent=dlg)`。删除：任务进行中先拒绝；确认框写明要删的绝对路径；只删 `browser_data/<key>/<商户>` 这一层（`delete_merchant_profile` 按 normpath 复核深度）；**先删目录成功才动界面**；已导出账单与 `downloads/待确认/` 不动。 |
| `_refresh_merchants()` | 左栏「刷新商户」：重新扫 `browser_data` 并走 `_rebuild_platform_list`（勾选按 `selection` 还原，不会被洗掉），日志写明新增了谁、外部删了谁，没变化也报总数。以前手工放进目录或从别的电脑拷来的 profile 要重启才看得见。 |
| `_open_platform_manager()` | 打开 `PlatformManagerDialog`，传 `_refresh` 回调：`reload_platforms()` + 重建左栏，使新增/改脚本无需重启即生效（重建异常吞掉，不能让平台列表炸掉管理窗口）。 |
| `_open_debug_dialog()` | 「脚本调试」：选平台+日期，缺日期时**在主线程读输入框**；工作线程里 `_ensure_browser(plat)`（**不传 merchant，用的是平台级 profile，没有商户登录态**）+ `DebugProbe` 跑 `plat.export(probe, ...)`，逐步打印动作与出错步。 |
| `_open_recording_dialog()` | 「录制→脚本」：左列 `list_recordings()`、右预览生成的骨架、可保存/复制。预览用硬编码占位（`某平台`/`XxxExporter`/`xxx`），**名字与 key 必须手改**；保存前目标已存在会二次确认（默认落点在 `platforms/` 下，很容易选中线上脚本）。 |
| `_open_stats_dialog()` / `_open_scheduler_dialog()` | 转 `dialogs.show_stats_dashboard` / 建 `SchedulerDialog`（`rebuild_cb` 传空函数，任务改动不需要刷新平台列表）。 |
| `trigger_job(job)` | CronScheduler 的回调：`pair_job_targets` 配出实际可跑组合（配不出来写一条含详情的日志而不是静默返回）、日期固定昨天~今天，**直接调 `_execute_export_tasks`**（因此定时导出既不做登录预检、也不做汇总/打开文件夹），`_run_async(notify_busy=False)`；返回是否真开始，忙时不写 `last_run` |

#### 核心业务流程方法

| 方法                                 | 说明                                                                                                                                                                                                                    |
| ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_do_login(tasks)`                   | **首次登录**：逐家商户跑 `_login_one_round`（可见浏览器 → 用户**自己关掉窗口**表示登完 → 释放 profile → 后台核实）。未登录时 `_ask_login_retry` 让用户选「回去继续登录 / 放弃这家」，最多 `LOGIN_RETRY_LIMIT` 轮。**界面上的「确定」按钮已经去掉**：以前用户常在短信/扫码还没完成时就点确定，程序据此存下一份没登录的 profile。收尾日志：`已核实登录成功 X / 未登录 Y / 没能核实 Z / 未完成 N`。**返回值是与 tasks 对齐的结论列表**（`ok`/`bad`/`unknown`/`skip`），导出前的登录预检靠它决定剔除谁。 |
| `_wait_login_window_closed(browser)` | 轮询 `browser.window_closed()`；期间只要 `login_signature()`（cookie 条数+内容指纹，不触发导航）变了就 `save_login_state(quiet=True)` 当场导出。原因：persistent context 在 Chromium 退出时不保留 session cookie，等窗口关完再存就来不及了。**刻意不调用 `plat.check_login`** —— 它会把页面导航到导出地址，用户正扫码会被拽走。返回 `("closed"/"aborted"/"timeout", 是否保存过)`。 |
| `_login_one_round(plat, key, merchant)` | 一轮完整登录：可见浏览器 → `plat.login` → 提示窗 → `_wait_login_window_closed` → `_close_browser()` 释放 profile → `_verify_login_after_close`；`finally` 再收一次浏览器。返回 `True/False/None/"skip"`（skip = 中止/超时/登录页没打开，这一轮不核实）。重试就是再跑一轮。 |
| `_verify_login_after_close(plat, key, merchant, saved_seen)` | 用 `force_headless=True` 重开浏览器跑 `plat.check_login`（此时导航不打扰任何人）。已登录→绿灯 `True`；未登录→红灯 `False` 并把原因存进 `_last_verify_reason`；核实异常→橙灯 + "没能核实" `None`，**不冒充未登录**。**只报结果，要不要再给一次机会由 `_do_login` 问用户。** |
| `_ask_login_retry(plat, merchant, attempt)` | 未登录时 `askretrycancel` 二选一：「重试」=重开这家的登录页继续登，「取消」=先放弃、稍后单独重登；文案带上 `_last_verify_reason`。为什么交给用户：微信支付那类同域平台靠页面文案判断，存在"其实登上了但被判未登录"的可能。弹窗起不来/一小时无人应答都按放弃返回 False，绝不把整批卡住。每家最多 `LOGIN_RETRY_LIMIT`（3）轮，到上限不再问。 |
| `_close_browser()`                   | 统一关闭点：`self.browser.close()` 后置 None，异常只写一句日志。因为 `BrowserManager.close()` 内部先导出登录态，导出收尾/保活/手工测试窗口/退出这些路径都会落盘。 |
| `_do_export(tasks, start, end, step_debug, preflight=True)` | **导出流水**：`preflight` 为真时先跑 `_preflight_login_check`，预检后一家不剩就直接结束（不报"导出完成"也不汇总）；否则转交 `_execute_export_tasks`，结束后调用 `_copy_export_outputs` 汇总并自动打开文件夹。任务列表/日期/单步开关/预检开关均由主线程取好传入，工作线程不回读界面。**定时任务走 `trigger_job` → `_execute_export_tasks`，不经过这里，因此不做预检。** |
| `_preflight_login_check(tasks)`        | **导出前登录预检**：逐家 `_probe_login`（`force_headless=True`，连着探不会一家闪一个窗口）→ 失效的一次性 `_ask_preflight_login` 问「集中重登 / 本次跳过」→ 选重登就把这些交给 `_do_login`，其中仍为 `bad` 的按**下标**从本批剔除（同平台多商户时按对象比会误伤）。`ok`/`unknown`/`skip` 一律保留，交给导出时那层 inline 预检再兜；一家都没失效就记「N 家商户登录状态检查通过」，**一个框都不弹**。返回本次要继续导出的任务列表。 |
| `_probe_login(plat, merchant, force_headless=False)` | 用这家自己的 profile 真跑一次 `plat.check_login`，返回 `(结论, 出错信息)`，结论 `True`/`False`/`None`(没能核实)。**任何异常都在内部转成 `None`**：一道附加检查把用户本该到手的账单挡掉是最坏结果。「检查登录状态」按钮与导出前预检共用。 |
| `_ask_preflight_login(expired, ok_count)` | 预检那一个确认框（`askretrycancel`：重试=集中重登这 N 家，取消=跳过它们只导其余），文案最多列 12 家并写明"等共 N 家"。与 `_ask_login_retry` 同一套"主线程弹、后台线程等"写法；弹窗起不来或一小时无人应答都按**跳过**收，不把整批吊死。 |
| `_do_check(tasks)`                   | **检查登录态**：逐个商户启动浏览器并调用 `plat.check_login(browser)`（多数平台靠 `export_url` 是否被重定向到登录路径判断），结果反映到平台状态灯。探测走 `_probe_login`，三档结果分别记「未登录 / 已登录 (页面标题) / 检查失败 - 原因」，读页面标题失败也只算这一家没查成。                                                                                                                    |
| `_execute_export_tasks(tasks, start, end, label="导出", step_debug=False)` | **批量循环**（手动导出与定时任务共用）。① `tasks.sort(key=lambda t: int(t[1].manual_intervention))` 原地稳定排序，需人工的平台压到队尾，并把 `intervention_hint` 去重后播报一条「已排到最后执行」；② 从 `load_settings()` 读 `retry_times`/`retry_interval_s`（读文件不读 Var）；③ 每家先查 `_aborted()`（剩余 N 项未执行 → break，**但仍走统计与待人工清单**）；④ `run_with_retry(_run_single_export)`，外层 try 把抛错折成 `"failed"`；⑤ 状态灯按**最差**：`worst[key]` 用 `_RESULT_RANK` 比大小（未知结果串比 `failed` 更差），注释 1866 记录动机——"先失败后成功时绿灯会把失败盖掉，界面上看着全绿、其实有一家店没出账单"；⑥ 计数 `成功/手动/失败` 并返回三元组（空 tasks 时打提示后返回 **None**）。⚠ `label` 形参在函数体内**从未使用**，但 `trigger_job` 与测试都按关键字 `label=` 传，删掉或改名会同时炸两处。 |
| `_run_single_export(plat, merchant, start, end, step_debug=False)` | **分发器**：平台声明了 `supports_sub_merchants` 且这家商户在界面「子商户」里录过清单 → 走 `_run_sub_merchants`；否则直接走 `_export_one_merchant`，也就是从前的那一条路，一个字都不变。清单读不出来时把原因写进日志再按"没配置"处理（读不到子商户最多是少导几份，不许把整批挡掉）。 |
| `_export_one_merchant(plat, merchant, start, end, step_debug=False, sub_merchant="")` | **一家商户（或一个子商户）的一次尝试**，返回 `success`/`manual`/`failed`。顺序：`_ensure_browser`（独立 profile）→ `set_export_context`（带子商户）→ 注入 `_ask_user_confirmed`（`wait_user` 用，见 5.2）与可选 `_step_cb` → **inline 登录预检**（`check_login` 抛异常时 `login_ok` 保持 True 继续导出；判未登录则 `manual` + `err_msg="登录已失效(导出前预检)"`）→ `_self_check_selectors` → `plat.export`。`finally` 无条件复位单步/user-wait 回调；**收尾无条件 `record_stat`**（注释 1954：以前预检失败直接 return，登录失效这个最主要的失败原因永远不进 stats.jsonl，看板成功率虚高），子商户那一档记的商户字段是 `主商户/子商户`，一家一次；`t0` 在浏览器启动之后重置，"耗时不含启动"。docstring 明写**本函数不允许向外抛异常**：抛出会被上层折成"一次失败"并跳过全部重试，而"浏览器起不来"恰是最该重试的那种。 |
| `_run_sub_merchants(plat, merchant, subs, start, end, step_debug)` | 同一份 profile、同一次登录里按清单逐个切子商户各导一份。**重试挂在每个子商户自己身上**（取值口径与 `_execute_export_tasks` 逐字一致，包括"0 就是 0"），所以整家汇总结论永远不是 `failed`——外层据此会把成功的几家重导一遍，归档里多堆 `历史/` 反而更难看清。全部 success 才 `success`，其余一律 `manual`。 |
| `_switch_sub_merchant(plat, merchant, sub)` | 切换 + **归属校验**，返回 False 表示这一家就此停手。三种情况分得很清：切换动作失败 → 停；页面读回来的商户和要导的对不上 → 停 + `snapshot` 留证 + 日志写清"页面是谁/要的是谁"；平台没实现读回或读回抛错 → **放行**但必留一句"归属未经校验"（读不到 ≠ 读对了，也不能因为没实现读回就彻底用不了）。 |
| `_self_check_selectors(plat)` | 导出前校验平台声明的 `SELECTORS`，**只报警不拦截**（注释 1996：缺元素不代表这次一定失败，自检自身出错更不能影响导出）；未声明的平台直接跳过。 |
| `_apply_export_result(key, plat, result)` | 只管平台状态灯（success→绿 / manual→橙 / 其余→红）；要人做的平台交给 `_prompt_manual_leftovers` 一次性汇总。 |
| `_prompt_manual_leftovers(items, date_str)` | 需要人工完成的平台**在整批结束后一次性列出一个窗**（含各自 `guide` 与"点右侧『打开』重进页面"的指引）；以前每个 manual 各弹一个，十个商户里五个要手动就会叠五个窗。全自动成功时完全不打扰。 |
| `_record_job_done(label, start, end)` | 收尾：记一条操作历史 → `_copy_export_outputs` → `os.startfile` 打开汇总目录。注释 2021 说明**不再依赖进度条取值**（Tk 字符串比较曾引发静默中断）；复制与打开各自吞异常。 |
| `_copy_export_outputs(start, end)` | 转 `outputs.copy_to_summary_dir`，把本次日期区间的成品去重复制到 `downloads/起_止_时间戳/`。**传的是平台显示名 `p.name`**（归档目录按显示名组织），与 profile/删除用的 `key` 是两套标识。 |
| `_do_check(tasks)` | **检查登录态**：逐个商户 `_probe_login`（不传 `force_headless`，跟随"显示浏览器"设置），三档结果分别记「未登录 / 已登录 (页面标题前 20 字) / 检查失败 - 原因」并反映到状态灯；读页面信息失败只算这一家没查成。 |

#### 任务线程管理与线程契约

| 方法 | 说明 |
| --- | --- |
| `_run_async(target, notify_busy=True)` | 在 `_task_lock` 内"检查+置位"`running`（忙时按 `notify_busy` 决定是否弹"已有任务正在执行"，弹窗也走 `_ui`）；`notify_busy=False` 专供定时任务：到点撞上手动导出不该弹窗打扰。之后全灯置 idle、`_abort.clear()`、启用中止按钮、起 daemon 线程跑 `_thread_wrapper`。**返回 bool**，`trigger_job` 与 `CronScheduler.trigger` 靠它决定要不要写 `last_run`。 |
| `_thread_wrapper(target)` | 线程入口：跑 target → 任何异常折成 `[错误] …` 日志 + 状态栏 → `finally` 锁内复位 `running`、进度归 0、置灰中止按钮、**关闭浏览器**（避免窗口残留/占用 profile）。 |
| `_ensure_browser(plat, merchant, force_visible=False, force_headless=False)` | 先关旧实例（异常吞掉）再建 `BrowserManager(headless=not show, log_callback=_append_log)` + `set_browser_profile(plat.key, merchant)` + `start()`。优先级 `force_headless` > `force_visible` > 设置。是否显示**读 settings.json 而不是 BooleanVar**（注释 966：本方法跑在任务线程，跨线程读 Tk 变量不安全）。 |
| `_ui(fn)` / `_pump_ui()` | 界面更新唯一通道：按 `threading.get_ident()` 判断——已在主线程**就地执行**（保证点击无延迟），否则入队；主线程每 60ms 抽最多 300 条并重新排程，单条回调抛错只丢这一条、**绝不停泵**，`after` 失败即认为窗口已销毁。 |
| `_set_progress(**kwargs)` / `_set_status(text)` | 投递主线程的包装（状态栏统一加 `状态: ` 前缀）。 |
| `_aborted()` | `getattr` 容错读 `_abort.is_set()`，因此测试用轻量替身类也能借用。 |
| `_collect_tasks(selected=None)` / `_get_selected()` / `_get_selected_merchants(key)` | 把"已勾平台 × 该平台已勾商户"展成 `[(key, plat, merchant)]`。**三个都只能在主线程调用**（读勾选框）；任务线程一律用主线程拍好的这份列表。 |
| 「工作线程要拿用户答复」的固定写法 | `answer` dict + `threading.Event` + `self._ui(_ask)` + `done.wait(timeout=…)`，且 `_ask` 的 `finally` 一定 set（防永久阻塞）、弹窗异常按保守默认返回。四处用到：`_ask_login_retry`(放弃)、`_ask_preflight_login`(跳过)、`_ask_user_confirmed`(扫码确认，上限 `USER_WAIT_TIMEOUT_S`=10 分钟、**必须回话**)、`_step_cb`(单步，默认继续)。**改成直接 `messagebox.xxx(...)` 会拿不到返回值**（`_ui` 是异步投递）。⚠ 这类等待里"没拿到答复"绝不能折算成"用户同意了"——`wait_user` 那条就是这么把"没扫码"变成"已确认"的（12 章第 45 条）。 |
| 只能主线程调用 | `_clear_log_screen`、`_paint_platform_status`、`_validate_dates`、各 `_action_*`/`_prompt_*`/`_build_*`/`_rebuild_*`、以及所有对话框构造函数。线程安全的只有 `_append_log`/`_ui`/`_set_status`/`_set_progress`/`set_platform_status`/`_aborted`/`_request_abort`。 |

#### 状态灯、日志缓冲与勾选持久化

| 方法 | 说明 |
| --- | --- |
| `_append_log(line)` | **任意线程可调**（注释 769：这条是量最大的跨线程入口，BrowserManager 每个动作、保活/调度线程都会调）。就地 append 到 `log_lines`，超过 500 条裁到最后 300；控件刷新打包成 `_paint` 走 `_ui`。**裁剪只作用于缓冲，Tk 文本控件本身不裁剪**（要 `_clear_log_screen` 才清）。 |
| `_copy_log()` / `_clear_log_screen()` / `_view_log_history()` | 复制的是缓冲（不是控件内容），`clipboard_*` 后调 `root.update()` 确保写入；清空只清显示、历史文件不动；后者转 `dialogs.show_log_history`。 |
| `set_platform_status(key, status)` / `_paint_platform_status` | 前者是线程安全入口（**保活服务按这个名字查找回调，改名要同步 `keepalive._mark`**），后者只能主线程调；灯控件挂在 `plat.status_label` 上，一个平台一盏灯。 |
| `_on_platform_toggled(key)` / `_sync_platform_on_merchant(key)` / `_add_merchant_checkbox` | 平台↔商户双向联动：取消平台前把每家商户的样子写进 `_merchant_memory` 再全清、勾回按记忆还原（从没记过=整平台全选）；勾任一商户自动勾回其平台；建商户行时初值取 `selection`；商户行右侧除「打开」「删」外，声明了 `supports_sub_merchants` 的平台还有「子商户」按钮（带已录数量，入口按平台显隐，见 14.3）。以前"取消=全不勾、勾回=全勾"，常年不勾的那几家只要手滑点一次平台框就悄悄回到任务里。 |
| `_load_selection()` / `_save_selection()` | `selection_state.json` 存 `{"platform":{key:bool},"merchant":{key:{name:bool}}}`；保存时**同时更新 `self.selection` 纯数据镜像**（注释 740：保活线程要读勾选，跨线程读 Tk 变量不安全），写入走 `write_json_atomic`；读侧非 dict/异常一律回空结构。 |
| `_refresh_merchant_badge(key)` / `_remove_merchant_row(key, name)` | 徽标数字以 `merchant_vars[key]` 为准（建好就不管的话，加/删商户后数字是骗人的）；删行是 pop Var + destroy Frame + 重扫 merchants + 刷徽标 + 存勾选，**目录删除由调用方负责**。 |
| `_on_ka_setting` / `_on_retry_setting` / `_on_cleanup_setting` | 设置即时落盘并同步内存副本。保活那两个改动**立刻作用到运行中的服务**（`keepalive.enabled/interval_min`，间隔只影响下一轮等待）；重试那个**只写 `retry_times`**（注释 869：`retry_interval_s` 界面没有输入框，以前每次点勾都被写回 30，手工调过的值一勾就没）；清理天数改完提示"下次启动时生效"（清理只在启动跑一次）。 |
| `_run_cleanup_once()` / `_start_cleanup()` | 启动后在 daemon 线程跑一次 `cleanup.run_cleanup`，结果一行 `[清理] …`；目录不存在/被占用/设置写怪值都只记日志（注释 248：绝不能把启动带崩）。 |
| `cleanup()` | 退出前 `_save_selection()` 再 `self.browser.close()`（**没包 try**）；由 `main()` 里的关闭回调按 `scheduler.stop → keepalive.stop → cleanup → destroy` 顺序调用，顺序不能换。 |

#### 模块级函数（同文件，可脱离界面单测）

| 函数 | 说明 |
| --- | --- |
| `discover_merchants(platform_keys, ignored=None)` | 扫 `browser_data/<key>/` 子目录，返回 `{key: [商户名]}`；整段吞异常 → 目录不存在就是空。**只有非空列表才入结果**，所以"没商户的平台"在 `self.merchants` 里没有键。⚠ 两类不算商户：程序保留名（`_平台调试`/`_未指定平台`）、Chromium 的内部目录（以前商户为空时 profile 落在平台目录本身，内部目录就被当成商户列进左栏并自动勾选，见 13.1）。内部目录**按内容认**（商户 profile 根里必有 `Default/` 或 `login_state.json`），名字名单只为兜住 `Safe Browsing` 这种空的内部目录。**整套判据的先决条件是「这个平台目录本身当过 profile 根」**（顶层有 `Local State`/`Last Version`）：商户目录允许用户手工拷进来搬登录态，干净的平台目录一律回到「每个子目录=一家商户」，免得拷了一半、或版本不同没有 `Default/` 的目录被静默吞掉；`ignored` 传 list 时被剔掉的名字会塞进去供调用方写日志——**剔除可以，静默剔除不行**。 |
| `find_duplicate_merchant(name, existing)` | 按 Windows 目录规矩（strip + casefold）判同一家，命中返回**已存在的那个旧名字**；注释理由："`Shop` 与 `shop` 建出来是同一个 profile 目录"。 |
| `merchant_changes(before, after)` | 两次扫目录的 `(新增, 消失)`，顺序变化不算改动。 |
| `merchant_profile_dir(base, key, merchant)` | 借用 `BrowserManager._safe_name` 同一套算法算 normpath，使建档/删除/运行期三处不会漂移（测试钉住一致性）。 |
| `delete_merchant_profile(base, key, merchant)` | 三重深度护栏（父目录必须是平台目录、target≠parent、basename 等于清洗后名字）后 `rmtree`，重试 4 次 ×0.5s（Chromium 刚退出时目录锁常有几秒残留）；本来不存在返回 `(True,"本来就不存在")`。注释 116：少核一层就会把整个平台目录甚至整个 `browser_data` 删掉。 |
| `run_with_retry(fn, retry_times, retry_interval_s, log)` | **只有返回值恰为 `"failed"`** 才重试，间隔 `interval × 2^(n-1)`；返回最后一次结果。`retry_times=0` 即一次定生死。⚠ 形参 `log` 遮蔽了模块级 `logger.log`。 |
| `check_date_range(start_text, end_text, today=None)` | 见 `_validate_dates`；纯函数、`today` 可注入。 |
| `check_dependencies()` | 启动前体检：`deps.probe()` → `ok`/`unknown` 直接放行（一行日志说明结论或吞掉了什么）。`no_playwright` → 问一句，装了（**钉死版本** + 内核）就 `os.execv` 原地重启，用户拒绝或装不上则返回 False——只有这一种情况会拦住启动。`no_kernel` → 只补内核下载；选"否"**照样进程序**（设置/日志/看板都能看，只写一句"导出会失败"）。`bad_version` → 只问不拦，选"继续用当前版本"就放行。装不上时弹窗带 pip 自己的输出尾部 + 两条能抄的命令 + 离线退路；Tk 弹窗起不来时**放行**（附加检查不许变成"程序打不开"）。 |
| `report_crash(summary, detail)` | `main()` 的异常兜底：先用顶部已导入的 `log` 记 ERROR 再弹窗，**返回值=日志是否真写成功**，文案跟着变（写不成就不谎称"已记录到 logs 文件夹"）。注释 2104 记录旧错：那里曾写 `from logger import log`，根目录没这个文件，必然 ModuleNotFoundError 又被自己吞掉。与 crashguard 分工：crashguard 兜导入期与子线程，这里兜 `main()` 起来之后。 |
| `main()` | `check_dependencies()` → 建 Tk → `LiushuiApp` → 绑关闭回调（顺序见 `cleanup()`）→ `mainloop`；整体 try/except → `report_crash`，不静默崩溃。 |

#### 其余界面辅助

| 方法 | 说明 |
| --- | --- |
| `_validate_dates(show_warning=True)` | 日期区间的四道校验（格式、起止顺序、**不晚于今天**、**超过 92 天先问一句**），实现在纯函数 `check_date_range`。超长不拦只问（有人确实要一次导一年）；未来日期直接拦（后台只会给空列表，等满超时+两轮重试等于白等半天）。通过时把 `2026-9-2` 回写成 `2026-09-02`——这串字符同时进日期框填写、归档目录名和汇总文件夹名，两种写法会分成两个目录。`show_warning=False`（检查登录态）时 ask 视为放行、不弹窗。 |
| `_set_date(start_offset, end_offset=-1)` | 「昨天/近7天/近30天」快捷按钮的实现，直接 set 两个 StringVar（trace 顺带刷概览）。 |
| `_select_all()` / `_deselect_all()` | 平台与商户 Var 全置 True/False；每次 set 都触发 trace → 连续多次 `_save_selection` 落盘（无防抖）。 |
| `_refresh_summary()` | 「已选 N 个商户 · M 个平台 [起 ~ 止]」；整段吞异常（构建早期控件未就绪时不打扰）。 |
| `_sanitize_name(name)` | 转 `outputs.sanitize_name`，fallback 为空串（便于"名字清干净后没内容"时提示重填）。 |
| `iter_selected_merchants`（= `__iter_merchants`） | 保活用的 `(key, 商户)` 迭代器，读 `self.selection` 镜像而非 Tk 变量。⚠ 因为类体内名字改写，**类外只能叫公开别名**；保活侧用 `getattr(app, "iter_selected_merchants", None)` 找，改错名字只会让巡检静默空转。 |

### 5.2 `BrowserManager`（core/browser.py）— 浏览器管理类

封装 Playwright 的**持久化上下文**（`launch_persistent_context`，`user_data_dir` 按平台/商户隔离），是平台脚本与浏览器之间的唯一桥梁。构造时初始化下载捕获状态机（`_dl_queue` 事件队列、`_dl_capture_on` 捕获开关、`_dl_temp_dir` 临时目录）。模块顶部还会在 `C:\pw_browsers` 存在时把 `PLAYWRIGHT_BROWSERS_PATH` 指过去（离线打包时内核随包放这里），**这条副作用必须发生在 `from playwright…` 导入之前**。

> **Playwright 内核目录** `resolve_browsers_path()`（写在模块顶部，必须早于 `from playwright…` 导入）：① 外部已设的 `PLAYWRIGHT_BROWSERS_PATH` ② 包内 `<程序目录>/runtime/ms-playwright`（绿色版随包内核）③ `C:\pw_browsers`（开发机既有位置），且**只有目录真实存在才设**。版本必须配对：内核目录名带版本号（`chromium-1234`），由同版本 playwright 生成，混用两套会直接起不来浏览器。

#### 实例状态（`__init__`，25-46）

| 属性 | 含义 |
| --- | --- |
| `headless` / `log_callback` | 由 GUI 按 `show_browser` 设置传入；日志出口注入点（保活传 None，此时只落文件+控制台） |
| `playwright` / `context` / `page` | 三层活句柄，`start()` 赋值；失败路径清空，`close()` **不置 None** |
| `_profile_dir` | 当前浏览器数据目录。没调 `set_browser_profile` 时是 `browser_data/_未指定平台`（**不再是 `browser_data` 根**，见 12 章第 41 条）；`set_browser_profile(key, "")`（脚本调试那条路）是 `browser_data/<key>/_平台调试` —— 都不许落在「商户目录的父目录」这一层 |
| `_export_platform` / `_export_merchant` / `_export_start` / `_export_end` / `_export_task_id` / `_export_sub_merchant` | 本次导出上下文；`task_id = 起_止`（缺一则为空 → 归档退化少一层）。`_export_sub_merchant` **只进文件名**（多一档 `商户_子商户`），不参与目录分层 |
| `_dl_queue` / `_dl_capture_on` / `_dl_capture_t0` / `_dl_temp_dir` | 下载捕获状态机：事件队列、开关、捕获起点（兜底扫根目录判"新文件"的唯一依据）、本次临时目录 |
| `_step_debug` / `_step_callback` / `_user_wait_callback` | 单步调试与"等用户操作"的回调注入点 |
| `_window_closed` | `threading.Event`，用户是否已关掉窗口（全类唯一的跨线程原语，`close` 事件里置位） |
| `_recording_file` | 操作录制 JSONL 路径（`enable_action_trace` 写） |

类常量：`LOGIN_STATE_FILE = "login_state.json"`、`ORPHAN_DIR_NAME = "待确认"`、`_IMAGE_MAGICS`（文件头魔数表）、`_VALIDATE_ERROR_KEYWORDS`（11 个中文错误文案 + `<html`/`<!doctype`）、`TRACE_SCRIPT`（注入页面捕获点击/输入的 JS）。

#### 生命周期与重试

| 方法                                   | 说明                                                                                                                                               |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `start()`                            | 启动前清理 Chrome 锁文件（`SingletonLock` 等）；`launch_persistent_context` 带 `accept_downloads=True`、`downloads_path` 指向 downloads 根目录；失败自动清理锁文件并重试，最多 3 次。 |
| `close()` / `__enter__` / `__exit__` | 关闭 context 与 playwright 实例，支持上下文管理器用法。**关之前先 `save_login_state_on_close()`**：Chromium 退出时不保留 session cookie，而关闭点远不止首次登录（导出收尾、保活巡检、手工测试窗口、程序退出）。守卫：当前 cookie 数比已存的登录态**少**时不覆盖（防页面清 cookie 把上次的登录态换成空的），条数一样但值变了(续期/换 token)照存；用户自己关掉窗口时存不上是常态，静默跳过不刷日志。 |
| `save_login_state_on_close()`        | 上面那层守卫的实现，返回是否真的写了文件；`_saved_cookie_count()` 读已落盘的 `login_state.json` 条数。 |
| `_cleanup_lock_files()`              | 删除 profile 目录中的 `Singleton*/lockfile/*.lock` 残留文件，防止 Chrome 启动失败。每个 `os.remove` 与整体各自吞异常，只数条数写日志。 |
| `_restore_login_state()`             | `start()` 的最后一步：读 `profile/login_state.json` → `context.add_cookies()`（**必须在任何导航之前**）→ 把 `origins[].localStorage` 汇成 `{origin:{k:v}}`，用 `add_init_script` 在 `DOMContentLoaded` 时按 `location.origin` 写回（只覆盖同名键，因此**只对注册之后加载的页面生效**）。整体异常只 warning，不阻断启动。 |
| `save_login_state(quiet=False)`      | `context.storage_state()` 把 cookie+localStorage（含无过期时间的 session cookie）整体写到 `profile/login_state.json`；`quiet=True` 不写日志（首次登录是"边等边存"，每次写一行会刷满屏）。返回 True/False。落盘走 `config.write_text_atomic`（**先 `json.dumps` 再原子替换**：以前 `open("w")` 会先把文件截断再序列化，一失败就把上次的登录态换成空文件）。 |
| `save_login_state_on_close()`        | `close()` 的第一步，也是上面那条守卫的实现：取当前 cookie 条数 `live`（取不到直接返回 False——窗口已被用户关掉，存不上也不该吵），再与 `_saved_cookie_count()` 比：**仅当 `live < saved` 且已有文件时不覆盖**。注释 1112 的理由：关闭点很多（导出前预检、保活巡检、手工测试窗口、程序退出），万一某平台页面会清 cookie，直接存就把上次好不容易存的登录态换成一份空的。条数相同但值变了（续期/换 token）照存。 |
| `_saved_cookie_count()`              | 读已落盘 `login_state.json` 的 cookie 条数；文件缺失或读不出返回 `-1`（于是"任何 live 数都不算更少"，第一次一定能存上）。 |
| `__enter__` / `__exit__`             | `start()` / `close()`；`__exit__` 返回 None，异常照常外抛。当前主流程都是手工 start/close，上下文管理器用法只是预留。 |

> `start()` 内部两条顺序约束（都写在源码注释里，改的时候别动）：① launch 成功**之后**的步骤（挂下载/关闭监听、恢复登录态）若抛错，必须先 `context.close()` 再置 None——只置空会留下一个活的 Chromium 占住 profile 目录，之后该商户每次都启动失败；② 失败重试前必须 `playwright.stop()`，否则其事件循环还在跑，下一次 `start()` 会被误判成 "Sync API inside asyncio loop" 而掩盖真实错误。

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
| `close_popup(retries)`               | **当前整体停用**：方法第一行就 `return False`，下面 8 种弹窗容器/图标/文字按钮的扫描逻辑全部不可达。注释 1006 说明原因："各平台导出流程均无引导/公告弹窗，为使每次导出不再空扫描，此特性先暂停，需要时删掉这一行即可恢复"。全仓 17 处 `.close_popup()` 调用点（骨架 `open_export_page`、保活巡检、11 个平台脚本）都保留着，所以现在一律空转；`retries` 参数当前无意义。 |
| `_same_target(a, b)`                 | 比较 scheme/netloc/path/query，**忽略 hash**。注释理由：query 不同算不同地址，避免有赞"预检带默认参数、导出带具体日期参数"被误判为同一个页面而跳过导航。异常返回 False。 |
| `_text_pattern(text, whole)`         | 逐字符 `re.escape` 后用 `\s*` 连接（`导 出` 这种文案命中得了）。`whole=True` 时两端锚定——注释点名动机：不锚定的 `导\s*出` 照样命中"导出历史"，**精确匹配会被自己的回退链削弱**。 |
| `_ambiguity_note(text, whole)`       | 命中多处时补一行 `[提醒] 页面有 N 处含"X"的文字, 已点中第一处: <该行文字>`。注释 900 的理由：平台脚本普遍点第一个匹配项，一旦 assumption 落空，拿到的文件仍会按本次区间命名并报 success——离线没法核对行内日期，**至少要让人看得见点的是哪一行**。只在 `click_text` 第一优先级路径调用，两条回退路径没有这条日志；整段吞异常（取文字失败不能影响已成功的点击）。 |

> `wait_for()` 不传 `selector` 也不传 `text` 时，等价于"固定等满 timeout 后打一条 `等待超时` warning 并返回 False"。`PlatformBase.check_login` 正是这么用 `wait_for(timeout=3)` 的（只为给未登录重定向留落地时间），所以**每次登录判定都固定花 3 秒并留一条 WARN**，商户多时预检/检查登录态会显得安静得可疑。`is_visible_text` 最坏会等约 2×timeout（正则一路、普通文本一路）。

#### 下载捕获与文件归档（核心机制）

| 方法                                                   | 说明                                                                                                                        |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `set_export_context(platform, start, end, merchant, sub_merchant="")` | 设置导出上下文；任务 ID = `开始日期_结束日期`，保证同平台/商户/区间落同一文件夹。第 5 个参数是可选子商户，只影响文件名那一档。                                                                            |
| `begin_wait_download()`                              | **开启捕获模式**（点击"下载"前调用）：清空事件队列 → 记录捕获起点时间 → 归位根目录残留 UUID 文件 → 清空并重建 `下载目录/平台/商户/日期/临时` 目录。                                  |
| `end_wait_download()`                                | 结束捕获：清队列、关开关、归位根目录残留、清理临时目录（空目录 rmdir，带 8 次重试防瞬时文件锁）。                                                                     |
| `_on_download(download)`                             | 浏览器下载事件回调：捕获模式下把 `{dl, name}` 推入队列，否则忽略。                                                                                  |
| `wait_download(timeout=120)`                         | **等待下载完成**：优先消费事件队列（`_save_download_to_temp` 保存到临时目录 → `_finalize_download` 归档）；兜底走 `_new_download_candidates`（见下）；超时返回 None 并清理残留。⚠ 没 `begin_wait_download` 就被调用时，这里会顺手补上 `_dl_capture_t0` 与临时目录——只置开关不记时刻，兜底那条路就没有"本轮从何时开始"这条线可参考。          |
| `_finalize_download(path)`                           | 将文件归入 `平台/商户/日期范围/`：顶层始终保留本次最新文件，同区间已存在同名旧文件则先由 `_archive_previous` 复制到 `历史/原名_时间戳.扩展名` 再替换。**归档没成或旧件删不掉（被 Excel 占用）时保留旧件，本次成品落 `<名字>_原件保留_HHMMSS.扩展名`**，两边都找得回并各写一行 warning（以前是"归档失败静默吞掉 + 照样删旧件"）；移动失败有 10 次重试 + 拷贝兜底。                                |
| `_archive_previous(final_path, save_name)` / `_keep_both_name(base_dir, save_name)` | 前者的返回值是"旧件是否已经在 `历史/` 里有一份"——只有 True 才允许删顶层；后者给出顶层被占时的替代名（`_原件保留_` 标记，名字仍带同一套前缀，幂等不受影响）。 |
| `_normalize_download_name(...)`                      | 按 `download_name_mode` 生成文件名。**`unified`（默认）= 前缀 + 原始文件名**：`平台_商户[_子商户]_起_止_原名.原扩展名`；原始名是完整 UUID 或为空时退化成只有前缀（有扩展名仍保留）。**`original` = 只加 `商户_` 前缀**，其余原样（UUID/无名同样回退统一命名）。**扩展名一律原样保留、大小写不动、不再过白名单**——以前 `.zip/.rar/.pdf/无后缀` 会被强行改成 `.xlsx`，压缩包顶着表格的名字进归档。末尾过 `_safe_name`（分隔符换 `_`）。已带同一前缀的名字**不再叠第二层**（`_save_download_to_temp` 已经把临时文件叫最终名，`_finalize_download` 会对同一个名字再算一次，幂等这点是硬要求）。 |
| `_carry_over_orphans()`                              | 把下载根目录残留的 UUID 文件**原名原字节**搬进 `downloads/待确认/`（不校验、不改名）。先跳过半成品后缀（`PARTIAL_DOWNLOAD_SUFFIXES`：搬不动正在写的文件，失败还会连累整轮），再**逐个 try**——某一家被占着只跳过它并写"留到下次"，其余照常归位。同名冲突时加 `_HHMMSS`。 |
| `_cleanup_stale_files(root)`                         | 清理 `.~`/`.crdownload/.tmp/.part` 半成品残留（**递归**）。只在 `wait_download` 超时路径调用；⚠ `keep` 形参从未被使用，且后缀匹配是全仓递归，理论上会删掉任何叫 `xxx.tmp/.part` 的正常文件。 |
| `_save_download_to_temp(dl)`                         | 事件路径落盘：三级取文件——① `dl.path()` 已存在就 `os.replace` **移动**进临时目录（注释：避免 downloads 根目录残留原始文件）；② `save_as`；③ 轮询等 `dl.path()` 出现（30×1s，极端情况可超出调用方 deadline 约 30 秒）。`_dl_temp_dir` 为空时直接返回 None——所以**没 `begin_wait_download` 就 `wait_download` 会退化成目录轮询那条路**。 |
| `_accept_download(path)` / `_accept_download_inner`  | **先 `_finalize_download` 归档、再 `_validate_download` 校验**，不过就删文件。注释 446 的理由：必须先归档，否则文件只留在临时目录，随后 `end_wait_download` 清临时目录会把它一起删掉，出现"日志说下载完成、磁盘上找不到文件"；且校验通过与否要以归档后的最终路径为准，避免"日志写 success 实为空表"。外层那一圈 `try` 是硬要求：**归档/校验里的任何文件系统意外都折成"这一份没认领成"(返回 None)**，以前异常会冒到 `wait_download` 之外，把 `_dl_capture_on` 卡在 True、成品留在 `临时/` 里，下一次 `begin_wait_download` 的 rmtree 再把它删掉。 |
| `_wait_root_download(root, deadline)`                | 事件路径没能落盘时的兜底：在 deadline 内轮询 `_find_new_candidate`，把根目录里**本次新出现的原始文件**交出去（`sleep(1.5)` 一轮）。**不归档** —— 归档统一由调用方 `_accept_download` 做一次（以前它自己 `_finalize_download` 一遍，于是同一文件被归档两次，`original` 命名下多叠一层商户前缀）。以前叫 `_take_new_download`，名字与实现不符（既不碰队列，也不该"移动到目标位置"）。 |
| `_find_new_candidate(root)`                          | 只在**下载根目录**找最新候选，四道过滤：跳过中间态后缀 → 跳过图片（注释："兜底扫描不能把它们认领成本次下载"）→ 只认 `_dl_capture_t0` 之后出现的（"避免误取上次残留"）→ 刚改过 2 秒内的视为还在写不选。`os.listdir` 异常吞掉返回 None。 |
| `_is_stable(path)`                                   | 间隔 1 秒两次取 size 相等才算写完（每次调用固定耗时 1 秒）。 |
| `_dir_snapshot(root)`                                | `os.walk` **递归**收集全部文件路径集合，供 `wait_download` 的路 2 差集用；与 `_find_new_candidate` 的"只看根目录"口径不同。 |
| `_validate_download(path)`                           | **成品校验**，按顺序：空文件/读不出 size → 否；文件头是图片魔数 → 否；名字是 `.xlsx` 但头两字节不是 `PK` → 否；**文件头是一份 HTML 网页（`_looks_like_web_page`）→ 否**；**先数数据行**（`_count_rows`）能读出 >0 行 → 直接通过；读不出行才用文案区分空表与错误页（`_match_error_keyword`，**大小写不敏感**）；<1KB 且无数据行 → 否；`rows == 0` → 否；数不出行时还要过 **`_seems_statement_format`** 这道格式门（扩展名属于 `.xlsx/.xls/.csv/.txt/.zip/.rar/.7z`，或**没有扩展名但文件头是 `PK`**）→ 才维持原有的宽松判定放行（老 `.xls` 走这一条）。513 行注释记录顺序不能反的原因：**以前整文件子串匹配错误文案优先，账单备注里一句"客户申请操作失败""请登录后台查看明细"就把真账单判无效并被 `_accept_download` 删掉**，用户看到的是"下载成功但文件没了"。HTML 那道门必须排在数行**之前**：会话过期时有些后台不把登录页返回成 302，而是 200 + `Content-Disposition: xxx.csv`，而 HTML 标签本身就占行 —— 按逗号数得出 4 行的登录页以前会被"先数行"直接放行，交出去一份网页却记成 success（`tests/test_download_integrity.py::test_multiline_html_named_csv_is_rejected` 钉着）。门只看文件头（跳 BOM 与空白），所以 `<?xml` 开头、Excel 2003 存成 `.xls` 的 XML 表格不被一起打死。 |
| `_new_download_candidates(root, known)` / `_is_internal_download_path` | 路 2 的候选：与开始快照的差集，再套上与 `_find_new_candidate` **同一口径**——跳半成品后缀、按 `_dl_capture_t0` 过滤时刻、并排除躺在 `临时/`、`历史/`、`待确认/` 下的文件（按路径分段判，`历史商户/` 这种商户目录与 `历史.csv` 都不受影响）。少了这道排除会出事：`_finalize_download` 在校验**之前**就把同名旧版复制进了 `历史/`，本轮校验一失败，同一个 while 循环就把那份**上一版的真账单**搬回顶层认领，通过全部校验、日志"下载完成并校验通过"、`stats` 记 success（`tests/test_download_fallback_history.py` 钉着，含一条反向用例：真下载照样要被兜底认领）。 |
| `_count_rows(path)` / `_xlsx_row_count(path)`         | CSV 数行去表头；`.xlsx` 用 `zipfile` 数 `<row>` 标签（**不引入 openpyxl**，近似值、可能含空行、假定首行是表头），**取所有工作表的最大值**——只数第一个 sheet 时，"汇总页在前(仅一行表头)、明细页在后"的真账单会被数成 0 行而当空表删掉。其它扩展名或解析失败返回 `None` 表示"读不出"，不参与判空。 |
| `_seems_statement_format(path)` + `_STATEMENT_SUFFIXES` | 数不出行时的格式门：`.xlsx/.xls/.csv/.txt/.zip/.rar/.7z` 算像对账单（zip 那几档是微信支付"账单打包完成"的真实产物），没扩展名但文件头是 `PK` 也放行。**这扇门是命名改造带出来的**：以前不认识的扩展名会被强行改成 `.xlsx`，"名字 `.xlsx` 而内容不是 PK"那条顺带把 PDF/ELF 之类挡在外面；扩展名改成原样保留后，这个拦截必须显式补回来，否则一份 PDF 也能当成品交出去（`tests/test_download_integrity.py` 钉住）。 |
| `_read_text_content(path)` / `_match_error_keyword(text)` | 多编码宽容解码（`latin-1` 永不失败，故总能返回内容；二进制/xlsx 得到近似文本不影响关键字检查）；关键字匹配**区分大小写**——页面写 `<HTML>`/`<!DOCTYPE` 不会命中，此时只能靠"读不出数据行 + 体积阈值"兜住。 |
| `_file_head(path, n)` / `_is_image_file(path)` / `_looks_like_web_page(path)` | 读前 n 字节比对 `_IMAGE_MAGICS`；文件不存在得到 `b""` → False。`_looks_like_web_page` 跳掉 BOM/空白/空字节后比对 `_WEB_PAGE_HEADS`（`<!doctype`/`<html`/`<head`/`<body`/`<script`/`<style`/`<iframe`/`<meta`），**刻意不含 `<?xml`**（Excel 2003 的 XML 表格存 `.xls` 真有人当账单发）。 |
| `_cleanup_temp_dir()`                                | 先置 `_dl_temp_dir=""` 再动手；空目录 `rmdir`（8 次 ×0.8s 防瞬时文件锁），非空 `rmtree(ignore_errors=True)`，注释："留待下次任务 begin 时清理"。 |

> **两条捕获通道的差别**（改这块前必须清楚）：路 1 事件队列——`_on_download` 只在捕获开启时入队（关了会 warning 丢弃，但 Chromium 仍把文件写进 downloads 根目录，于是变成下一轮 `_carry_over_orphans` 处理的 UUID 残留）；路 2 目录轮询——`_dir_snapshot` 递归差集（**递归**，与 `_find_new_candidate` 的"只看根目录"不同），靠 `_new_download_candidates` 补齐 t0 过滤与内部目录排除，再过 `_is_stable`，图片仍不在这里挡（由校验第 0 道负责）。每轮固定 `sleep(1.5)`；多个下载同时到达时只认第一个通过校验的，其余留在根目录、随后被同一次 `end_wait_download` 归进 `待确认/`。

#### 信息与辅助

| 方法                    | 说明                                        |
| --------------------- | ----------------------------------------- |
| `screenshot(name)`    | 截图保存到 downloads 根目录（`名字_时间戳.png`），用于失败排查。 |
| `get_page_info()`     | 返回当前页 `{url, title}`，用于登录态判断；异常时返回两个空串。             |
| `window_closed()`     | 浏览器窗口是否已被用户关掉：`context` 的 close 事件 / 已无页面 / 与 Chromium 通讯断开，任一成立即算；本身不抛异常。首次登录用它当"我登好了"的信号。⚠ `context is None`（**从未 `start()`**）时也返回 True，别把它等价成"用户关掉了窗口"。 |
| `login_signature()`   | `(cookie 条数, 内容指纹)`，**不触发导航**；窗口没了返回 None。登录等待期间用它判断"有登录写入"，因为 `check_login` 会把页面跳走。 |
| `save_login_state(quiet=False)` | `context.storage_state()` 导出 cookie+localStorage（含 session cookie）到 profile 目录。`quiet=True` 不写日志（登录等待期间是轮询保存的）。 |
| `_safe_name(name)`    | 静态方法：转 `outputs.sanitize_name`，兜底值 `"default"`（profile 目录不能为空）。防目录逃逸。 |
| `_task_base_dir()` / `_task_tmp_dir()` | 任务目录 `downloads/平台/商户[/子商户]/起_止`（四层按顺序拼、**空掉的层直接省略**，所以没配子商户的商户路径与从前逐字一致）与其下的 `临时/`。子商户名在 `set_export_context` 时就过一遍 `_safe_name`——它会直接成为一级目录名。 |
| `latest_export_dir()` | 转 `_task_base_dir()`。**全仓无调用点**（只有本 wiki 提过），属预留 API，别在文档里写成"界面用它定位文件"。 |

#### 人工介入与调试支持

| 方法 | 说明 |
| --- | --- |
| `set_step_debug(enabled, callback)` | 设开关；关闭时把回调一起清掉。GUI 在每次任务 `finally` 里复位，注释："避免影响后续任务（浏览器可能没起来）"。 |
| `step_pause(name)` | 未开调试直接返回 True；开了先 `snapshot("DEBUG_…")` 再等回调。**回调返回假值 → 抛 `RuntimeError`**（用户点"中止"），且这条 `except RuntimeError: raise` 被刻意放在"回调自身异常只记日志继续"之前——两种失败语义必须区分。没有回调时"只截图不阻塞"。 |
| `set_user_wait_callback(cb)` | GUI 注入"弹窗等用户操作"的回调（扫码/确认场景）。**回调必须回话**：返回值就是"用户是否真的点了确定"。 |
| `wait_user(prompt, timeout=600)` | 先 `snapshot("等待用户确认")` + warning，再走回调。**True 只来自用户亲手点"确定"**；取消、弹窗没弹出来、到点没人应答、回调自身异常，一律 False（以前是"回调一返回就无条件记 用户已确认,继续 return True"，微信上没扫码也会接着点下载）。有回调时 `timeout` 参数不起作用，实际等待时长由 GUI 侧 `LiushuiApp.USER_WAIT_TIMEOUT_S` 决定；**没回调**（录制/调试）时仍按 `timeout` 静默停留后返回 True——没人可问的场合保持旧兜底。 |
| `snapshot(step_name)` | 按当前任务上下文存 `…/snapshots/步骤_时间.png`；建目录失败回落到下载根目录；**失败返回 None**。 |
| `screenshot(name)` | 存到 **downloads 根目录**（`名字_时间戳.png`）；**失败也返回路径**——与 `snapshot` 语义不一致，容易误用；也正是"兜底扫描会认领截图"那个历史事故的源头。 |
| `snapshot_on_failure(where)` | `get_page_info` 打 url/title + `snapshot("FAIL_…")` 的组合，失败取证用；取信息失败静默。 |

#### 操作录制（喂给「录制→脚本」）

`enable_action_trace(record_to_file=True)`：`context.add_init_script(TRACE_SCRIPT)` + 给当前页挂 console 监听 + 准备 `recordings/<平台>_<商户>_<时间戳>.jsonl`；`_on_trace_console` 只处理 `[TRACE_` 前缀的消息，逐条 open-append-close 写 `{ts, type, url, element}`，**全程双层吞异常**（绝不能让页面脚本拖崩浏览器）。注入脚本 `TRACE_SCRIPT` 用 `__trace_injected__` 幂等标志，在捕获阶段监听 click/change，`brief()` 只取 `tag/text(≤50)/cls(≤120)/id/ph/name/href`，且 click 只记按钮类元素、change 只记输入类元素。产物由 `tools/recording_to_script.py` 消费（见 8.4）。

> 线程约定：Playwright 同步句柄（`page`/`context`）只能由启动它的那个线程碰，所以 `navigate/start/close/wait_download/snapshot` 等一律在任务线程调用；`_log` 任意线程可调；`wait_user`/`step_pause` 的回调被 GUI 实现成"投递主线程弹窗 + 工作线程 `Event.wait`"。

#### 平台专项逻辑放在哪里

平台独有的做法（例：有赞把日期塞进流水页 URL 的毫秒时间戳参数、微信支付的 `.el-range-input` 只能靠键盘输入）**一律写在 `platforms/<key>/export.py` 里，不进 `core/`**。逐平台的实现见 [8.2](#82-11-个平台逐家实现)。

> 早期版本曾有 `fill_zent_*` 一类的有赞日历辅助方法，当前 URL 参数方案不再需要操作日期组件，相关方法已删除；`zent-dialog__close` 只残留在已停用的 `close_popup` 选择器列表里。

### 5.3 `PlatformBase`（core/platform\_base.py）— 平台基类

全平台脚本必须继承的抽象基类，接口约定如下：

| 成员                                      | 类型  | 说明                                                                                            |
| --------------------------------------- | --- | --------------------------------------------------------------------------------------------- |
| `key`                                   | 类属性 | 唯一标识（与文件夹名一致，如 `"youzan"`），加载器以此为索引。                                                          |
| `name`                                  | 类属性 | 界面显示名（如 `"有赞"`）。                                                                              |
| `login_url` / `export_url`              | 类属性 | 登录页 / 流水导出页地址。                                                                                |
| `guide`                                 | 类属性 | 给用户看的操作指引文字。                                                                                  |
| `enabled`                               | 类属性 | 是否在界面中显示（默认 True）。11 个平台目前都是 True。 |
| `manual_intervention`                   | 类属性 | 是否需要人工操作（扫码/手机确认）。True 时该平台在批量循环里被**稳定排序到队尾**，先自动跑完其它平台。全仓只有「视频号」声明了它。 |
| `intervention_hint`                     | 类属性 | 人工操作说明，供日志与提示（`_execute_export_tasks` 去重后播报"已排到最后执行"）。 |
| `login(browser)`                        | 方法  | 默认实现：`navigate(login_url)` 等待用户手动登录；可覆盖做特殊处理。唯一调用点在 `_login_one_round`。 |
| `check_login(browser)`                   | 方法  | **登录态判据**：直接访问受保护的 `export_url` → `_wait_redirect_landed` 等未登录重定向落地 → 用 `_is_login_url` 看当前 URL 是否落到独立登录路径。等待从 2026-09-26 起改为**按次采样**（`LOGIN_POLL_STEP/MAX/STABLE` = 0.2s/15 次/连续 8 次相同即落地）：旧写法 `wait_for(timeout=3)` 不传条件必然走满 3 秒并在收尾打一条"等待超时"WARN，而每家商户至少查两遍（预检 + inline），10 家就是 60 秒白等 + 20 行假警告。唯一的语义收窄处（重定向晚于静止窗口才发生 → 判成已登录）由 `tests/test_check_login_wait.py` 钉住，方向是**宁可多跑一次空导也不拿"没登录"挡掉账单**。注释 62-67 说明为什么不用旧判法（"访问 login_url + URL/标题含 login/登录 就判未登录"会把"已登录但登录页标题仍带登录字样"误判成未登录）。**扫码页与后台同域的平台必须覆盖它**改用文案/元素判断（现只有微信支付覆盖，用 `is_visible_text("交易中心")` + 扫码文案表）。边界：`export_url` 自身含 `/login`、`login.` 时退化为旧判法；`login_url` 为空时 `navigate("")` 只会重试失败返回 False（不抛错），随后取到的还是旧 URL → **可能假阳性判已登录**。 |
| `_is_login_url(url)`                     | 静态方法 | token 子串表：`/login`、`login.`、`login.cgi`、`passport`、`/signin`、`sign_in`、`sso`、`cas`、`auth.`（URL 先 lower）。⚠ `cas`/`sso` 这类短 token 可能命中普通业务路径（`/purchase_case`）而误判未登录。 |
| `export(browser, start_date, end_date)` | 方法  | **核心接口**。返回 `"success"`（成功）/ `"manual"`（需手动）/ `"failed"`（失败）。默认实现：懒加载 `SmartExporter` 执行智能导出，并把 `log_callback=browser._log` 接上——否则 SmartExporter 的 `_log()` 是空操作，默认导出全程静默（11 个内置平台都自带 `export()`，走不到这里，但"平台管理新建、还没写脚本"的平台第一次导出就会）。 |
| `open_export_page(browser)`            | 方法  | 骨架钩子①：`navigate(export_url)` + `sleep(PAGE_SETTLE_S)` + `close_popup()`（后者当前空转，调用点保留以便恢复）。 |
| `set_date_range(browser, start, end)`  | 方法  | 骨架钩子②：按 placeholder 填「开始日期/结束日期」（各取 `[:DATE_VALUE_SLICE]`）。**返回两个框是否都填成功**；页面改版/框名不同导致填不进去时调用方必须停手——否则后台按自己的默认区间出账单，文件名却是本次请求的区间，从产物上根本看不出错了。日期框名字不同的平台（视频号"动账开始/结束时间"、微信 `.el-range-input`）需覆盖本方法。 |
| `trigger_export(browser, start, end)`  | 方法  | 骨架钩子③：点「查询」`sleep(3)` 再点「导出」`sleep(5)`。需先切标签、或导出后还要去历史报表页的平台覆盖此方法（现被 6 个平台覆盖）。 |
| `download_export_file(browser, label, settle_s)` | 方法 | 骨架钩子④：`begin_wait_download` → 点 `label`（默认 `DOWNLOAD_LABEL`「下载」）→ `sleep(DOWNLOAD_SETTLE_S)` → `wait_download(timeout=DOWNLOAD_TIMEOUT_S)`。**只返回 `"success"`/`"manual"` 两态**，`failed` 由上层 `_run_single_export` 从异常折算；`settle_s=None` 判定允许显式传 0。 |
| `PAGE_SETTLE_S` / `DOWNLOAD_TIMEOUT_S` / `DOWNLOAD_LABEL` / `DOWNLOAD_SETTLE_S` / `DATE_VALUE_SLICE` | 类属性 | 骨架的五个差异声明点：打开页面后等几秒（3）、下载最多等多久（60）、下载按钮的真实文字（有的叫「下载全部」「下载明细」，小红书就是「导出」）、点完给几秒落地（3）、填进日期框的字符串长度（天猫「月汇总」设成 7，只吃 `2026-09`）。 |
| `run_standard_flow(browser, start, end)` | 方法 | 串起上述四步；日期没全部填进去时**在点导出之前停下**（`[中止]` warning + `snapshot("日期未填入")` + 返回 `"manual"`）。平台脚本 `export()` 里 `return self.run_standard_flow(...)` 即采用骨架；**不调用则行为完全不变**。 |
| `SELECTORS`                             | 类属性 | 关键元素的选择器表（`{"date_start": ".el-range-input", …}`）。声明后导出前会被自动校验；目前只有微信支付声明了 4 个键。 |
| `supports_sub_merchants`                | 类属性 | 默认 `False`。置 `True` 才会被"一次登录、按清单逐个切子商户导多份"的循环接管；不声明的平台连循环都进不去，行为与从前逐字一致。⚠ 必须是真布尔值——写成字符串会让"没声明"看着像声明了（有测试钉住）。 |
| `switch_sub_merchant(browser, sub)`     | 方法  | 切到指定子商户，返回 `True` 只表示"切换动作点完了"。**默认返回 False 并写一条 warning**：声明支持却没实现切换的平台就此整家停手，绝不带着没确认过的归属去点导出。覆盖时两条底线：切不过去必须返回 False；这里不许点导出/下载（那是 `export()` 的事，混进来重试语义就说不清）。 |
| `current_sub_merchant(browser)`         | 方法  | 从页面读回"现在是谁"。默认空串——空串在流程里走"归属未经校验"分支（放行 + 提醒），**不等于匹配通过**。这是整套机制里唯一防住"A 的账单记到 B 名下"的东西，能实现尽量实现。 |
| `verifies_sub_merchant_identity()`      | 方法  | 脚本有没有真的覆盖读回（比对函数对象）。界面上拿它决定要不要提示"这个平台的归属不会被校验"，免得用户以为切错了会有人拦。 |
| `check_selectors(browser, timeout=2)`   | 方法  | 面检：遍历 `SELECTORS`，`locator.count()==0` 或选择器非法都记进 `missing` 并 warning，返回 `(ok, missing)`。**由 `_run_single_export` 在导出前自动调用**，缺失只写日志不拦截；未声明直接跳过。⚠ `timeout` 形参未被使用。 |
| `assert_selector(browser, key)`         | 方法  | 点检：单个键 `wait_for(timeout*1000)`，超时先 `snapshot_on_failure` 再**抛 `RuntimeError`**（附平台声明的选择器）。未声明该键时只 warning 并返回 True——"没声明就不拦"。微信支付在填日期前、点查询前各断言一次。 |

### 5.4 `SmartExporter`（core/exporters.py）— 智能导出器

当平台不自定义 `export()` 时由基类调用，自动执行通用导出流程。**现状核实**：11 个内置平台**没有一个**在用它（各自实现了 `export()`），它只服务"用平台管理新建、还没写脚本"的平台。构造签名是 `(browser, log_callback=None)`；`PlatformBase.export` 现在传的是 `log_callback=browser._log`，于是它认出了几个日期框、点了哪个按钮、为什么转 manual 都会进统一日志（以前没传，默认导出全程静默）。

| 方法                                  | 说明                                                                                                                        |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `_find_date_inputs()`               | 遍历页面 input，只看 placeholder（小写）：命中 `开始/起始/起/start/from` 记为 start，**elif** 命中 `结束/截止/止/end/to` 记为 end，各取第一个；单元素异常 `continue`。 |
| `_find_button(texts, exact)`        | 按文本 `get_by_text(...).first` 返回第一个命中的 locator。 |
| `_fill_dates(start, end)`           | 点击并填入日期，**返回成功填写的个数**（0/1/2）。 |
| `_click_query()` / `_click_confirm()` | 依次尝试「查询/搜索/确定/筛选/查找」；确认按钮表「确定/确认/下载/导出/保存」，用于二次确认弹窗兜底。 |
| `_click_export()`                   | 两轮匹配：**外层 exact True→False**，即先把整张长标签表精确匹配跑完，全无才降级子串；`_EXPORT_LABELS` **长标签在前**。注释 85-86/93-95 记录两个旧坑：旧实现整表 `exact=False` 且「导出」在最前，页面上任何含"导出"的静态文字、"导出记录"导航项都会被当成按钮点掉，而「导出报表」这类长标签永远轮不到。命中后日志标明"(精确匹配)"还是"(文字包含)"。 |
| `_setup_dialog_handler()` / `_handle_dialog(dialog)` | **只自动接受**文案含「导出/下载/生成报表/账单/对账单」的 JS 弹窗，其余 `dismiss()` 并写"弹窗内容与导出无关, 已取消(需人工确认)"；处理弹窗自身出错只记一行。注释 128-130：以前是 `page.on("dialog", lambda d: d.accept())` 无条件点确定，商家后台弹"确定要作废这张发票吗"这类破坏性确认也会被自动接受。注册失败也 `pass`。 |
| `export(start, end, timeout)`       | 主流程：填日期 → 点查询 → `begin_wait_download` → 点导出 → `wait_download`；拿不到文件再 `_click_confirm` 兜一次二次确认并重等；仍失败截图 `manual_<时间戳>.png` 返回 `"manual"`。**`filled==1`（只填进一个日期框）时直接转 `manual`**（注释 163-165：另一端还是页面默认区间，硬导会得到错区间账单还被记 success）；而 **`filled==0` 反而继续硬试**（只提示"可能使用日期选择器组件"就直奔导出按钮）——与 `PlatformBase.set_date_range` 的严格策略正好相反，是刻意的"能猜就猜"。 |
| `quick_export(start, end, timeout)` | 不填日期的精简版（页面已有默认日期的场景）。 |

***

### 5.5 KeepAliveService（core/keepalive.py）— 登录保活服务

| 方法/属性 | 说明 |
|-----------|------|
| `__init__(app, interval_min=30, enabled=True, make_browser=None)` | `interval_min = max(1, int(...))` 防 0/负数；`make_browser` 工厂可注入便于测试（默认 `_default_browser`：`headless=True` + `set_browser_profile(key, merchant)`，**惰性 import `BrowserManager`**，模块导入不拖入 Playwright）。 |
| `start()` / `stop()` | 已活就跳过（重复调用安全）；`stop` 置事件 + `join(timeout=5)`，**无论线程存不存在都打"保活服务已停止"**。 |
| `_run()` | `while not self._stop.wait(interval*60)`：`enabled` 与 `app.running` 的检查都在 wait **之后**——改间隔只影响下一轮等待，不会立刻唤醒；`run_once` 的异常被 `log(..., "error")` 吞掉，不会结束线程。 |
| `run_once()` | 遍历 `app.iter_selected_merchants()`，逐个用独立 profile 打开 `app.login_urls[key]` 刷新会话（`navigate → sleep(3) → close_popup()`），再**只看 URL** 判失效。 |
| `_iter_merchants()` / `_mark()` | 鸭子类型取 `app.iter_selected_merchants` 与 `app.set_platform_status`，**没有就什么都不做**（不报错，所以改名会让巡检静默空转）。 |
| `enabled` / `interval_min` | 界面「登录保活」勾选与间隔框改动即时应用到运行中的实例。 |

> 判据口径（2026-09-26 起）：`run_once` 只用 `"login" in url` 判失效；标题里带"登录"字样改为**只写一行日志、不置红**。以前是 `url or title` 两个都算，而那正是 `PlatformBase.check_login` 注释点名抛弃的旧判法，于是"灯红着、导出却一切正常"成了设计出来的结果——状态灯一旦不可信就没人看它了。仍存的口径差（有意保留）：保活是"打开登录页刷新会话"，导出侧是"打开导出页看会不会被踢"，两者的 URL 本来就不是同一个页面，所以保活漏报（灯绿但要重登）比误报可接受得多，真失效由导出前的预检兜住。

> 并发约定：巡检轮询时若 `app.running == True`（正在导出/调试）跳过本轮，绝不与任务并发；浏览器实例工厂注入便于单元测试。

### 5.6 PlatformAdmin（core/platform_admin.py）— 平台管理与调试

| 成员 | 说明 |
| --- | --- |
| `validate_platform_key(key)` | `_KEY_RE` 要求**小写字母/下划线开头**、其后字母数字下划线。注释 70-72 的理由：首位不能是数字——骨架会生成 `class 2MeiExporter` 这种非法标识符，文件写下去编译不过，而目录已经建好 → 再建提示"平台已存在"、列表里又看不到它，成了死路。 |
| `render_platform_skeleton(...)` | 纯函数生成脚本源码（类名 `key.title().replace("_","")+"Exporter"`）。所有用户输入经 `_py_str` = `json.dumps` 变成合法 Python 字面量，进 docstring 的部分再用 `_plain` 把引号/反斜杠/换行换成空格。注释 22-24 记录旧事故：以前用 `str.format` 把这些值裸插进 `"..."`，操作指引里写一个英文引号就产出语法错误文件。骨架里带一段中文提示指导后来人去改 `run_standard_flow`。 |
| `save_platform_script(path, content)` | **先 `compile()` 再写盘**：`SyntaxError` 抛「第 N 行语法错误: msg」，其它异常抛「脚本无法编译」；通过才 `write_text_atomic`。注释 79-82：以前是先 `open("w")` 写盘再 py_compile 检查——检查失败时磁盘上**已经是坏文件**，下一次 loader 扫描/reload 会静默跳过该平台，用户原本能跑的脚本被一次误编辑毁掉。 |
| `generate_platform_skeleton(...)` | 校验 key → 目录已存在抛「平台已存在」→ 渲染 → 建目录 + 空 `__init__.py` → `save_platform_script`；**任一步失败 `rmtree` 回滚再 raise**（不留空目录，否则下次"新增"被 `os.path.isdir` 判成已存在）。 |
| `DebugProbe(inner, steps)` | 只留 `inner/steps/page` 三个真属性，其余全走 `__getattr__`：非可调用直接返回属性，可调用则包 `_call` 转发并**如实抛出异常**，每步记 `{i, action, args, ok, error}`。两条注释理由：① 以前逐个手写 13 个方法，漏了 `wait_for/snapshot/wait_user/_log` 等真实存在的方法，平台脚本或 `check_login` 只在"脚本调试"里 AttributeError、生产却跑得好好的，而且列死名单会随 `BrowserManager` 签名漂移（`navigate` 的 `skip_if_same` 就被丢过）；② 吞掉异常会让同一段脚本在调试里得到 `manual`、在生产里得到 `failed`，**调试结果反而骗人**。**边界**：`page` 是构造时的快照，探针建好之后才启动浏览器会拿到 None。 |
| `PlatformManagerDialog` / `PlatformWizard` / `PlatformEditor` / `DebugDialog` | 平台列表 + 新增向导 + 内置编辑器（保存→语法校验→原子写→`on_saved`→`reload_platforms`）+ 试运行对话框。全部**必须主线程构造**；`DebugDialog._run` 只做取值和设状态，真正跑脚本交给 `main_gui._run_async` 的 worker 线程。 |

### 5.7 CronScheduler（core/scheduler.py）— 定时任务

- `CronExpr(expr)`：支持 `*`/`*/n`/`a-b`/`a,b`/`?`；`match(dt)` 按分时日月周匹配（dom 与 dow 同时受限为 OR）；`next_run(after)` 返回下一个匹配分钟（一年内）。`_parse` 对**读不懂的东西一律报错而不是当"不设限"**：反向区间（`5-1`）、越界起点（`70/5`）、非正步长（`*/0`）都抛 `ValueError`——少一道判据，`range()` 得到空集，而空集在末尾被判成"这个字段完全不设限"（`0 9 * * 5-1` 于是变成天天跑）。`impossible_date()` 只在"日、月都受限而周没受限"时给出"这个日期不存在（2 月 30 日）"，日/周同时受限是 OR 语义所以不该报（`0 9 30 2 1` 靠每周一会跑），2 月按闰年 29 天算所以 `0 9 29 2 *`（四年一次）也不报。
- `CronJob`/`TaskStore`：任务模型与 `scheduled_tasks.json`（version 1）读写；损坏条目跳过。**新建任务走 `CronJob.new(...)`**，它把 `last_run` 记为创建时间。`load()` 对同一 `job_id` **复用内存里已有的对象**（`sync_from_disk` 只搬盘上字段）——`check_all` 每 30 秒 load 一次，换成新对象会让对话框握着的引用变成孤儿（见 12 章第 45 条）。`save()` 返回是否真的落盘并把原因留在 `last_error`；`mark_triggered(job, now)` 写 `last_run`，**写不进盘时在内存里兜一份**（`trigger_floor`，按"名字\|cron"记），`should_trigger` 取盘上与内存中**较新**的那份。
- `CronScheduler(app, store, poll_interval=30)`：后台线程轮询；`should_trigger` 基于「上次运行后的 next_run <= now」（`last_run` 为空视为已到期，故新建任务必须用 `CronJob.new` 记起算点，否则保存后 30s 内就会执行一次）；触发经 `app.trigger_job(job)` 后 `mark_triggered`；错过不补跑。写盘失败经 `_warn_store_failure` 说一次（记号带错误原因，同一种毛病不刷屏）。
- `app.trigger_job(job)`：用 `pair_job_targets` 把任务的平台/商户配成实际可跑的组合（商户只与其所属平台配对），无匹配时在日志里提示而不是静默返回；返回 `_run_async` 是否真的开始（正忙时 False，且不弹"已有任务在执行"的窗打扰人）。
- `CronScheduler.trigger(job, now)`：**只有真的开始执行才写 `last_run`**；到点时若被手动导出占着，本次不记，下个轮询周期自动重试（"程序关闭期间错过不补跑"的语义保持不变）。
- 界面：`SchedulerDialog` 列表的「对象」列显示 `pair_job_targets` 算出的**实际项数**（配不出来标「0 项(商户与平台不匹配)」）；`JobEditDialog` 保存时对空商户/不匹配商户先问一句再存。
- `SchedulerDialog`/`JobEditDialog`：任务新增/编辑/删除/启停；cron 带常用模板与校验。编辑走 `CronJob.apply_edit(...)`：**改了 cron 就把起算点挪到编辑时刻**，否则老 `last_run` 配新 cron 早已"到期"，保存后 30 秒内会立刻跑一次。`JobEditDialog._save` 在**保存这一刻按 `job_id` 回查当前列表**（不用点"编辑"时抓住的引用），查不到就弹窗拒绝本次保存而不是把已删的任务写回盘上复活；`cron` 语法错与"没有真实存在的日期"两种都在当场拦住，写盘失败则不关窗、明说"没保存成功"。
- 几个容易被忽略的细节：`next_run` 是**逐分钟步进**、上限 527040 次（一年），判成"一年无解"的表达式记进模块级 `_NEVER_MATCHING`（实测一次年扫约 460ms，调度线程每 30 秒为每个任务算一次，不记就是常年白烧），更常见的"2 月 30 日"那类由 `impossible_date()` 提前给出人话；`check_all` 每轮先 `store.load()`（手改 json 也能生效，且复用对象），但 `_run` 的 `except: continue` 意味着**一条坏任务会跳过当轮剩余任务**；`poll_interval = max(10, int(...))` 有下限；`CronJob.from_dict` 对没写 `id` 的条目**每轮都新生成一个随机 job_id**（所以任何"按 id 记一次"的机制都会失效，见 12 章第 40 条；内存兜底的记号同样不用 id）；cron 缺失/非法的任务不跑、并且日志会说一句（以前兜成"每分钟跑一次"）。
- 失败重试：`run_with_retry(fn, retry_times, retry_interval_s, log)` 与 `_execute_export_tasks`（自 `_do_export` 提炼）；settings `retry_times`(默认 2)/`retry_interval_s`(默认 30，递增 ×2)；`retry_times=0` 关闭。`_run_single_export` 对任何异常都在内部消化成返回值 `"failed"`（含浏览器启动阶段），否则抛出会让重试整批失效；导出时那层登录预检失败返回 `"manual"` 且同样落 `stats.jsonl`（`error` 写明「登录已失效」）。
- 人工介入集中：settings `preflight_login_check`(默认 `True`) 让手动导出在开跑前先逐家 `_probe_login`（无头）核一遍登录态，失效的一次性列出、集中重登，仍未登录的从本批剔除；需扫码的平台按 `manual_intervention` 排在队尾。`_probe_login` 把异常折成"没能核实"并照常导出——检查失败绝不能变成业务失败。定时任务（`trigger_job`）不做预检：确认框无人应答会把整批吊住。

### 5.8 模块级关键函数

| 函数                                    | 位置                 | 说明                                                                                                           |
| ------------------------------------- | ------------------ | ------------------------------------------------------------------------------------------------------------ |
| `discover_platforms()`                | `core/loader.py`   | 扫描 `platforms/` 下含 `export.py` 的文件夹（跳过 `_` 开头的目录），动态导入，找出继承 `PlatformBase` 且定义 `key` 的子类并实例化，返回 `{key: 实例}`。 |
| `discover_merchants(platform_keys)`   | `core/main_gui.py` | 扫描 `browser_data/<平台key>/` 下的子目录，发现已建档商户，返回 `{key: [商户名]}`。                                                  |
| `load_settings()` / `save_settings()` | `core/config.py`   | 读写 `settings.json`，缺失字段回退 `DEFAULT_SETTINGS`（8 个键，见 10 章）。                           |
| `log(message, level, callback)`       | `core/logger.py`   | 统一日志：输出控制台（容错 `pythonw` 无 stdout 场景）→ 写入 `logs/run_YYYYMMDD.log` → 转发 GUI 回调。                                |
| `check_dependencies()` / `main()`     | `core/main_gui.py` | 启动前三档依赖体检（缺包/版本不对/缺内核，见 `core/deps.py`）与引导安装；程序入口。 |
| `report_crash(summary, detail)`       | `core/main_gui.py` | `main()` 的异常兜底：先用模块顶部已导入的 `log` 记 ERROR，再弹窗。**返回值=日志是否真写成功**，弹窗文案跟着变（写不成功就不谎称"已记录到 logs 文件夹"）。与 crashguard 的区别：这里兜的是 `main()` 内部已起来之后的异常。 |
| `find_duplicate_merchant(name, existing)` | `core/main_gui.py` | 返回 `existing` 中与该名字视为同一家商户的那个名字（忽略首尾空格、不分大小写），没有则空串。 |
| `merchant_profile_dir(base, key, merchant)` / `delete_merchant_profile(...)` | `core/main_gui.py` | 前者按 `BrowserManager.set_browser_profile` 同一套算法算出 profile 路径（有测试钉住两者一致）；后者只删这一层目录，路径深度不对/名字为空一律拒绝，被占用时重试 4 次后如实返回失败。 |
| `merchant_changes(before, after)`    | `core/main_gui.py` | 两次扫目录结果的差集，返回 `(新增, 消失)`，顺序变化不算改动。 |
| `check_date_range(start, end, today=None)` | `core/main_gui.py` | 返回 `DateCheck(verdict, title, message, start, end)`，`verdict` ∈ `ok`/`ask`/`bad`；`start/end` 是补零规范化后的日期。纯函数，`today` 可注入以便测试。 |
| `run_cleanup(log_dir, downloads_dir, keep_days, dry_run=False)` | `core/cleanup.py` | 删除过期 `run_*.log`/`crash_*.txt` 与过期汇总副本目录，返回 `{"logs": [...], "summary_dirs": [...]}`；`keep_days<=0` 完全不动，`dry_run` 只列不删。 |

### 5.9 配置与日志（core/config.py、core/logger.py）

| 成员 | 说明 |
| --- | --- |
| `ROOT_DIR` / `DOWNLOAD_DIR` / `BROWSER_DATA_DIR` / `SETTINGS_FILE` / `SCHEDULED_TASKS_FILE` / `SELECTION_FILE` | 全部**基于项目根的绝对路径**（注释：不依赖运行目录）。`.vbs` 双击时 CWD 不定，早期用相对路径会把产物落到别处。这里只拼路径不建目录（`downloads`/`browser_data` 由 `BrowserManager._setup_dirs` 建，`logs` 由 logger 导入时建）。 |
| `ROOT_DIR` / `PROGRAM_DIR` vs `DATA_ROOT` | **程序目录**（代码/平台脚本/内核，可只读）与**工作空间**（账单、浏览器数据、日志、三个 json、录制，必须可写）分成两类，绿色版靠这条界线成立。`ROOT_DIR` 仍由 `__file__` 上溯得到；七个用户可见路径全部由 `_derive(root)` 一处算出。 |
| `resolve_data_root()` | 定工作空间并返回 `(路径, 是否还要问用户)`，优先级：命令行 `--data-dir=` > 环境变量 `LIUSHUI_DATA_DIR` > 书签 `workspace.json` > 就地（程序目录已有"用户真动过"的痕迹）> 程序目录 + 待选。取到的目录会**试写探针文件**（`_probe_writable`：只 `mkdir` 不算数，只读介质上 mkdir 也可能成功）。指定的目录不可用时退回程序目录并把原因记进 `RESOLVE_NOTES` —— 悄悄换位置比报错更糟。 |
| `LEGACY_MARKERS` | 只认 `settings.json`/`selection_state.json`/`scheduled_tasks.json` 三个。⚠ 不能把 `logs`/`downloads`/`browser_data` 当痕迹：`import core.logger` 就会建 `logs/`、`BrowserManager()` 就会建另两个 —— 那样自检跑过一次之后新包就永远不再问用户，账单与登录态被写回可能只读的程序目录（有回归测试钉住）。 |
| `apply_data_root(root, remember=True)` | 换工作空间的唯一入口：试写 → 重算派生常量 → 清 `PENDING_PICK` → 写工作空间标记 `.liushui_workspace.json` + 书签。不可写返回 `(False, 原因)`。⚠ 各模块是 `from .config import DOWNLOAD_DIR` 的**取值拷贝**，换完必须重启进程才传导（见 14 章）。 |
| `read_pointer()` / `write_pointer()` | 书签的两个位置：程序目录 `workspace.json` 优先，写不进（只读介质）就退到 `~/.liushui_export/workspace.json`。这是全仓唯一一处 `expanduser`：权威标记在工作空间内，书签只负责"下次去哪找它"。 |
| `default_workspace_suggestion()` | 首启给用户的默认位置：`~/Documents/流水导出工作空间`。 |
| `DEFAULT_SETTINGS` | 8 个键的唯一默认值来源。⚠ `load_settings()` **只遍历这 8 个键**，用户在 `settings.json` 里手加的未知键会被静默丢弃。 |
| `load_settings()` | 读文件、缺字段回默认；文件缺失/坏 JSON/顶层不是 dict 三种情况都吞掉，返回值恒为完整 8 键副本。不写盘。 |
| `write_text_atomic(path, text)` | 同目录唯一临时名 `path.tmp-<pid>-<序号>` → write + `flush` + `os.fsync` → `_replace_with_retry`（对 Windows 的"另一个程序正在使用此文件"做 8 次退避重试）；失败时删临时文件后 **raise**（"失败的这次不算数，别留垃圾文件"）。⚠ 临时名**不许改回固定 `path+".tmp"`**：`scheduled_tasks.json` 是调度线程与主线程都会写的文件，共用一个暂存名时后开的 `open("w")` 会截断前者正在写的那份、前者的 `os.replace` 会撞 WinError 32（实测 8 线程同写 48 次里 38 次抛错，而调用方一律 `except: pass` → 静默丢改动）。同目录保证 `os.replace` 不跨卷。 |
| `write_json_atomic(path, data)` | `ensure_ascii=False, indent=2` 后委托上者。注释 76-79 是全部理由：`settings`/`scheduled_tasks`/`selection_state` 都是"界面每次改动整体重写"，非原子写时进程写一半崩掉文件就是截断的，而三处读取都是 `except → 用默认值/返回空`，**用户看到的是"配置和定时任务被静默清空"**。 |
| `save_settings(partial)` | **以 `load_settings()` 现值为底只覆盖传进来的键**。注释 87-90 记录旧事故：旧实现以 `dict(DEFAULT_SETTINGS)` 为底，界面各处"点一下只写自己那一两个键"，于是勾一下"失败重试"就把首次重试间隔写回 30、动一下保活开关就把文件名模式和显示浏览器统统恢复默认。整体 try/except **pass**：写盘失败静默，界面不会提示"保存失败"。 |
| `_ensure_log_dir()` / `LOGGER_NOTES` | 日志目录建不出来（只读介质、U 盘、权限）时退到 `%TEMP%/liushui_export/logs`；连 `FileHandler` 打不开也只是没有文件通道，**绝不在 import 期抛错** —— `logger` 是最早被导入的模块之一，那时除了 crashguard 没有别的兜底。经过记进 `LOGGER_NOTES`，由界面 `_report_paths` 在启动日志里说一声。 |
| `log(message, level="info", callback=None)` | 三通道：① `print`（整句包 try，`pythonw` 下 `sys.stdout` 可能是 None）；② `logging.FileHandler` 写 `logs/run_<启动时刻>.log`（格式 `时间 [LEVEL] 消息`）；③ GUI 回调（同样吞异常，回调可能是已销毁的文本控件）。界面行带 `[HH:MM:SS] [WARN]/[ERROR]` 前缀，与文件行不同；`info` 无前缀，`critical` 未在映射表里会落 INFO。返回值是拼好的界面行。 |
| `logger` | 模块级单例，`if not logger.handlers` 防重复挂 handler（重复导入/测试重跑）。 |
| `record_stat(...)` | 往 `logs/stats.jsonl` 追加一行 JSON，固定 8 字段：`ts/platform/merchant/start_date/end_date/result/duration_s/error`(截 200 字)。**整个函数 `except: pass`** —— 统计绝不许影响导出。唯一调用点在 `_run_single_export` 收尾无条件执行。 |
| `_read_tail_lines(path, max_lines)` | `seek(END)` 每次回退 64KB，块首可能被切断的那段留作 `leftover` 与下一块拼接，最后补回头部 → 返回文件顺序的尾部行。 |
| `load_stats(limit=None)` | 用上面的尾部读法 + `reverse()` 得到"最新在前"；坏行 `except: pass`。注释 43 说明动机：`stats.jsonl` 每次导出追加、永不清理，整文件 `readlines` 会让"打开看板"随使用时长越来越慢（33MB/20 万行实测 667ms → 7.2ms）。 |
| `summarize_stats()` | **仍走全量读**（注释 44：它要统计全部历史）。按平台分组，`result` 只认 `success/manual/failed` 三个键值，平台返回别的字符串只进 `total` 不进任何分类；按 `total` 倒序。 |
| `list_history_logs()` | `glob("logs/run_*.log")` 按 mtime 倒序 → `(文件名, 路径, mtime 串)`。**不含 `crash_*.txt`**；无 try，列举与取 mtime 之间文件被清理线程删掉会抛 OSError（调用方 dialogs 未捕获）。 |

### 5.10 汇总与清理（core/outputs.py、core/cleanup.py）

| 成员 | 说明 |
| --- | --- |
| `INCOMPLETE_SUFFIXES` / `sanitize_name(name, fallback)` / `task_folder(start, end)` | 半成品后缀表；路径非法字符 `<>:"/\|?*` 与控制字符替换（兜底值由调用方决定：商户名用空串提示重填、profile 用 `default`）；日期目录名 `起_止`（与 `BrowserManager._export_task_id` 同形，两处各写一份）。 |
| `find_output_files(base, platform_names, start, end)` | 认三种结构：`平台/日期/`（旧）、`平台/商户/日期/`、`平台/商户/子商户/日期/`，都收并按出现顺序去重。`_iter_task_dirs` 的层数**封顶在平台目录下两层**（`MAX_TASK_DEPTH=2`），并且**找到日期目录就不再往下走**，所以 `历史/`、`临时/` 里的归档不进汇总（原件仍在任务目录，不算丢）。跳过非文件与半成品后缀。传入的是平台**显示名**。 |
| `summary_name(task_path, used)` | 汇总目录里用什么文件名：默认原名；撞名时加"上一级**非日期**目录名"作前缀（也就是商户或子商户——直接取父目录会得到一串日期，等于没加），还撞就再叠时间戳，`getmtime` 失败也不打断汇总。会撞的场景只有一个：「原始文件名」模式下两个子商户的原始名一模一样，静默覆盖等于把一份账单弄丢。 |
| `copy_to_summary_dir(base, names, start, end, log, now)` | 复制到 `downloads/起_止_<落地时间戳>/`；无起止或没有可汇总文件返回 **None**（调用方据此不再打开文件夹）。逐文件 `copy2`，**单个失败 `pass`**（注释 83：不能影响其余，更不能中断导出收尾）。 |
| `SUMMARY_DIR_RE` / `LOG_FILE_RES` / `KEEP_FOREVER` | 汇总目录名格式 `起_止_YYYYMMDD_HHMMSS`；日志文件名白名单收三种：`^run_\d{8}\.log$`（早期按天）、`^run_\d{8}_\d{6}\.log$`（现在每次启动一个）、`^crash_\d{8}_\d{6}_\d{6}\.txt$`；`stats.jsonl` 再旧也不删（看板唯一历史来源）。白名单是用来「认名字」的——不在表里的文件一律当别人的东西不碰。 |
| `_cutoff` / `_old_enough` | 只看 `os.path.getmtime`；stat 失败（被占用/权限）按"不动"处理。 |
| `prune_logs(log_dir, cutoff, dry_run)` | 只删目录内**文件名匹配白名单**且够旧的普通文件；`keep_days<=0` 直接返回 `[]`。正在写的日志 mtime 一直被刷新，不会被清。 |
| `prune_summary_dirs(dl_dir, cutoff, dry_run)` | 只看 `downloads/` **顶层**、名字匹配汇总格式的目录（`re.match` 不中就 `continue`，注释 88："平台目录/待确认/杂项一律不看"），跳过符号链接；`rmtree` 的 OSError 吞掉。 |
| `describe(report)` | 把结果拼成一行 `[清理] …` 供日志。 |

> 为什么按**目录自身** mtime 判定汇总副本：汇总目录在导出收尾那一刻创建、之后再没人写它，mtime 恒等于落地时间；反过来用 `max(子文件 mtime)` 会错——`shutil.copy2` 把源文件 mtime 一起带过来，原件可能是很久以前的账单日期。`tests/test_cleanup.py:31` 钉住了这条（同时钉住 Windows 上"往目录里写文件不更新该目录 mtime"这个坑，测试要自己 `os.utime`）。模块 docstring 的"绝不碰"清单：账单原件目录、`downloads/待确认/`、`stats.jsonl`、各类 json 配置。

### 5.11 崩溃兜底与配色（core/crashguard.py、core/theme.py）

| 成员 | 说明 |
| --- | --- |
| `install()` | 装 `sys.excepthook = report_uncaught`（进程级）与 `threading.excepthook = thread_excepthook`（子线程）。自身失败返回 False 且**不抛**。`main_gui.py` 在 `tkinter`/`core.*` 之前第一件事就调它，所以连"导入期就炸"都覆盖得到。 |
| `write_crash_file(text)` | `logs/crash_<YYYYMMDD>_<HHMMSS>_<微秒>.txt`（注释：精确到微秒是因为"同一秒内崩两次，用户连点两下，不该把第一条覆盖掉"）；`logs/` 写不了退到项目根，两个都失败返回 None；写完 `flush + fsync`。 |
| `show_crash_dialog(path, text)` | `_dialog_shown` 保证**一个进程只弹一次**（免得子线程连环崩溃刷屏）；拿不到路径就把堆栈末 600 字贴进弹窗让人截图发维护；窗口起不来返回 False。 |
| `report_uncaught(exc_type, exc, tb, dialog=True)` | 落文件 + （仅主线程）弹窗；`dialog=False` 供子线程用——**在非主线程里拉 Tk 窗口可能把界面吊住**，对业务用户比"看不见崩溃"更糟。弹窗自身的异常单独吞（"弹窗坏了不能盖住真实崩溃，文件已经落了"）。返回崩溃文件路径。 |
| `theme.py` 的 11 个配色常量 | 存在的唯一理由（注释 2-4）：让 `core/dialogs.py` 这类"只画窗口"的代码能用同一套颜色，而不必反向 import `core.main_gui`（那会成环）。数值与原来写在 main_gui 里时完全一致。 |

### 5.12 查看类对话框（core/dialogs.py）

| 成员 | 说明 |
| --- | --- |
| `show_log_history(root)` | 模态窗口：左 Listbox 列 `list_history_logs()`（按 mtime 倒序），右只读文本；选中/双击都整份重读（`errors="replace"`），读失败把错误文本显示出来；「复制此日志」绑当前内容。**没有刷新按钮**——文件列表只在开窗那一刻取一次。列表为空时插占位并禁用后提前 return。 |
| `show_stats_dashboard(app)` | 上半 Treeview 是 `summarize_stats()`（全量读）按平台成功率，<60 红 / <85 橙 / 其余绿；下半是 `load_stats(limit=200)` 的最近明细。依赖注入面只有两点：`app.root`（剪贴板/父窗）与 `app._set_status`——测试就按这两点造假对象。刷新会重读一遍，注意一次刷新读两遍全量。 |
| 线程要求 | 两个函数都直接建控件、`grab_set`、读写剪贴板 → **必须主线程调用**（入口是界面按钮）。 |

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
| `core/loader.py`        | `core.logger`                  | 加载失败/重复 key/没有平台类都走统一日志（`pythonw` 下只 print 等于看不见） |
| `core/platform_base.py` | `core.exporters.SmartExporter` | 方法内懒加载（默认导出实现）                                                  |
| `core/browser.py`       | `core.outputs.sanitize_name`   | 方法内延迟导入（避免循环），profile 目录名与归档名共用一套清理规则                    |
| `core/outputs.py` / `core/cleanup.py` / `core/config.py` | 标准库 | 纯文件/正则操作，**都不 import GUI**，所以可脱离界面单测 |
| `core/scheduler.py`     | `core.config`                  | `SCHEDULED_TASKS_FILE` + `write_json_atomic`（方法内延迟导入避免成环）        |
| `core/keepalive.py`     | `core.browser`、`app`（鸭子类型）  | 浏览器工厂可注入；靠 `app` 的 `login_urls`/`iter_selected_merchants`/`running`/`set_platform_status` **属性名**协作 |
| `core/main_gui.py`      | `core.outputs` / `cleanup` / `keepalive` / `scheduler` / `dialogs` / `theme` / `platform_admin` | 汇总复制、启动清理、保活服务、定时任务、两个查看窗口、配色、平台管理与调试 |
| `core/dialogs.py`       | `core.logger` + `core.theme`   | 刻意**不 import main_gui**（会成环），只借 `app.root` 与 `app._set_status` |
| `platforms/*/export.py` | `core.platform_base`           | `from core.platform_base import PlatformBase`；专项逻辑（如有赞 `_build_youzan_url` 的 `urllib.parse` + UTC+8 毫秒戳）留在各平台脚本内 |
| `tools/recording_to_script.py` | 标准库 | 被 `main_gui._open_recording_dialog` 直接 import 四个函数（`list_recordings/load_records/render_skeleton/write_skeleton`） |
| `core/logger.py`        | 标准库                            | `logging`                                                               |
| 全部                      | Playwright                     | 外部依赖 `playwright==1.62.0`（`requirements.txt`）；`browser.py` 顶部按 `C:\pw_browsers` 是否存在决定 `PLAYWRIGHT_BROWSERS_PATH` |

### 6.3 外部依赖

- **Python 3.10+**（启动脚本探测 Python3.10\~3.14 常见路径）

- **playwright==1.62.0** + Chromium 浏览器（版本钉在三处，见 12 章第 30 条。全量版随包带；核心版/源码版首次运行由 `check_dependencies()` 分三档引导安装 —— 以前是 `启动工具.vbs` 自己 `pip install playwright`（没钉版本），现在判断只留 Python 一处）

***

## 7. 核心流程解析

### 7.1 程序启动流程

```
启动脚本(start.bat / 启动工具.vbs)
  → 探测 Python 解释器(vbs 按五档找: 写死路径 → py 启动器 → 用户级目录 → .venv → C:\Python3xx)
  → 运行 python -m core.main_gui
      ├─ crashguard.install()         # 先装兜底，之后任何导入失败都会落 logs/crash_*.txt
      ├─ check_dependencies()         # 三档体检: 缺包(可退出)/版本不对(只问不拦)/缺内核(只补内核)
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
主线程取好 日期/单步开关/预检开关 + _collect_tasks 后 _run_async(_do_export)
【登录预检】(settings.preflight_login_check，定时任务不做):
  逐家 _probe_login(force_headless=True) → 失效的一次性弹窗: 集中重登(_do_login) / 本次跳过
  重登后仍 bad 的按下去标剔除；没能核实的照常留下 → 一家不剩就直接结束，不进导出循环
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
需人工操作(manual_intervention)的平台在循环开始前就被排到队尾
结算统计 → _copy_export_outputs 汇总文件 → 自动打开汇总文件夹 → 待人工的清单一窗列出
```

> 人工介入点因此集中在三处：开跑前的预检框、队尾的扫码确认、结束时的待人工清单。
> 以前登录失效是"跑到那一家才发现"（只在 inline 预检里被折成 `manual` 跳过），
> 用户走开一会儿回来，批任务已经在等第 N 个框。

### 7.4 下载捕获与文件归档时序

```
点击"下载"前:  begin_wait_download()
               ├─ 清空事件队列, 打开捕获开关
               ├─ _carry_over_orphans()  # 归位上次残留 UUID 文件
               └─ 重建 <平台/商户[/子商户]/日期>/临时 目录
点击"下载"后:  context "download" 事件 → _on_download → 入队
wait_download(): 出队 → _save_download_to_temp(写入临时目录)
                 → _finalize_download(归档到 平台/商户[/子商户]/日期/)
                     ├─ 顶层同名旧文件 → 复制到 历史/原名_时间戳.ext
                     └─ os.replace 移动(10 次重试 + 拷贝兜底)
兜底: 轮询 downloads 根目录新出现的稳定文件(两份采样大小一致)
end_wait_download(): 清队列、归位残留、清理临时目录
```

### 7.5 登录态检查流程（`_do_check` / 导出前预检共用 `_probe_login`）

```
逐家任务: _probe_login(plat, merchant)
  _ensure_browser(该商户独立 profile) → plat.check_login(browser)
      多数平台: 打开 export_url → 等 3s 重定向落地 → 看 URL 是否落到 /login|passport|sso… 路径
      微信支付(同域): 找已登录文案「交易中心」, 否则找扫码类文案, 都没有 → 保守判未登录
  结论 True/False/None(异常折成"没能核实")
    「检查登录状态」按钮: 三档各写一行日志 + 平台灯 ok/error/error, 可见性跟随"显示浏览器"设置
    导出前预检: 同上但 force_headless=True, 失效的攒到最后一次性弹窗(见 7.3)
```

### 7.6 平台新增与调试流程

① **新增**：`PlatformWizard` 填元信息 → `validate_platform_key`（小写字母/下划线开头）→ `render_platform_skeleton`（输入按 `json.dumps` 字面量转义）→ 建目录 + 空 `__init__.py` + `save_platform_script` → 任一步失败 `rmtree` 回滚 → `on_saved` 触发 `reload_platforms()`，左侧列表立即出现，**无需重启**。

② **编辑**：`PlatformEditor` 内置编辑器改 `export.py` → `save_platform_script` **先 `compile()` 再原子写**，语法错误直接抛「第 N 行语法错误」且**磁盘上的原文件不动** → `reload_platforms()`（连删 `sys.modules`、importlib stat 缓存与磁盘 `.pyc` 三处）。

③ **调试**：`DebugProbe` 用 `__getattr__` 通用透传包装 browser → `plat.export(probe, ...)` 试运行 → 每步记 `{i, action, args, ok, error}`，异常**如实抛出**（吞掉会让调试结果与生产结果不一致）。⚠ 调试走的是 `_ensure_browser(plat)` 不带 merchant，用的是**平台级 profile，没有商户登录态**。

***

## 8. 平台插件体系

### 8.1 开发模型

每个平台 = `platforms/<key>/` 文件夹下的 `export.py`，定义一个继承 `PlatformBase` 的类并实现 `export()`。加载器按 `key` 注册，GUI 自动展示。详细编写规范见《脚本编写指南.md》，要点：

- `export()` 必须返回 `"success" / "manual" / "failed"` 三选一；
- 优先使用 `BrowserManager` 辅助方法（表格见 5.2）；
- 差异小的平台直接 `return self.run_standard_flow(browser, start, end)`，只覆盖有差异的钩子或五个类属性；
- 平台专项逻辑（如有赞的 URL 日期参数）**写在平台脚本里，不进 `core/`**；
- 通用兜底：完全不写 `export()` 才走 `SmartExporter`（现无任何内置平台这么做）。

### 8.2 11 个平台逐家实现

| 平台 | 类名 | 在骨架上 | 需人工 | 下载等待 | 点下载的真实文字 | SELECTORS 自检 | 自定义 `check_login` |
|---|---|---|---|---|---|---|---|
| 有赞 `youzan` | YouzanExporter | 否（日期走 URL） | 否 | 60s | 下载报表 | 无 | 无 |
| 微信支付 `wechatpay` | WechatpayExporter | 否（全手写） | 否 | **90s** | `a.popups.download`「业务明细账单」 | **有**（4 键 + 2 处 assert） | **有**（文案判据） |
| 京东 `jingdong` | JingdongExporter | 否（全手写） | 否 | 60s | 立即下载，否则 查看文件 | 无 | 无 |
| 拼多多 `pinduoduo` | PinduoduoExporter | 否（全手写） | 否 | 60s | 下载账单 | 无 | 无 |
| 视频号 `shipinhao` | ShipinhaoExporter | 否（全手写） | **是**（手机微信扫码） | 60s | 下载数据 | 无 | 无 |
| 支付宝 `alipay` | AlipayExporter | 是 | 否 | 60s | 下载全部 | 无 | 无 |
| 抖音 `douyin` | DouyinExporter | 是 | 否 | 60s | 下载 | 无 | 无 |
| 快手 `kuaishou` | KuaishouExporter | 是 | 否 | 60s | 下载 | 无 | 无 |
| 天猫 `tmall` | TmallExporter | 是 | 否 | 60s | 下载明细 | 无 | 无 |
| 小红书 `xiaohongshu` | XiaohongshuExporter | 是 | 否 | 60s | 导出 | 无 | 无 |
| 银联 `yinlian` | YinlianExporter | 是 | 否 | 60s | 下载 | 无 | 无 |

统计：6 个在骨架上（`alipay/douyin/kuaishou/tmall/xiaohongshu/yinlian`，各覆盖 1~2 个钩子），5 个全手写（`jingdong/pinduoduo/shipinhao/wechatpay/youzan`）。`enabled` 没有平台改成 False；`manual_intervention=True` 只有视频号。**只有微信支付会返回 `"failed"`**，其余平台的失败一律表现为 `"manual"`。

#### 有赞 `youzan`（日期不进日期框）

`_build_youzan_url`（40-59）把 `startTime/endTime` 按 UTC+8 换成 `起 00:00:00.000` 与 `止 23:59:59.999` 的毫秒戳、`dateType=SETTLE_TIME`，`urlencode(doseq=True)` 重组后 `open_youzan_record` **只导航一次**。docstring 41-45 是关键设计理由：`export_url` 里保留着录制当时的时间戳，**日期解析失败必须抛出去**，退回原 URL 会静默导出那个死区间、账单看起来完全正常。步骤：导航（含日期）→ `sleep(3)` → `close_popup` → `sleep(1)` → 点「筛选」`sleep(3)` → 点「导出报表」`sleep(10)` → `begin_wait_download` → 点「下载报表」`sleep(3)` → 有「确定」弹窗就点 → `wait_download(60)`。注释 100-101：以前只等 10 秒，配合前面 `sleep(10)` 基本判不到成功，表现为"有赞需要手动导出"的误报。

#### 微信支付 `wechatpay`（自检与取证最重）

`check_login` 覆盖（57-74）：扫码页与后台**同域**，URL 区分不了 → 先找已登录特征 `is_visible_text("交易中心", 5)`（快，避免逐个等扫码文案超时），否则轮询「扫码登录/微信扫码/扫一扫登录/请使用微信」，都没命中**保守判未登录**。`SELECTORS` 4 个键。`_set_dates`（76-100）：`.el-range-input` ≥2 个时走 `click → Control+a → keyboard.type → Enter`（docstring 77：`fill()` 对 Vue 受控组件不生效），数量不足才退回 `fill_placeholder`，**返回两条 placeholder 是否都填成功**；`_export_inner` 拿到 False 就停手转 `manual`（键盘那条路走通才算成功）。`_download_one_bill`（102-133）：`begin_wait_download` → 点账单类型链接 → 等「账单打包完成」→ 用 `.el-dialog:visible .el-button--primary` 点确认（注释 115：按钮文案是含空格的「确 定」且页面有多个隐藏 el-dialog）→ 可能再进「下载列表」→「立即下载」→ `wait_download(90)`；失败 `snapshot_on_failure`。`_export_inner`：`check_selectors` 缺失只记日志 → `assert_selector("date_range_input")` → 填日期 → `snapshot`+`step_pause` → Escape 关日期浮层 → `assert_selector("query_btn")` → **`click_text("查询", exact=True)`**（注释 168：左侧菜单有「已结算查询」，不精确会跳页）→ 循环 `bills = ["业务明细账单"]`（注释 178：要加"业务汇总账单"在这里追加，多文件落同一归档目录）→ 全成功 `success` / 部分 `manual` / 全失败 `failed`。整个 `_export_inner` 被 try 包住，任何异常 → 截图 + `"failed"`。

#### 京东 `jingdong` / 拼多多 `pinduoduo`（事件驱动的手写流程）

两者都把固定 `sleep` 换成 `wait_for(text=…)`，并**内联复制**了"日期没全填进就停手"的检查（与骨架同源）。共同形状：进页 → 等账单标签出现 → 切标签（京东「日账单」/拼多多「货款明细」）→ 填日期 → 查询 → 等导出入口出现 → 点导出 → 进历史列表（京东「去下载列表」/拼多多「导出历史」）→ `begin_wait_download` → 点下载（京东「立即下载」，看不到就点「查看文件」；拼多多「下载账单」+ 可能的「确定」弹窗）。两处专项理由值得记：京东注释 76-77——弹窗判定必须匹配整串「去下载列表」，以前只匹配「去」，任何含"去"的文案都会被判成弹窗出现并点掉；拼多多注释 69-70——同页「导出」与「导出历史」都含"导出"，子串匹配下点中哪个由 DOM 顺序决定，所以先 `exact=True` 再退回子串。

#### 视频号 `shipinhao`（唯一需人工）

`manual_intervention=True` + `intervention_hint`（注释 42 写明 True 会被排到最后）。真实 placeholder 是「动账开始/结束时间」，所以覆盖成私有 `_set_time`（46-83）：正则 placeholder 定位 → `fill()` 后**回读 `input_value()` 校验** `[:10]` 是否生效，没生效就 `click → Control+a → keyboard.type → 回车`，值拼成 `"YYYY-MM-DD 00:00:00"` / `" 23:59:59"`，结束 `Escape` 关面板。步骤：进页 → 「资金流水」→ `_set_time` → 查询 → 「全部导出」`sleep(10)` → **`wait_user("…手机上使用微信扫码确认…")`**（注释 107：平台弹"需手机微信扫码"，这里阻塞等用户）→ `begin_wait_download` → 30 秒内可见才点「下载数据」→ `wait_download(60)`。`_set_time` 返回 False（找不到输入框或过程报错）时**当场停手**：写 `[中止]` 日志 + `snapshot(「日期未填入」)` + 返回 `manual`，不再往下点查询/全部导出。**`wait_user` 的返回值同样要看**（2026-09-26 起）：False（取消/弹窗没弹出来/超时）就 `snapshot("未确认扫码")` + `manual`，不 `begin_wait_download` —— 手机没扫码还点下载，拿到的多半是空文件或上一轮的文件，而流程会把它记成 success。

#### 六个骨架平台的差异点

| 平台 | 覆盖了什么 | 该平台的特殊之处 |
|---|---|---|
| 支付宝 | `trigger_export`（点查询，**没有"导出"这一步**）、`DOWNLOAD_LABEL="下载全部"`、`DOWNLOAD_SETTLE_S=5` | docstring 记：仅支持 2025-08-24 及以后的流水；页面有今日/昨日/7日/30日快捷项，脚本没用 |
| 抖音 | `open_export_page`（先点「日汇总」，注释：默认停在「资金流水明细」）、`trigger_export`（查询 → **生成报表** `sleep(10)` → **历史报表** → 才有下载） | 报表异步生成，必须去历史列表取 |
| 快手 | `trigger_export`（查询 → 导出 → **查看导出记录** → 才有下载） | docstring 列了方式 A(货款账单/结算时间，建议半年) 与 B(收支明细月账单)，**只实现了 A** |
| 天猫 | `open_export_page`（点「月汇总」）、`trigger_export`（该后台把「查询」叫「搜索」）、`DATE_VALUE_SLICE=7`、`DOWNLOAD_LABEL="下载明细"` | **全仓唯一缩短日期串**的平台：日期框只吃 `2026-09` |
| 小红书 | `open_export_page`（点「动账明细」）、`trigger_export`、`DOWNLOAD_LABEL="导出"`、`DOWNLOAD_SETTLE_S=5` | 点「导出」就直接出文件，没有单独「下载」按钮；docstring 的方式 B（订单结算明细→导出订单）未实现 |
| 银联 | `trigger_export`（查询 → 点「商户对账单下载」→ 选「所有交易账单」→ **弹窗里二次填时间** → 导出 `sleep(10)` → 再点列表里的「下载」） | "选类型 + 二次填时间"这段业务逻辑写在脚本里；注释 55：银联生成对账单明显偏慢 |

### 8.3 共同模板与三类常见差异

骨架平台的真实形状：打开 `export_url` → 固定 3 秒落地 →（关弹窗，**当前空转**）→ 有的先切标签 → 按 placeholder 填起止日期 → 点查询/搜索 → 若干固定 sleep → 异步报表类再去"历史/导出记录"列表一跳 → `begin_wait_download` → 点下载入口 → 落地 sleep → `wait_download` → 拿到路径即 `success`，否则 `manual`。手写平台是同一序列的"事件驱动版"（`sleep` 换成 `wait_for(text=…)`，日期校验内联）。

最常见的三类差异，写新平台时先对号入座：

1. **报表要先生成、再去历史列表下载** —— 基类 `trigger_export` 的"查询+导出"两步表达不了，靠覆盖 `trigger_export`（支付宝/抖音/快手/银联/小红书）或整段手写（京东/拼多多/微信/视频号）解决。
2. **日期控件不吃标准 placeholder、甚至不吃 `fill()`** —— 微信 `.el-range-input`（Vue 受控，只能键盘）、视频号「动账开始/结束时间」（fill 后回读校验、失败退键盘）、天猫只吃年月、有赞干脆不填日期改 URL。
3. **文案撞车与人工介入** —— 需要 `exact=True` 消歧（拼多多「导出」vs「导出历史」、微信「查询」vs「已结算查询」）、弹窗按钮要匹配整串（京东「去下载列表」）、可见弹窗与含空格文案（微信「确 定」+ `.el-dialog:visible`）、需要手机扫码确认的（视频号 `wait_user`）。

### 8.4 录制 → 脚本骨架（tools/recording_to_script.py）

| 成员 | 说明 |
| --- | --- |
| `list_recordings()` / `load_records(path)` | 按 mtime 倒序列 `recordings/*.jsonl`；逐行解析，**空行与坏行静默跳过**。 |
| `first_css_class(cls)` / `gen_selector(el)` | 选择器优先级即稳定度：`#id` → placeholder（不产 CSS，改走 `fill_placeholder`）→ `tag.首个稳定class`（文字 ≤12 字再补 `:has-text("…")`）→ 兜底按文字点击。class 会跳过 `active/selected/hover/focus/disabled/loading` 这类状态类。 |
| `render_skeleton(records, platform_name, class_name, platform_key)` | 生成"继承 `PlatformBase` + 覆盖 `set_date_range`(带"必须返回 bool"的 docstring) + 覆盖 `trigger_export`(逐步录屏动作 + 结尾 `snapshot`) + `export()` 里 `try: return self.run_standard_flow(...)` / `except: 截图 + "failed"`"。规则：`(type, selector, hint)` 去重（重复点击只留首次）；**录到的「下载」点击一律丢弃**并补注释说明由基类 `download_export_file` 负责（否则会点到列表另一行）；`change` 步骤的值写成 `"TODO_VALUE"` 字符串保证骨架不 NameError；每步后补 `sleep(1)`。**收尾的 navigate / `begin_wait_download` / `wait_download` 一律不生成**（避免各平台超时再各抄一份）。模块 docstring 明说局限："录制只看得见点了什么，看不见点完之后页面变成什么样"——日期组件真实交互、异步报表等待、下载按钮所在列表行仍需人工核对。 |
| `write_skeleton(path, code, force=False)` | 目标存在且未 `force` 直接 `FileExistsError`（默认落点正是 loader 会导入的 `platforms/xxx/export.py`）；自动建父目录。 |
| CLI | `jsonl` 位置参数 + `--out/--force/--latest/--list/--platform/--class-name/--key`。 |

**与 GUI 的接线**：商户行「打开」→ `_open_merchant_browser` 里 `_ensure_browser(force_visible=True)` 之后、导航之前调 `enable_action_trace()` → 用户在那个窗口里手工点一遍导出流程，点击/输入被录进 `recordings/` → 工具栏「录制→脚本」对话框预览/保存骨架（预览用硬编码占位名，**key 与类名必须手改**）。`recordings/` 已在 `.gitignore`。`tests/test_recording_to_script.py` 钉住：生成代码语法合法、不含 `begin_wait_download`/`wait_download(timeout=`/`navigate(self.export_url)`、「下载」不重复、骨架走基类默认 60 秒超时、拒绝覆盖线上脚本。

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

1. 双击 `启动工具.vbs` 启动（或 `start.bat` 看控制台报错）；
2. 左侧勾选平台，点平台右侧 `+` 添加商户（建 `browser_data/<key>/<商户>/` 登录目录）；
3. 勾选商户 → 点「首次登录」→ 在浏览器里登录，**登完直接关掉那个浏览器窗口**即可（程序边等边存登录态，关掉后还会无头重开核实一次）；
4. 选日期范围（或点「昨天/近7天/近30天」）→ 点「开始导出」→ 确认任务清单；
5. **导出前登录预检**（默认开）：失效的一次列出，选「集中重登」或「本次跳过」；
6. 自动导出跑完，需扫码的视频号排在队尾；结束后自动打开 `downloads/起_止_时间戳/` 汇总目录，仍要人工的平台一次性列出。

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
| `downloads/<平台名>/<商户>/<起_止>/临时/` | 是              | 单次任务下载临时暂存（事件路径先落这里），`begin` 时删旧建新、`end` 时清空                                       |
| `downloads/<平台名>/<商户>/<起_止>/snapshots/` | 是 | 平台脚本 `snapshot()` 的步骤截图（`步骤_时间.png`），与成品同目录但分开存，不会被当成账单交付 |
| `downloads/待确认/`                | 有残留时才生成 | 归属不明的 UUID 下载残留：**原名原字节、不经过任何校验**，等人工核对；清理逻辑永不碰 |
| `downloads/<起_止>_<时间戳>/`         | 是              | 导出完成后全平台汇总目录（`_copy_export_outputs` 生成，原件仍在各商户目录）；**清理工具只认这种名字的目录**                    |
| `browser_data/<平台key>/<商户>/`     | 是（点 `+` 添加商户时） | Chromium 持久化 profile，登录态隔离的边界；删商户只删这一层 |
| `browser_data/<平台key>/<商户>/login_state.json` | 保存登录态时 | `context.storage_state()` 导出的 cookie+localStorage（含 session cookie）；启动时由 `_restore_login_state` 注回 |
| `recordings/<平台>_<商户>_<时间>.jsonl` | 点商户「打开」时 | 操作录制（点击/输入的 tag/text/id/placeholder…），喂给「录制→脚本」；已 gitignore |
| `settings.json`                  | 是              | 用户设置：`show_browser`（显示/隐藏浏览器）、`download_name_mode`（`unified` 前缀+原始名 / `original` 只加商户前缀）、`enable_keepalive`（登录保活开关）、`keepalive_interval_min`（保活间隔分钟）、`retry_times`/`retry_interval_s`（失败重试次数与首次间隔）、`preflight_login_check`（导出前统一查登录，默认开；只影响手动「开始导出」，定时任务不预检）、`cleanup_keep_days`（日志/汇总副本保留天数，0=不清理，默认 365） |
| `selection_state.json`            | 是              | 平台/商户勾选状态（重启恢复），同时维护内存里的 `selection` 镜像供保活线程读                          |
| `scheduled_tasks.json`            | 是              | 定时任务持久化（`{"version":1,"jobs":[...]}`，新增/编辑后原子保存）                                    |
| `logs/run_YYYYMMDD_HHMMSS.log`    | 是              | **每次启动一个文件**（不是按天），格式 `时间 [LEVEL] 消息`，DEBUG 级；超过 `cleanup_keep_days` 的会被清理 |
| `logs/stats.jsonl`                | 是（每次导出一条）   | 稳定性统计流水：`ts/platform/merchant/start_date/end_date/result/duration_s/error`；看板唯一历史来源，**任何清理都不许碰** |
| `logs/crash_YYYYMMDD_HHMMSS_微秒.txt` | 崩溃时才生成 | 未捕获异常的完整堆栈（含子线程里的）。**双击没反应/闪一下就退时先看这里**，弹窗上写的是它的路径；写不进 `logs` 时退回项目根同名文件。                        |

***

## 11. 附录：文件清单

| 文件                       | 规模（当前实际行数）         | 作用                              |
| ------------------------ | ------------- | ------------------------------- |
| `core/main_gui.py`       | 2659 行    | GUI 主程序与三大业务流程、导出前登录预检、界面更新队列、中止控制、商户增删改与刷新、日期区间校验、等待用户手工确认（`_ask_user_confirmed`）、录制/调试/平台管理对话框入口 |
| `core/browser.py`        | 1533 行      | 浏览器管理、下载捕获与归档（含"本轮自己写的目录"排除与旧件保留）、成品校验（含格式门与多 sheet 数行）、登录态存取、操作录制（项目体量最大的核心模块） |
| `core/scheduler.py` | 675 行 | 定时任务（cron 解析含"读不懂就报错"的判据 / `TaskStore` 保身份读写与写盘失败兜底 / 轮询触发 / 管理界面） |
| `core/platform_admin.py` | 336 行 | 平台管理/脚本调试（骨架纯函数生成 + 先验语法再原子写 + DebugProbe 通用透传 + 三个弹窗） |
| `core/exporters.py`      | 229 行       | 智能导出器（无人写 `export()` 时的默认兜底导出）  |
| `core/platform_base.py`  | 253 行       | 平台基类（元信息 + 登录判据 + 选择器自检 + 通用导出骨架钩子 + 子商户切换/读回钩子，默认全关） |
| `core/dialogs.py` | 198 行 | 历史日志窗口与稳定性看板（只依赖 `logger` + `theme`） |
| `core/logger.py`         | 200 行       | 日志三通道 + 稳定性统计（`stats.jsonl` 尾部读与全量汇总） |
| `core/cleanup.py` | 121 行 | 过期运行日志与汇总副本的按保留期清理（白名单认领文件名，原件/统计/待确认不碰） |
| `core/loader.py`         | 112 行        | 平台加载器与免重启 reload（含清磁盘 `.pyc`） |
| `core/config.py`         | 355 行       | 路径常量、`DEFAULT_SETTINGS`、原子读写（唯一暂存名 + 占用重试）与"只覆盖传入键"的设置保存 |
| `core/keepalive.py` | 96 行 | 登录保活服务（后台线程周期巡检，任务执行中跳过本轮） |
| `core/crashguard.py` | 119 行 | 启动期崩溃兜底（堆栈落 `logs/crash_*.txt`，主线程才弹窗） |
| `core/deps.py` | 219 行 | 依赖三档体检（缺包/版本不对/缺内核）、内核目录识别、修复命令与离线退路；不 import playwright、不碰界面，见 14.2 |
| `core/outputs.py` | 148 行 | 导出成品查找（三种层级、下探封顶两层）与汇总副本复制，含撞名加前缀的 `summary_name`（纯文件操作，可脱离界面单测） |
| `core/submerchants.py` | 155 行 | 子商户清单存储 + 归属比对（独立出现才算命中）；清单落在 `sub_merchants.json`，坏文件按未配置处理并把原因带出去，见 14.3 |

| `core/theme.py` | 17 行 | 配色常量（供 dialogs 复用，避免反向 import main_gui 成环） |
| `tools/build_portable.py` | 282 行 | 绿色包组装：`--flavor full/core` 两版、白名单复制、`scan_forbidden` 挡用户数据/凭证、`check_flavor` 核结构与版本一致、`--verify` 自检路径（不需要依赖） |
| `packaging/` + `.github/workflows/build-portable.yml` | — | 两个版本的启动器与说明（`run-portable.*` / `run-core.*`，纯 ASCII vbs + 可看错的 bat）和出包流水线，见 14.2 |
| `tools/recording_to_script.py` | 272 行 | 录制 JSONL → 脚本骨架生成器：输出基类钩子形状（`set_date_range`/`trigger_export` 覆盖 + `run_standard_flow`），目标文件已存在时默认拒绝覆盖（`--force` 才写） |
| `platforms/*/export.py` | 11 个平台共 1053 行 | 平台导出脚本（微信支付 202 行最重，京东 98 / 拼多多 95 / 视频号 134 / 有赞 104，六个骨架平台各 56-64 行）。**6 个用 `run_standard_flow` 骨架**（快手、支付宝、天猫、抖音、小红书、银联）；京东/拼多多用 `wait_for` 驱动、视频号与微信支付日期控件特殊、有赞走 URL 带日期参数，这 5 个保留逐步写法（强套骨架会改变操作）。 |
| `tests/` | 52 个文件约 8659 行（含 `conftest.py` 的 CI 依赖自举） | pytest 测试（**589 项**）：日志与统计尾部读、加载器、保活、平台管理、重试、调度、导出结果落库、界面线程模型、平台调用序列与骨架迁移、有赞日期、录制生成器、文件汇总与原子写、对话框构造、商户测试窗口、文字点击的精确性与歧义提醒、崩溃兜底与启动器找 Python 的五档顺序、商户增删改与查重、平台勾选联动、日期区间校验、过期文件清理、首次登录"关窗口即完成"的等待与核实、关浏览器前保存登录态（含"更空的一份不覆盖"守卫与原子写）、导出前登录预检（一次弹窗/集中重登后按下标剔除/没能核实不拦人/定时任务不预检）、下载归位与单次归档、日期未填入即停手、下载文件命名（前缀+原始名/扩展名原样/幂等/子商户档）与成品格式门、工作空间解析与打包白名单、CI 里测试依赖从包内借（conftest 的挂载顺序）、依赖三档体检与修复命令、核心版启动器找 Python 的三档与商店占位桩、两个版本的成品结构、启动前依赖弹窗的三种走法（缺包可退、缺内核放行、版本不对只问）、子商户清单与归属比对、一次登录切着导多份的循环与「归属没确认就停手」。**2026-09-26 本轮新增**：兜底扫描不认领 `历史/`（含"真下载仍要认领"的反向钉）、多 sheet xlsx 数行、定时任务编辑与 30 秒重载的竞态（真 Tk）、任务文件写不进盘的内存兜底与出声、cron 反向区间与永不成立的表达式、`wait_user` 的四种回话与"没人应答≠已确认"、归档失败/旧件被占用时两份文件都保留。 |
| `start.bat` / `启动工具.vbs` | 40 / 124 行 | 源码版的启动脚本（vbs 五档找 Python；**必须保持纯 ASCII**，见 13 章）。它**不再**自己检查/安装依赖 —— 那个判断只在 `core/deps.py` + `check_dependencies()`（见 12 章第 29 条） |
| `requirements-dev.txt` | — | 开发依赖（pytest，已装入 `.venv`；`python -m pytest -q` 或全局 `py -m pytest -q` 均可，全套约 4.5 秒） |
| `使用说明.md` / `脚本编写指南.md`  | —             | 用户文档 / 开发文档                     |

> 本表末尾原有四行是历史遗留的重复条目（`recording_to_script`/`platforms`/`start.bat`/两份文档各写了两遍），本轮清掉；改规模数字时以 `wc -l` 为准，别照抄旧值。

## 12. 全仓硬约束（改这些之前先读）

这些约束大多是"踩过坑之后写进注释"的，破坏任何一条都不会立刻报错，而是让用户拿到错账单或者程序悄悄失灵。

**浏览器与登录态**

1. 一个 Chromium profile 同时只能被一个进程占用 → 任何"换个浏览器再检查"的动作，必须先 `_close_browser()` 释放再重开（`_login_one_round` 里"核实排在关浏览器之后"就是这个原因）。
2. **`plat.check_login` 会导航页面**（打开 `export_url` 看是否被重定向），所以绝不能在用户正在登录/扫码的过程中轮询它 → 等待期只看 `login_signature()`（cookie 条数+指纹，不触发导航）。
3. persistent context 在 Chromium 退出时**不保留 session cookie**（微信支付就靠它）→ 登录态必须"边等边存"，并在 `close()` 第一件事导出；"关窗那一刻"既存不了也查不了是物理约束，不是实现缺陷。
4. `close()` 前存登录态有"更空的一份不覆盖"守卫：live cookie 数比已落盘的少就不写（防某页面清 cookie 把上次的登录态换成空的）。
5. `start()` 里 launch 成功之后才抛错也必须 `context.close()` + `playwright.stop()`，否则活的 Chromium 占死 profile、下一次启动被误判成 "Sync API inside asyncio loop"。
6. `window_closed()` 在"从未 start()"时也返回 True，不要当"用户关掉了窗口"的等价信号用。

**下载、校验与交付**

7. 必须先 `_finalize_download` 归档再校验，且校验**先认文件头、再数数据行、后看错误文案**：顺序反了会把备注里写着"操作失败/请登录"的真账单当错误页删掉；先删后归档会出现"日志说下载完成、磁盘上找不到文件"。"数行"之前还要有一道**HTML 文件门头**——会话过期时后台常把登录页以 `200 + Content-Disposition: xxx.csv` 发下来，而 HTML 标签本身就占行，按逗号数得出行的登录页会被数行那道门直接放行（交出去一份网页却记 success）。
8. 兜底扫描（路 2）与事件队列（路 1）口径不同：一个递归看整个 downloads、一个只看根目录且要求 mtime 在 `_dl_capture_t0` 之后。往 downloads 根目录写文件的方法只有 `screenshot()`，它正是"截图被认领成账单"事故的源头。
9. 归属不明的 UUID 残留一律原名原字节进 `downloads/待确认/`，**不按当前上下文改名归到某个商户目录**——对账最怕拿错人的账单。`待确认/`、`browser_data/`、账单原件、`stats.jsonl` 在任何清理逻辑里都不得碰。
10. 日期没全部设成功就必须停手，不许继续点查询/导出：骨架 `run_standard_flow`、京东/拼多多的手写检查、视频号 `_set_time`、微信支付 `_set_dates` 五处都遵守（后两处曾把返回值丢掉过）。另一端还是页面默认区间时，硬导会得到一份错区间的账单，而文件名看起来完全正常。

**线程与界面**

11. Tk 控件/变量只能在主线程读写。任务线程需要的东西**一律在主线程取好作参数传入**（tasks、日期、单步、预检开关、重试次数读 settings.json、保活读 `selection` 镜像）。
12. 界面更新唯一通道是 `_ui` → `_pump_ui`（60ms、每轮 ≤300 条、单条异常不停泵）。**主线程调 `_ui` 是就地同步执行**，所以任何"直接弹 messagebox"拿返回值的写法搬到工作线程都会失效——必须换成 `answer` dict + `Event` + 有 `finally: set()`。
13. `running` 的检查+置位必须在 `_task_lock` 内；所有长任务入口都走 `_run_async`。
14. 中止只在**商户/步骤边界**生效（`_aborted()` 在各循环开头），不许中途掐浏览器——那会留下半截下载文件。
15. 保活/调度线程与 `CronScheduler` 靠**属性名**跟 app 打交道：`set_platform_status`、`iter_selected_merchants`（`__iter_merchants` 的公开别名）、`login_urls`、`running`、`selection`、`trigger_job` 的返回值。改名或把 `trigger_job` 改成无返回值都会静默失效（`getattr(..., None)` 兜底，不会报错）。

**任务、数据与脚本**

16. `settings.json` / `scheduled_tasks.json` / `selection_state.json` 三个"整体重写"的文件必须走 `write_json_atomic`，读侧才有"截断≠清空"的保证。
17. `save_settings` 以现值为底只覆盖传入的键；`load_settings` 只认 `DEFAULT_SETTINGS` 里的 8 个键。
18. 新建/编辑定时任务必须挪 `last_run` 起算点（`CronJob.new` / `apply_edit`），否则保存后 30 秒内就会跑一次。
19. 平台脚本改动要免重启生效，`reload_platforms` 必须同时清 `sys.modules`、importlib 的 stat 缓存和**磁盘上的 `.pyc`**（字节码头存"截断到秒的 mtime + 字节数"，同秒等长改动会执行旧代码）。
20. 平台脚本要保存成功才算数：先 `compile()` 再原子写（否则坏文件落盘，loader 静默跳过该平台，用户原本能跑的脚本被毁）。
21. 骨架生成的 key 必须小写字母/下划线开头（数字开头会生成非法类名，目录还留着 → 死路）。
22. `启动工具.vbs` 必须保持**纯 ASCII**且各档找 Python 的原文不能被改动（`tests/test_launcher_vbs.py` 以 ascii 解码并按每档原文精确替换）；档位顺序里"写死路径排第一"是有意的——现在能用的机器不许悄悄换解释器。
23. 平台显示名与平台 key 是两套标识：归档目录、`stats.jsonl`、汇总用**显示名**，profile 目录与删除用 **key**。给平台改名会同时断掉历史统计与旧归档。

**绿色版与工作空间**

24. 路径分两类：程序目录可只读，**工作空间必须可写**（`_probe_writable` 用探针文件判定，不看 `mkdir` 成不成功）。
25. 判断"这份拷贝用过没有"只能看三个 json；`logs/`、`downloads/`、`browser_data/` 是跑一次就会出现的副产品，拿它们当痕迹会让新包不再问用户，把账单和登录态写回可能只读的程序目录。
26. 换工作空间必须重启进程（`main_gui.relaunch_for_workspace`）：模块常量是取值拷贝，就地改不传导；书签写不进程序目录时靠环境变量把路径递给下一个进程，否则会陷入"每次启动都问一遍"。
27. 绿色包只能白名单组装（`tools/build_portable.py` 的 `APP_ITEMS`）：整目录复制 + 排除项迟早会把 `browser_data`（明文会话凭证）发出去，所以成品还有一道 `scan_forbidden` 兜底。

**依赖体检与两个版本**

28. **附加检查不许拦住业务**：`deps.probe()` 自身出错 → 结论 `unknown` 并照常启动，只把"吞了什么"写进 `notes`；`check_dependencies()` 里只有"连 playwright 包都没有"这一种情况会让程序退出（那时确实跑不动），版本不对、内核没下都是**只问不拦**。
29. **"缺什么/怎么装"的判断只许有一处**（`core/deps.py` + `main_gui.check_dependencies()`）。三个 `.vbs`/`.bat` 启动器只负责挑解释器——它们各自判断过一次依赖时，一处钉版本一处装最新，而内核目录名带版本号，混版本直接起不来（`tests/test_launcher_vbs.py::test_nothing_in_the_launchers_installs_dependencies` 钉着这条）。
30. 同一个 playwright 版本钉在**三处**：`core/deps.REQUIRED_PLAYWRIGHT_VERSION`、`requirements.txt`、工作流的 `PLAYWRIGHT_VERSION`。前两处由测试核，三处齐全由 CI 那一步核（见 14.2）。
31. 判断内核在不在**不许写死子目录名**：`chrome-win` 在新版本里叫 `chrome-win64`，先认 `INSTALLATION_COMPLETE` 标记、再逐个子目录找 `chrome.exe`；`chromium_headless_shell-*` 不算内核（我们用持久化上下文）。体检解析出的内核目录**不回退**到 Playwright 默认位置——运行时 `PLAYWRIGHT_BROWSERS_PATH` 已按解析结果设好，回退会让体检和实际行为各说一套。
32. 启动器的 `--print-python` 诊断分支必须**排在任何会弹窗的检查之前**且自己绝不弹窗：测试/支持人员是在"未必是一个完整包"的目录里问"你会挑中哪个解释器"，一个模态框就能把调用方吊死（`run-core.vbs` 第一版真这么挂过一次）。

**子商户（一次登录导多份）**

33. **归属没确认就不许点导出**。`_switch_sub_merchant` 里"切换失败"和"页面读回来不是要导的那一家"都必须让整家停手转人工——省登录次数顺带省掉的正是"人眼看着切对了才按导出"那道人工确认；机器这边不守住，结果就是把 A 的账单记到 B 名下：文件是真的、日期是真的、校验也过得去，只有归属是错的，查起来最费工夫。平台没实现读回时**放行**但必须写一句"归属未经校验"（读不到 ≠ 读对了，可也不能因为没实现读回就彻底用不了）。
34. 归属比对只认**独立出现**：`submerchants.matches` 归一化后相等，或一方作为独立片段出现在另一方里（边界只认 ASCII 字母数字——`8234000540已激活` 算命中，`98234000540` 里的 `9` 不算）。少了这条，录错的短号 `8234` 会因为页面显示 `8234000540` 而"匹配通过"。全角→半角必须逐对写映射表：`zip` 两个字符串一旦长度不齐，会把数字**静默翻译**成另一串数字（本次真踩过，测试里钉了一条 `８２３４ → 8234`）。
35. 子商户这一档的重试挂在**每个子商户自己身上**，整家汇总永远不返回 `failed`（全失败也报 `manual`）：外层 `run_with_retry` 若把整家重跑，成功的几家会再导一遍、往归档里堆 `历史/`，反而看不出哪家真没出来。统计按 `主商户/子商户` 记一条。取值口径必须与 `_execute_export_tasks` 一致，包括"间隔 0 就是 0"（写成 `int(x or 30)` 会把 0 变成睡满 30 秒）。
36. 子商户名会直接成为 `downloads/平台/商户/` 下的一级目录名，所以在 `set_export_context` 就要过 `_safe_name`，清单清洗时还要挡掉 `.`/`..`/纯点下划线的名字——`sanitize_name` 不吃它们，留着就是目录逃逸。汇总目录那侧另有 `summary_name` 防同名静默覆盖（见 5.8）。

**校验与登录判定的口径（2026-09-26 补）**

37. **"是不是账单"只许看文件头，不许拿全文文案判生死**。HTML 那道门必须排在"数数据行"**之前**（登录页存成 `.csv` 时按逗号数得出行），而它只比对跳掉 BOM/空白后的开头若干字节；`_match_error_keyword` 只在"读不出数据行"时用，且两侧都小写。反过来把这两条合起来写成一个"全文找关键字"的判断，就会退回到"备注里一句'请登录'把真账单删掉"的老事故（第 7 条）。
38. **登录判定的等待只许往宽、不许往严**：`check_login` 现在按 `LOGIN_POLL_STEP/MAX/STABLE` 采样看 URL，落地即返回。要调参数就调 `LOGIN_POLL_STABLE`（越大越接近旧的 3 秒死等），但**不许改回 `wait_for(timeout=3)` 这种不传条件的写法** —— 它必然耗满预算还会打一条假的"等待超时"WARN。判成"未登录"会把本该到手的账单挡掉（第 28 条同一条红线），判成"已登录"最多白跑一次，所以拿不准时取后者，`tests/test_check_login_wait.py::test_stability_window_is_a_documented_trade_off` 就是钉这个方向的。
39. **状态灯宁可漏报也别误报**。保活巡检现在只看 URL 里有没有 `login`，页面标题带"登录"字样只写日志不置红 —— 灯一旦因为误报失去可信度，用户就不再信它了。同理 `deps.probe()` 自身出错折成 `unknown` 放行（第 28 条）。
41. **`browser_data/<平台key>/` 这一层只放商户目录**。往这一层启动一个 Chromium（= 商户为空时的旧行为）就等于让浏览器把 `Default`/`Crashpad`/`Safe Browsing` 写进「商户名单」—— 它们会被列成商户、被自动勾上、每次批量任务各起一次浏览器。三条一起才成立：① 没有商户时用保留目录 `_平台调试`/`_未指定平台`，默认 `_profile_dir` 也不是 `browser_data` 根；② 商户发现剔内部目录**必须先确认这个平台目录自己当过 profile 根**（顶层有 `Local State`/`Last Version`），否则用户手工拷进来搬登录态的目录会被静默吞掉——那比"多出几个假商户"更糟；③ 剔了谁要写日志说出口，两个保留名不许拿来建商户。
40. **cron 缺失/非法 = 这个任务不跑，并且要说一次**。`from_dict` 不许再给空 cron 兜 `* * * * *`（那是每分钟起一次浏览器）；`CronScheduler._warn_once` 按任务只提醒一次、整段包在 `try` 里，提示本身绝不能把调度线程带下去。用户显式写 `* * * * *` 是他的选择，照旧生效。⚠ 这个"只说一次"**不许按 `job_id` 记**：json 里没写 `id` 的条目，`from_dict` 每次 load 都新生成一个随机 id，而 `check_all` 每轮都 load —— 按 id 记等于每 30 秒刷一遍同一句（现在按「名字 + cron + 毛病」记）。

**下载、校验与交付（2026-09-26 廿三轮补）**

42. **兜底扫描（路 2）的候选必须与 `_find_new_candidate` 同一口径**：按 `_dl_capture_t0` 过滤时刻，并排除躺在 `临时/`、`历史/`、`待确认/` 下的文件。"`_dir_snapshot` 的差集"里出现的新文件**很可能就是本轮自己刚写进去的**——`_finalize_download` 在校验之前就把同名旧版复制进了 `历史/`，于是"本轮校验失败"与"兜底认领了上一版真账单"发生在同一个 while 循环里，日志、`stats`、文件名全都像是成功。要排除谁按**路径分段**判，别按文件名前缀（`历史商户/`、`历史.csv` 都是用户的正常目录与文件）。
43. **删除只许发生在"另一份已经落定"之后**。往 `历史/` 的复制没成功就不许删顶层那份唯一的旧账单；旧件被 Excel 占用删不掉时，本次成品换 `_原件保留_HHMMSS` 的名字落下，两条路都要写一行 warning。同理 `_accept_download` 里任何文件系统意外都折成"这一份没认领成"返回 None——异常冒出 `wait_download` 会把 `_dl_capture_on` 卡住、成品留在 `临时/` 里被下一次 `begin_wait_download` 删掉（明明下成功却报 failed，越重试越丢）。
44. **判空表不许只看第一个工作表**。`_xlsx_row_count` 必须遍历所有 sheet 取最大值："汇总页在前（只有一行表头）、明细页在后"是账单 xlsx 的常见结构，只数首页会把一份好文件判成空表并删掉，表现为"这个平台永远下载失败"。

**交互与安全（同轮补）**

45. **"没人应答"永远不许折算成"用户同意"**。`wait_user` 的回调必须回话，True 只来自用户亲手点"确定"；取消、弹窗起不来、等待超时、回调自身异常都是 False，平台脚本拿到 False 必须停手转人工（视频号那条路以前会照常点"下载数据"，拿到空文件却记 success）。这与第 33 条"归属没确认就不许点导出"是同一条红线。工作线程等主线程弹窗时，等待要**分段**并每段看一眼 `_aborted()`，弹窗本身要 `topmost`（Chromium 窗口常常盖在主窗口上面）。

**任务与配置（同轮补）**

46. **`TaskStore.load()` 必须对同一 `job_id` 复用内存里已有的对象**（`sync_from_disk` 只搬盘上字段），而界面保存前必须**按 id 回查当前列表**。`check_all` 每 30 秒 load 一次，换成新对象会让 `JobEditDialog` 握着的引用变孤儿——`apply_edit` 改在孤儿上、`save()` 序列化新对象，改动静默丢失且界面显示回旧值。**写盘失败必须有内存兜底**：`mark_triggered` 在 `save()` 返回 False 时把 `last_run` 记在内存（按"名字\|cron"，不用 id），`should_trigger` 取盘上与内存中**较新**的一份；否则下一轮 load 读回旧值 → 同一任务每 30 秒重跑一整批。`write_text_atomic` 的暂存名必须唯一（`path.tmp-<pid>-<n>`）并对占用退避重试，`TaskStore.save`/界面保存失败都必须说出口——这几个文件的读侧是 `except → 用默认值/返回空`，静默失败等于骗人。
47. **cron 里"读不懂"的东西一律报错，不许折成"这个字段不设限"**。`_parse` 末尾是 `values or None`，而空集就是 `None`——所以反向区间（`5-1` → `range(5,2)` 为空）、越界起点（`70/5`）、非正步长（`*/0`）若不检查，`0 9 * * 5-1` 会静默变成**天天跑**。语法正确但永不成立的日期（`0 9 30 2 *`）要当场拦下并说清（`impossible_date()`），且 `next_run` 的"一年无解"结论要缓存（一次年扫约 460ms，调度线程每 30 秒一次）。判"永不成立"必须避开两个坑：日/周同时受限是 OR 语义（`0 9 30 2 1` 靠每周一会跑），2 月按闰年 29 天算（`0 9 29 2 *` 是四年一次）。

***

## 13. 已知不一致、未接线与待议

> 这一节是把"读代码时容易当成 bug、其实要么是有意的、要么确实坏着"的地方摊开。**有意为之的别顺手清理，确实坏着的改动前先确认。**

### 13.1 确实失效/会出错的地方（2026-09-25、2026-09-26 三批已全部修掉，留此备查）

| 位置 | 曾经的现象 | 修法与提交 |
| --- | --- | --- |
| `core/cleanup.py` `LOG_FILE_RES` | 白名单只认按天名 `^run_\d{8}\.log$`，而 logger 写的是 `run_YYYYMMDD_HHMMSS.log` → 「日志保留(天)」对运行日志一直没生效（实测本机 189 个日志里 187 个永不被清） | 白名单收进按启动时刻那一种格式；`44edef8` |
| `core/browser.py` `_carry_over_orphans` | 不过滤中间态后缀 → 去搬浏览器还在写的文件，Windows 上 `shutil.move` 必失败，而失败被外层 `except` 吞掉、**整轮归位中止**，本该搬走的残留也留在根目录 | 先跳 `PARTIAL_DOWNLOAD_SUFFIXES`，再逐个 try，搬不动的写「留到下次」；`3a64b72` |
| `core/browser.py` `save_login_state` | `open("w")` 先截断再序列化 → 一旦失败就把上次的登录态换成空文件，下次启动恢复不出登录 | 先 `json.dumps` 再 `config.write_text_atomic`；`e960f08` |
| `core/browser.py` `_take_new_download` | 返回**已归档**路径，`_accept_download` 又归档一次 → `download_name_mode=original` 时多叠一层商户前缀；方法名也与实现不符 | 改名 `_wait_root_download`、只等写完不归档（归档统一在 `_accept_download` 做一次）；`4c27687` |
| `core/platform_base.py` `export` | `SmartExporter(browser)` 没传 `log_callback` → 默认导出全程静默 | 传 `log_callback=browser._log`；`1b0c29c` |
| `platforms/shipinhao`、`platforms/wechatpay` | `_set_time` / `_set_dates` 的 bool 返回值没人看 → 日期没设上照样点查询/导出，会得到页面默认区间的账单却按本次区间命名 | 两家都补「停手 + `[中止]` 日志 + `snapshot(日期未填入)` + 转 `manual`」；微信的回退分支改为如实返回两条 placeholder 的结果；`6f023bb` |
| `core/browser.py` `_validate_download` | 会话过期时后台把登录页以 `200 + Content-Disposition: xxx.csv` 发下来，而"先数数据行"那道门按逗号数、HTML 标签本身就占行 → **数得出 4 行的登录页被当成品放行**，日志报 success、`stats.jsonl` 记成功，交出去的是一份网页（实测：5 行 HTML 存成 `.csv` → `_count_rows=4` → `_validate_download=True`） | 数行之前加 `_looks_like_web_page` 文件门头；文案匹配改大小写不敏感；`8b0f075` |
| `core/platform_base.py` `check_login` | `browser.wait_for(timeout=3)` 不传条件 → 已登录/未登录两个分支都必然等满 3.00s（实测），收尾还固定打一条"等待超时"WARN；预检 + inline 预检每家两遍，10 家商户 60 秒白等 + 20 行假警告 | 改成 `LOGIN_POLL_STEP/MAX/STABLE` 采样、落地即返回，不再借道 `wait_for`；`e8a1241` |
| `core/keepalive.py` `run_once` | `"login" in url or "登录" in title` —— 正是 `check_login` 注释点名抛弃的旧判法，后台已登录而页面名仍带"登录"字样时把状态灯判红，于是"灯红着、导出却一切正常" | 判失效只看 URL，标题命中只写一行"只当提示不判失效"；`18d9b2f` |
| `core/scheduler.py` `CronJob.from_dict` | 缺 cron 的条目兜 `* * * * *` = **每分钟起一次浏览器导账单**，而界面新建/编辑都有校验、只有手改 `scheduled_tasks.json` 漏写这个键的人会把整台机器点着 | 空 cron 原样留着（`CronExpr` 解析不过 → 该任务不跑），调度线程按任务写一句"不会被执行"且只说一次；`ea5b7a1`。第一次提交的记号用 `job_id`，而 `from_dict` 对没写 id 的条目**每轮 load 都新生成一个随机 id** → 变成每 30 秒刷屏，改按"名字+cron+毛病"记；`5529689` |
| `core/browser.py` `set_browser_profile` + `core/main_gui.py` `discover_merchants` | 商户为空时 profile 目录就是 `browser_data/<平台key>/` 本身（脚本调试那条路 `_ensure_browser(plat)` 不传商户），Chromium 直接往这一层写下 `Default`、`Crashpad`、`Safe Browsing` 等内部目录，而商户发现的规矩是「这一层每个子目录=一家商户」→ 内部目录被列成商户并自动勾选。本机实测 `discover_merchants(["youzan"])` 返回 10 项、只有 `test` 是真的，`selection_state.json` 里那 9 项全是 `true`：「检查登录状态」与批量导出会为它们各起一次浏览器，商户数徽标与定时任务「对象」项数一起骗人 | 空商户时垫一层保留目录 `_平台调试`（连平台都没有时是 `browser_data/_未指定平台`），有商户时路径逐字不变、存量登录态不搬家；商户发现按「先内容后名字」认内部目录，且**只在这个平台目录本身当过 profile 根时**才启用；启动与「刷新商户」各写一行「已忽略 N 个浏览器自己的目录」；两个保留名不许拿来建商户；磁盘上的旧残留一律不动。`d798c46` + `55e99f3` |
| `core/main_gui.py` `_action_help` | 弹窗仍教"在浏览器完成登录并点『确定』"，而第九轮起登完的标志是**关掉浏览器窗口** —— 用户照旧文案在没登完时点掉窗口，正是那次改造要治的病 | 文案跟回现流程并补上预检/「中止」，加 `tests/test_action_help_text.py` 钉住；`a62a7ed` |
| `core/browser.py` `wait_download` 路 2 | 兜底扫描只做"`_dir_snapshot` 递归差集 + 跳半成品后缀"，**不按 `_dl_capture_t0` 过滤、也不排除本轮自己写进 `历史/` 的那份** → 同名重导 + 本轮拿到登录页时：校验失败删掉本轮文件，同一个 while 循环把刚归档进 `平台/商户/区间/历史/` 的**上一版真账单**搬回顶层认领，通过全部校验、日志"下载完成并校验通过"、`stats` 记 success，而 `历史/` 被搬空（离线实测：返回值逐字等于上一版） | 候选改走 `_new_download_candidates`：与 `_find_new_candidate` 同口径（t0 + 内部目录 `临时/历史/待确认` 排除，按路径分段判）；顺带补上"没 begin 就 wait"时漏记的 t0 与临时目录。附一条反向用例钉住"真下载照样要被兜底认领"；`54e1002` |
| `core/browser.py` `_xlsx_row_count` | 只数 `namelist()` 里第一个 `sheetN.xml`。手搓"汇总页 1 行 + 明细页 120 行"的 6.2KB xlsx 实测：`_count_rows → 0` → `[校验] 表格无有效数据行` → `_accept_download` 直接 `os.remove`，**一份好账单被判成空表删掉**，表现为"这个平台每次下载都失败" | 遍历所有 sheet 取最大值（`a367bda`）；另钉一条"每页都只有表头仍是空表"，防止改宽变成什么都放行 |
| `core/scheduler.py` + `SchedulerDialog`/`JobEditDialog` | `check_all` 每 30 秒 `store.load()` 且每轮换成**全新对象**，而对话框点"编辑"时抓的是旧对象 → 用户改商户名/挑平台超过 30 秒，`apply_edit` 改在孤儿上、`save()` 序列化新对象 → **改动静默丢失**，界面 `_reload` 当场显示回旧值、日志零提示，到点仍按老商户导出（实测真 Tk + 真 JobEditDialog） | `load()` 对同一 `job_id` 复用对象（`sync_from_disk`，盘上内容仍然胜出）+ 保存时按 id 回查、查不到就弹窗拒绝（不把已删的任务复活）；`a0cc95c` |
| `core/scheduler.py` `TaskStore.save` + `core/config.py` `write_text_atomic` | `except Exception: pass`。任务文件被云盘/Excel/杀软占住时 `last_run` 落不了盘 → 下一轮 load 读回旧值 → **同一任务每 30 秒重跑一整批**（实测注入 PermissionError：5 轮 = 5 次触发），日志一个字都没有。另：暂存名固定 `path+".tmp"`，8 线程同写一个 json 实测 48 次里 38 次抛 WinError 32，全被上层咽掉 | 写失败时 `mark_triggered` 把起算点兜在内存（记号"名字\|cron"，不用每轮都会变的 job_id），`should_trigger` 取盘上/内存中较新的一份；调度与界面各说一次，界面没落盘就不关窗。暂存名改 `path.tmp-<pid>-<n>` + 对占用退避重试（只换唯一名仍剩 5/8 失败，加重试才每次都落盘）；`2a5f0e4` |
| `core/main_gui.py` 等用户扫码 + `core/browser.py` `wait_user` | `evt.wait(timeout=3600)` 的返回值被丢掉、回调不回话，而 `wait_user` 一看回调返回就写死"用户已确认,继续" return True → **手机没扫码也会点"下载数据"**（空文件/旧文件记 success）。提示是 `showinfo` 且无 topmost（常被最大化的 Chromium 挡住），模态框还压着主窗口让「中止」点不动；回调抛异常那条会掉下去再睡满 timeout（默认 600 秒，本轮做变异实验时被这个形状真吊死过一次） | 抽成 `LiushuiApp._ask_user_confirmed`：`askokcancel` + topmost + 分段等（每秒看 `_aborted()`）+ `USER_WAIT_TIMEOUT_S`=10 分钟，True 只来自用户点"确定"；`wait_user` 照实返回 bool，取消/弹窗失败/超时/异常一律 False；视频号拿到 False 截图 + 转 manual，不再 `begin_wait_download`；`27a928a` |
| `core/scheduler.py` `CronExpr._parse` | `-` 分支不查 `a<=b`，`range(5,2)` 是空集，末尾 `values or None` 又把空集判成"不设限" → `0 9 * * 5-1`（本意周五到周一）实测**七天全命中**，`cron_problem()` 也说没问题；`0 0 1 11-2 *` 变成每月 1 号。另一半：`0 9 30 2 *` 解析得动却永不成立，`next_run` 走满 52.7 万次才返回 None（实测 460ms）→ 任务静默不跑 + 调度线程每 30 秒白烧一次 | 反向区间/越界起点/非正步长一律 `ValueError`（界面当场拦 + 调度说一次，跨周末教人写 `5-6,0-1`）；新增 `impossible_date()`（避开 OR 语义与闰年 2/29 两个误报口）；`_NEVER_MATCHING` 缓存无解结论；`f2de521` |
| `core/browser.py` `_finalize_download` | 往 `历史/` 的 `copy2` 整包 `except: pass`，紧接着**无条件** `os.remove(顶层旧件)` → 归档失败（路径超 260 字符、磁盘满、杀软拦）等于把上一版账单凭空删掉，全树搜不到、日志零条，而用户以为 `历史/` 有备份；兜底那句 `shutil.copy2(path, final_path)` 自己没包 try → 旧件被 Excel 占用时 PermissionError 冒出 `wait_download`，`_dl_capture_on` 卡在 True、成品留在 `临时/` 被下次 rmtree 删掉 | 归档失败/删不掉 → 保留旧件，本次成品落 `_原件保留_HHMMSS` 并各写一行 warning；`_accept_download` 整体包 try，文件系统意外折成"这一份没认领成"；`7f2e494` |

> 同批还修了序列快照 harness 的一处失真：`tests/test_platform_sequences.py` 的 `Recorder.page` 以前返回记录函数，平台脚本里 `browser.page.locator(...)` 取属性直接 AttributeError、被脚本自己的 try 吞掉，于是录出来的是"页面操作整段失败"那一支（微信支付停在 `assert_selector` 结果 `failed`、视频号停在 `_set_time`）。给 `page` 一个可链式哑对象后两家录到完整流程（16→18、11→29 次调用，结果 `failed`→`success`），两条快照按新口径重录。

新发现的问题继续往这张表里加：**每条都要有"现象"和"为什么算问题"**，不要只写"这里可以更优雅"。

### 13.2 口径不一致（两处逻辑对同一件事给出不同答案）

- **`SmartExporter.export` 的日期策略**：`filled==1` 直接转 manual，但 `filled==0` 继续硬试；基类骨架是"任一框没填上就停手"。
- **`snapshot()` 失败返回 None，`screenshot()` 失败仍返回路径**——两个相近名字语义相反。
- **`_is_login_url` 的短 token**：`cas`/`sso` 是子串匹配，普通业务路径（`/purchase_case`）可能被误判为未登录。2026-09-26 把 11 个平台的 `export_url`（含查询串）逐个核过一遍，**没有任何一家命中这些 token**，所以这条目前只是理论风险，别为它改判据；接新平台时如果它的落地地址里带这类词再回来看这里。
- **保活与导出侧仍不是一回事**：`keepalive.run_once` 打开的是 `login_url`（顺带刷新会话），`check_login` 打开的是 `export_url`（看会不会被踢），两边 URL 本就不同。2026-09-26 统一的是**判据的严格程度**（保活不再因为页面标题带"登录"就置红），不是把两次导航合并——合并就要在保活里多起一次浏览器，不值。
- **`record_stat`/汇总用显示名、profile 用 key**（见 12 章第 23 条）。

### 13.3 有意停用/预留，**别当死代码清掉**

- `close_popup()` 首行 `return False`，下面约 70 行扫描逻辑保留备用（注释 1006 写明"需要时删掉这一行即可恢复"）；全仓 17 处调用点是刻意留的，别顺手删。
- `latest_export_dir()`、`__enter__/__exit__`、`quick_export()` 目前无调用点，属预留 API。
- `_execute_export_tasks(label=...)`、`_ask_login_retry(attempt=...)`、`check_selectors(timeout=...)`、`_cleanup_stale_files(keep=...)`、`_normalize_download_name(merchant=...)` 这些形参当前未被使用，但调用方/测试按关键字传，删签名会连带炸。
- `trigger_job` 走 `_execute_export_tasks` 而**不经过 `_do_export`**，所以定时导出既不做登录预检、也不做汇总复制/打开文件夹。不预检有注释背书；不做汇总只是顺带的结果，要不要补待议。
- `_merchant_memory` 只在内存：重启后第一次"取消平台→勾回"仍会走整平台全选。旧行为有意保留，是否持久化待议。
- `_IMAGE_MAGICS` 里的 `b"IDNA"` 对不上任何常见图片格式（疑似笔误），只会造成"以 IDNA 开头的文件不被当账单"，无实际危害，未动。
- `tests/test_launcher_vbs.py` 会在仓库根生成并删除 `_probe_launcher.vbs`（已 gitignore）。

### 13.4 文档/注释落后于实现

- 2026-09-26 已把这几处对齐并加了测试：`_action_help()` 的"登录并点『确定』"改成"关掉浏览器窗口就代表登完"（并补上登录预检与「中止」的说明，`tests/test_action_help_text.py` 钉住文案）；`browser.py` 里 `_finalize_download` 的 `his/` 改成实际的 `历史/`，四处目录注释补上漏掉的商户/子商户层；"归档时扩展名已被改成 .xlsx"那句注释删掉（第十四轮起扩展名原样保留）；快手/小红书 docstring 标清"方式B 未实现，记着备用"；支付宝把写死的"仅支持 2025-08-24 及以后"改成"后台可查区间是滚动的，别把某个日期当固定下限"。
- `使用说明.md` 第 4 节列出的"11 个平台均已内置"是对的；本文档以前写"仅实现有赞"的部分已在本版全面更正（见 1、2、3、8 章）。

### 13.5 已查到、**还没动手**（等用户点头）

| 位置 | 现象 | 为什么算问题 |
| --- | --- | --- |
| `core/main_gui.py` `_show_login_hint` | 首次登录提示窗写的是「平台 · 商户」，没有「第 i/N 家」，也没有这个商户对应哪个登录账号；Chromium 窗口标题跟着网页走，任务栏里看不出在登谁。 | 一个平台勾了 3 个商户时，同一个提示窗会连着弹 3 次、内容只有商户名不同，用户很难确认自己现在登的是第几家、刚才关的是不是同一个账号 —— 结果常是"三家都登成同一个账号"，而三家用的是各自独立的 profile，另外两家其实是空的。 |
| `core/main_gui.py` `_step_cb`（单步调试） | 与本轮修掉的 `wait_user` 同一形状：`evt.wait(timeout=3600)` 的返回值被丢掉、弹窗不带 topmost，`_ask` 抛异常时默认 `choice=True`（继续）。 | 只影响"单步调试"这条路径（业务用户不勾），且默认"继续"比默认"停手"更合调试的意图，所以本轮**有意没动**。要把调试也统一成"必须回话"再改。 |
| `core/main_gui.py` `main()` 的 `_on_close` | 关主窗口时不查 `self.running`、不二次确认，直接 `scheduler.stop()`（内部 `join(timeout=5)`）+ `cleanup()` + `destroy()`。 | 导出进行中点 X，当前那一家直接蒸发：不记 `stats.jsonl`、日志里也不留"未完成"字样，`downloads` 根里会留一个 `.crdownload`（清理侧特意跳过中间态后缀，永远没人收）。加一次 `askyesno` 就能挡掉，但这是"改交互"，等你点头。 |
| `core/main_gui.py` `_run_async` | 在 `_task_lock` 里置好 `running=True` 之后，函数体还有一句主线程之外的 `for key in self.platform_vars: set_platform_status(...)`（注释位置 1085）。 | 字典由主线程 `_rebuild_platform_list` 重建，撞上就是 `RuntimeError: dictionary changed size during iteration`，而这个异常在 `_run_async` 里、**线程包装器之外** → `running` 永久为 True，之后每个按钮都报"已有任务正在执行"，只能重启进程。窗口很窄（只在定时任务触发的那一刻），修法是遍历 `list(self.platform_vars)` 或把状态灯重置搬进 `_ui`。 |
| `core/platform_base.py` `DOWNLOAD_TIMEOUT_S = 60` | 走骨架的 6 个平台只等 60 秒下载完成；`wait_download` 默认值是 120 秒，两边不一致。 | 后台"先生成报表、再给下载"的平台一旦生成超过 60 秒，这一家必然转 manual，而真文件随后落地时会被下一次 `begin_wait_download` 的归位扫进 `downloads/待确认/` —— 用户看到的是"失败"，东西其实在待确认里。**需要真实后台量一次**再决定改哪个数（这类"页面等待"没有离线证据）。 |

***

## 14. 绿色版与工作空间

### 14.1 一条界线

| 类别 | 内容 | 权限要求 |
| --- | --- | --- |
| **程序目录** `ROOT_DIR` | `core/`、`platforms/`、`tools/`、`site-packages/`（依赖）、`runtime/ms-playwright/`（Chromium）、启动器 | 只读即可 |
| **工作空间** `DATA_ROOT` | `downloads/`（账单）、`browser_data/`（登录态，**含明文会话凭证**）、`logs/`、`recordings/`、`settings.json`/`selection_state.json`/`scheduled_tasks.json`/`sub_merchants.json` | 必须可写 |

默认两者相同（就地模式），现有安装零迁移；全新的一份包第一次启动会问用户"账单和登录数据存哪儿"（`ensure_workspace` 在建界面之前跑，因为商户列表与日志目录都是 `LiushuiApp.__init__` 里按当前路径建的）。解析优先级与兜底见 5.9；选完/换完都要**重启一次进程**（`relaunch_for_workspace`），原因是各模块 `from .config import DOWNLOAD_DIR` 是取值拷贝。界面「工作空间」按钮可以看路径、打开它、换到别的文件夹；启动日志的 `_report_paths` 会说明工作空间在哪以及是否发生过兜底（书签指向的盘没了、日志退到临时目录）。

### 14.2 打包：两个版本 + GitHub Actions

一次构建出**两个包**，程序部分完全一样，区别只在带不带依赖：

    python tools/build_portable.py --out dist/全量版 --verify                        # full
    python tools/build_portable.py --out dist/核心版 --flavor core --verify           # core

| | 全量版 `-full` | 核心版 `-core` |
| --- | --- | --- |
| 里面 | 代码 + `python\` + `app\site-packages\` + `app\runtime\ms-playwright\` | 只有代码树（几 MB） |
| 双击 | `run-portable.vbs`（只用包内解释器，找不到就报错） | `run-core.vbs`（`%WINDIR%\pyw.exe` → PATH 的 `pythonw.exe` → `%LocalAppData%\Programs\Python\Python3xx`） |
| 排错 | `run-portable.bat` | `run-core.bat`（会先打印用的是哪个 Python） |
| 缺依赖时 | 不会缺 | 启动后 `check_dependencies()` 按三档引导安装 |
| 给谁 | **业务用户就给这个**，不需要装任何东西 | 已有 Python、pip 能通的机器 |

- **两版启动器文件名故意不同**：用户说"我双击的是 run-core.vbs"就能立刻判断他手上是哪一版；共用一个名字时这件事只能靠猜。核心版找解释器时跳过 `WindowsApps` 下 0 字节的 `pythonw.exe`（微软的"还没装，点开会跳商店"占位桩）。
- **`build()` 拒收错参数**：核心版拿到 `--python-dir/--site-dir/--browsers-dir` 直接失败中止——CI 里两版只差这三个参数，抄错太容易；另有一道 `check_flavor()`：成品结构与名义版本对不上（名叫 core 却躺着 `site-packages`/`runtime`，或包里混进另一版的入口）就中止，绝不发一个"看起来更小、其实一模一样"的包。
- **白名单复制** `APP_ITEMS = core/ platforms/ tools/ 三份 md`，不是"整仓复制再删"。新增的数据目录不会因为忘加排除项而被发出去。
- 成品里出现 `browser_data`/`login_state.json`/`downloads`/三个 json/`workspace.json` → `scan_forbidden` 直接失败中止（这些是各人工作空间里的东西，也是明文的后台会话凭证）。
- `--verify` **只 import `core.config`**：断言 `LIUSHUI_DATA_DIR` 被尊重、派生路径跟着走、成品里没有用户配置，并打印解析出的内核目录。⚠ 它把数据目录指到自己的临时目录，否则"只是验一下"就会在成品里留下 `logs/`。⚠ 自检里不许出现 `core.browser`（顶层就 import playwright）—— 那样"组装 + 自检"必须排在装依赖之后，核心版更是永远自检不了；依赖真能不能用是 CI 冒烟那一步的事。自检里那句 `assert 'playwright' not in sys.modules` 就是防以后有人顺手把重依赖请回来。
- 布局与启动器：`packaging/run-portable.vbs`（双击，纯 ASCII、只用包内 `python\pythonw.exe`）、`packaging/run-portable.bat`（同一件事但保留控制台，排错用）、`packaging/README-绿色版.txt`（给业务用户的说明）。
- **CI 的测试步骤不另装依赖**：playwright 只按 `--target` 装进包里，runner 全局没有它，而 9 个测试模块 import `core.browser`（它顶层就 `from playwright.sync_api import ...`）就需要它 → 第一次 CI 就是红在 collection。`tests/conftest.py` 把仓库根插到 `sys.path` 最前（以前只有"在仓库根 `python -m pytest`"这一种起法能 import core），包内依赖按 `build/*/app/site-packages` **追加到最后**：本机有真依赖就用本机的，不会被包内那份或本地跑过打包留下的半成品抢走；只挂 site-packages，绝不挂 `build/.../app`（那会让测试跑进打进包的代码副本里）。⚠ 改 `PKG` 目录结构要连带看 conftest；工作流里也补了一行"包内没有 playwright 就直接 throw"，别再留一个莫名其妙缺模块的错。
- **`test_launcher_vbs` 在 CI 机上会跳过其中一条**：runner 的 Python 在 `hostedtoolcache` 里，既不在 `.vbs` 的写死路径、也没注册 py 启动器，"五个档全落空"是脚本的正常行为，不该拿来点红打包构建；另加一步打印 `py --list-paths`，把这台机器到底有什么解释器留在日志里。CI 机没有 `.venv`、也没有用户数据 → 已用"`git archive` 出来的干净树 + 没装 playwright 的干净解释器 + 只有包内一份假依赖"实测：409 passed, 1 skipped。
- **runner 的 stdout 是 cp1252 管道**：Actions 的 windows runner 是英文区域，Python 按 cp1252 建 `sys.stdout`，第一次构建就死在 `print("组装绿色包 → …")` 上——`UnicodeEncodeError` 的 traceback，而不是打包结论。`build_portable.harden_streams()` 只在**当前编码写不出中文探针**时把流换成 UTF-8（探针 `PROBE = "组装→校验"`，连箭头一起测，只测汉字会放过 `→`）；简中控制台（cp936）写得出来 → 原样不动，免得花屏。流被框架换成没有 `reconfigure` 的对象时退到 `emit()` 的 `\uXXXX` 转义——**宁可丑，不能丢行**；`fail()` 在抛 `SystemExit` 前先调 `harden_streams()`，因为中文的报错信息自己触发编码错误是最坑的失败方式。工作流另设 `PYTHONIOENCODING=utf-8`（pytest 的失败报告也是中文的），但**故意不设 `PYTHONUTF8`**：那会连 `open()` 的默认编码一起改，让 CI 与开发机不一致。业务侧不受影响——`logger.log()` 的 `print` 本来就包在 try 里。

`.github/workflows/build-portable.yml`（`workflow_dispatch` 或打 `v*` tag，14 步）：`setup-python` 3.14 → 打印这台机器有哪些解释器 → **核版本**（`deps.REQUIRED_PLAYWRIGHT_VERSION` / `requirements.txt` / `PLAYWRIGHT_VERSION` 三处不一致就红：发出去的包不能是没实测过的组合）→ 组装两棵目录（`build/full --verify`、`build/core --flavor core --verify`）→ `pip install --target build/full/app/site-packages` → `PLAYWRIGHT_BROWSERS_PATH` 指包内后 `playwright install chromium`（**内核必须由同版本 playwright 生成**）→ 拷整个解释器目录进全量包（先断言 `Lib/tkinter` 在，界面全靠它）→ **全量包冒烟**：包内解释器 + 包内依赖 + 包内内核起一次真 Chromium，并断言 `deps.probe()` 在那棵目录里得出 `ok` → **核心版冒烟**：在没有随包依赖的解释器里跑体检，结论必须是 `no_playwright`（这一版靠引导安装活着，体检认不出缺什么就等于启动后一声不响）→ 跑仓库离线测试 → 压两个 zip → 一起进 artifact（tag 时同时发 Release，说明里写清"发给业务用户就给全量版"）。体积量级：Chromium 428M（`chromium_headless_shell` 另有 272M，随包只带 `chromium-*` 时需实测 headless 是否仍可用）+ playwright 108M + 解释器与代码 ~100M；核心版只有几 MB。

- **推之前先在本地彩排**（这条是两次红下来的教训）：CI 的失败要么是"某步偷偷依赖了上一步的产物"，要么是"runner 环境与开发机不一致"，两类都能在本地逼出来——`py -m venv` 建一个**没装 playwright** 的干净解释器 + `git archive HEAD | tar -x` 导出的**干净树**（CI 没有 `.venv`/`settings.json`/`downloads`），再把依赖目录假造出来跑一遍：核版本、两版组装+自检、核心版体检、借到依赖后不再报缺、全套测试。真需要联网装内核的那几步只能留给 CI。

### 14.3 子商户：主账号登录一次，切着导多份

银联这类平台是"一个主商户下挂多个子商户，账单要一份一份导"。老做法只能把每个子商户
建成一个独立商户条目，代价是**每家都要重新登录一次**（商户列表本来就是扫
`browser_data/<平台>/` 得出来的，商户 = 一份 profile）。现在改成一个商户条目 + 一份
profile，一次登录里按清单逐个切子商户、各导一份：

    界面「子商户」录清单(一行一个, 顺序就是导出顺序)
      → 每轮: switch_sub_merchant → current_sub_merchant 比对 → export → 归位
      → downloads/银联/主账号甲/8234000540/起_止/   (文件名也多一段子商户)

- **入口只在需要的地方出现**：平台得声明 `supports_sub_merchants`，商户行上才长出「子商户」按钮（按钮上直接显示已录数量）。清单存在工作空间的 `sub_merchants.json`，随包分发的那道 `FORBIDDEN` 收着它——它是业务档案，不是程序的一部分。
- **为什么必须"没确认就停手"**：省登录次数顺带省掉了"人眼看着切对了才按导出"这道人工确认。切换失败或页面读回来不是要导的那一家时，整家停手转人工并截图留证；平台没实现读回时放行但写明"归属未经校验"。详见 12 章第 33-36 条。
- **不做勾选树**：想少导几家就把那几行删掉。"选了但没导"是一种新的、只能靠界面同步的状态。
- **银联那份是实现样例**（`platforms/yinlian/export.py` 的 `SUB_*` 字面量）：切换走"点入口 → 搜索框填商户号（填不上就按列表文字精确点）→ 提交"。**这些字面量还没在真实页面上核过**，核的办法是用「平台管理 → 打开录制」照着切一遍；没核之前会发生的是"整家停手"或"提示归属未经校验"，不是拿错账单。测试里有一条专门盯着 `SUB_ID_SELECTOR == ""`，就是为了别让它看起来像已经可用。
- 一次登录切 N 家的其它代价：登录预检与保活仍按**商户**逐家跑（不会翻倍），但一轮导出会连着跑 N 次页面流程，中止按钮在子商户之间也会生效（剩下的家数记为需人工）。

***

*文档生成基于当前仓库代码。第 12、13 章的每条都对应到具体文件与行号；行号会随改动漂移，改完请同步维护本文档（第 11 章的规模列同理）。*
