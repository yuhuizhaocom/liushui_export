"""程序版本号与发布坐标。

只用标准库、单独成模块, 为的是三处都能廉价读它: 界面标题、「检查更新」(core/update.py)、
以及 CI 打 tag 时核"tag 与常量是否同一个版本"。以前版本只活在 `vportable_0.0.6` 这种 tag 里,
代码里一处都没有, 用户问"手上这份是哪版"没人答得出。
发布坐标见下面的 RELEASE_REPO: 用代码仓自己的 Releases, 前提是这个仓库是**公开的**。
"""

APP_VERSION = "0.0.7"

# tag 就叫 `v0.0.7`(0.0.7 起)。0.0.6 及以前发出去的是 `vportable_0.0.x`, 发布页上的
# latest 可能还是那种写法, 所以解析两种都得认 —— 认不出来的版本会被当成"没新版"。
TAG_PREFIX = "v"
LEGACY_TAG_PREFIXES = ("vportable_",)

# 「检查更新」的发布坐标就是**代码仓自己的 Releases**。
# 前提是这仓库保持公开: 私有仓的 Release 匿名访问实测 404, 而拿到绿色包的业务用户
# 手里没有 GitHub 权限 —— 仓库一旦转回私有, 所有人的「检查更新」就永远查不到新版。
RELEASE_REPO = "yuhuizhaocom/liushui_export"


def parse(text):
    """把"0.0.6"/"v0.0.6"/"vportable_0.0.6"这类写成可比较的元组; 认不出来返回 None。

    只取数字段: 后缀(-beta 之类)一律忽略, 因为发布页上出现的永远是纯三段号。
    """
    if not text:
        return None
    core = str(text).strip()
    for prefix in LEGACY_TAG_PREFIXES:
        if core.startswith(prefix):
            core = core[len(prefix):]
            break
    if core.startswith(TAG_PREFIX):
        core = core[len(TAG_PREFIX):]
    parts = core.replace("-", ".").split(".")
    nums = []
    for p in parts:
        if not p.isdigit():
            break
        nums.append(int(p))
    return tuple(nums) if nums else None


def from_tag(tag):
    """Release 的 tag → 版本元组(v0.0.7 或 vportable_0.0.7 都 → (0, 0, 7))。"""
    return parse(tag)


def is_newer(remote, local):
    """远端是否比本地新。任一版本读不出 → False(宁可不提示, 也不拿脏数据让用户覆盖)。"""
    r, l = parse(remote), parse(local)
    if r is None or l is None:
        return False
    n = max(len(r), len(l))
    return r + (0,) * (n - len(r)) > l + (0,) * (n - len(l))
