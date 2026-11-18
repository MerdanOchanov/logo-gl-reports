import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import type { Column, Result, ResultRow } from '../api'
import { fmtCompact, fmtInt, fmtMoney, fmtPlain, fmtQty, type NumFmt } from '../format'

/**
 * Универсальная таблица-дерево для Result (используется всеми типами отчётов).
 * - двухъярусная шапка по columns[].group
 * - закреплены шапка, итоговая строка, графы «код», «название» и первые N денежных граф
 * - раскрытие/свёртывание по parent, «раскрыть до уровня N»
 * - рендерятся только видимые строки (окно по прокрутке, фиксированная высота строки)
 * - поиск по коду/названию, выделение строк и копирование в буфер (TSV для Excel)
 * - подсветка строки и графы под курсором
 * - при печати рендерятся все раскрытые строки
 */

export const ROW_H = 22
const HEAD_H = 22
const CODE_W = 104
const NAME_W = 300
const OVERSCAN = 12

export interface CellClick {
  row: ResultRow
  col: Column
}

interface Props {
  result: Result
  /** Можно ли расшифровывать ячейку (по умолчанию — денежные ячейки строк с drill) */
  isDrillable?: (row: ResultRow, col: Column) => boolean
  onCellClick?: (c: CellClick) => void
  /** Открыть ордер строки (если сервер дал row.fiche) */
  onOpenFiche?: (row: ResultRow) => void
  /** Открыть карточку счёта строки (для карточки счёта) */
  onOpenCard?: (row: ResultRow) => void
  /** Заголовки двух левых граф (по умолчанию «Код» и «Наименование») */
  codeTitle?: string
  nameTitle?: string
}

const FMT_LABEL: Record<NumFmt, string> = { full: 'полные числа', thousand: 'тысячи', million: 'миллионы', compact: 'авто (тыс./млн)' }

