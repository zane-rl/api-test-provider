# -*- coding: utf-8 -*-
"""场景注册表：按 A~E 五类汇总，顺序即 README / `/__admin/scenarios` 里的展示顺序。"""

from collections import OrderedDict

from . import auth, pagination, payload, depend, faults
from .common import reset_tokens

GROUPS = [
    ("A 鉴权", auth),
    ("B 分页", pagination),
    ("C 请求与响应", payload),
    ("D 依赖", depend),
    ("E 故障", faults),
]


def build_registry():
    """构建 {场景名: Scenario} 有序表，并校验名字不重复"""
    registry = OrderedDict()
    for group_name, module in GROUPS:
        for scenario in module.build_scenarios():
            if scenario.name in registry:
                raise RuntimeError("场景名重复：%s" % scenario.name)
            scenario.group = group_name
            registry[scenario.name] = scenario
    return registry


REGISTRY = build_registry()


def reset_all():
    """复位所有计数器与已签发令牌（请求日志由 server 侧清空）"""
    reset_tokens()
    faults.reset_counters()
