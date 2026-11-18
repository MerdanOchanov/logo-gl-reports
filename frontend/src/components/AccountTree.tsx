import { useEffect, useMemo, useState, type ReactNode } from 'react'
import type { Account } from '../api'

const MAX_RENDER = 2500
const EXTRA: [string, string][] = [
  ['units', 'Единица'],
  ['full', 'Полное название'],
  ['note', 'Примечание'],
  ['created', 'Создан'],
  ['specode_name', 'Метка (расшифровка)'],
]

/** Дерево плана счетов с поиском. extra — дополнительное содержимое строки (например, чекбокс). */
export default function AccountTree({
  accounts,
  showSpecode = false,
  extraCols = false,
  renderAction,
  onPick,
  initialLevel = 1,
}: {
  accounts: Account[]
  showSpecode?: boolean
  /** настраиваемые колонки (единица, полное название, примечание, создан, расшифровка метки) и фильтр «только валютные» */
  extraCols?: boolean
  renderAction?: (a: Account) => ReactNode
  onPick?: (a: Account) => void
  initialLevel?: number
}) {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState<Set<string>>(new Set())
  const [cols, setCols] = useState<Record<string, boolean>>(() => {
    try {
      return JSON.parse(localStorage.getItem('acc-cols') || '{}')
    } catch {
      return {}
    }
  })
  const [curOnly, setCurOnly] = useState(false)
  const setCol = (k: string, v: boolean) =>
    setCols((p) => {
      const n = { ...p, [k]: v }
      try {
        localStorage.setItem('acc-cols', JSON.stringify(n))
      } catch {
        /* без сохранения */
      }
      return n
    })
  const show = (k: string) => extraCols && !!cols[k]

  const { kids, roots, byCode } = useMemo(() => {
    const kids = new Map<string, Account[]>()
    const byCode = new Map<string, Account>()
    for (const a of accounts) byCode.set(a.code, a)
    const roots: Account[] = []
    for (const a of accounts) {
      if (a.parent && byCode.has(a.parent)) {
        const l = kids.get(a.parent) ?? []
        l.push(a)
        kids.set(a.parent, l)
      } else roots.push(a)
    }
    return { kids, roots, byCode }
  }, [accounts])

  useEffect(() => {
    // стартово раскрыты узлы до initialLevel (верхний уровень виден всегда)
    const s = new Set<string>()
    if (initialLevel > 0)
      for (const a of accounts) if (kids.has(a.code) && a.level < initialLevel) s.add(a.code)
    setOpen(s)
  }, [accounts, kids, initialLevel])

  const matchSet = useMemo(() => {
    const t = q.trim().toLowerCase()
    if (!t && !curOnly) return null
    const s = new Set<string>()
    for (const a of accounts) {
      if (curOnly && !a.is_currency) continue
      if (!t || a.code.toLowerCase().startsWith(t) || a.name?.toLowerCase().includes(t) || (a.specode ?? '').toLowerCase().includes(t)) {
        let c: Account | undefined = a
        while (c) {
          s.add(c.code)
          c = c.parent ? byCode.get(c.parent) : undefined
        }
      }
    }
    return s
  }, [q, accounts, byCode, curOnly])

  const list = useMemo(() => {
    const out: { a: Account; depth: number }[] = []
    const walk = (a: Account, depth: number) => {
      if (matchSet && !matchSet.has(a.code)) return
      out.push({ a, depth })
      if (out.length > MAX_RENDER) return
      if (matchSet || open.has(a.code)) for (const k of kids.get(a.code) ?? []) walk(k, depth + 1)
    }
    for (const r of roots) walk(r, 0)
    return out
  }, [roots, kids, open, matchSet])

  const toggle = (c: string) =>
    setOpen((p) => {
      const s = new Set(p)
      if (s.has(c)) s.delete(c)
      else s.add(c)
      return s
    })

  return (
    <div className="acc-tree">
      <div className="acc-search no-print">
        <input type="search" placeholder="Поиск по коду, названию или метке…" value={q} onChange={(e) => setQ(e.target.value)} />
        <button className="btn small" onClick={() => setOpen(new Set(accounts.filter((a) => kids.has(a.code)).map((a) => a.code)))}>
          Раскрыть все
        </button>
        <button className="btn small" onClick={() => setOpen(new Set())}>
          Свернуть
        </button>
        <span className="muted">{accounts.length.toLocaleString('ru-RU')} счетов</span>
      </div>
      {extraCols && (
        <div className="acc-search no-print">
          <label className="chk">
            <input type="checkbox" checked={curOnly} onChange={(e) => setCurOnly(e.target.checked)} /> только валютные
          </label>
          <span className="muted">Колонки:</span>
          {EXTRA.map(([k, label]) => (
            <label key={k} className="chk">
              <input type="checkbox" checked={!!cols[k]} onChange={(e) => setCol(k, e.target.checked)} /> {label}
            </label>
          ))}
        </div>
      )}
      <div className="acc-scroll">
        <table className="simple acc">
          <thead>
            <tr>
              <th style={{ width: 230 }}>Код</th>
              <th>Название</th>
              {showSpecode && <th style={{ width: 130 }}>Метка Logo</th>}
              {show('units') && <th style={{ width: 70 }}>Единица</th>}
              {show('full') && <th>Полное название</th>}
              {show('note') && <th>Примечание</th>}
              {show('created') && <th style={{ width: 110 }}>Создан</th>}
              {show('specode_name') && <th>Метка (расшифровка)</th>}
              {renderAction && <th style={{ width: 90 }} />}
            </tr>
          </thead>
          <tbody>
            {list.slice(0, MAX_RENDER).map(({ a, depth }) => {
              const has = kids.has(a.code)
              return (
                <tr key={a.code} className={(onPick ? 'click' : '') + (extraCols && a.inactive ? ' dim' : '')} title={extraCols && a.inactive ? 'Счёт не используется' : undefined} onDoubleClick={() => onPick?.(a)}>
                  <td className="nowrap" style={{ paddingLeft: 6 + depth * 14 }}>
                    {has ? (
                      <button className="tog" onClick={() => toggle(a.code)}>
                        {matchSet || open.has(a.code) ? '▾' : '▸'}
                      </button>
                    ) : (
                      <span className="tog-sp" />
                    )}
                    <span className={has ? 'bold' : ''}>{a.code}</span>
                  </td>
                  <td title={a.full_name || undefined}>
                    {a.name}
                    {extraCols && a.is_currency && (
                      <span className="muted" title="Валютный счёт" style={{ marginLeft: 6 }}>
                        $
                      </span>
                    )}
                  </td>
                  {showSpecode && <td className="mono">{a.specode}</td>}
                  {show('units') && <td>{a.units}</td>}
                  {show('full') && <td>{a.full_name}</td>}
                  {show('note') && <td>{a.note}</td>}
                  {show('created') && <td className="nowrap" title={a.created_by ? `Создал: ${a.created_by}` : undefined}>{a.created ? a.created.split('-').reverse().join('.') : ''}</td>}
                  {show('specode_name') && <td>{a.specode_name}</td>}
                  {renderAction && <td>{renderAction(a)}</td>}
                </tr>
              )
            })}
          </tbody>
        </table>
        {list.length > MAX_RENDER && <div className="muted pad">Показаны первые {MAX_RENDER} строк — уточните поиск.</div>}
        {list.length === 0 && <div className="muted pad">Ничего не найдено</div>}
      </div>
    </div>
  )
}
