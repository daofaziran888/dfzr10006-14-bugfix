"""回滚回执领域服务。

编排流程的每一次回滚尝试都会留下一张回执（成功或失败），回执是编排流程终止结论的
权威来源：编排流程列表页与回滚回执详情页读取同一份数据，保证两个页面的终止结论吻合。

回执数据保存在独立的内存表 ``receipts`` 中，与“服务版本回退”的版本快照模块互不干扰。
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "receipts"
_guard = threading.Lock()
_seq = 0


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _next_no() -> str:
    global _seq
    with _guard:
        _seq += 1
        return f"RCPT-{datetime.now().strftime('%Y%m%d')}-{_seq:04d}"


def _table() -> list[dict[str, Any]]:
    return store.rows(MODULE)


def create_receipt(
    *,
    flow_entry: dict[str, Any],
    conclusion: str,
    reason: str,
    revoked_steps: list[str],
    degradation: dict[str, Any] | None,
) -> dict[str, Any]:
    """落一张回滚回执；一次回滚尝试只产生一张回执，不允许残留半成品。"""
    rows = _table()
    receipt = {
        "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
        "回执编号": _next_no(),
        "流程编号": str(flow_entry.get("流程编号", "")),
        "流程名称": str(flow_entry.get("流程名称", "")),
        "终止结论": conclusion,
        "失败原因": reason,
        "撤销步骤": list(revoked_steps),
        "降级预案": degradation,
        "流程状态": str(flow_entry.get("status", "")),
        "出具时间": _now(),
    }
    rows.append(receipt)
    return receipt


def list_receipts(
    *,
    keyword: str | None = None,
    flow_no: str | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    rows = _table()
    if flow_no:
        rows = [row for row in rows if row.get("流程编号") == flow_no]
    if keyword:
        rows = [
            row
            for row in rows
            if keyword in str(row.get("回执编号", "")) or keyword in str(row.get("流程编号", ""))
        ]
    total = len(rows)
    start = max(page - 1, 0) * size
    return rows[start:start + size], total


def get_receipt(receipt_id: int) -> dict[str, Any] | None:
    return store.find(MODULE, receipt_id)


def get_by_no(receipt_no: str) -> dict[str, Any] | None:
    for row in _table():
        if row.get("回执编号") == receipt_no:
            return row
    return None
