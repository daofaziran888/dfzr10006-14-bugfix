"""编排状态机 / 回滚回执 / 限流降级 回归脚本。

直接驱动服务层（线程并发），覆盖：
1. ORCH-0002 运行中回滚 -> 失败回执：撤销步骤 + 触发限流降级，失败步骤不进结果清单。
2. ORCH-0001 草稿归档 -> 被拒绝；草稿只能启动；已结束不能再归档。
3. ORCH-0003 已完成回滚 -> 成功并终止于已回滚；重复回滚必须拒绝。
4. 回滚与步骤完成并发 -> 只保留一个终止结论、一张回执。
运行：python -I /workspace/tests/regression_orchestra.py
"""
from __future__ import annotations

import sys
import threading

sys.path.insert(0, "/workspace/backend")

from app.services import limiter, receipts  # noqa: E402
from app.services.orchestra import (  # noqa: E402
    ACTION_ARCHIVE,
    ACTION_ROLLBACK,
    ACTION_START,
    CONCLUSION_FAILED,
    CONCLUSION_SUCCESS,
    STATUS_DONE,
    STATUS_DRAFT,
    STATUS_ROLLED_BACK,
    STATUS_RUNNING,
    STEP_FAILED,
    STEP_REVOKED,
    STEP_SUCCESS,
    OrchestraService,
)
from app.store import store  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def find(flow_no: str) -> dict:
    return next(row for row in store.rows("orchestra") if row["流程编号"] == flow_no)


def main() -> int:
    svc = OrchestraService()

    print("轨迹1：ORCH-0002 运行中回滚（含失败步骤）")
    entry, msg = svc.run_action(find("ORCH-0002")["id"], ACTION_ROLLBACK)
    check("回滚动作返回条目（失败也有回执，不报 500）", entry is not None, msg)
    check("主状态仍停留在运行中", entry["status"] == STATUS_RUNNING, entry["status"])
    check("终止结论=回滚失败", entry["terminal_conclusion"] == CONCLUSION_FAILED)
    check("失败步骤未进入结果清单", "数据抽取" not in entry["results"], str(entry["results"]))
    check("成功步骤被撤销", next(s for s in entry["steps"] if s["名称"] == "参数校验")["状态"] == STEP_REVOKED)
    check("失败步骤保留失败标记", next(s for s in entry["steps"] if s["名称"] == "数据抽取")["状态"] == STEP_FAILED)
    receipt_list, total = receipts.list_receipts(flow_no="ORCH-0002")
    check("出具且仅出具一张失败回执", total == 1 and receipt_list[0]["终止结论"] == CONCLUSION_FAILED)
    receipt = receipt_list[0]
    check("回执记录撤销步骤", receipt["撤销步骤"] == ["参数校验"], str(receipt["撤销步骤"]))
    check("回执挂载降级预案", receipt["降级预案"] and receipt["降级预案"]["来源流程"] == "ORCH-0002")
    limi2 = next(r for r in store.rows("limiter") if r["规则编号"] == "LIMI-0002")
    check("限流规则被触发降级", limi2["status"] == "已触发", limi2["status"])
    check("执行日志与终止结论一致（无错位）", "回滚失败" in entry["执行日志"] and entry["流程状态"] == STATUS_RUNNING)

    print("轨迹2：ORCH-0001 草稿阶段动作约束")
    rejected, msg = svc.run_action(find("ORCH-0001")["id"], ACTION_ARCHIVE)
    check("草稿归档被拒绝", rejected is None and "只有运行中" in msg, msg)
    check("拒绝后状态仍是草稿", find("ORCH-0001")["status"] == STATUS_DRAFT)
    started, msg = svc.run_action(find("ORCH-0001")["id"], ACTION_START)
    check("草稿可以启动到运行中", started is not None and started["status"] == STATUS_RUNNING, msg)
    bad_start, _ = svc.run_action(find("ORCH-0001")["id"], ACTION_START)
    check("运行中不能再次启动", bad_start is None)
    # 模拟步骤全部执行成功后再归档
    for step in started["steps"]:
        step["状态"] = STEP_SUCCESS
    done, msg = svc.run_action(find("ORCH-0001")["id"], ACTION_ARCHIVE)
    check("运行中归档产生已完成", done is not None and done["status"] == STATUS_DONE, msg)
    re_archive, msg = svc.run_action(find("ORCH-0001")["id"], ACTION_ARCHIVE)
    check("已结束流程不能再次归档", re_archive is None and "已结束" in msg, msg)
    check("结果清单只含成功步骤", set(done["results"]) == {"参数校验", "数据抽取", "结果落库"}, str(done["results"]))

    print("轨迹3：ORCH-0003 已完成回滚 + 重复回滚拒绝")
    rolled, msg = svc.run_action(find("ORCH-0003")["id"], ACTION_ROLLBACK)
    check("已完成可回滚", rolled is not None and rolled["status"] == STATUS_ROLLED_BACK, msg)
    check("终止结论=回滚成功", rolled["terminal_conclusion"] == CONCLUSION_SUCCESS)
    check("所有步骤已撤销", all(s["状态"] == STEP_REVOKED for s in rolled["steps"]))
    rcpt3, total3 = receipts.list_receipts(flow_no="ORCH-0003")
    check("ORCH-0003 仅一张成功回执", total3 == 1 and rcpt3[0]["终止结论"] == CONCLUSION_SUCCESS)
    again, msg = svc.run_action(find("ORCH-0003")["id"], ACTION_ROLLBACK)
    check("重复回滚被拒绝", again is None and "不能重复回滚" in msg, msg)
    check("拒绝后回执数量不增加", receipts.list_receipts(flow_no="ORCH-0003")[1] == 1)

    print("并发：回滚与步骤完成（归档）同时打到同一条运行中流程")
    # 用一条全新流程：草稿 -> 运行中，再起并发
    new_entry, _ = svc.create_entry(
        {"流程编号": "ORCH-0009", "流程名称": "并发验证", "编排步骤": "步骤甲,步骤乙"}
    )
    new_id = new_entry["id"]
    svc.run_action(new_id, ACTION_START)
    outcomes: list[str] = []

    def do_archive() -> None:
        _, message = svc.run_action(new_id, ACTION_ARCHIVE)
        outcomes.append("归档:" + message)

    def do_rollback() -> None:
        _, message = svc.run_action(new_id, ACTION_ROLLBACK)
        outcomes.append("回滚:" + message)

    t1 = threading.Thread(target=do_archive)
    t2 = threading.Thread(target=do_rollback)
    t1.start(); t2.start(); t1.join(); t2.join()
    final = svc.get_entry(new_id)
    check("并发后只有一个终止结论", final["terminal_conclusion"] in (CONCLUSION_SUCCESS, "正常完成"), str(final["terminal_conclusion"]))
    check("并发后状态唯一（已完成或已回滚，不含中间态）", final["status"] in (STATUS_DONE, STATUS_ROLLED_BACK), final["status"])
    rcpt9, total9 = receipts.list_receipts(flow_no="ORCH-0009")
    check("回滚若成功只产生一张回执", total9 in (0, 1))
    print(f"    并发动作结果：{outcomes}")

    print(f"\n合计：{PASS} 通过 / {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
