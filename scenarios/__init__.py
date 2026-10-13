# -*- coding: utf-8 -*-
"""场景注册表：按 A~E 五类汇总，顺序即 README / `/__admin/scenarios` 里的展示顺序。"""

from collections import OrderedDict

from . import auth, pagination, payload, depend, faults, faultswitch, typemismatch
from .common import reset_tokens

GROUPS = [
    ("A 鉴权", auth),
    ("B 分页", pagination),
    ("C 请求与响应", payload),
    ("D 依赖", depend),
    ("E 故障", faults),
    ("F 类型不一致", typemismatch),
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


def reset_all(scenario=None):
    """复位计数器与已签发令牌（请求日志由 server 侧清空）。

    给 scenario 则只复位该场景相关的状态：令牌按场景过滤，
    故障计数器只属于 `faults` 场景，所以别的场景不碰它；
    带故障开关的场景（`depend-switchable`、`depend-two-roots`、`page-fail-midway`）同理，
    开关与调用计数只在全量复位或复位它自己时还原。
    """
    reset_tokens(scenario)
    if scenario is None or scenario == "faults":
        faults.reset_counters()
    for name, board in faultswitch.BOARDS.items():
        if scenario is None or scenario == name:
            board.reset()