export default function ResultTable({ result, isDrillable, onCellClick, onOpenFiche, onOpenCard, codeTitle = 'Код', nameTitle = 'Наименование' }: Props) {
  // строку «Итого» сервер дублирует в rows — рисуем её только в подвале
  const hasTotals = !!result.totals && Object.keys(result.totals).length > 0
  const rows = useMemo(() => (hasTotals ? result.rows.filter((r) => r.kind !== 'total') : result.rows), [result.rows, hasTotals])
  const cols = useMemo(
    () => result.columns.filter((c) => !(c.type === 'text' && (c.key === 'code' || c.key === 'name'))),
    [result.columns],
  )

  const [fmt, setFmt] = useState<NumFmt>('full')
  const [frozen, setFrozen] = useState(0)
  const [query, setQuery] = useState('')
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [anchor, setAnchor] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)

  const moneyW = fmt === 'full' ? 124 : fmt === 'compact' ? 118 : 84
  // ширина графы: не меньше обычной, но заголовок помещается целиком (до 200 px)
  const colWidth = (c: Column) =>
    c.type === 'money' || c.type === 'qty' ? Math.max(c.type === 'qty' ? 96 : moneyW, Math.min(200, Math.round(c.title.length * 6.6) + 16)) : c.type === 'int' ? 70 : Math.max(160, Math.min(200, Math.round(c.title.length * 6.6) + 16))

  const childCount = useMemo(() => {
    const m = new Map<string, number>()
    for (const r of rows) if (r.parent) m.set(r.parent, (m.get(r.parent) ?? 0) + 1)
    return m
  }, [rows])
  const maxLevel = useMemo(() => rows.reduce((a, r) => Math.max(a, r.level), 0), [rows])

  // Свёрнутые узлы. Начальное состояние: всё раскрыто, а для больших отчётов — до уровня 2.
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const applyLevel = useCallback(
    (n: number | 'all') => {
      if (n === 'all') {
        setCollapsed(new Set())
        return
      }
      const s = new Set<string>()
      for (const r of rows) if (childCount.has(r.id) && r.level >= n - 1) s.add(r.id)
      setCollapsed(s)
    },
    [rows, childCount],
  )
  useEffect(() => {
    if (rows.length > 600) applyLevel(2)
    else setCollapsed(new Set())
    setSel(new Set())
    setAnchor(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows])

  const toggle = useCallback(
    (id: string) =>
      setCollapsed((prev) => {
        const s = new Set(prev)
        if (s.has(id)) s.delete(id)
        else s.add(id)
        return s
      }),
    [],
  )

  const q = query.trim().toLowerCase()
  const visible = useMemo(() => {
    if (q) {
      // поиск: совпавшие строки и все их предки (свёрнутость игнорируется)
      const byId = new Map(rows.map((r) => [r.id, r]))
      const keep = new Set<string>()
      for (const r of rows) {
        if ((r.code ?? '').toLowerCase().includes(q) || (r.name ?? '').toLowerCase().includes(q)) {
          let cur: ResultRow | undefined = r
          while (cur && !keep.has(cur.id)) {
            keep.add(cur.id)
            cur = cur.parent ? byId.get(cur.parent) : undefined
          }
        }
      }
      return rows.filter((r) => keep.has(r.id))
    }
    const vis = new Map<string, boolean>()
    const out: ResultRow[] = []
    for (const r of rows) {
      const v = r.parent == null ? true : !!vis.get(r.parent) && !collapsed.has(r.parent)
      vis.set(r.id, v)
      if (v) out.push(r)
    }
    return out
  }, [rows, collapsed, q])

  // Окно прокрутки
  const boxRef = useRef<HTMLDivElement>(null)
  const [scrollTop, setScrollTop] = useState(0)
  const [boxH, setBoxH] = useState(600)
  const [printing, setPrinting] = useState(false)
  const rafRef = useRef(0)

  useLayoutEffect(() => {
    const el = boxRef.current
    if (!el) return
    const upd = () => setBoxH(el.clientHeight)
    upd()
    const ro = new ResizeObserver(upd)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  useEffect(() => {
    const b = () => flushSync(() => setPrinting(true))
    const a = () => setPrinting(false)
    window.addEventListener('beforeprint', b)
    window.addEventListener('afterprint', a)
    return () => {
      window.removeEventListener('beforeprint', b)
      window.removeEventListener('afterprint', a)
    }
  }, [])

  const onScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const t = (e.target as HTMLDivElement).scrollTop
    cancelAnimationFrame(rafRef.current)
    rafRef.current = requestAnimationFrame(() => setScrollTop((p) => (Math.abs(p - t) >= ROW_H / 2 ? t : p)))
  }

  const total = visible.length
  let start = 0
  let end = total
  if (!printing) {
    start = Math.max(0, Math.floor(scrollTop / ROW_H) - OVERSCAN)
    end = Math.min(total, Math.ceil((scrollTop + boxH) / ROW_H) + OVERSCAN)
  }
  const slice = visible.slice(start, end)

  // Шапка
  const groups = useMemo(() => {
    const out: { title: string; span: number; plain: boolean }[] = []
    for (const c of cols) {
      const g = c.group ?? ''
      const last = out[out.length - 1]
      if (g && last && !last.plain && last.title === g) last.span++
      else out.push({ title: g, span: 1, plain: !g })
    }
    return out
  }, [cols])
  const twoTier = cols.some((c) => c.group)

  const nFrozen = Math.min(frozen, cols.length)
  const leftOf = (i: number) => {
    let x = CODE_W + NAME_W
    for (let k = 0; k < i; k++) x += colWidth(cols[k])
    return x
  }
  const tableW = CODE_W + NAME_W + cols.reduce((a, c) => a + colWidth(c), 0)
  const nCols = 2 + cols.length

  // стабильные ссылки на обработчики, чтобы строки не перерисовывались зря
  const drillRef = useRef({ isDrillable, onCellClick, onOpenFiche, onOpenCard })
  drillRef.current = { isDrillable, onCellClick, onOpenFiche, onOpenCard }

  // подсветка графы под курсором — без перерисовки React
  const hotCol = useRef<number>(-1)
  const onMove = (e: React.MouseEvent) => {
    const td = (e.target as HTMLElement).closest('td[data-c]') as HTMLElement | null
    const c = td ? Number(td.dataset.c) : -1
    if (c === hotCol.current) return
    const box = boxRef.current
    if (!box) return
    if (hotCol.current >= 0) box.querySelectorAll(`[data-c="${hotCol.current}"]`).forEach((n) => n.classList.remove('hotcol'))
    hotCol.current = c
    if (c >= 0) box.querySelectorAll(`[data-c="${c}"]`).forEach((n) => n.classList.add('hotcol'))
  }
  const onLeave = () => {
    const box = boxRef.current
    if (box && hotCol.current >= 0) box.querySelectorAll(`[data-c="${hotCol.current}"]`).forEach((n) => n.classList.remove('hotcol'))
    hotCol.current = -1
  }

  // выделение строк
  const onSelect = useCallback(
    (id: string, e: React.MouseEvent) => {
      if (e.shiftKey && anchor) {
        const ids = visible.map((r) => r.id)
        const a = ids.indexOf(anchor)
        const b = ids.indexOf(id)
        if (a >= 0 && b >= 0) {
          const [lo, hi] = a < b ? [a, b] : [b, a]
          setSel(new Set(ids.slice(lo, hi + 1)))
          return
        }
      }
      setSel((prev) => {
        const s = e.ctrlKey || e.metaKey ? new Set(prev) : new Set<string>()
        if (prev.has(id) && (e.ctrlKey || e.metaKey || prev.size === 1)) s.delete(id)
        else s.add(id)
        return s
      })
      setAnchor(id)
    },
    [anchor, visible],
  )

  const copy = useCallback(
    async (all: boolean) => {
      const list = all || sel.size === 0 ? visible : visible.filter((r) => sel.has(r.id))
      const head = [codeTitle, nameTitle, ...cols.map((c) => (c.group ? c.group + ' / ' : '') + c.title)].join('\t')
      const lines = list.map((r) =>
        [
          r.code ?? '',
          ' '.repeat(r.level * 2) + (r.name ?? ''),
          ...cols.map((c) => {
            const v = r.values?.[c.key]
            if (c.type === 'text') return v == null ? '' : String(v)
            if (c.type === 'qty') return fmtQty(v).replace(/\s/g, '')
            return fmtPlain(v)
          }),
        ].join('\t'),
      )
      const text = [head, ...lines].join('\n')
      try {
        await navigator.clipboard.writeText(text)
      } catch {
        const ta = document.createElement('textarea')
        ta.value = text
        document.body.appendChild(ta)
        ta.select()
        document.execCommand('copy')
        ta.remove()
      }
      setToast(`Скопировано строк: ${list.length} (вставляйте в Excel)`)
      setTimeout(() => setToast(null), 2500)
    },
    [sel, visible, cols, codeTitle, nameTitle],
  )

  const onKeyDown = (e: React.KeyboardEvent) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'c' && !(e.target as HTMLElement).matches('input')) {
      if (window.getSelection()?.toString()) return
      e.preventDefault()
      copy(false)
    }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a' && !(e.target as HTMLElement).matches('input')) {
      e.preventDefault()
      setSel(new Set(visible.map((r) => r.id)))
    }
  }

  const fmtCell = useCallback(
    (v: unknown, c: Column) => {
      if (c.type === 'money') return fmt === 'full' ? fmtMoney(v) : fmtCompact(v, fmt)
      if (c.type === 'int') return fmtInt(v)
      if (c.type === 'qty') return fmtQty(v)
      return v == null ? '' : String(v)
    },
    [fmt],
  )

  if (printing) return <PrintTables cols={cols} rows={visible} totals={hasTotals ? result.totals : undefined} fmtCell={fmtCell} codeTitle={codeTitle} nameTitle={nameTitle} />

  return (
    <div className="rt-wrap">
      <div className="rt-toolbar no-print">
        {childCount.size > 0 && (
          <>
            <span className="rt-label">Раскрыть до уровня:</span>
            {[1, 2, 3].map((n) => (
              <button key={n} className="btn small" onClick={() => applyLevel(n)} disabled={maxLevel + 1 < n && n > 1}>
                {n}
              </button>
            ))}
            <button className="btn small" onClick={() => applyLevel('all')}>
              все
            </button>
          </>
        )}
        <input type="search" className="rt-search" placeholder="Поиск по коду или названию…" value={query} onChange={(e) => setQuery(e.target.value)} />
        <select className="rt-sel" value={fmt} onChange={(e) => setFmt(e.target.value as NumFmt)} title="Формат чисел на экране">
          {(Object.keys(FMT_LABEL) as NumFmt[]).map((k) => (
            <option key={k} value={k}>
              {FMT_LABEL[k]}
            </option>
          ))}
        </select>
        <select className="rt-sel" value={frozen} onChange={(e) => setFrozen(Number(e.target.value))} title="Сколько граф закрепить слева при прокрутке">
          {[0, 1, 2, 3].map((n) => (
            <option key={n} value={n}>
              закрепить граф: {n}
            </option>
          ))}
        </select>
        <button className="btn small" onClick={() => copy(false)} title="Ctrl+C. Клик по коду/названию выделяет строки (Ctrl — добавить, Shift — диапазон). Без выделения копируются все видимые строки.">
          Копировать{sel.size ? ` (${sel.size})` : ''}
        </button>
        <span className="rt-count">
          строк: {visible.length}
          {visible.length !== rows.length ? ` из ${rows.length}` : ''} · граф: {cols.length}
        </span>
        {toast && <span className="rt-toast">{toast}</span>}
      </div>
      <div className="rt-box" ref={boxRef} onScroll={onScroll} onMouseMove={onMove} onMouseLeave={onLeave} onKeyDown={onKeyDown} tabIndex={0}>
        <table className="rt" style={{ width: tableW, tableLayout: 'fixed' }}>
          <colgroup>
            <col style={{ width: CODE_W }} />
            <col style={{ width: NAME_W }} />
            {cols.map((c) => (
              <col key={c.key} style={{ width: colWidth(c) }} />
            ))}
          </colgroup>
          <thead>
            <tr style={{ height: HEAD_H }}>
              <th className="sticky-l c0 corner" rowSpan={twoTier ? 2 : 1}>
                {codeTitle}
              </th>
              <th className="sticky-l c1 corner" rowSpan={twoTier ? 2 : 1}>
                {nameTitle}
              </th>
              {twoTier
                ? groups.map((g, i) =>
                    g.plain ? (
                      <th
                        key={i}
                        rowSpan={2}
                        className={'hd' + (groupStart(groups, i) < nFrozen ? ' frz' : '')}
                        data-c={groupStart(groups, i)}
                        style={groupStart(groups, i) < nFrozen ? { left: leftOf(groupStart(groups, i)) } : undefined}
                        title={cols[groupStart(groups, i)].name ? `${cols[groupStart(groups, i)].title} ${cols[groupStart(groups, i)].name}` : cols[groupStart(groups, i)].title}
                      >
                        {cols[groupStart(groups, i)].title}
                      </th>
                    ) : (
                      <th key={i} colSpan={g.span} className="hd grp">
                        {g.title}
                      </th>
                    ),
                  )
                : cols.map((c, i) => (
                    <th key={c.key} className={'hd' + (i < nFrozen ? ' frz' : '')} data-c={i} style={i < nFrozen ? { left: leftOf(i) } : undefined} title={c.name ? `${c.title} ${c.name}` : c.title}>
                      {c.title}
                    </th>
                  ))}
            </tr>
            {twoTier && (
              <tr style={{ height: HEAD_H }}>
                {cols.map((c, i) =>
                  c.group ? (
                    <th key={c.key} className={'hd sub' + (i < nFrozen ? ' frz' : '')} data-c={i} style={{ top: HEAD_H, ...(i < nFrozen ? { left: leftOf(i) } : {}) }} title={c.name ? `${c.title} ${c.name}` : c.title}>
                      {c.title}
                    </th>
                  ) : null,
                )}
              </tr>
            )}
          </thead>
          <tbody>
            {start > 0 && (
              <tr aria-hidden style={{ height: start * ROW_H }}>
                <td colSpan={nCols} style={{ padding: 0, border: 0 }} />
              </tr>
            )}
            {slice.map((r) => (
              <RowView
                key={r.id}
                r={r}
                cols={cols}
                hasKids={childCount.has(r.id)}
                collapsed={collapsed.has(r.id)}
                selected={sel.has(r.id)}
                nFrozen={nFrozen}
                fmt={fmt}
                moneyW={moneyW}
                drillRef={drillRef}
                toggle={toggle}
                onSelect={onSelect}
                widthOf={colWidth}
                fmtCell={fmtCell}
              />
            ))}
            {end < total && (
              <tr aria-hidden style={{ height: (total - end) * ROW_H }}>
                <td colSpan={nCols} style={{ padding: 0, border: 0 }} />
              </tr>
            )}
          </tbody>
          {hasTotals && (
            <tfoot>
              <tr style={{ height: ROW_H }}>
                <td className="sticky-l c0 foot" />
                <td className="sticky-l c1 foot">Итого</td>
                {cols.map((c, i) => {
                  const raw = result.totals?.[c.key]
                  const neg = c.type === 'money' && typeof raw === 'number' && raw < -0.005
                  return (
                    <td key={c.key} data-c={i} className={`num foot ${neg ? 'neg' : ''} ${i < nFrozen ? 'frz' : ''}`} style={i < nFrozen ? { left: leftOf(i) } : undefined}>
                      {c.type === 'text' ? (raw == null ? '' : String(raw)) : fmtCell(raw, c)}
                    </td>
                  )
                })}
              </tr>
            </tfoot>
          )}
        </table>
      </div>
    </div>
  )
}

