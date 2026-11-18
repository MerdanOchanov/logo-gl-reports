import { useEffect, useState } from 'react'
import { api, errMsg, type AccountLink, type AutoItem, type AutoReport } from '../api'
import { ErrorBox } from '../components/ui'
import { askConfirm } from '../components/confirm'
import { fmtMoney } from '../format'
import { useYears } from '../years'

const pct = (v?: number | null) => (v == null ? '—' : (v * 100).toFixed(1).replace('.', ',') + ' %')
const METHOD: Record<string, string> = {
  manual: 'вручную',
  'auto:code+name': 'код и название',
  'auto:code+similar': 'код, похожее название',
  'auto:name': 'по названию',
  'auto:name~': 'по похожему названию',
}

/** Связи счетов между годами: автоподбор (предпросмотр и применение), таблица сохранённых связей, ручная правка. */
export default function AccountLinks() {
  const YEARS = useYears()
  const [from, setFrom] = useState(2025)
  const [to, setTo] = useState(2026)
  const [rep, setRep] = useState<AutoReport | null>(null)
  const [items, setItems] = useState<AccountLink[]>([])
  const [total, setTotal] = useState(0)
  const [edited, setEdited] = useState<Record<number, Partial<AccountLink>>>({})
  const [q, setQ] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [nw, setNw] = useState({ code_from: '', code_to: '' })

  const load = async () => {
    try {
      const r = await api.accountLinks({ year_from: from, year_to: to, q: q.trim() || undefined, limit: 500 })
      setItems(r.items)
      setTotal(r.total)
      setEdited({})
    } catch (e) {
      setErr(errMsg(e))
    }
  }
  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [from, to])

  const run = async (f: () => Promise<void>) => {
    setBusy(true)
    setErr(null)
    try {
      await f()
    } catch (e) {
      setErr(errMsg(e))
    } finally {
      setBusy(false)
    }
  }
  const preview = () =>
    run(async () => {
      setRep(await api.autoLinks(from, to))
      setInfo(null)
    })
  const apply = () =>
    run(async () => {
      if (!(await askConfirm(`Записать найденные связи ${from} → ${to}? Ручные связи не затрагиваются, прежние автоматические заменятся.`, { okLabel: 'Записать' }))) return
      const r = await api.applyAutoLinks(from, to)
      setRep(r)
      setInfo(`Автоподбор применён: записано связей ${r.applied}.`)
      await load()
    })
  const addLink = (code_from: string, code_to: string) =>
    run(async () => {
      await api.createLinks([{ year_from: from, code_from, year_to: to, code_to }])
      setInfo(`Связь ${code_from} → ${code_to} сохранена.`)
      await load()
    })
  const saveRow = (l: AccountLink) =>
    run(async () => {
      await api.updateLink(l.id!, { ...l, ...edited[l.id!] })
      setInfo('Связь изменена.')
      await load()
    })
  const del = (l: AccountLink) =>
    run(async () => {
      if (!(await askConfirm(`Удалить связь ${l.code_from} → ${l.code_to}?`, { okLabel: 'Удалить', danger: true }))) return
      await api.deleteLink(l.id!)
      await load()
    })
  const ed = (l: AccountLink, p: Partial<AccountLink>) => setEdited((e) => ({ ...e, [l.id!]: { ...e[l.id!], ...p } }))

  const s = rep?.summary

  return (
    <>
      <p className="hint">
        Связь говорит, что счёт одного года — это тот же счёт другого года под другим кодом. «Сравнение лет» и отчёты по нескольким годам приводят счета к плану выбранного года по этим связям; где связи нет, счета
        сводятся по совпадению кода.
      </p>
      <ErrorBox error={err} onClose={() => setErr(null)} />
      {info && <div className="msg ok">{info}</div>}
      <div className="toolbar">
        <label className="inline">
          из года
          <select value={from} onChange={(e) => setFrom(Number(e.target.value))}>
            {YEARS.map((y) => (
              <option key={y}>{y}</option>
            ))}
          </select>
        </label>
        <label className="inline">
          в год
          <select value={to} onChange={(e) => setTo(Number(e.target.value))}>
            {YEARS.map((y) => (
              <option key={y}>{y}</option>
            ))}
          </select>
        </label>
        <button className="btn" onClick={preview} disabled={busy || from === to}>
          {busy ? 'Подождите…' : 'Подобрать автоматически (посмотреть)'}
        </button>
        <button className="btn primary" onClick={apply} disabled={busy || from === to || !rep}>
          Применить подбор
        </button>
      </div>

      {s && (
        <>
          <div className="statrow">
            <div className="stat">
              <b>{s.all.matched}</b>найдено пар из {s.all.total}
            </div>
            <div className="stat">
              <b>{s.all.disputed}</b>спорных
            </div>
            <div className="stat">
              <b>{s.all.unmatched}</b>без пары
            </div>
            <div className="stat">
              <b>{pct(s.leaves_with_turnover.matched_turnover_share)}</b>оборота листьев сопоставлено
            </div>
          </div>
          {rep!.disputed.length > 0 && (
            <>
              <h4>Спорные (по обороту) — выберите нужный счёт вручную</h4>
              <div className="acc-scroll" style={{ maxHeight: 260 }}>
                <table className="simple">
                  <thead>
                    <tr>
                      <th style={{ width: 140 }}>Счёт {s.year_from}</th>
                      <th>Название</th>
                      <th className="r" style={{ width: 120 }}>
                        Оборот
                      </th>
                      <th>Кандидаты в {s.year_to}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rep!.disputed.map((r) => (
                      <DisputedRow key={r.code_from} r={r} onPick={addLink} />
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
          {rep!.unmatched.length > 0 && (
            <>
              <h4>Без пары (по обороту)</h4>
              <div className="acc-scroll" style={{ maxHeight: 200 }}>
                <table className="simple">
                  <tbody>
                    {rep!.unmatched.map((r) => (
                      <tr key={r.code_from}>
                        <td style={{ width: 140 }}>{r.code_from}</td>
                        <td>{r.name_from}</td>
                        <td className="num" style={{ width: 120 }}>
                          {fmtMoney(r.turnover)}
                        </td>
                        <td style={{ width: 260 }}>
                          <ManualAdd from={r.code_from} onAdd={addLink} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      )}

      <h4>
        Сохранённые связи {from} → {to}: {total}
      </h4>
      <div className="toolbar">
        <input type="search" placeholder="Поиск по коду (начало)…" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && load()} />
        <button className="btn small" onClick={load}>
          Найти
        </button>
      </div>
      <div className="acc-scroll">
        <table className="simple">
          <thead>
            <tr>
              <th style={{ width: 170 }}>Счёт {from}</th>
              <th style={{ width: 30 }}>→</th>
              <th style={{ width: 170 }}>Счёт {to}</th>
              <th style={{ width: 150 }}>Как найдена</th>
              <th>Примечание</th>
              <th style={{ width: 150 }} />
            </tr>
          </thead>
          <tbody>
            {items.map((l) => {
              const e = edited[l.id!] ?? {}
              return (
                <tr key={l.id}>
                  <td>
                    <input type="text" value={e.code_from ?? l.code_from} onChange={(ev) => ed(l, { code_from: ev.target.value })} />
                  </td>
                  <td>→</td>
                  <td>
                    <input type="text" value={e.code_to ?? l.code_to} onChange={(ev) => ed(l, { code_to: ev.target.value })} />
                  </td>
                  <td className="muted">
                    {METHOD[l.method ?? ''] ?? l.method}
                    {l.method !== 'manual' && l.confidence != null ? ` (${Math.round(l.confidence * 100)} %)` : ''}
                  </td>
                  <td>
                    <input type="text" value={e.note ?? l.note ?? ''} onChange={(ev) => ed(l, { note: ev.target.value })} />
                  </td>
                  <td className="actions">
                    {edited[l.id!] && (
                      <button className="btn small primary" onClick={() => saveRow(l)} disabled={busy}>
                        Сохранить
                      </button>
                    )}
                    <button className="btn small danger" onClick={() => del(l)} disabled={busy}>
                      ✕
                    </button>
                  </td>
                </tr>
              )
            })}
            {items.length === 0 && (
              <tr>
                <td colSpan={6} className="muted">
                  Связей между этими годами нет.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <h4>Добавить связь вручную ({from} → {to})</h4>
      <div className="toolbar">
        <input type="text" style={{ width: 180 }} placeholder={`счёт ${from}`} value={nw.code_from} onChange={(e) => setNw({ ...nw, code_from: e.target.value })} />
        <span>→</span>
        <input type="text" style={{ width: 180 }} placeholder={`счёт ${to}`} value={nw.code_to} onChange={(e) => setNw({ ...nw, code_to: e.target.value })} />
        <button
          className="btn primary"
          disabled={busy || !nw.code_from.trim() || !nw.code_to.trim() || from === to}
          onClick={() => addLink(nw.code_from.trim(), nw.code_to.trim()).then(() => setNw({ code_from: '', code_to: '' }))}
        >
          Добавить
        </button>
      </div>
    </>
  )
}

function DisputedRow({ r, onPick }: { r: AutoItem; onPick: (from: string, to: string) => void }) {
  return (
    <tr>
      <td>{r.code_from}</td>
      <td title={r.reason}>
        {r.name_from}
        {r.reason && <div className="muted">{r.reason}</div>}
      </td>
      <td className="num">{fmtMoney(r.turnover)}</td>
      <td>
        {r.candidates?.map((c) => (
          <button key={c.code} className="btn small" style={{ marginRight: 4 }} title={`${c.name} (сходство ${Math.round(c.score * 100)} %)`} onClick={() => onPick(r.code_from, c.code)}>
            {c.code}
          </button>
        ))}
        <ManualAdd from={r.code_from} onAdd={onPick} />
      </td>
    </tr>
  )
}

function ManualAdd({ from, onAdd }: { from: string; onAdd: (from: string, to: string) => void }) {
  const [v, setV] = useState('')
  return (
    <span style={{ display: 'inline-flex', gap: 3 }}>
      <input type="text" style={{ width: 110 }} placeholder="другой счёт" value={v} onChange={(e) => setV(e.target.value)} />
      <button className="btn small" disabled={!v.trim()} onClick={() => (onAdd(from, v.trim()), setV(''))}>
        Связать
      </button>
    </span>
  )
}

