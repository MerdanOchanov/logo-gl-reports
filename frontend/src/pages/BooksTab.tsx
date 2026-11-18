import { askConfirm } from '../components/confirm'
import { useEffect, useMemo, useRef, useState } from 'react'
import { api, errMsg, type Book, type BookRole, type DatasetParams, type Proposal, type ScanState, type SourceStat, type Status, type ValidateResp } from '../api'
import { ErrorBox, Modal, useLoad } from '../components/ui'
import { fmtDate, fmtDateTime, fmtInt, fmtMoney } from '../format'
import { reloadDataset, useDataset } from '../dataset'
import { resetYears } from '../years'

type Row = Book & { _k: string }

const ROLE_RU: Record<BookRole, string> = { main: 'Основная', alt: 'Альтернативная', off: 'Не используется' }
const ROLE_ORDER: Record<BookRole, number> = { main: 0, alt: 1, off: 2 }

const bname = (b: { label: number; src_firm: number; src_period?: number }) =>
  `${b.label} · фирма ${b.src_firm}${b.src_period && b.src_period !== 1 ? `, период ${String(b.src_period).padStart(2, '0')}` : ''}`

const srcOf = (b: Book) => b.src ?? (b.src_period === 1 ? b.src_firm : b.src_period * 1000 + b.src_firm)

const toRows = (bs: Book[]): Row[] => bs.map((b, i) => ({ ...b, _k: 'b' + (b.id ?? 'n' + i) }))

/** Тело запроса: только изменяемые поля (то, что считает сервер, не отправляется). */
const payload = (rows: Row[]): Book[] =>
  rows.map((r) => ({
    id: r.id,
    slot: r.slot,
    label: r.label,
    part: r.part,
    src_firm: r.src_firm,
    src_period: r.src_period,
    date_from: r.date_from,
    date_to: r.date_to,
    role: r.role,
    name: r.name,
    divisor: r.divisor,
    oem: r.oem,
    reason: r.reason,
    note: r.note,
  }))

const sig = (rows: Row[]) => JSON.stringify(rows.map((r) => [r.id, r.label, r.part, r.src_firm, r.src_period, r.date_from, r.date_to, r.role, r.divisor, r.oem]))

const sortRows = (rows: Row[]) => [...rows].sort((a, b) => a.label - b.label || a.part - b.part || ROLE_ORDER[a.role] - ROLE_ORDER[b.role] || a.src_firm - b.src_firm)

const overlap = (a: Book, b: Book) => a.date_from <= b.date_to && b.date_from <= a.date_to

function addDays(iso: string, n: number): string {
  const d = new Date(iso + 'T00:00:00Z')
  d.setUTCDate(d.getUTCDate() + n)
  return d.toISOString().slice(0, 10)
}

/** Сводка изменений разметки для подтверждения. */
function summarize(base: Book[], rows: Row[]) {
  const byId = new Map(base.map((b) => [b.id, b]))
  const added: string[] = []
  const changed: string[] = []
  const reload: string[] = []
  const stopped: string[] = []
  for (const r of rows) {
    const o = r.id != null ? byId.get(r.id) : undefined
    if (!o) {
      added.push(`${bname(r)} (${ROLE_RU[r.role].toLowerCase()}, ${fmtDate(r.date_from)}–${fmtDate(r.date_to)})`)
      if (r.role === 'main') reload.push(bname(r))
      continue
    }
    const d: string[] = []
    if (o.label !== r.label) d.push(`год ${o.label} → ${r.label}`)
    if (o.date_from !== r.date_from || o.date_to !== r.date_to) d.push(`даты ${fmtDate(o.date_from)}–${fmtDate(o.date_to)} → ${fmtDate(r.date_from)}–${fmtDate(r.date_to)}`)
    if (o.role !== r.role) d.push(`роль «${ROLE_RU[o.role]}» → «${ROLE_RU[r.role]}»`)
    if (o.divisor !== r.divisor) d.push(`делитель ${o.divisor} → ${r.divisor}`)
    if (o.oem !== r.oem) d.push(`старая кодировка: ${o.oem ? 'да' : 'нет'} → ${r.oem ? 'да' : 'нет'}`)
    if (o.part !== r.part) d.push(`часть ${o.part} → ${r.part}`)
    if (d.length) changed.push(`${bname(r)}: ${d.join('; ')}`)
    if (r.role === 'main' && (o.role !== 'main' || o.oem !== r.oem)) reload.push(bname(r))
    if (o.role === 'main' && r.role !== 'main') stopped.push(bname(o))
  }
  const ids = new Set(rows.map((r) => r.id))
  const removed = base.filter((b) => !ids.has(b.id)).map((b) => bname(b))
  for (const b of base) if (!ids.has(b.id) && b.role === 'main') stopped.push(bname(b))
  return { added, changed, reload, stopped, removed }
}