interface RowProps {
  r: ResultRow
  cols: Column[]
  hasKids: boolean
  collapsed: boolean
  selected: boolean
  nFrozen: number
  fmt: NumFmt
  moneyW: number
  drillRef: React.MutableRefObject<{ isDrillable?: (row: ResultRow, col: Column) => boolean; onCellClick?: (c: CellClick) => void; onOpenFiche?: (row: ResultRow) => void; onOpenCard?: (row: ResultRow) => void }>
  toggle: (id: string) => void
  onSelect: (id: string, e: React.MouseEvent) => void
  widthOf: (c: Column) => number
  fmtCell: (v: unknown, c: Column) => string
}

const RowView = memo(function RowView({ r, cols, hasKids, collapsed, selected, nFrozen, fmt, moneyW, drillRef, toggle, onSelect, widthOf, fmtCell }: RowProps) {
  const cls = ['kind-' + r.kind, r.bold || r.kind === 'group' || r.kind === 'total' ? 'bold' : '', selected ? 'selrow' : ''].join(' ')
  const { isDrillable, onCellClick, onOpenFiche, onOpenCard } = drillRef.current
  void fmt
  let left = CODE_W + NAME_W
  return (
    <tr className={cls} style={{ height: ROW_H }}>
      <td className="sticky-l c0 code" title={r.code} onClick={(e) => onSelect(r.id, e)}>
        {r.code}
      </td>
      <td className="sticky-l c1 name" title={r.name} onClick={(e) => onSelect(r.id, e)}>
        <div className="name-in" style={{ paddingLeft: r.level * 14 }}>
          {hasKids ? (
            <button
              className="tog no-print"
              onClick={(e) => {
                e.stopPropagation()
                toggle(r.id)
              }}
              aria-label={collapsed ? 'Раскрыть' : 'Свернуть'}
            >
              {collapsed ? '▸' : '▾'}
            </button>
          ) : (
            <span className="tog-sp no-print" />
          )}
          <span className="name-txt">{r.name}</span>
          {r.fiche && onOpenFiche && (
            <button
              className="tog no-print fiche-btn"
              title="Открыть ордер"
              onClick={(e) => {
                e.stopPropagation()
                onOpenFiche(r)
              }}
            >
              ↗
            </button>
          )}
          {r.fiche && onOpenCard && typeof r.values?.account === 'string' && (
            <button
              className="tog no-print fiche-btn card-btn"
              title="Открыть карточку счёта по этой строке за тот же период"
              onClick={(e) => {
                e.stopPropagation()
                onOpenCard(r)
              }}
            >
              ▤
            </button>
          )}
        </div>
      </td>
      {cols.map((c, i) => {
        const raw = r.values?.[c.key]
        const neg = c.type === 'money' && typeof raw === 'number' && raw < -0.005
        const drillable =
          !!onCellClick &&
          (c.type === 'money' || c.type === 'qty') &&
          (isDrillable ? isDrillable(r, c) : !!r.drill) &&
          typeof raw === 'number' &&
          Math.abs(raw) >= 0.005
        const frz = i < nFrozen
        const style = frz ? { left } : undefined
        left += widthOf(c)
        return (
          <td
            key={c.key}
            data-c={i}
            className={`num ${c.type === 'text' ? 'txt' : ''} ${neg ? 'neg' : ''} ${drillable ? 'drill' : ''} ${frz ? 'frz' : ''}`}
            style={style}
            onClick={drillable ? () => onCellClick!({ row: r, col: c }) : undefined}
            title={drillable ? 'Показать проводки' : undefined}
          >
            {fmtCell(raw, c)}
          </td>
        )
      })}
    </tr>
  )
})

