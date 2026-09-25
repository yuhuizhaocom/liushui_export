"""取消勾选再勾回平台, 不能把用户辛苦取消掉的商户又全勾上。

左栏的平台勾选框是"联动"它底下商户的: 以前取消 = 商户全不勾, 再勾回来 = 商户全勾。
于是一个平台下有 5 家店、用户常年不勾其中 2 家(停业/另一个结算主体), 只要手滑点过一
次平台框, 那 2 家就悄悄回到任务里, 导出的时候才被跑一遍。
"""
from core.main_gui import LiushuiApp


class _Var:
    """够用的 tk.BooleanVar 替身: 只要 get/set。"""

    def __init__(self, value=False):
        self._v = bool(value)
        self.writes = []

    def get(self):
        return self._v

    def set(self, v):
        self._v = bool(v)
        self.writes.append(self._v)


class _App:
    _on_platform_toggled = LiushuiApp._on_platform_toggled

    def __init__(self, platform_on=True, merchants=("A", "B", "C"), memory=None):
        self.platform_vars = {"youzan": _Var(platform_on)}
        self.merchant_vars = {"youzan": {m: _Var(True) for m in merchants}}
        self._merchant_memory = {} if memory is None else memory

    def states(self):
        return {n: v.get() for n, v in self.merchant_vars["youzan"].items()}


def test_unchecking_the_platform_remembers_each_merchant():
    app = _App()
    app.merchant_vars["youzan"]["B"].set(False)     # 用户平时就不勾 B
    app.platform_vars["youzan"].set(False)
    app._on_platform_toggled("youzan")
    assert app.states() == {"A": False, "B": False, "C": False}
    assert app._merchant_memory["youzan"] == {"A": True, "B": False, "C": True}


def test_rechecking_restores_the_saved_per_merchant_state():
    app = _App()
    app.merchant_vars["youzan"]["B"].set(False)
    app.platform_vars["youzan"].set(False)
    app._on_platform_toggled("youzan")              # 取消
    app.platform_vars["youzan"].set(True)
    app._on_platform_toggled("youzan")              # 勾回来
    assert app.states() == {"A": True, "B": False, "C": True}, "B 不该被强行勾回"


def test_first_check_still_selects_every_merchant():
    """没记过状态时保持旧行为: 整平台全选(用户第一次勾平台就是想要全部)。"""
    app = _App(platform_on=False, merchants=("A", "B"))
    for mv in app.merchant_vars["youzan"].values():
        mv.set(False)
    app.platform_vars["youzan"].set(True)
    app._on_platform_toggled("youzan")
    assert app.states() == {"A": True, "B": True}


def test_merchant_added_after_the_snapshot_defaults_to_checked():
    app = _App(memory={"youzan": {"A": True, "B": False}})
    app.platform_vars["youzan"].set(True)
    app._on_platform_toggled("youzan")
    assert app.states() == {"A": True, "B": False, "C": True}, "新建的商户该默认勾上"


def test_missing_platform_variable_is_harmless():
    app = _App()
    app.platform_vars = {}
    app._on_platform_toggled("youzan")              # 不抛错
    assert app.states() == {"A": True, "B": True, "C": True}