export default function BooksTab() {
  const { data, error, reload } = useLoad(() => api.books(), [])
  const { data: ds } = useDataset()
  const base = data?.books ?? []
  const [rows, setRows] = useState<Row[]>([])
  useEffect(() => {
    if (data) setRows(toRows(data.books))
  }, [data])
  const dirty = data ? sig(rows) !== sig(toRows(data.books)) : false

  const [err, setErr] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)
  const [edit, setEdit] = useState<Row | null>(null)
  const [split, setSplit] = useState<Row | null>(null)
  const [vres, setVres] = useState<ValidateResp | null>(null)
  const [confirmSave, setConfirmSave] = useState(false)
  const [confirmApply, setConfirmApply] = useState(false)
  const [flash, setFlash] = useState<string | null>(null)

  // ---- состояние загрузки книг (после сохранения) ----
  const [status, setStatus] = useState<Status | null>(null)
  useEffect(() => {
    let alive = true
    let t: number | undefined
    const tick = async () => {
      let next = 6000
      try {
        const s = await api.status()
        if (!alive) return
        setStatus(s)
        if (s.syncing) next = 1500
      } catch {
        /* нет связи — повторим */
      }
      if (alive) t = window.setTimeout(tick, next)
    }
    tick()
    return () => {
      alive = false
      window.clearTimeout(t)
    }
  }, [])

  // ---- сканирование и предложение ----
  const [scan, setScan] = useState<ScanState | null>(null)
  const [prop, setProp] = useState<Proposal | null>(null)
  const prevScan = useRef<string | null>(null)
  const loadProposal = async () => {
    try {
      setProp(await api.proposal())
    } catch {
      setProp(null)
    }
  }
  useEffect(() => {
    let alive = true
    let t: number | undefined
    const tick = async () => {
      let next = 8000
      try {
        const s = await api.scanState()
        if (!alive) return
        setScan(s)
        if (s.state === 'running') next = 1200
        if (prevScan.current === 'running' && s.state === 'done') loadProposal()
        prevScan.current = s.state
      } catch {
        /* повторим */
      }
      if (alive) t = window.setTimeout(tick, next)
    }
    tick()
    loadProposal()
    return () => {
      alive = false
      window.clearTimeout(t)
    }
  }, [])
  const startScan = async () => {
    try {
      setErr(null)
      await api.startScan()
      setScan({ state: 'running', done: 0, total: 0, current: '' })
      prevScan.current = 'running'
    } catch (e) {
      setErr(errMsg(e))
    }
  }

  const afterChange = () => {
    resetYears()
    reload()
    reloadDataset()
  }

  const startSync = async (src: number[] | undefined) => {
    if (!src || !src.length) return
    try {
      await api.sync(src)
    } catch (e) {
      setErr('Разметка сохранена, но загрузку данных запустить не удалось: ' + errMsg(e))
    }
  }

  // ---- действия над строками ----
  const sorted = useMemo(() => sortRows(rows), [rows])
  const upd = (k: string, p: Partial<Row>) => setRows((l) => l.map((r) => (r._k === k ? { ...r, ...p } : r)))
  const applyEdit = (n: Row) => {
    setRows((l) =>
      l.map((r) => {
        if (r._k === n._k) return n
        // новая основная книга вытесняет другую основную с пересекающимися датами
        if (n.role === 'main' && r.role === 'main' && overlap(r, n)) return { ...r, role: 'alt' as BookRole }
        return r
      }),
    )
    setVres(null)
    setEdit(null)
  }
  const makeMain = async (r: Row) => {
    if (dirty || r.id == null) {
      applyEdit({ ...r, role: 'main' })
      setInfo(`Книга «${bname(r)}» станет основной после сохранения разметки.`)
      return
    }
    if (!(await askConfirm(`Отчёты за ${r.label} год будут строиться по книге «${r.name || bname(r)}». Данные будут загружены заново, это займёт до минуты.`, { okLabel: 'Сделать основной' }))) return
    try {
      const x = await api.makeMain(r.id)
      setErr(null)
      setInfo(x.changed ? `Основная книга ${r.label} года изменена. Идёт загрузка данных.` : 'Эта книга уже основная — ничего не изменилось.')
      afterChange()
    } catch (e) {
      setErr(errMsg(e))
    }
  }
  const doSplit = (r: Row, date: string) => {
    const a: Row = { ...r, date_to: addDays(date, -1) }
    const nl = Number(r.date_to.slice(0, 4))
    const b: Row = {
      ...r,
      _k: 'n' + Date.now(),
      id: undefined,
      slot: undefined,
      label: nl,
      part: nl === r.label ? r.part + 1 : 1,
      date_from: date,
      reason: 'Вторая часть периода, разделённого вручную',
      synced_at: null,
      line_count: undefined,
    }
    setRows((l) => [...l.map((x) => (x._k === r._k ? a : x)), b])
    setVres(null)
    setSplit(null)
    setInfo('Период разделён на две книги. Проверьте год-метки и даты, затем сохраните разметку.')
  }
  /** Добавить в таблицу книгу на источник: по предложению программы, а для источников без предложения — по датам проводок. */
  const addManual = (r: PRow) => {
    const st = r.stat
    const from = r.label ? r.date_from : st?.turn_date_min || st?.date_min || st?.begdate || r.date_from || ''
    const to = r.label ? r.date_to : st?.turn_date_max || st?.date_max || st?.enddate || r.date_to || ''
    if (!from || !to) {
      setErr(`У источника «фирма ${r.firm}, период ${String(r.period).padStart(2, '0')}» нет проводок и дат: книгу на него добавить нельзя.`)
      return
    }
    const denom = [...(ds?.params?.denomination ?? [])].sort((a, b) => a.before.localeCompare(b.before)).find((d) => to < d.before)
    const free = !rows.some((x) => x.role === 'main' && x.date_from <= to && from <= x.date_to)
    const row: Row = {
      _k: 'n' + Date.now(),
      label: r.label ?? Number(to.slice(0, 4)),
      part: 1,
      src: r.src,
      src_firm: r.firm,
      src_period: r.period,
      date_from: from,
      date_to: to,
      role: free ? 'main' : r.role === 'main' ? 'alt' : r.role === 'off' ? 'alt' : r.role,
      name: r.name,
      firm_name: r.name,
      divisor: r.divisor ?? denom?.divisor ?? 1,
      oem: r.oem ?? false,
      reason: r.label ? r.reason : 'Добавлена вручную из списка найденных в Logo источников',
    }
    setRows((l) => [...l, row])
    setVres(null)
    setEdit(row)
    setInfo(`Книга «${bname(row)}» добавлена в таблицу${row.role === 'main' ? '' : ' как альтернативная (даты пересекаются с основной книгой)'}. Проверьте год, даты, делитель и кодировку, затем сохраните разметку.`)
  }
  const resetDraft = () => {
    if (data) setRows(toRows(data.books))
    setVres(null)
    setInfo(null)
  }

  // ---- проверка и сохранение ----
  const [checking, setChecking] = useState(false)
  const check = async () => {
    setChecking(true)
    try {
      setErr(null)
      const v = await api.validateBooks(payload(rows))
      setVres(v)
      return v
    } catch (e) {
      setErr(errMsg(e))
      return null
    } finally {
      setChecking(false)
    }
  }
  const goRow = (label: number, firm: number) => {
    const r = rows.find((x) => x.label === label && x.src_firm === firm) ?? rows.find((x) => x.src_firm === firm)
    if (!r) return
    setFlash(r._k)
    document.getElementById('bk-' + r._k)?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    window.setTimeout(() => setFlash(null), 2500)
  }

  const proposalRows = useMemo(() => (prop && data ? buildProposalRows(prop, data.books) : []), [prop, data])
  const diffCount = proposalRows.filter((p) => p.status === 'new' || p.status === 'changed').length

  const mainCount = base.filter((b) => b.role === 'main').length
  const syncing = !!status?.syncing || !!status?.queued
  const notLoaded = base.filter((b) => b.role === 'main' && !b.synced_at).length

  return (
    <>
      <p className="hint">
        Книга — это фирма и период Logo с датами «с — по», привязанные к году. По основной книге года строятся отчёты. Если на один год в Logo две фирмы, одна из них основная, другая — альтернативная (запасная).
      </p>
      <ErrorBox error={error || err} onClose={() => setErr(null)} />
      {info && (
        <div className="msg ok">
          <span>{info}</span>
          <button className="btn small ghost" onClick={() => setInfo(null)}>
            ✕
          </button>
        </div>
      )}

      <div className="toolbar">
        <h3 style={{ margin: 0 }}>Текущая разметка</h3>
        <span className="muted">
          {data ? `основных книг: ${mainCount}, всего: ${base.length}` : 'загрузка списка…'}
          {ds?.last_sync_at ? ` · данные загружены ${fmtDateTime(ds.last_sync_at)}` : ''}
        </span>
        <div className="spacer" />
        {syncing && status?.queued && <span className="tag">в очереди на загрузку</span>}
        {syncing && !status?.queued && (
          <span className="tag">
            идёт загрузка{typeof status?.progress === 'object' && status?.progress?.total ? `: ${status.progress.done ?? 0} из ${status.progress.total}` : '…'}
          </span>
        )}
        {!syncing && notLoaded > 0 && <span className="tag red">не загружено книг: {notLoaded}</span>}
      </div>

      {!data && !error && <div className="muted pad">Загрузка…</div>}
      <div className="table-scroll">
      <table className="simple books-table">
        <thead>
          <tr>
            <th style={{ width: 60 }}>Год</th>
            <th>Фирма и период Logo</th>
            <th style={{ width: 160 }}>Даты</th>
            <th style={{ width: 110 }}>Роль</th>
            <th style={{ width: 70 }} title="Деление сумм (деноминация): суммы книги делятся на это число">
              Делитель
            </th>
            <th style={{ width: 70 }} title="Тексты в названиях записаны в старой (DOS) кодировке">
              Стар. код.
            </th>
            <th className="r" style={{ width: 80 }}>
              Проводок
            </th>
            <th style={{ width: 110 }}>Загружено</th>
            <th style={{ width: 170 }} />
          </tr>
        </thead>
        <tbody>
          {sorted.map((r, i) => {
            const first = i === 0 || sorted[i - 1].label !== r.label
            const group = sorted.filter((x) => x.label === r.label)
            const changed = r.id == null || !base.some((b) => b.id === r.id && sig([{ ...b, _k: '' }]) === sig([r]))
            return (
              <tr key={r._k} id={'bk-' + r._k} className={[r.role === 'main' ? 'book-main' : r.role === 'off' ? 'dim' : '', first ? 'grp-start' : '', flash === r._k ? 'flash' : ''].join(' ')}>
                <td>
                  <b>{first ? r.label : ''}</b>
                  {first && group.length > 1 && <span className="tag" title="В этом году несколько книг"> {group.length}</span>}
                  {!first && <span className="muted">{r.part > 1 || group.filter((x) => x.role === 'main').length > 1 ? `ч. ${r.part}` : ''}</span>}
                </td>
                <td>
                  Фирма {r.src_firm}, период {String(r.src_period).padStart(2, '0')}
                  <span className="muted"> — {r.firm_name || r.name}</span>
                  {changed && <span className="tag"> изменено</span>}
                  {!!r.rows_outside && <span className="tag red" title="В источнике есть проводки вне дат этой книги"> вне дат: {fmtInt(r.rows_outside)}</span>}
                </td>
                <td className="nowrap">
                  {fmtDate(r.date_from)} — {fmtDate(r.date_to)}
                </td>
                <td>
                  <b>{r.role === 'main' ? ROLE_RU.main : ''}</b>
                  {r.role !== 'main' && ROLE_RU[r.role]}
                </td>
                <td className="num">{r.divisor !== 1 ? r.divisor : <span className="muted">1</span>}</td>
                <td>{r.oem ? 'да' : <span className="muted">нет</span>}</td>
                <td className="num">{r.line_count != null ? r.line_count.toLocaleString('ru-RU') : <span className="muted">ещё не загружено</span>}</td>
                <td className="nowrap">{r.synced_at ? fmtDateTime(r.synced_at) : r.role === 'main' ? <span className="muted">не загружено</span> : ''}</td>
                <td className="actions">
                  {r.role === 'alt' && (
                    <button className="btn small" onClick={() => makeMain(r)}>
                      Сделать основной
                    </button>
                  )}
                  <button className="btn small" onClick={() => setSplit(r)} title="Разделить период книги на две книги по дате">
                    Разделить
                  </button>
                  <button className="btn small" onClick={() => setEdit(r)}>
                    Изменить
                  </button>
                  {r.id == null && (
                    <button className="btn small ghost" title="Убрать новую строку" onClick={() => setRows((l) => l.filter((x) => x._k !== r._k))}>
                      ✕
                    </button>
                  )}
                </td>
              </tr>
            )
          })}
          {data && sorted.length === 0 && (
            <tr>
              <td colSpan={9} className="muted pad">
                Книг пока нет. Нажмите «Прочитать фирмы из Logo» и примите предложение программы.
              </td>
            </tr>
          )}
        </tbody>
      </table>
      </div>
      <div className="toolbar">
        <button className="btn" onClick={check} disabled={checking || rows.length === 0}>
          {checking ? 'Проверяем…' : 'Проверить'}
        </button>
        <button className="btn primary" onClick={() => setConfirmSave(true)} disabled={!dirty}>
          Сохранить разметку
        </button>
        {dirty && (
          <button className="btn" onClick={resetDraft}>
            Отменить правки
          </button>
        )}
        {dirty && <span className="tag">есть несохранённые правки</span>}
      </div>
      {vres && <ValidateBox v={vres} onGo={goRow} />}

      {/* ---- чтение Logo и предложение ---- */}
      <h3 style={{ marginTop: 22 }}>Предложение программы</h3>
      <p className="hint">
        Программа читает из Logo список всех фирм и периодов, смотрит на их проводки и сама предлагает, какие книги использовать и почему. Чтение занимает до нескольких минут; можно уйти на другую страницу — оно продолжится.
      </p>
      <div className="toolbar">
        <button className="btn primary" onClick={startScan} disabled={scan?.state === 'running'}>
          {prop ? 'Прочитать фирмы из Logo заново' : 'Прочитать фирмы из Logo'}
        </button>
        {scan?.state === 'running' && <ScanProgress s={scan} />}
        {scan?.state === 'done' && scan.scanned_at && <span className="muted">прочитано {fmtDateTime(scan.scanned_at)}</span>}
        {scan?.state === 'error' && <span className="tag red">{scan.error || 'Чтение Logo не удалось'}</span>}
        <div className="spacer" />
        {diffCount > 0 && (
          <button className="btn danger" onClick={() => setConfirmApply(true)}>
            Применить предложение программы целиком
          </button>
        )}
      </div>
      {!prop && scan?.state !== 'running' && <div className="msg warn">Предложения пока нет: нажмите «Прочитать фирмы из Logo».</div>}
      {prop && (
        <ParamsSuggest
          prop={prop}
          onApplied={() => {
            loadProposal()
            reloadDataset()
          }}
          onError={setErr}
          onInfo={setInfo}
        />
      )}
      {prop && data && <ProposalTable prop={prop} rows={proposalRows} diffCount={diffCount} onAdd={addManual} />}

      {edit && <BookEditor book={edit} rows={rows} onSave={applyEdit} onClose={() => setEdit(null)} />}
      {split && <SplitDialog book={split} onSplit={(d) => doSplit(split, d)} onClose={() => setSplit(null)} />}
      {confirmSave && (
        <SaveConfirm
          base={base}
          rows={rows}
          onClose={() => setConfirmSave(false)}
          onSaved={async (r) => {
            setConfirmSave(false)
            setVres(null)
            await startSync(r.sync_needed)
            const n = r.sync_needed?.length ?? 0
            setInfo('Разметка сохранена.' + (n ? ` Идёт загрузка данных: ${n} ${n === 1 ? 'книга' : n < 5 ? 'книги' : 'книг'}.` : '') + (r.warnings?.length ? ' Замечания: ' + r.warnings.join(' ') : ''))
            afterChange()
          }}
        />
      )}
      {confirmApply && prop && (
        <ApplyConfirm
          prop={prop}
          rows={proposalRows}
          dirty={dirty}
          onClose={() => setConfirmApply(false)}
          onDone={async (r) => {
            setConfirmApply(false)
            await startSync(r.sync_needed)
            setInfo('Предложение программы применено.' + (r.sync_needed?.length ? ' Идёт загрузка данных.' : ''))
            afterChange()
          }}
        />
      )}
    </>
  )
}

