"""HTTP 端到端：同时读取编排流程列表页与回滚回执详情页所用接口，核对终止结论一致。

前置：后端已在 127.0.0.1:8000 启动。仅用标准库，可 `python -I` 运行。
"""
from __future__ import annotations

import json
import urllib.request

BASE = "http://127.0.0.1:8000"
PASS = 0
FAIL = 0


def call(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def main() -> int:
    # 三条动作轨迹
    r2 = call("POST", "/api/orchestra/2/actions", {"values": {"action": "回滚流程"}})
    check("ORCH-0002 运行中回滚返回失败回执结论", r2["ok"] and r2["entry"]["terminal_conclusion"] == "回滚失败")
    r1 = call("POST", "/api/orchestra/1/actions", {"values": {"action": "归档流程"}})
    check("ORCH-0001 草稿归档被拒绝", r1["ok"] is False and "只有运行中" in r1["message"])
    r3 = call("POST", "/api/orchestra/3/actions", {"values": {"action": "回滚流程"}})
    check("ORCH-0003 已完成回滚成功", r3["ok"] and r3["entry"]["status"] == "已回滚")
    r3b = call("POST", "/api/orchestra/3/actions", {"values": {"action": "回滚流程"}})
    check("ORCH-0003 重复回滚被拒绝", r3b["ok"] is False and "重复回滚" in r3b["message"])

    # 编排列表页接口
    orch_rows = {row["流程编号"]: row for row in call("GET", "/api/orchestra")["items"]}
    # 回执列表/详情页接口
    receipt_rows = call("GET", "/api/receipts")["items"]
    receipts_by_flow = {row["流程编号"]: row for row in receipt_rows}

    print("跨页一致性：编排列表页 vs 回滚回执详情页")
    for flow_no, receipt in receipts_by_flow.items():
        orch = orch_rows[flow_no]
        detail = call("GET", f"/api/receipts/{receipt['id']}")
        check(
            f"{flow_no} 终止结论两页吻合",
            orch["terminal_conclusion"] == receipt["终止结论"] == detail["终止结论"],
            f"{orch.get('terminal_conclusion')} / {receipt['终止结论']} / {detail['终止结论']}",
        )
        check(
            f"{flow_no} 回执编号两页吻合",
            orch["receipt_no"] == receipt["回执编号"] == detail["回执编号"],
        )

    check("回执总数=回滚尝试数（2 次，无残留/重复）", len(receipt_rows) == 2, str(len(receipt_rows)))
    check("ORCH-0001 无回执（被拒绝的动作不留回执）", "ORCH-0001" not in receipts_by_flow)

    # 三条轨迹各一个最终状态
    finals = {no: orch_rows[no]["status"] for no in ("ORCH-0001", "ORCH-0002", "ORCH-0003")}
    check("三条流程各保留唯一最终状态", finals == {"ORCH-0001": "草稿", "ORCH-0002": "运行中", "ORCH-0003": "已回滚"}, str(finals))

    print(f"\n合计：{PASS} 通过 / {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
