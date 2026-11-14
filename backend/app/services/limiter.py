"""调用限流降级业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "limiter"
REQUIRED_FIELDS = ["规则编号", "限流对象", "限流算法"]
STATUS_ORDER = ["草稿", "生效中", "已触发", "已关闭"]
ACTION_RULES = {"启用限流": "草稿", "触发降级": "生效中", "关闭限流": "已触发"}
NEGATIVE_ACTIONS = []

_degradation_lock = threading.Lock()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def trigger_degradation(flow_no: str) -> dict[str, Any]:
    """编排回滚失败时调用的限流降级预案。

    选取一条生效中的限流规则并置为“已触发”，记录触发时间与来源流程，
    供回滚回执引用。没有可用规则时返回一份显式的兜底说明，避免回执缺字段。
    """
    with _degradation_lock:
        target = next(
            (row for row in store.rows(MODULE) if row.get("status") == "生效中"),
            None,
        )
        if target is None:
            return {
                "规则编号": "N/A",
                "降级动作": "无生效限流规则，采用兜底降级（快速失败）",
                "触发时间": _now(),
                "来源流程": flow_no,
            }
        target["status"] = "已触发"
        target["pending"] = False
        target["abnormal"] = True
        target["触发时间"] = _now()
        target["降级来源流程"] = flow_no
        return {
            "规则编号": target.get("规则编号"),
            "限流对象": target.get("限流对象"),
            "降级动作": target.get("降级动作"),
            "触发时间": target["触发时间"],
            "来源流程": flow_no,
        }


class LimiterService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("规则编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"限流规则 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于调用限流降级可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"限流规则已{action}"