function ScanProgress({ s }: { s: ScanState }) {
  const pct = s.total ? Math.round((s.done / s.total) * 100) : null
  return (
    <span className="scan-prog">
      <span className="bar2">
        <i style={{ width: (pct ?? 3) + '%' }} />
      </span>
      <span>
        Чтение Logo{pct !== null ? `: ${s.done} из ${s.total}` : '…'}
        {s.current ? ` · ${s.current}` : ''}
      </span>
    </span>
  )
}

function ValidateBox({ v, onGo }: { v: ValidateResp; onGo: (label: number, firm: number) => void }) {
  const links = (t: string) => {
    const out: { label: number; firm: number }[] = []
    for (const m of t.matchAll(/«(\d{4}) · фирма (\d+)/g)) out.push({ label: Number(m[1]), firm: Number(m[2]) })
    return out
  }
  if (v.ok && !v.errors.length && !v.warnings.length) return <div className="msg ok">Проверка пройдена: ошибок и замечаний нет.</div>
  const item = (t: string, i: number, cls: string) => (
    <div key={cls + i} className={'msg ' + cls}>
      <span>{t}</span>
      <span>
        {links(t).map((l, j) => (
          <button key={j} className="btn small" onClick={() => onGo(l.label, l.firm)}>
            К строке {l.label} · {l.firm}
          </button>
        ))}
      </span>
    </div>
  )
  return (
    <>
      {v.errors.map((t, i) => item(t, i, 'err'))}
      {v.warnings.map((t, i) => item(t, i, 'warn'))}
      {v.errors.length === 0 && <div className="msg ok">Ошибок нет, есть замечания: их можно сохранить как есть.</div>}
    </>
  )
}

