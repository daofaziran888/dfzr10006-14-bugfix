"""服务编排组合业务规则。

这里收口编排流程的全部领域规则：
- 显式状态机：草稿只能进入运行中；已完成只允许从运行中产生；回滚只能从运行中/已完成进入；
  已结束（已完成、已回滚）的流程不能再次归档，已回滚的流程不能再次回滚。
- 编排步骤结构化并记录执行日志，失败步骤不进入编排结果清单。
- 回滚产生唯一回执：失败回执同时撤销编排步骤并调用限流降级预案。
- 每条流程一把锁，回滚与步骤完成并发时只保留一个终止结论。
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from app.services import limiter as limiter_module
from app.services import receipts as receipts_module
from app.store import store

MODULE = "orchestra"
REQUIRED_FIELDS = ["流程编号", "流程名称", "编排步骤"]

# 状态序列：草稿 -> 运行中 -> 已完成；运行中/已完成 -> 已回滚（终止态）
STATUS_DRAFT = "草稿"
STATUS_RUNNING = "运行中"
STATUS_DONE = "已完成"
STATUS_ROLLED_BACK = "已回滚"
TERMINAL_STATUSES = (STATUS_DONE, STATUS_ROLLED_BACK)

ACTION_START = "启动编排"
ACTION_ROLLBACK = "回滚流程"
ACTION_ARCHIVE = "归档流程"

# 动作允许的来源状态（显式状态机，替代旧的“动作->目标状态”映射）
ACTION_ALLOWED_FROM: dict[str, tuple[str, ...]] = {
    ACTION_START: (STATUS_DRAFT,),
    ACTION_ARCHIVE: (STATUS_RUNNING,),
    ACTION_ROLLBACK: (STATUS_RUNNING, STATUS_DONE),
}
ACTION_TARGET = {
    ACTION_START: STATUS_RUNNING,
    ACTION_ARCHIVE: STATUS_DONE,
    ACTION_ROLLBACK: STATUS_ROLLED_BACK,
}

# 编排步骤生命周期
STEP_PENDING = "待执行"
STEP_RUNNING = "执行中"
STEP_SUCCESS = "成功"
STEP_FAILED = "失败"
STEP_REVOKED = "已撤销"

# 回滚回执终止结论
CONCLUSION_SUCCESS = "回滚成功"
CONCLUSION_FAILED = "回滚失败"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize_steps(raw: Any) -> list[dict[str, str]]:
    """把种子里的字符串步骤或外部传入的步骤统一成结构化步骤列表。"""
    if isinstance(raw, list) and raw and all(isinstance(item, dict) for item in raw):
        return [
            {
                "名称": str(item.get("名称", "")),
                "状态": str(item.get("状态", STEP_PENDING)),
            }
            for item in raw
        ]
    text = str(raw or "").strip()
    if not text:
        return []
    return [{"名称": part.strip(), "状态": STEP_PENDING} for part in text.split(",") if part.strip()]


def _append_log(entry: dict[str, Any], message: str) -> None:
    """执行日志只追加、不覆盖，避免旧的静态占位文本造成日志错位。"""
    logs = entry.setdefault("logs", [])
    if not isinstance(logs, list):
        logs = []
        entry["logs"] = logs
    logs.append({"时间": _now(), "内容": message})
    # 同步表格里的扁平「执行日志」与「流程状态」，保证列表页与结构化日志结论一致
    entry["执行日志"] = message
    entry["流程状态"] = str(entry.get("status", ""))


class OrchestraService:
    def __init__(self) -> None:
        # 每条编排流程一把锁：序列化同一条流程上的并发动作
        self._locks: dict[int, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    def _lock_for(self, entry_id: int) -> threading.RLock:
        with self._locks_guard:
            lock = self._locks.get(entry_id)
            if lock is None:
                lock = threading.RLock()
                self._locks[entry_id] = lock
            return lock

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
            rows = [row for row in rows if keyword in str(row.get("流程编号", ""))]
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
        entry["status"] = STATUS_DRAFT
        entry["pending"] = True
        entry["abnormal"] = False
        entry["steps"] = _normalize_steps(values.get("steps", values.get("编排步骤")))
        entry["results"] = []
        entry["logs"] = [{"时间": _now(), "内容": "编排流程登记，进入草稿"}]
        entry["receipt_no"] = None
        entry["terminal_conclusion"] = None
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"编排流程 {entry_id} 不存在或已归档"
        if action not in ACTION_ALLOWED_FROM:
            return None, f"动作「{action}」不属于服务编排组合可执行范围"

        # 同一条流程的动作串行化：回滚与步骤完成并发时只落一个终止结论
        with self._lock_for(entry_id):
            current = str(entry.get("status"))
            allowed = ACTION_ALLOWED_FROM[action]
            if current not in allowed:
                return None, self._reject_message(action, current)

            if action == ACTION_START:
                return self._start(entry)
            if action == ACTION_ARCHIVE:
                return self._archive(entry)
            return self._rollback(entry)

    # ---- 具体动作 -------------------------------------------------------

    def _start(self, entry: dict[str, Any]) -> tuple[dict[str, Any], str]:
        entry["status"] = STATUS_RUNNING
        entry["pending"] = True
        entry["abnormal"] = False
        steps = entry.setdefault("steps", _normalize_steps(entry.get("编排步骤")))
        for index, step in enumerate(steps):
            if step.get("状态") == STEP_PENDING:
                step["状态"] = STEP_RUNNING if index == 0 else STEP_PENDING
        _append_log(entry, "启动编排：草稿 -> 运行中")
        return entry, "编排流程已启动，进入运行中"

    def _archive(self, entry: dict[str, Any]) -> tuple[dict[str, Any], str]:
        steps = entry.setdefault("steps", [])
        running = [step for step in steps if step.get("状态") == STEP_RUNNING]
        for step in running:
            step["状态"] = STEP_SUCCESS
        # 编排结果清单只收纳成功步骤，失败步骤不得进入
        results = [step["名称"] for step in steps if step.get("状态") == STEP_SUCCESS]
        entry["results"] = results
        entry["status"] = STATUS_DONE
        entry["pending"] = False
        entry["abnormal"] = False
        entry["terminal_conclusion"] = "正常完成"
        _append_log(entry, f"归档流程：运行中 -> 已完成，结果清单收录 {len(results)} 个成功步骤")
        return entry, "编排流程已归档，状态为已完成"

    def _rollback(self, entry: dict[str, Any]) -> tuple[dict[str, Any], str]:
        steps = entry.setdefault("steps", [])
        has_failure = any(step.get("状态") == STEP_FAILED for step in steps)

        if has_failure:
            return self._record_failed_rollback(entry, steps)
        return self._record_successful_rollback(entry, steps)

    def _record_failed_rollback(
        self, entry: dict[str, Any], steps: list[dict[str, str]]
    ) -> tuple[dict[str, Any], str]:
        # 失败回执：撤销已完成/执行中的编排步骤，失败步骤不进入结果清单
        revoked: list[str] = []
        for step in steps:
            if step.get("状态") in (STEP_SUCCESS, STEP_RUNNING):
                step["状态"] = STEP_REVOKED
                revoked.append(step["名称"])
        entry["results"] = [step["名称"] for step in steps if step.get("状态") == STEP_SUCCESS]

        # 调用限流降级预案
        degradation = limiter_module.trigger_degradation(str(entry.get("流程编号", "")))

        receipt = receipts_module.create_receipt(
            flow_entry=entry,
            conclusion=CONCLUSION_FAILED,
            reason="编排步骤存在失败项，回滚未完成终止",
            revoked_steps=revoked,
            degradation=degradation,
        )
        entry["receipt_no"] = receipt["回执编号"]
        entry["terminal_conclusion"] = CONCLUSION_FAILED
        entry["abnormal"] = True
        # 回滚失败不改变流程主状态（仍停留在运行中/已完成），等待处置
        _append_log(
            entry,
            f"回滚失败：撤销 {len(revoked)} 个步骤，已触发限流降级预案"
            f"（{degradation.get('规则编号', '-')}），回执 {receipt['回执编号']}",
        )
        return entry, (
            f"回滚失败：存在失败步骤，已撤销 {len(revoked)} 个步骤并触发限流降级预案，"
            f"回执 {receipt['回执编号']}"
        )

    def _record_successful_rollback(
        self, entry: dict[str, Any], steps: list[dict[str, str]]
    ) -> tuple[dict[str, Any], str]:
        revoked: list[str] = []
        for step in steps:
            if step.get("状态") in (STEP_SUCCESS, STEP_RUNNING):
                step["状态"] = STEP_REVOKED
                revoked.append(step["名称"])
        entry["results"] = []

        receipt = receipts_module.create_receipt(
            flow_entry=entry,
            conclusion=CONCLUSION_SUCCESS,
            reason="",
            revoked_steps=revoked,
            degradation=None,
        )
        entry["receipt_no"] = receipt["回执编号"]
        entry["terminal_conclusion"] = CONCLUSION_SUCCESS
        entry["status"] = STATUS_ROLLED_BACK
        entry["pending"] = False
        entry["abnormal"] = False
        _append_log(
            entry,
            f"回滚成功：撤销 {len(revoked)} 个步骤，回执 {receipt['回执编号']}，流程终止于已回滚",
        )
        return entry, f"编排流程已回滚，回执 {receipt['回执编号']}，最终状态为已回滚"

    def _reject_message(self, action: str, current: str) -> str:
        if action == ACTION_START:
            return f"当前状态为「{current}」，只有草稿流程才能启动编排"
        if action == ACTION_ARCHIVE:
            if current in TERMINAL_STATUSES:
                return f"当前状态为「{current}」，已结束流程不能再次归档"
            return f"当前状态为「{current}」，只有运行中的流程才能归档为已完成"
        if action == ACTION_ROLLBACK:
            if current == STATUS_ROLLED_BACK:
                return "流程已回滚，回滚回执已出具，不能重复回滚"
            if current == STATUS_DRAFT:
                return "草稿流程尚未运行，不能回滚"
            return f"当前状态为「{current}」，只有运行中或已完成的流程才能回滚"
        return f"当前状态「{current}」不允许执行动作「{action}」"
