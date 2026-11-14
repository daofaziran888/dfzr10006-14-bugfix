import { useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { request } from '@/api/client'

type Degradation = {
  规则编号?: string
  限流对象?: string
  降级动作?: string
  触发时间?: string
  来源流程?: string
}

type Receipt = {
  id: number
  回执编号: string
  流程编号: string
  流程名称: string
  终止结论: string
  失败原因: string
  撤销步骤: string[]
  降级预案: Degradation | null
  流程状态: string
  出具时间: string
}

export default function ReceiptsView() {
  const [searchParams] = useSearchParams()
  const flowNo = searchParams.get('flow_no') ?? ''
  const [receipts, setReceipts] = useState<Receipt[]>([])
  const [selected, setSelected] = useState<Receipt | null>(null)
  const [errorMessage, setErrorMessage] = useState('')

  const load = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(flowNo ? { flow_no: flowNo } : {}).toString()
    try {
      const response = await request(`/api/receipts?${query}`)
      if (!response.ok) throw new Error('回滚回执列表读取失败')
      const payload = await response.json()
      const items: Receipt[] = payload.items ?? []
      setReceipts(items)
      setSelected((prev) => prev ?? items[0] ?? null)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '回滚回执读取失败')
    }
  }, [flowNo])

  useEffect(() => {
    void load()
  }, [load])

  async function openDetail(receiptId: number) {
    const response = await request(`/api/receipts/${receiptId}`)
    if (response.ok) setSelected(await response.json())
  }

  return (
    <section className="page" data-module="receipts">
      <header className="page-head">
        <div>
          <h2>回滚回执详情</h2>
          <p className="page-desc">
            每张回执对应一次回滚尝试，终止结论与服务编排组合列表页同源。
            {flowNo ? ` 当前按流程 ${flowNo} 过滤。` : ''}
          </p>
        </div>
        <div className="page-actions">
          <Link className="btn" to="/orchestra">返回编排流程列表</Link>
        </div>
      </header>

      <div className="receipt-layout">
        <table className="data-table">
          <thead>
            <tr>
              <th>回执编号</th>
              <th>流程编号</th>
              <th>终止结论</th>
              <th>流程状态</th>
              <th>出具时间</th>
            </tr>
          </thead>
          <tbody>
            {receipts.map((receipt) => (
              <tr
                key={receipt.id}
                className={selected?.id === receipt.id ? 'row-selected' : ''}
                onClick={() => void openDetail(receipt.id)}
              >
                <td>{receipt.回执编号}</td>
                <td>{receipt.流程编号}</td>
                <td>
                  <span className={`conclusion ${receipt.终止结论 === '回滚失败' ? 'bad' : 'ok'}`}>
                    {receipt.终止结论}
                  </span>
                </td>
                <td>{receipt.流程状态}</td>
                <td>{receipt.出具时间}</td>
              </tr>
            ))}
            {receipts.length === 0 && (
              <tr><td className="empty-state" colSpan={5}>暂无回滚回执</td></tr>
            )}
          </tbody>
        </table>

        {selected && (
          <article className="receipt-detail">
            <h3>{selected.回执编号}</h3>
            <dl className="detail-grid">
              <dt>流程编号</dt><dd>{selected.流程编号}</dd>
              <dt>流程名称</dt><dd>{selected.流程名称}</dd>
              <dt>终止结论</dt>
              <dd>
                <span className={`conclusion ${selected.终止结论 === '回滚失败' ? 'bad' : 'ok'}`}>
                  {selected.终止结论}
                </span>
              </dd>
              <dt>流程状态</dt><dd>{selected.流程状态}</dd>
              <dt>失败原因</dt><dd>{selected.失败原因 || '—'}</dd>
              <dt>出具时间</dt><dd>{selected.出具时间}</dd>
            </dl>

            <h4>已撤销编排步骤</h4>
            {selected.撤销步骤.length ? (
              <ul className="step-list">
                {selected.撤销步骤.map((name) => (
                  <li key={name} className="step step-已撤销"><span>{name}</span><em>已撤销</em></li>
                ))}
              </ul>
            ) : <p>无</p>}

            <h4>限流降级预案</h4>
            {selected.降级预案 ? (
              <dl className="detail-grid">
                <dt>规则编号</dt><dd>{selected.降级预案.规则编号}</dd>
                <dt>限流对象</dt><dd>{selected.降级预案.限流对象 ?? '—'}</dd>
                <dt>降级动作</dt><dd>{selected.降级预案.降级动作 ?? '—'}</dd>
                <dt>触发时间</dt><dd>{selected.降级预案.触发时间 ?? '—'}</dd>
                <dt>来源流程</dt><dd>{selected.降级预案.来源流程 ?? '—'}</dd>
              </dl>
            ) : <p>本次回滚成功，未触发降级预案。</p>}
          </article>
        )}
      </div>

      <footer className="page-foot">
        <span>共 {receipts.length} 张回滚回执</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
