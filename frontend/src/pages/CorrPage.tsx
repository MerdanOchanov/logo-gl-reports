import { useEffect, useState } from 'react'
import { api, errMsg, type CorrStats, type DisputedFiche, type Firm } from '../api'
import FicheModal from '../components/FicheModal'
import { ErrorBox, NotConfigured, useLoad } from '../components/ui'
import { useDataset } from '../dataset'
import { fmtDate, fmtMoney } from '../format'

const METHODS: [string, string, string][] = [
  ['SEQ', 'По порядку', 'Пары определены однозначно по порядку проводок ордера: дебеты и кредиты разобраны последовательно'],
  ['CROSS', 'По перекрёстной ссылке', 'Корреспондирующий счёт указан в самой проводке (перекрёстная ссылка)'],
  ['PROP', 'Пропорционально', 'Многосторонние ордера: сумма распределена пропорционально (расчётные пары)'],
  ['MANUAL', 'Вручную', 'Пары закреплены вручную бухгалтером'],
]
const METHOD_RU: Record<string, string> = Object.fromEntries(METHODS.map(([k, l]) => [k, l.toLowerCase()]))

/** Пояснение сервера: служебное перечисление способов (method: SEQ — …) заменяется русскими названиями. */
function ruNote(note: string): string {
  const cut = note.search(/;?\s*method:/i)
  const base = (cut >= 0 ? note.slice(0, cut) : note).replace(/[;\s]+$/, '')
  return base + (cut >= 0 ? '; способы определения пар: ' + METHODS.map(([, l]) => l.toLowerCase()).join(', ') : '')
}

interface Norm {
  total: number
  disputed: number
  methods: Record<string, number>
}

/** Приводит ответ /corr/stats к одному виду; сервер может отдавать суммы либо {amount, share}. */
function normalize(s: CorrStats | undefined): Norm {
  const methods: Record<string, number> = {}
  for (const [k, v] of Object.entries(s?.methods ?? {})) {
    const n = typeof v === 'number' ? v : (v?.amount ?? 0)
    methods[k.toUpperCase()] = n
  }
  const sum = Object.values(methods).reduce((a, b) => a + b, 0)
  return { total: s?.total && s.total > 0 ? s.total : sum, disputed: s?.disputed ?? 0, methods }
}

const pct = (a: number, t: number) => (t > 0 ? (a / t) * 100 : 0)
const fmtPct = (v: number) => (v < 0.05 ? '' : v.toFixed(1).replace('.', ',') + ' %')

