"""能力子代理包。

包内模块自动发现：新增子代理 = 在本包新建一个模块文件，定义
``BaseSubAgent`` 子类即可（见 base.py 的说明），此处无需改动。
"""

import importlib
import pkgutil

from app.subagents.base import (
    BaseSubAgent,
    SubAgentContext,
    registered_subagent_classes,
    registered_subagents,
    subagent_catalog,
)

# 导入包内全部模块以触发类注册（base 已导入，跳过避免重复）
for _mod in pkgutil.iter_modules(__path__):
    if _mod.name != "base":
        importlib.import_module(f"{__name__}.{_mod.name}")

__all__ = [
    "BaseSubAgent",
    "SubAgentContext",
    "registered_subagent_classes",
    "registered_subagents",
    "subagent_catalog",
]
