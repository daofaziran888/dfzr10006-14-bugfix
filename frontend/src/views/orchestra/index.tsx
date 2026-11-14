import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { request } from '@/api/client'

const ENDPOINT = '/api/orchestra'
const columns = [
  "流程编号",
  "流程名称",
  "编排步骤",
  "终止结论",
  "流程状态",
  "执行日志",
  "回滚回执"
]
const actions = [
  "启动编排",
  "回滚流程",
  "归档流程"
]

type Step = { 名称: string; 状态: string }
type Row = Record<string, string | number | null | Step[] | string[]>

export default function OrchestraView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => ["流程编号", "流程名称", "编排步骤"], [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('编排流程列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '服务编排组合列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ values: { action } }),
      })
      const payload = await response.json().catch(() => null)
      // 后端把不允许的动作以 ok=false 拒绝（HTTP 200），这里必须把真实原因透传到页脚
      if (!response.ok || !payload?.ok) {
        throw new Error(payload?.message ?? '服务编排组合动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '服务编排组合操作失败')
    }
  }

  function renderCell(row: Row, column: string) {
    if (column === '编排步骤') {
      const steps = (row.steps ?? []) as Step[]
      if (!steps.length) return '—'
      return (
        <ul className="step-list">
          {steps.map((step) => (
            <li key={step.名称} className={`step step-${step.状态}`}>
              <span>{step.名称}</span>
              <em>{step.状态}</em>
            </li>
          ))}
        </ul>
      )
    }
    if (column === '终止结论') {
      const conclusion = (row.terminal_conclusion as string) || '—'
      const tone = conclusion === '回滚失败' ? 'bad' : conclusion === '—' ? '' : 'ok'
      return <span className={`conclusion ${tone}`}>{conclusion}</span>
    }
    if (column === '回滚回执') {
      const receiptNo = row.receipt_no as string | null
      if (!receiptNo) return '—'
      return <Link className="link" to={`/receipts?flow_no=${encodeURIComponent(String(row.流程编号))}`}>{receiptNo}</Link>
    }
    if (column === '流程状态') {
      return <strong>{(row.流程状态 as string) ?? row.status ?? '—'}</strong>
    }
    return <>{(row[column] as string) ?? '—'}</>
  }

  return (
    <section className="page" data-module="orchestra">
      <header className="page-head">
        <div>
          <h2>服务编排组合管理</h2>
          <p className="page-desc">维护服务编排的步骤定义、并行策略与失败处理。状态机约束：草稿→运行中→已完成；运行中/已完成可回滚；已结束流程不可再次归档或重复回滚。</p>
        </div>
        <div className="page-actions">
          <Link className="btn" to="/receipts">查看回滚回执</Link>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出服务编排组合清单
          </button>
        </div>
      </header>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => <td key={column}>{renderCell(row, column)}</td>)}
              <td className="row-actions">
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无服务编排组合数据</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条服务编排组合记录</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
