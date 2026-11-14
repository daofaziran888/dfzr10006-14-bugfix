"""回滚回执接口：编排流程每次回滚尝试都在此留痕，供回执详情页核对终止结论。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.schemas import PageResult
from app.services import receipts

router = APIRouter(prefix="/api/receipts", tags=["回滚回执"])


@router.get("", response_model=PageResult[dict])
def list_receipts(
    keyword: str | None = Query(default=None, description="按回执编号或流程编号检索"),
    flow_no: str | None = Query(default=None, description="按编排流程编号过滤"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """列出回滚回执；与编排流程列表读取同一份终止结论。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = receipts.list_receipts(keyword=keyword, flow_no=flow_no, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{receipt_id}", response_model=dict)
def get_receipt(receipt_id: int) -> dict:
    """回滚回执详情：包含终止结论、撤销步骤与降级预案。"""
    receipt = receipts.get_receipt(receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail=f"回滚回执 {receipt_id} 不存在")
    return receipt
