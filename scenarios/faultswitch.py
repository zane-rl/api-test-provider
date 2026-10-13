# -*- coding: utf-8 -*-
"""按接口的故障开关与调用计数（通用版）。

`depend-switchable` 的开关写在它自己的 `switchable.py` 里（先有的，保持原样不动）；
后来的场景（`depend-two-roots`、`page-fail-midway`）用这里的 `SwitchBoard`，
并统一登记到 `BOARDS`，由 `/__admin/faults?scenario=<场景>` 按场景名分发、
`reset_all` 按场景名复位——每个场景一块独立的开关板，互不影响。

请求体格式与 `depend-switchable` 完全一致：
`{"endpoint": 键, "mode": "normal" | "fail", "status": 状态码}` 或 `{"switches": [...]}`。
"""

import threading
from collections import OrderedDict

from .common import ApiError, json_resp

MODE_NORMAL = "normal"
MODE_FAIL = "fail"

# 场景名 -> 开关板（含 depend-switchable 的适配器），顺序即提示信息里的展示顺序
BOARDS = OrderedDict()


def register(board):
    if board.name in BOARDS:
        raise RuntimeError("开关板重复登记：%s" % board.name)
    BOARDS[board.name] = board
    return board


class SwitchBoard:
    """一个场景的全部开关。

    keys：[(开关键, 方法, 路径模板)]；default_mode：复位后的模式；
    default_status：切到失败且没给 status 时用的状态码。
    """

    def __init__(self, name, keys, default_mode=MODE_NORMAL, default_status=503):
        self.name = name
        self.keys = list(keys)
        self.default_mode = default_mode
        self.default_status = default_status
        self._lock = threading.Lock()
        self._state = {}
        self.reset()

    def _fresh(self):
        return {"mode": self.default_mode, "failStatus": self.default_status,
                "succeeded": 0, "failed": 0}

    def reset(self):
        """全部开关还原为缺省模式、调用计数归零"""
        with self._lock:
            self._state.clear()
            for key, _, _ in self.keys:
                self._state[key] = self._fresh()

    def snapshot(self):
        with self._lock:
            return [
                {"key": key, "method": method, "path": path,
                 "mode": self._state[key]["mode"], "failStatus": self._state[key]["failStatus"],
                 "calls": {"total": self._state[key]["succeeded"] + self._state[key]["failed"],
                           "succeeded": self._state[key]["succeeded"],
                           "failed": self._state[key]["failed"]}}
                for key, method, path in self.keys
            ]

    def apply(self, patch):
        """按请求体切换开关。先整体校验再生效：有一项不合法就整体拒绝，不会只切一半。"""
        if not isinstance(patch, dict):
            raise ApiError(400, "请求体必须是 JSON 对象")
        items = patch["switches"] if "switches" in patch else [patch]
        if not isinstance(items, list) or not items:
            raise ApiError(400, "switches 必须是非空数组")
        keys = [key for key, _, _ in self.keys]
        changes = []
        for item in items:
            if not isinstance(item, dict):
                raise ApiError(400, "开关项必须是 JSON 对象，收到：%r" % (item,))
            key, mode = item.get("endpoint"), item.get("mode")
            if key not in keys:
                raise ApiError(400, "endpoint 必须是 %s 之一，收到：%r" % (" / ".join(keys), key))
            if mode not in (MODE_NORMAL, MODE_FAIL):
                raise ApiError(400, "mode 必须是 %s 或 %s，收到：%r" % (MODE_NORMAL, MODE_FAIL, mode))
            status = item.get("status", self.default_status)
            if isinstance(status, bool) or not isinstance(status, int) or not 400 <= status <= 599:
                raise ApiError(400, "status 必须是 400~599 的整数，收到：%r" % (status,))
            # 切回正常时失败状态码一并还原成缺省值，避免下次切失败时沿用上一次的
            changes.append((key, mode, status if mode == MODE_FAIL else self.default_status))
        with self._lock:
            for key, mode, status in changes:
                self._state[key]["mode"] = mode
                self._state[key]["failStatus"] = status
        return self.snapshot()

    def fail_status(self, key):
        """处在失败模式时返回要用的状态码，否则返回 None（不计数）"""
        with self._lock:
            state = self._state[key]
            return state["failStatus"] if state["mode"] == MODE_FAIL else None

    def count(self, key, succeeded):
        with self._lock:
            self._state[key]["succeeded" if succeeded else "failed"] += 1

    def failure(self, key, status):
        """记一次失败并给出统一的失败响应"""
        self.count(key, False)
        return json_resp(
            {"code": status,
             "message": "接口 %s 处在失败模式（/__admin/faults?scenario=%s 可切换）"
                        % (key, self.name)},
            status=status)

    def counted(self, key, handler, ctx):
        """照常执行处理函数并计数：4xx / 5xx 与抛出的 ApiError 都算失败"""
        try:
            resp = handler(ctx)
        except ApiError:
            self.count(key, False)
            raise
        self.count(key, resp.status < 400)
        return resp

    def guarded(self, key, handler):
        """给处理函数套上「整接口持续失败」的开关与计数；失败先于参数校验返回"""
        def wrapper(ctx):
            # 生成文档时不看开关、不计数：文档必须与运行期状态无关
            if ctx.doc_mode:
                return handler(ctx)
            status = self.fail_status(key)
            if status is not None:
                return self.failure(key, status)
            return self.counted(key, handler, ctx)
        return wrapper


class _SwitchableAdapter:
    """把 `switchable.py` 里先有的模块级开关包成同样的接口，行为不变"""

    def __init__(self, module):
        self._module = module
        self.name = module.NAME

    def reset(self):
        self._module.reset_switches()

    def snapshot(self):
        return self._module.switches_snapshot()

    def apply(self, patch):
        return self._module.apply_switches(patch)


def register_switchable(module):
    return register(_SwitchableAdapter(module))