// ---------- редактирование строки ----------
function BookEditor({ book, rows, onSave, onClose }: { book: Row; rows: Row[]; onSave: (b: Row) => void; onClose: () => void }) {
  const [b, setB] = useState<Row>(book)
  const { data: ds } = useDataset()
  const p = (x: Partial<Row>) => setB((o) => ({ ...o, ...x }))
  const bad = b.date_from > b.date_to ? 'Дата начала позже даты окончания.' : null
  const other = rows.filter((r) => r._k !== b._k && r.role === 'main' && b.role === 'main' && overlap(r, b))
  const denom = ds?.params?.denomination?.[0]
  return (
    <Modal
      title={`Книга: фирма ${b.src_firm}, период ${String(b.src_period).padStart(2, '0')}`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Отмена
          </button>
          <button className="btn primary" disabled={!!bad} onClick={() => onSave(b)}>
            Готово
          </button>
        </>
      }
    >
      <div className="form">
        <div className="row2">
          <label>
            Год (метка)
            <input type="number" value={b.label} onChange={(e) => p({ label: Number(e.target.value) })} />
          </label>
          <label>
            Часть года
            <input type="number" min={1} value={b.part} onChange={(e) => p({ part: Math.max(1, Number(e.target.value)) })} />
          </label>
        </div>
        <div className="row2">
          <label>
            Даты с
            <input type="date" value={b.date_from} onChange={(e) => p({ date_from: e.target.value })} />
          </label>
          <label>
            по
            <input type="date" value={b.date_to} onChange={(e) => p({ date_to: e.target.value })} />
          </label>
        </div>
        {bad && <div className="msg err">{bad}</div>}
        <p className="hint">
          Даты должны лежать внутри периода фирмы в Logo. Финансовый год не с января (например 01.07.2025–30.06.2026) задаётся этими датами; меткой обычно берут год окончания. Если в одном периоде Logo лежит
          несколько лет — разделите его кнопкой «Разделить» на книги по годам.
        </p>
        <label>
          Роль
          <select value={b.role} onChange={(e) => p({ role: e.target.value as BookRole })}>
            <option value="main">Основная: по ней строятся отчёты</option>
            <option value="alt">Альтернативная: запасная, в отчётах не участвует</option>
            <option value="off">Не используется</option>
          </select>
        </label>
        {other.length > 0 && <div className="msg warn">Основной в году может быть одна книга: «{other.map(bname).join('», «')}» станет альтернативной.</div>}
        <label>
          Делитель сумм (деноминация)
          <input type="number" min={1} value={b.divisor} onChange={(e) => p({ divisor: Math.max(1, Number(e.target.value) || 1) })} />
        </label>
        <p className="hint">
          Суммы этой книги делятся на делитель при выводе (для старых лет до деноминации — например 5000, для новых — 1).
          {denom ? ` Дата деноминации в настройках набора: ${fmtDate(denom.before)}, делитель ${denom.divisor}; изменить — на вкладке «Параметры набора».` : ''}
        </p>
        <label className="chk">
          <input type="checkbox" checked={b.oem} onChange={(e) => p({ oem: e.target.checked })} />
          Тексты книги записаны в старой кодировке (если в названиях счетов «абракадабра» — включите)
        </label>
        <label>
          Заметка
          <input type="text" value={b.note ?? ''} onChange={(e) => p({ note: e.target.value })} />
        </label>
        {book.reason && <p className="hint">Причина (из предложения программы): {book.reason}</p>}
      </div>
    </Modal>
  )
}