export default function CorrPage() {
  const { data: firms, error: firmsErr } = useLoad<Firm[]>(() => api.firms(), [])
  const { data: ds } = useDataset()
  const years = (firms ?? []).filter((f) => f.enabled && f.synced_at).map((f) => f.year).sort((a, b) => b - a)
  const [stats, setStats] = useState<Record<number, Norm | string>>({})
  const [year, setYear] = useState<number | null>(null)
  const [note, setNote] = useState<string | null>(null)

  useEffect(() => {
    if (!years.length) return
    let alive = true
    setStats({})
    ;(async () => {
      // по 4 запроса одновременно
      const queue = [...years]
      const worker = async () => {
        for (;;) {
          const y = queue.shift()
          if (y === undefined || !alive) return
          try {
            const s = await api.corrStats(y)
            if (s?.note) setNote(s.note)
            if (alive) setStats((p) => ({ ...p, [y]: normalize(s) }))
          } catch (e) {
            if (alive) setStats((p) => ({ ...p, [y]: errMsg(e) }))
          }
        }
      }
      await Promise.all([worker(), worker(), worker(), worker()])
    })()
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [firms])

  useEffect(() => {
    if (year === null && years.length) setYear(years[0])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [firms])

  const allEmpty = years.length > 0 && years.every((y) => typeof stats[y] === 'object' && (stats[y] as Norm).total === 0)

  return (
    <div className="page">
      <div className="page-head">
        <h2>Корреспонденция</h2>
      </div>
      <p className="hint">
        Корреспонденция (пары Дт/Кт) нужна для шахматки, анализа счёта, главной книги и журнала-ордера. Здесь видно, как определены пары по годам и какие ордера
        спорные — там порядок проводок противоречит заполненной перекрёстной ссылке.
      </p>
      <ErrorBox error={firmsErr} />
      {firms && years.length === 0 && (
        ds && (ds.state === 'new' || ds.books_total === 0) ? (
          <NotConfigured text="Корреспонденция считается по проводкам книг Logo — добавьте книги за нужные годы." />
        ) : (
          <div className="msg warn">Нет загруженных лет: корреспонденция появится после загрузки данных (Настройки → Книги и годы).</div>
        )
      )}
      {note && <div className="msg ok" style={{ justifyContent: 'flex-start' }}>{ruNote(note)}</div>}
      {allEmpty && !note && <div className="msg ok">Корреспонденция ещё не рассчитана.</div>}
      {years.length > 0 && (
      <table className="simple" style={{ maxWidth: 1100 }}>
        <thead>
          <tr>
            <th style={{ width: 60 }}>Год</th>
            {METHODS.map(([k, l]) => (
              <th key={k} className="r" title={METHODS.find((m) => m[0] === k)?.[2]}>
                {l}
              </th>
            ))}
            <th>Доля оборота</th>
            <th className="r">Спорных ордеров</th>
          </tr>
        </thead>
        <tbody>
          {years.map((y) => {
            const s = stats[y]
            return (
              <tr key={y} className={'click' + (year === y ? ' sel' : '')} onClick={() => setYear(y)}>
                <td>
                  <b>{y}</b>
                </td>
                {typeof s === 'string' ? (
                  <td colSpan={6} className="neg">
                    {s}
                  </td>
                ) : !s ? (
                  <td colSpan={6} className="muted">
                    загрузка…
                  </td>
                ) : s.total === 0 && !s.disputed ? (
                  <td colSpan={6} className="muted">
                    не рассчитано
                  </td>
                ) : (
                  <>
                    {METHODS.map(([k]) => (
                      <td key={k} className="num">
                        {s.total ? fmtPct(pct(s.methods[k] ?? 0, s.total)) : ''}
                      </td>
                    ))}
                    <td>
                      {s.total > 0 && (
                        <div className="share-bar">
                          {METHODS.map(([k]) => (
                            <i key={k} className={'sb-' + k} style={{ width: pct(s.methods[k] ?? 0, s.total) + '%' }} title={`${METHOD_RU[k] ?? k}: ${fmtPct(pct(s.methods[k] ?? 0, s.total))}`} />
                          ))}
                        </div>
                      )}
                    </td>
                    <td className={'num' + (s.disputed ? ' neg' : '')}>{s.disputed || ''}</td>
                  </>
                )}
              </tr>
            )
          })}
        </tbody>
      </table>
      )}
      {year !== null && <Disputed year={year} />}
    </div>
  )
}

function Disputed({ year }: { year: number }) {
  const { data, error, loading, reload } = useLoad<DisputedFiche[]>(() => api.corrDisputed(year), [year])
  const [open, setOpen] = useState<DisputedFiche | null>(null)
  const [q, setQ] = useState('')
  const list = (data ?? []).filter((d) => !q.trim() || `${d.fiche_no} ${d.text ?? ''}`.toLowerCase().includes(q.trim().toLowerCase()))
  return (
    <>
      <div className="page-head" style={{ marginTop: 18 }}>
        <h3 style={{ fontSize: 15 }}>Спорные ордера за {year} год</h3>
        <input type="search" placeholder="Поиск по номеру или тексту…" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 260 }} />
        {data && <span className="muted">всего: {data.length}</span>}
      </div>
      <ErrorBox error={error} />
      {loading && !data && <div className="muted pad">Загрузка…</div>}
      {data && data.length === 0 && <div className="muted pad">Спорных ордеров нет.</div>}
      {list.length > 0 && (
        <div className="acc-scroll">
          <table className="simple">
            <thead>
              <tr>
                <th style={{ width: 90 }}>Дата</th>
                <th style={{ width: 100 }}>Ордер</th>
                <th className="r" style={{ width: 130 }}>
                  Сумма
                </th>
                <th>Содержание</th>
              </tr>
            </thead>
            <tbody>
              {list.slice(0, 500).map((d) => (
                <tr key={`${d.firm}-${d.fiche_id}`} className="click" onClick={() => setOpen(d)} title="Открыть ордер и поправить пары">
                  <td className="nowrap">{fmtDate(d.date)}</td>
                  <td className="nowrap">{d.fiche_no}</td>
                  <td className="num">{fmtMoney(d.amount)}</td>
                  <td className="txt-cell" style={{ maxWidth: 700 }}>
                    {d.text}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {list.length > 500 && <div className="muted pad">Показаны первые 500 из {list.length}. Уточните поиск.</div>}
        </div>
      )}
      {open && <FicheModal firm={open.firm} ficheId={open.fiche_id} ficheNo={open.fiche_no} editable onClose={() => setOpen(null)} onChanged={reload} />}
    </>
  )
}
