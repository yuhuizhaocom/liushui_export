"""让离线测试在任何环境下都能 import 到 `core` 与 playwright。

CI 第一次红在 collection 阶段: `core/browser.py` 顶层就 `from playwright.sync_api import ...`,
而 runner 全局没装 playwright —— 它是按 `--target` 装进绿色包的 site-packages 的, 那份本来
就要随包发出去, 为跑测试再下一遍纯属浪费。于是 9 个测试模块一律 ModuleNotFoundError。

两条规则:
- **仓库根插到最前** —— 以前只有"在仓库根用 `python -m pytest`"这一种起法能 import core,
  现在从任意目录、任意起法都行; 排第一也保证测的是仓库里的 `core/`。
- **包内依赖追加到最后** —— 本机装了真 playwright 就仍用本机的, 不让包内那份(或本地跑过
  `build_portable` 留下的半成品)把真依赖悄悄换掉; 只有环境里确实没有时(=CI)才轮到它。
  目录按 `build/*/app/site-packages` 找, 工作流里改 `PKG` 名字这边不用跟着改。
  ⚠ 只加 site-packages, 绝不加 `build/.../app`。
"""
import glob
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
PKG_SITE_GLOB = "build/*/app/site-packages"


def _site_paths():
    """CI 里随包组装出来的依赖目录(本机没跑过打包时是空的)。"""
    return [p for p in sorted(glob.glob(os.path.join(_REPO, PKG_SITE_GLOB))) if os.path.isdir(p)]


def mount():
    if os.path.isdir(_REPO) and _REPO not in sys.path:
        sys.path.insert(0, _REPO)
    for _p in _site_paths():
        if _p not in sys.path:
            sys.path.append(_p)


mount()
