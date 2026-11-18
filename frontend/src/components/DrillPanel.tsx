import { useEffect, useState } from 'react'
import { api, errMsg, saveBlob, type DrillLine, type DrillRequest, type DrillResult } from '../api'
import { fmtDate, fmtMoney, fmtQty } from '../format'
import ColumnsMenu, { EXTRA_KEYS, EXTRA_LABELS } from './ColumnsMenu'
import { ErrorBox, Modal } from './ui'
import FicheModal from './FicheModal'
import { openAccountCard } from '../report/cardLink'

const PAGE = 200

export interface DrillTarget {
  title: string
  req: Omit<DrillRequest, 'limit' | 'offset'>
  /** Параметры отчёта — для кнопки «Открыть карточку счёта» */
  ctx?: { period: { date_from: string; date_to: string }; include_closing: boolean; currency?: string; scale?: number; denominate?: boolean }
  /** Пояснение над таблицей */
  note?: string
  /** Дополнительные колонки, включённые для этого открытия (например «Количество» из графы количества) */
  extra?: string[]
}

const LS_KEY = 'buh.drill.extra'
function loadExtra(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(LS_KEY) ?? '[]')
    return Array.isArray(v) ? v.filter((k) => EXTRA_KEYS.includes(k)) : []
  } catch {
    return []
  }
}

export default function DrillPanel({ target, onClose }: { target: DrillTarget; onClose: () => void }) {
  const [offset, setOffset] = useState(0)
  const [data, setData] = useState<DrillResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [fiche, setFiche] = useState<DrillLine | null>(null)
  const [saved, setSaved] = useState<string[]>(loadExtra)
  const extra = EXTRA_KEYS.filter((k) => saved.includes(k) || target.extra?.includes(k))
  const setExtra = (v: string[]) => {
    setSaved(v)
    try {
      localStorage.setItem(LS_KEY, JSON.stringify(v))
    } catch {
      /* не сохранилось — не страшно */
    }
  }
  const [exporting, setExporting] = useState(false)
  const onExcel = async () => {
    setExporting(true)
    try {
      saveBlob(await api.exportDrill({ ...target.req, extra, limit: 50000, title: target.title }), 'Расшифровка')
    } catch (e) {
      setError(errMsg(e))
    } finally {
      setExporting(false)
    }
  }
  const cell = (l: DrillLine, k: string) => {
    const v = (l as unknown as Record<string, unknown>)[k]
    if (k === 'qty') return fmtQty(v)
    return v == null ? '' : String(v)
  }

  useEffect(() => {
    let alive = true
    setLoading(true)
    api
      .drill({ ...target.req, limit: PAGE, offset })
      .then((d) => alive && (setData(d), setError(null)))
      .catch((e) => alive && setError(errMsg(e)))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [target, offset])

  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE)) : 1
  const page = Math.floor(offset / PAGE) + 1

  return (
    <>
      <Modal title={'Расшифровка: ' + target.title} onClose={onClose} wide>
        <ErrorBox error={error} />
        {loading && !data && <div className="muted pad">Загрузка…</div>}
        {data && (
          <>
            <div className="drill-sum">
              Проводок: <b>{data.total.toLocaleString('ru-RU')}</b> · Дебет: <b className="mono">{fmtMoney(data.sum_dt) || '0,00'}</b> · Кредит:{' '}
              <b className="mono">{fmtMoney(data.sum_kt) || '0,00'}</b>
              {loading && <span className="muted"> · обновление…</span>}
            </div>
            {target.note && <div className="hint">{target.note}</div>}
            {(target.req.mode === 'open' || target.req.mode === 'close') && (
              <div className="drill-sum">
                Сальдо (Дебет − Кредит): <b className="mono">{fmtMoney(data.sum_dt - data.sum_kt) || '0,00'}</b>
              </div>
            )}
            <div className="toolbar" style={{ margin: '4px 0' }}>
              <ColumnsMenu selected={extra} onChange={setExtra} />
              <button className="btn small" onClick={onExcel} disabled={exporting} title="Выгрузить все проводки расшифровки (до 50 000) с выбранными колонками">
                {exporting ? 'Выгрузка…' : 'Excel'}
              </button>
            </div>
            <div className="drill-box">
              <table className="simple">
                <thead>
                  <tr>
                    <th>Дата</th>
                    <th>Документ</th>
                    <th>Счёт</th>
                    <th>Корр. счёт</th>
                    <th className="r">Дебет</th>
                    <th className="r">Кредит</th>
                    <th>Содержание</th>
                    {extra.map((k) => (
                      <th key={k} className={k === 'qty' ? 'r' : ''}>
                        {EXTRA_LABELS[k]}
                      </th>
                    ))}
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {data.lines.map((l, i) => (
                    <tr key={i} className="click" onClick={() => setFiche(l)} title="Открыть ордер целиком">
                      <td className="nowrap">{fmtDate(l.date)}</td>
                      <td className="nowrap">{l.fiche_no}</td>
                      <td className="nowrap" title={l.account_name}>
                        {l.account}
                      </td>
                      <td className="nowrap">
                        {l.corr}
                        {l.method === 'PROP' && <span className="tag" title="Корреспонденция расчётная (пропорционально)"> расч.</span>}
                      </td>
                      <td className="num">{fmtMoney(l.dt)}</td>
                      <td className="num">{fmtMoney(l.kt)}</td>
                      <td className="txt-cell" title={l.text}>
                        {l.text}
                      </td>
                      {extra.map((k) => (
                        <td key={k} className={k === 'qty' ? 'num' : k === 'text2_4' ? 'txt-cell' : 'nowrap'} title={k === 'text2_4' ? cell(l, k) : undefined}>
                          {cell(l, k)}
                        </td>
                      ))}
                      <td className="nowrap">
                        <button
                          className="btn small ghost card-btn"
                          title="Открыть карточку счёта этой проводки за тот же период (в новой вкладке)"
                          onClick={(e) => {
                            e.stopPropagation()
                            openAccountCard(l.account, target.ctx ?? { period: target.req.period, include_closing: target.req.include_closing })
                          }}
                        >
                          Открыть карточку счёта
                        </button>
                      </td>
                    </tr>
                  ))}
                  {data.lines.length === 0 && (
                    <tr>
                      <td colSpan={8 + extra.length} className="muted pad">
                        Проводок нет
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            {pages > 1 && (
              <div className="pager">
                <button className="btn small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
                  ← Назад
                </button>
                <span>
                  Страница {page} из {pages}
                </span>
                <button className="btn small" disabled={page >= pages} onClick={() => setOffset(offset + PAGE)}>
                  Вперёд →
                </button>
              </div>
            )}
          </>
        )}
      </Modal>
      {fiche && <FicheModal firm={fiche.firm} ficheId={fiche.fiche_id} ficheNo={fiche.fiche_no} onClose={() => setFiche(null)} />}
    </>
  )
}

