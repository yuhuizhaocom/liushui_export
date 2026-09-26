流水自动导出工具 · 核心版（用本机已有的 Python）

这个版本和全量版的区别只有一句话：包里只有程序，没有 Python、没有 playwright、
也没有 Chromium。所以压缩包只有几 MB，但**这台电脑要能凑齐那三样**。

谁该用这个版本
  · 已经装了 Python 的同事/自己维护的机器；
  · 内网有 Python 源、pip 能通的机器；
  · 要把程序放到共享盘、又不想放几百 MB 的场合。
  给业务用户直接用的话，请给「全量版」—— 它什么都不用装，双击就开。

怎么用
  1) 先确认这台电脑有 Python 3.10 以上（开发与实测用的是 3.14）。
     命令行里输入 python -V 能看到版本就行。
  2) 双击 run-core.vbs。第一次启动程序会自己检查缺什么，并给出一键安装的选项：
       · 没有 playwright 包  → 装上（会钉住实测过的那个版本，不会给你装个新的）
       · 有包但没有浏览器内核 → 只补内核下载（一百多 MB，约 1-3 分钟，需要联网）
       · 版本和实测过的不一致 → 提示一句，你自己决定换还是继续用
     选"否"也能进程序看设置和日志，只是导出一定会失败。
  3) 双击没反应时用 run-core.bat 启动：它会把窗口留着，报错直接看得见，
     并会先打印它到底用了哪个 Python。

装不上（内网、代理、证书拦截）时的两条退路
  1) 找一台已经装好的电脑，把它的 ms-playwright 目录整个拷过来（一般在
     C:\Users\你的用户名\AppData\Local\ms-playwright，或全量版的
     app\runtime\ms-playwright），然后设环境变量
       PLAYWRIGHT_BROWSERS_PATH=D:\共享目录\ms-playwright
     内核目录名带版本号（chromium-1234），必须和 playwright 的版本是配套的，
     两样东西一起拷才保险。
  2) 直接用全量版压缩包。它对网络的要求只在你第一次登录商户时才有。

这个压缩包里有什么
  app\                程序本体：core\ 代码、platforms\ 各平台脚本
  run-core.vbs/.bat   启动入口
  第一次启动会装的依赖装到了本机 Python 的目录里，**不在**这个文件夹内 ——
  所以把整个文件夹复制给别人时，对方还是要自己装一次依赖。

哪些东西不在包里（每台电脑、每个用户自己长出来）
  browser_data\   各商户的浏览器数据与登录状态   —— 属于工作空间
  downloads\      导出的账单                     —— 属于工作空间
  logs\           运行日志与稳定性统计           —— 属于工作空间
  settings.json / selection_state.json / scheduled_tasks.json  设置、勾选、定时任务
  首次启动会让你选一个「工作空间」文件夹，之后界面里的「工作空间」按钮可以看和改。
  登录状态是明文的会话凭证，请按和账号密码同等的保密级别处理，不要发给无关的人。

给运维/IT 的补充
  统一产出目录：设环境变量 LIUSHUI_DATA_DIR=D:\流水导出，
  或启动时加参数 --data-dir=D:\流水导出。
  统一依赖位置：设 PLAYWRIGHT_BROWSERS_PATH 指到共享的 ms-playwright 目录，
  一台机器下载内核、多台机器共用是可以的（版本必须与各自的 playwright 一致）。
  程序目录可以只读（放共享盘没问题）；产出目录必须可写。
  启动器找 Python 的顺序（run-core.vbs）：%WINDIR%\pyw.exe → PATH 上的 pythonw.exe →
  %LocalAppData%\Programs\Python\Python3xx。想看它挑中了哪个，命令行执行：
    cscript //nologo run-core.vbs --print-python