function groupStart(groups: { span: number }[], idx: number) {
  let s = 0
  for (let i = 0; i < idx; i++) s += groups[i].span
  return s
}

/**
 * Печатная форма: простая статическая таблица без прокрутки. Широкие таблицы (шахматка, анализ по месяцам)
 * режутся на части по графам (до 8 граф на лист с повтором кода и названия) — ничего не обрезается.
 */
const PRINT_COLS = 8

function PrintTables({
  cols,
  rows,
  totals,
  fmtCell,
  codeTitle,
  nameTitle,
}: {
  cols: Column[]
  rows: ResultRow[]
  totals?: Record<string, number | string | null>
  fmtCell: (v: unknown, c: Column) => string
  codeTitle: string
  nameTitle: string
}) {
  const n = Math.max(1, Math.ceil(cols.length / PRINT_COLS))
  const per = Math.ceil(cols.length / n)
  const chunks: Column[][] = []
  for (let i = 0; i < cols.length; i += per) chunks.push(cols.slice(i, i + per))
  const flat = rows.every((r) => r.parent == null)
  return (
    <div className="print-tables">
      {chunks.map((cc, ci) => {
        const groups: { title: string; span: number }[] = []
        for (const c of cc) {
          const g = c.group ?? ''
          const last = groups[groups.length - 1]
          if (last && last.title === g) last.span++
          else groups.push({ title: g, span: 1 })
        }
        const twoTier = cc.some((c) => c.group)
        const shown = chunks.length > 1 && flat ? rows.filter((r) => r.kind === 'total' || cc.some((c) => { const v = r.values?.[c.key]; return v != null && v !== '' && v !== 0 })) : rows
        return (
          <div key={ci} className={'pt-chunk' + (ci > 0 ? ' brk' : '')}>
            {chunks.length > 1 && (
              <div className="pt-cap">
                Графы {cols.indexOf(cc[0]) + 1}–{cols.indexOf(cc[cc.length - 1]) + 1} из {cols.length} (лист {ci + 1} из {chunks.length})
              </div>
            )}
            <table className="pt">
              <colgroup>
                <col style={{ width: '11%' }} />
                <col style={{ width: '27%' }} />
                {cc.map((c) => (
                  <col key={c.key} />
                ))}
              </colgroup>
              <thead>
                <tr>
                  <th rowSpan={twoTier ? 2 : 1}>{codeTitle}</th>
                  <th rowSpan={twoTier ? 2 : 1}>{nameTitle}</th>
                  {twoTier
                    ? groups.map((g, i) =>
                        g.title ? (
                          <th key={i} colSpan={g.span}>
                            {g.title}
                          </th>
                        ) : (
                          <th key={i} rowSpan={2} colSpan={g.span}>
                            {cc[groups.slice(0, i).reduce((a, x) => a + x.span, 0)].title}
                          </th>
                        ),
                      )
                    : cc.map((c) => <th key={c.key}>{c.title}</th>)}
                </tr>
                {twoTier && (
                  <tr>
                    {cc.map((c) => (c.group ? <th key={c.key}>{c.title}</th> : null))}
                  </tr>
                )}
              </thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={r.id} className={'kind-' + r.kind + (r.bold || r.kind === 'group' || r.kind === 'total' ? ' bold' : '')}>
                    <td className="code">{r.code}</td>
                    <td style={{ paddingLeft: 3 + r.level * 8 }}>{r.name}</td>
                    {cc.map((c) => {
                      const v = r.values?.[c.key]
                      const neg = c.type === 'money' && typeof v === 'number' && v < -0.005
                      return (
                        <td key={c.key} className={(c.type === 'text' ? '' : 'num ') + (neg ? 'neg' : '')}>
                          {fmtCell(v, c)}
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
              {totals && (
                <tfoot>
                  <tr>
                    <td />
                    <td>Итого</td>
                    {cc.map((c) => (
                      <td key={c.key} className="num">
                        {c.type === 'text' ? '' : fmtCell(totals[c.key], c)}
                      </td>
                    ))}
                  </tr>
                </tfoot>
              )}
            </table>
          </div>
        )
      })}
    </div>
  )
}