function SplitDialog({ book, onSplit, onClose }: { book: Row; onSplit: (date: string) => void; onClose: () => void }) {
  const [d, setD] = useState('')
  const bad = d && (d <= book.date_from || d > book.date_to) ? 'Дата должна быть внутри периода книги, не раньше второго дня.' : null
  return (
    <Modal
      title={`Разделить книгу ${bname(book)}`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Отмена
          </button>
          <button className="btn primary" disabled={!d || !!bad} onClick={() => onSplit(d)}>
            Разделить
          </button>
        </>
      }
    >
      <p>
        Период {fmtDate(book.date_from)} — {fmtDate(book.date_to)} будет разделён на две книги одной и той же фирмы Logo: первая заканчивается накануне выбранной даты, вторая начинается с неё. Год второй книги
        — год её окончания; позже его можно поправить.
      </p>
      <label>
        Вторая книга начинается с даты
        <input type="date" value={d} min={book.date_from} max={book.date_to} onChange={(e) => setD(e.target.value)} />
      </label>
      {bad && <div className="msg err">{bad}</div>}
    </Modal>
  )
}

// ---------- подтверждение сохранения ----------
function SummaryList({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null
  return (
    <div style={{ marginBottom: 8 }}>
      <b>{title}</b>
      <ul style={{ margin: '2px 0 0', paddingLeft: 20 }}>
        {items.map((t, i) => (
          <li key={i}>{t}</li>
        ))}
      </ul>
    </div>
  )
}

function SaveConfirm({ base, rows, onClose, onSaved }: { base: Book[]; rows: Row[]; onClose: () => void; onSaved: (r: { sync_needed?: number[]; warnings?: string[] }) => void }) {
  const s = useMemo(() => summarize(base, rows), [base, rows])
  const [busy, setBusy] = useState(false)
  const [errs, setErrs] = useState<string[]>([])
  const [warns, setWarns] = useState<string[]>([])
  const go = async () => {
    setBusy(true)
    setErrs([])
    try {
      const v = await api.validateBooks(payload(rows))
      if (v.errors.length) {
        setErrs(v.errors)
        setWarns(v.warnings)
        return
      }
      onSaved(await api.saveBooks(payload(rows)))
    } catch (e) {
      setErrs([errMsg(e)])
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal
      title="Сохранить разметку книг?"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            Отмена
          </button>
          <button className="btn primary" onClick={go} disabled={busy}>
            {busy ? 'Сохраняем…' : 'Сохранить'}
          </button>
        </>
      }
    >
      <SummaryList title="Будут добавлены:" items={s.added} />
      <SummaryList title="Изменятся:" items={s.changed} />
      <SummaryList title="Будут загружены заново:" items={s.reload} />
      <SummaryList title="Перестанут использоваться в отчётах:" items={[...s.stopped, ...s.removed.map((x) => x + ' (будет удалена)')]} />
      {!s.added.length && !s.changed.length && !s.reload.length && !s.stopped.length && !s.removed.length && <p>Изменений нет.</p>}
      {errs.map((t, i) => (
        <div key={i} className="msg err">
          {t}
        </div>
      ))}
      {warns.map((t, i) => (
        <div key={i} className="msg warn">
          {t}
        </div>
      ))}
      <p className="hint">Отчёты за затронутые годы временно будут недоступны, пока данные загружаются заново.</p>
    </Modal>
  )
}

// ---------- предложение ----------
type PStatus = 'new' | 'changed' | 'same' | 'gone'
interface PRow {
  key: string
  src: number
  firm: number
  period: number
  name: string
  label?: number
  date_from?: string
  date_to?: string
  lines?: number
  role: BookRole
  divisor?: number
  oem?: boolean
  reason: string
  warnings: string[]
  status: PStatus
  diffs: string[]
  stat?: SourceStat
  /** источник можно сделать книгой вручную (нет в разметке) */
  canBook?: boolean
  cannot?: string
}

function buildProposalRows(prop: Proposal, base: Book[]): PRow[] {
  const out: PRow[] = []
  const seen = new Set<number>()
  const byIdx = (s: number) => base.filter((b) => srcOf(b) === s)
  const info = new Map<number, NonNullable<Proposal['sources']>[number]>((prop.sources ?? []).map((x) => [x.src, x]))
  const extra = (src: number) => {
    const i = info.get(src)
    return { stat: i as SourceStat | undefined, canBook: i ? i.can_be_book !== false : undefined, cannot: i?.cannot_reason }
  }
  for (const p of prop.books) {
    seen.add(p.src)
    const cands = byIdx(p.src)
    const cur = cands.find((c) => c.date_from === p.date_from) ?? (cands.length === 1 ? cands[0] : undefined)
    const diffs: string[] = []
    let status: PStatus = 'same'
    if (!cur || (cur.role === 'off' && p.role !== 'off')) {
      status = 'new'
    } else {
      if (cur.role !== p.role) diffs.push(`роль: сейчас «${ROLE_RU[cur.role].toLowerCase()}»`)
      if (cur.label !== p.label) diffs.push(`год: сейчас ${cur.label}`)
      if (cur.date_from !== p.date_from || cur.date_to !== p.date_to) diffs.push(`даты: сейчас ${fmtDate(cur.date_from)}–${fmtDate(cur.date_to)}`)
      if (cur.divisor !== p.divisor) diffs.push(`делитель: сейчас ${cur.divisor}`)
      if (cur.oem !== p.oem) diffs.push(`старая кодировка: сейчас ${cur.oem ? 'да' : 'нет'}`)
      if (diffs.length) status = 'changed'
    }
    out.push({
      key: `${p.src}|${p.date_from}`,
      src: p.src,
      firm: p.firm,
      period: p.period,
      name: p.name,
      label: p.label,
      date_from: p.date_from,
      date_to: p.date_to,
      lines: cur?.line_count,
      role: p.role,
      divisor: p.divisor,
      oem: p.oem,
      reason: p.reason + (p.oem_note ? ' ' + p.oem_note : ''),
      warnings: p.warnings ?? [],
      status,
      diffs,
      stat: p,
      canBook: cands.length ? undefined : info.get(p.src)?.can_be_book !== false,
    })
  }
  const un = new Map(prop.unassigned_data.map((u) => [u.src, u]))
  for (const o of prop.off) {
    if (seen.has(o.src)) continue
    seen.add(o.src)
    const cands = byIdx(o.src)
    const used = cands.filter((c) => c.role !== 'off')
    const u = un.get(o.src)
    const ex = extra(o.src)
    out.push({
      key: 'off|' + o.src,
      src: o.src,
      firm: o.firm,
      period: o.period,
      name: o.firm_name || o.title || cands[0]?.firm_name || cands[0]?.name || '',
      date_from: u?.date_from ?? cands[0]?.date_from,
      date_to: u?.date_to ?? cands[0]?.date_to,
      lines: u?.lines ?? o.lines ?? cands[0]?.line_count,
      role: 'off',
      reason: o.reasons.join('; '),
      warnings: [],
      status: used.length ? 'changed' : 'same',
      diffs: used.length ? [`сейчас используется как «${ROLE_RU[used[0].role].toLowerCase()}» (${used[0].label} год)`] : [],
      ...ex,
      stat: o,
      canBook: ex.canBook && !used.length,
    })
  }
  for (const u of prop.unassigned_data) {
    if (seen.has(u.src)) continue
    seen.add(u.src)
    const ex = extra(u.src)
    out.push({
      key: 'un|' + u.src,
      src: u.src,
      firm: u.firm,
      period: u.period,
      name: u.firm_name || u.title || '',
      date_from: u.date_from,
      date_to: u.date_to,
      lines: u.lines,
      role: 'off',
      reason: u.reasons.join('; '),
      warnings: [],
      status: 'same',
      diffs: [],
      ...ex,
      stat: u,
      canBook: ex.canBook && !byIdx(u.src).some((c) => c.role !== 'off'),
    })
  }
  // остальные найденные источники (не вошли ни в предложение, ни в «не использовать»)
  for (const i of prop.sources ?? []) {
    if (seen.has(i.src)) continue
    seen.add(i.src)
    out.push({
      key: 'src|' + i.src,
      src: i.src,
      firm: i.firm,
      period: i.period,
      name: i.firm_name || i.title || '',
      date_from: i.turn_date_min || i.date_min || i.begdate || undefined,
      date_to: i.turn_date_max || i.date_max || i.enddate || undefined,
      lines: i.lines,
      role: 'off',
      reason: (i.reasons ?? []).join('; ') || 'Не вошёл в предложение программы.',
      warnings: [],
      status: 'same',
      diffs: [],
      stat: i,
      canBook: i.can_be_book !== false && !byIdx(i.src).some((c) => c.role !== 'off'),
      cannot: i.cannot_reason,
    })
  }
  for (const b of base) {
    if (seen.has(srcOf(b)) || b.role === 'off') continue
    seen.add(srcOf(b))
    out.push({
      key: 'gone|' + srcOf(b),
      src: srcOf(b),
      firm: b.src_firm,
      period: b.src_period,
      name: b.firm_name || b.name || '',
      label: b.label,
      date_from: b.date_from,
      date_to: b.date_to,
      lines: b.line_count,
      role: b.role,
      reason: 'Этой книги нет в предложении программы: она добавлена вручную или в Logo больше не найдена.',
      warnings: [],
      status: 'gone',
      diffs: [],
    })
  }
  const rank = (r: PRow) => (r.role === 'off' ? 1 : 0)
  return out.sort((a, b) => rank(a) - rank(b) || (a.label ?? 9999) - (b.label ?? 9999) || a.src - b.src)
}

const STATUS_RU: Record<PStatus, string> = { new: 'новое', changed: 'изменится', same: 'без изменений', gone: 'нет в предложении' }

function ProposalTable({ prop, rows, diffCount, onAdd }: { prop: Proposal; rows: PRow[]; diffCount: number; onAdd: (r: PRow) => void }) {
  const [onlyDiff, setOnlyDiff] = useState(false)
  const [hideOff, setHideOff] = useState(false)
  const shown = rows.filter((r) => (!onlyDiff || r.status !== 'same') && (!hideOff || r.role !== 'off'))
  const gaps = prop.gaps_between_labels.filter((g) => g.days > 0)
  return (
    <>
      <div className="toolbar">
        <span className="muted">
          найдено источников: {prop.sources_total} (в справочнике Logo: {prop.sources_in_logo}); отличий от текущей разметки: <b>{diffCount}</b>
        </span>
        <div className="spacer" />
        <label className="chk" style={{ margin: 0 }}>
          <input type="checkbox" checked={onlyDiff} onChange={(e) => setOnlyDiff(e.target.checked)} />
          только отличия
        </label>
        <label className="chk" style={{ margin: 0 }}>
          <input type="checkbox" checked={hideOff} onChange={(e) => setHideOff(e.target.checked)} />
          скрыть неиспользуемые
        </label>
      </div>
      {gaps.map((g, i) => (
        <div key={i} className="msg warn">
          Между {g.after} и {g.before} годами не покрыто дней: {g.days}.
        </div>
      ))}
      <table className="simple">
        <thead>
          <tr>
            <th style={{ width: 150 }}>Фирма и период Logo</th>
            <th style={{ width: 170 }}>Даты</th>
            <th style={{ width: 250 }}>Что в Logo</th>
            <th style={{ width: 120 }}>Предложение</th>
            <th style={{ width: 150 }}>Сравнение с текущей</th>
            <th>Причина</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((r) => (
            <tr key={r.key} className={r.role === 'off' ? 'dim' : r.role === 'main' ? 'book-main' : ''}>
              <td>
                Фирма {r.firm}, период {String(r.period).padStart(2, '0')}
                {r.name && <div className="muted">{r.name}</div>}
              </td>
              <td className="nowrap">{r.date_from ? `${fmtDate(r.date_from)} — ${fmtDate(r.date_to)}` : <span className="muted">нет данных</span>}</td>
              <td className="small">
                <SourceStats r={r} />
              </td>
              <td>
                {r.role !== 'off' && r.label ? <b>{r.label} · </b> : ''}
                {ROLE_RU[r.role]}
                {r.divisor && r.divisor !== 1 ? <div className="muted">делитель {r.divisor}</div> : null}
              </td>
              <td>
                <span className={'tag ' + (r.status === 'new' ? 'green' : r.status === 'same' ? 'gray' : r.status === 'gone' ? 'red' : '')}>{STATUS_RU[r.status]}</span>
                {r.diffs.map((d, i) => (
                  <div key={i} className="muted small">
                    {d}
                  </div>
                ))}
              </td>
              <td>
                <span className="reason">{r.reason}</span>
                {r.status !== 'gone' && r.stat && !r.canBook && r.cannot && <div className="reason warn-t">Книгой сделать нельзя: {r.cannot}.</div>}
                {r.status !== 'gone' && r.stat && r.canBook && (
                  <div>
                    <button className="btn small" onClick={() => onAdd(r)} title="Добавить в таблицу «Текущая разметка» книгу на этот источник">
                      Добавить книгой
                    </button>
                  </div>
                )}
                {r.warnings.map((w, i) => (
                  <div key={i} className="reason warn-t">
                    ⚠ {w}
                  </div>
                ))}
              </td>
            </tr>
          ))}
          {shown.length === 0 && (
            <tr>
              <td colSpan={6} className="muted pad">
                Ничего не найдено по выбранным условиям.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </>
  )
}

const mny = (v: number | undefined) => (v == null ? '—' : fmtMoney(v))

function SourceStats({ r }: { r: PRow }) {
  const t = r.stat
  const cnt = (v: number | null | undefined) => (v == null ? '—' : v.toLocaleString('ru-RU'))
  if (!t || t.lines == null) return <span className="muted">{r.lines != null ? `проводок: ${cnt(r.lines)}` : 'ещё не загружено'}</span>
  return (
    <>
      <div>
        проводок: <b>{cnt(t.lines)}</b> (открытие {cnt(t.open_lines)}, обороты {cnt(t.turn_lines)}); ордеров: {cnt(t.fiches)}
      </div>
      {t.turn_dt != null && (
        <div className="muted">
          обороты: Дт {mny(t.turn_dt)} / Кт {mny(t.turn_kt)}
        </div>
      )}
      {t.turn_date_min && (
        <div className="muted">
          обороты с {fmtDate(t.turn_date_min)} по {fmtDate(t.turn_date_max)}
        </div>
      )}
      {t.begdate && (
        <div className="muted">
          период в Logo: {fmtDate(t.begdate)} — {fmtDate(t.enddate)}
        </div>
      )}
      <div>
        {t.has_open === false && <span className="tag">нет открытия</span>} {!!t.oem_rows && <span className="tag">старая кодировка: {fmtInt(t.oem_rows)} строк</span>}
      </div>
    </>
  )
}

const PARAM_RU: Record<string, string> = {
  currency_local: 'Валюта учёта',
  currency_report: 'Отчётная валюта',
  charset: 'Кодировка текстов',
  denomination: 'Деноминация',
  plan_profile: 'Профиль плана счетов',
  carry_notes: 'Пометки о разрывах переноса',
  sync: 'Обновление данных',
}

function paramText(k: string, v: unknown): string {
  if (v == null) return 'не задано'
  const x = v as Record<string, unknown>
  if (k === 'currency_local' || k === 'currency_report') return `${x.code ?? ''}${x.name ? ' — ' + x.name : ''}${k === 'currency_report' && x.enabled === false ? ' (выключена)' : ''}`
  if (k === 'charset') return `основная ${x.ansi}, старая ${x.oem}`
  if (k === 'denomination') return Array.isArray(v) && v.length ? (v as { before: string; divisor: number }[]).map((d) => `до ${fmtDate(d.before)} ÷ ${d.divisor}`).join('; ') : 'нет'
  if (k === 'plan_profile') return `${x.template === 'turkish' ? 'турецкий план' : 'свой план'}, ${x.mode === 'width' ? 'уровни по длине' : `разделитель «${x.sep ?? ''}»`}, корень ${x.root_len ?? '?'}`
  return JSON.stringify(v)
}

/** Предложенные программой параметры: показываются отдельно и применяются только по кнопке. */
function ParamsSuggest({ prop, onApplied, onError, onInfo }: { prop: Proposal; onApplied: () => void; onError: (e: string) => void; onInfo: (t: string) => void }) {
  const items = Object.entries(prop.params_suggested ?? {}).filter(([, v]) => v.differs || !v.configured)
  const [busy, setBusy] = useState<string | null>(null)
  if (!items.length) return null
  const apply = async (k: string, v: unknown) => {
    if ((k === 'charset' || k === 'plan_profile') && !(await askConfirm('Смена этого параметра заставит программу заново прочитать названия или планы счетов из Logo (проводки не затрагиваются). Применить?', { okLabel: 'Применить' }))) return
    setBusy(k)
    try {
      await api.saveParams({ [k]: v } as DatasetParams)
      onInfo(`Параметр «${PARAM_RU[k] ?? k}» применён.`)
      onApplied()
    } catch (e) {
      onError(errMsg(e))
    } finally {
      setBusy(null)
    }
  }
  return (
    <div className="msg warn" style={{ display: 'block' }}>
      <b>Программа предлагает изменить параметры набора</b>
      <span className="muted"> — заданные вами значения сами не меняются.</span>
      <table className="simple" style={{ marginTop: 6 }}>
        <thead>
          <tr>
            <th style={{ width: 170 }}>Параметр</th>
            <th>Сейчас</th>
            <th>Предложено</th>
            <th>Откуда</th>
            <th style={{ width: 110 }} />
          </tr>
        </thead>
        <tbody>
          {items.map(([k, v]) => (
            <tr key={k}>
              <td>{PARAM_RU[k] ?? k}</td>
              <td>{v.configured ? paramText(k, v.current) : <span className="muted">не задано</span>}</td>
              <td>{paramText(k, v.value)}</td>
              <td className="reason">{v.source}</td>
              <td>
                <button className="btn small" disabled={busy !== null} onClick={() => apply(k, v.value)}>
                  {busy === k ? 'Применяем…' : 'Применить'}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ApplyConfirm({ prop, rows, dirty, onClose, onDone }: { prop: Proposal; rows: PRow[]; dirty: boolean; onClose: () => void; onDone: (r: { sync_needed?: number[] }) => void }) {
  const { data: ds } = useDataset()
  const [withParams, setWithParams] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const nw = rows.filter((r) => r.status === 'new')
  const ch = rows.filter((r) => r.status === 'changed')
  const pdiff = Object.entries(prop.params_suggested ?? {})
    .filter(([, v]) => !v.configured)
    .map(([k]) => PARAM_RU[k] ?? k)
  const go = async () => {
    setBusy(true)
    try {
      onDone(await api.applyProposal(withParams))
    } catch (e) {
      setErr(errMsg(e))
    } finally {
      setBusy(false)
    }
  }
  const line = (r: PRow) => `${r.label ? r.label + ' · ' : ''}фирма ${r.firm}${r.period !== 1 ? `, период ${String(r.period).padStart(2, '0')}` : ''} — ${ROLE_RU[r.role].toLowerCase()}${r.diffs.length ? ' (' + r.diffs.join('; ') + ')' : ''}`
  return (
    <Modal
      title="Применить предложение программы целиком?"
      onClose={onClose}
      wide
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            Отмена
          </button>
          <button className="btn danger" onClick={go} disabled={busy}>
            {busy ? 'Применяем…' : 'Применить'}
          </button>
        </>
      }
    >
      <p>Текущая разметка книг будет заменена предложением программы. Все книги, ставшие основными, будут загружены заново.</p>
      {dirty && <div className="msg warn">Несохранённые правки в таблице книг будут потеряны.</div>}
      <SummaryList title={`Новые (${nw.length}):`} items={nw.map(line)} />
      <SummaryList title={`Изменятся (${ch.length}):`} items={ch.map(line)} />
      <label className="chk">
        <input type="checkbox" checked={withParams} onChange={(e) => setWithParams(e.target.checked)} />
        Записать и параметры набора, которые ещё не заданы (валюта, кодировка, профиль плана счетов)
      </label>
      {pdiff.length > 0 && withParams && (
        <div className="msg warn">
          <span>Будут записаны незаданные параметры: {pdiff.join('; ')}. Уже заданные значения не изменятся — их можно применить отдельно в блоке «Программа предлагает изменить параметры».</span>
        </div>
      )}
      <ErrorBox error={err} />
    </Modal>
  )
}
