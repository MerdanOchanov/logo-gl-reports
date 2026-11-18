import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, downloadXlsx, errMsg, type Column, type DatasetInfo, type Definition, type ReportType, type Result, type ResultRow } from '../api'
import DrillPanel, { type DrillTarget } from '../components/DrillPanel'
import FicheModal from '../components/FicheModal'
import ResultTable from '../components/ResultTable'
import ChecksBar from '../components/Checks'
import { ErrorBox, SaveDialog } from '../components/ui'
import { defaultDefinition, fillCheckNames, fmtDate, normalizeDefinition, typeLabel } from '../format'
import { currencyOptions, getDataset, paramsUnset, useDataset } from '../dataset'
import { navigate, setNavGuard } from '../router'
import { openAccountCard } from './cardLink'
import { AccountFilterBlock, AttrFilterSection, CurrencyBlock, PeriodBlock, Section, hasAttrFilter, type SetDef } from './common'
import ColumnsMenu, { EXTRA_LABELS } from '../components/ColumnsMenu'
import { TYPE_CONFIG } from './types'

interface Props {
  reportId?: number
  newType?: ReportType
  /** Параметры из ссылки «Открыть карточку счёта»: #/report/new/card?account=...&from=...&to=... */
  preset?: URLSearchParams
}

const ACCOUNT_REQUIRED: Partial<Record<ReportType, string>> = {
  card: 'Укажите счёт для карточки (например 100.01) или выберите группу классификатора.',
  analysis: 'Укажите счёт для анализа (например 320) или выберите группу классификатора.',
  journal: 'Укажите счёт для журнала-ордера (например 100) или выберите группу классификатора.',
}

/** Организация для шапки: название фирмы Logo за год конца периода без хвостового года («Фирма 2026» → «Фирма»). */
function useOrgName(year: number): string {
  const [name, setName] = useState('')
  useEffect(() => {
    let alive = true
    api
      .firms()
      .then((l) => {
        const f = l.filter((x) => x.year === year).sort((a, b) => Number(!!b.enabled) - Number(!!a.enabled) || (b.line_count ?? 0) - (a.line_count ?? 0))[0]
        if (alive && f) setName(f.name.replace(/\s*\b(19|20)\d{2}\b\s*$/, '').trim() || f.name)
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [year])
  return name
}

/** «Формируется…» с пояснением, если расчёт идёт долго (первый расчёт после запуска сервера может занять до минуты). */
function RunningNote() {
  const [sec, setSec] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setSec((x) => x + 1), 1000)
    return () => clearInterval(t)
  }, [])
  return (
    <div className="muted pad">
      Формируется… {sec} с{sec >= 8 ? ' — первый расчёт после запуска сервера или обновления данных занимает до минуты, дальше будет быстро.' : ''}
    </div>
  )
}

const SCALE_WORD: Record<number, string> = { 1: 'в единицах', 1000: 'в тысячах', 1000000: 'в миллионах' }

/** Строка под названием: организация · период · валюта, единицы · с закрывающими ордерами / без. */
export function paramLine(def: Definition, org: string, ds?: DatasetInfo | null): string {
  let period = `${fmtDate(def.period?.date_from)} – ${fmtDate(def.period?.date_to)}`
  if (def.type === 'compare_years') period = `${(def.years ?? []).join(', ')} (${def.mmdd_from ?? '01-01'} … ${def.mmdd_to ?? '12-31'})`
  const cur = currencyOptions(ds).find((o) => o.code === def.currency)?.label ?? def.currency ?? ''
  return [
    org || 'организация не определена',
    period,
    `${cur ? cur + ', ' : ''}${SCALE_WORD[def.scale] ?? 'в единицах'}`,
    def.include_closing ? 'с закрывающими ордерами' : 'без закрывающих ордеров',
  ].join(' · ')
}

const TURN_FN = /(OBDK|OBD|OBK|ОБДК|ОБД|ОБК)\s*\(/i
/** Для ячейки формы: в формуле строки (с учётом ссылок на другие строки) есть обороты? */
function formulaHasTurnover(def: Definition, code: string | undefined, seen = new Set<string>()): boolean {
  if (!code || seen.has(code)) return false
  seen.add(code)
  const line = def.form?.lines.find((l) => l.code === code)
  if (!line) return false
  if (TURN_FN.test(line.formula)) return true
  const refs = [...line.formula.matchAll(/\[([^\]]+)\]/g)].map((m) => m[1])
  return refs.some((r) => formulaHasTurnover(def, r, seen))
}

/** Ключ параметров для сравнения «изменились ли данные»: набор дополнительных колонок на расчёт не влияет. */
const keyOf = (d: Definition) => JSON.stringify({ ...d, columns: { ...(d.columns ?? {}), extra: undefined } })

const MONTH_KEY = /(\d{4})-(\d{2})/

export default function ReportScreen({ reportId, newType, preset }: Props) {
  const [id, setId] = useState<number | undefined>(reportId)
  const [name, setName] = useState('')
  const [folder, setFolder] = useState<string | null>(null)
  // новый отчёт создаётся после чтения параметров набора: валюта по умолчанию — из набора
  const [def, setDefState] = useState<Definition | null>(null)
  const { data: ds } = useDataset()
  const [ranKey, setRanKey] = useState<string | null>(null)
  const [result, setResult] = useState<Result | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const [panelOpen, setPanelOpen] = useState(true)
  const [drill, setDrill] = useState<DrillTarget | null>(null)
  const [ficheOpen, setFicheOpen] = useState<{ firm: number; fiche_id: number; no: string } | null>(null)
  const [saveDlg, setSaveDlg] = useState<'save' | 'saveas' | null>(null)
  const [folders, setFolders] = useState<string[]>([])
  const runSeq = useRef(0)

  const setDef: SetDef = useCallback((f) => {
    setDefState((d) => (d ? f(d) : d))
    setDirty(true)
  }, [])

  const run = useCallback(async (d: Definition) => {
    const seq = ++runSeq.current
    setRunning(true)
    setError(null)
    try {
      const r = await api.run(d)
      if (seq === runSeq.current) {
        setResult(r)
        setRanKey(keyOf(d))
      }
    } catch (e) {
      if (seq === runSeq.current) setError(errMsg(e))
    } finally {
      if (seq === runSeq.current) setRunning(false)
    }
  }, [])

  // загрузка сохранённого отчёта: определение приводится к полному виду (в примерах и старых отчётах часть разделов может отсутствовать)
  useEffect(() => {
    if (reportId === undefined) return
    let alive = true
    Promise.all([api.report(reportId), getDataset()])
      .then(([r, dsi]) => {
        if (!alive) return
        const d = normalizeDefinition(r.definition, dsi?.params?.currency_local?.code)
        setId(r.id)
        setName(r.name)
        setFolder(r.folder)
        setDefState(d)
        setDirty(false)
        run(d)
      })
      .catch((e) => alive && setError(errMsg(e)))
    return () => {
      alive = false
    }
  }, [reportId, run])

  // новый отчёт; карточка, открытая ссылкой из расшифровки, сразу формируется
  useEffect(() => {
    if (!newType) return
    let alive = true
    getDataset().then((dsi) => {
      if (!alive) return
      const local = dsi?.params?.currency_local?.code ?? ''
      const d = defaultDefinition(newType, undefined, local)
      if (!dsi) setError('Не удалось прочитать параметры набора данных. Обновите страницу.')
      if (preset && preset.get('account')) {
        d.account = preset.get('account')!
        if (preset.get('from') && preset.get('to')) d.period = { date_from: preset.get('from')!, date_to: preset.get('to')! }
        d.include_closing = preset.get('closing') !== '0'
        const pc = preset.get('cur')
        d.currency = pc && currencyOptions(dsi).some((o) => o.code === pc) ? pc : local
        d.scale = (Number(preset.get('scale')) || 1) as Definition['scale']
        d.denominate = preset.get('den') !== '0'
        d.title = `Карточка счёта ${d.account}`
      }
      setDefState(d)
      if (preset && preset.get('account')) run(d)
    })
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // предупреждение об уходе со страницы при несохранённых изменениях
  useEffect(() => {
    if (!dirty) {
      setNavGuard(null)
      return
    }
    setNavGuard(() => 'В отчёте есть несохранённые изменения параметров. Уйти без сохранения?')
    const h = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', h)
    return () => {
      window.removeEventListener('beforeunload', h)
      setNavGuard(null)
    }
  }, [dirty])

  const extra = def?.columns?.extra
  const optCols = useMemo(() => (result ? result.columns.filter((c) => c.optional) : []), [result])
  const viewResult = useMemo(() => {
    if (!result || !optCols.length) return result
    const sel = extra ?? []
    return {
      ...result,
      columns: result.columns.filter((c) => !c.optional || sel.includes(c.key)).map((c) => (c.optional && EXTRA_LABELS[c.key] ? { ...c, title: EXTRA_LABELS[c.key] } : c)),
    }
  }, [result, optCols, extra])

  const org = useOrgName(Number(def?.period?.date_to?.slice(0, 4)) || new Date().getFullYear())

  useEffect(() => {
    if (!info) return
    const t = setTimeout(() => setInfo(null), 3500)
    return () => clearTimeout(t)
  }, [info])

  if (!def) {
    return (
      <div className="page">
        <ErrorBox error={error} />
        {!error && <div className="muted pad">Загрузка отчёта…</div>}
      </div>
    )
  }

  const cfg = TYPE_CONFIG[def.type] ?? TYPE_CONFIG.osv
  const bp = { def, setDef, result }

  const onRun = () => {
    if (!cfg.ready) {
      setError('Этот тип отчёта пока в разработке.')
      return
    }
    const need = ACCOUNT_REQUIRED[def.type]
    if (need && !(def.account ?? '').trim() && !def.filters.groups.length) {
      setError(need)
      return
    }
    const d = fillCheckNames(def)
    if (d !== def) setDef(() => d)
    run(d)
  }

  const payload = () => ({ name: name.trim() || def.title || typeLabel(def.type), type: def.type, folder, definition: { ...fillCheckNames(def), title: def.title } })

  const openSave = (mode: 'save' | 'saveas') => {
    api
      .reports()
      .then((l) => setFolders([...new Set(l.map((r) => r.folder).filter((f): f is string => !!f))]))
      .catch(() => {})
    setSaveDlg(mode)
  }
  const onSave = () => (id === undefined ? openSave('saveas') : openSave('save'))
  const onSaveAs = () => openSave('saveas')

  const doSave = async (v: { name: string; folder: string | null; comment: string }) => {
    if (saveDlg === 'save' && id !== undefined) {
      await api.saveReport(id, { ...payload(), name: v.name, folder: v.folder, comment: v.comment })
      setName(v.name)
      setFolder(v.folder)
      setDirty(false)
      setSaveDlg(null)
      setInfo('Отчёт сохранён (в историю добавлена версия).')
    } else {
      const r = await api.createReport({ ...payload(), name: v.name, folder: v.folder, comment: v.comment })
      setId(r.id)
      setName(v.name)
      setFolder(v.folder)
      setDirty(false)
      setSaveDlg(null)
      setInfo('Отчёт сохранён.')
      navigate(`/report/${r.id}`, true)
    }
  }

  /** Название для шапки печати и Excel: заголовок на печати, если задан; иначе название отчёта. */
  const customTitle = def.title && def.title !== typeLabel(def.type) ? def.title : ''
  const shownName = name || def.title
  const onExcel = async () => {
    try {
      await downloadXlsx(def, name || def.title || 'report', customTitle || name || undefined)
    } catch (e) {
      setError(errMsg(e))
    }
  }

  const isDrillable = (row: ResultRow, col: Column) => {
    const d = row.drill
    if (!d || !(d.accounts?.length || d.group != null || d.groups?.length)) return false
    // форма по строкам: графы без собственной расшифровки — по счетам формулы за период графы
    if (def.type === 'form') return /^(begin|end|period|prev|prev_begin)$/.test(col.key)
    // графы сальдо расшифровать проводками нельзя — только обороты
    if (col.drill === null) return false
    return !/^(open|close|beg|end|bal|saldo|sn|sk|prev|chg|pct|gap|diff|delta)/i.test(col.key)
  }

  const onCell = ({ row, col }: { row: ResultRow; col: Column }) => {
    const d = row.drill!
    let period = col.period ?? d.period ?? def.period
    const mk = MONTH_KEY.exec(col.key)
    if (mk && !d.period && !col.period) {
      const y = Number(mk[1])
      const m = Number(mk[2])
      const last = new Date(y, m, 0).getDate()
      period = { date_from: `${mk[1]}-${mk[2]}-01`, date_to: `${mk[1]}-${mk[2]}-${String(last).padStart(2, '0')}` }
    }
    const side: 'dt' | 'kt' | undefined = d.side ?? col.drill ?? (/_dt$|dt$/i.test(col.key) ? 'dt' : /_kt$|kt$|_cr$/i.test(col.key) ? 'kt' : undefined)
    let mode: 'turn' | 'open' | 'close' | undefined
    if (def.type === 'form') {
      // сальдо на начало/конец — проводки, из которых оно сложилось; обороты — проводки периода
      if (col.key === 'begin' || col.key === 'prev_begin') mode = 'open'
      else mode = formulaHasTurnover(def, row.code) ? 'turn' : 'close'
    }
    setDrill({
      title: `${row.code ?? ''} ${row.name ?? ''} — ${col.group ? col.group + ', ' : ''}${col.title}`.trim(),
      ctx: { period, include_closing: def.include_closing, currency: def.currency, scale: def.scale, denominate: def.denominate },
      note:
        mode === 'open' || mode === 'close'
          ? 'Показаны проводки, из которых сложилось сальдо. Сальдо ячейки = Дебет − Кредит этой расшифровки (для «развёрнутого» сальдо и расчётных строк итог может отличаться).'
          : undefined,
      req: {
        mode,
        period,
        accounts: d.accounts,
        group: d.group,
        groups: d.groups,
        side,
        corr: col.corr ?? d.corr,
        corr_groups: col.corr_groups ?? d.corr_groups,
        corr_exclude: col.corr_exclude ?? d.corr_exclude,
        include_closing: def.include_closing,
        branches: col.branch != null ? [col.branch] : def.filters.branches?.length ? def.filters.branches : undefined,
        departments: def.filters.departments?.length ? def.filters.departments : undefined,
        sources: def.filters.sources?.length ? def.filters.sources : undefined,
        authors: def.filters.authors?.length ? def.filters.authors : undefined,
      },
      extra: col.type === 'qty' ? ['qty'] : undefined,
    })
  }

  return (
    <div className="report-screen">
      <aside className={'params no-print' + (panelOpen ? '' : ' closed')}>
        {panelOpen ? (
          <>
            <div className="params-head">
              <b>Параметры</b>
              <button className="btn small ghost" onClick={() => setPanelOpen(false)} title="Свернуть панель">
                ◀
              </button>
            </div>
            <div className="params-scroll">
              <Section title="Отчёт" open={false}>
                <label>
                  название
                  <input type="text" value={name} placeholder={def.title} onChange={(e) => (setName(e.target.value), setDirty(true))} />
                </label>
                <label>
                  заголовок на печати
                  <input type="text" value={def.title} onChange={(e) => setDef((d) => ({ ...d, title: e.target.value }))} />
                </label>
                <label>
                  папка
                  <input type="text" value={folder ?? ''} onChange={(e) => (setFolder(e.target.value || null), setDirty(true))} />
                </label>
              </Section>
              {cfg.common.period && (
                <Section title="Период">
                  <PeriodBlock {...bp} />
                </Section>
              )}
              {cfg.common.currency && (
                <Section title="Валюта и единицы">
                  <CurrencyBlock {...bp} />
                </Section>
              )}
              {cfg.common.accounts && (
                <Section title="Фильтр счетов">
                  <AccountFilterBlock {...bp} />
                </Section>
              )}
              {def.type !== 'carry_gaps' && <AttrFilterSection {...bp} />}
              {cfg.Params(bp)}
            </div>
          </>
        ) : (
          <button className="params-open" onClick={() => setPanelOpen(true)} title="Показать параметры">
            ▶
          </button>
        )}
      </aside>

      <div className="report-main">
        <div className="toolbar no-print">
          <div className="rtitle">
            <div>
              <b>{shownName}</b>
              {shownName !== typeLabel(def.type) && <span className="muted"> · {typeLabel(def.type)}</span>}
              {dirty && <span className="tag" title="Есть несохранённые изменения параметров"> не сохранено</span>}
            </div>
            <div className="rsub">{paramLine(def, org, ds)}</div>
          </div>
          <div className="spacer" />
          <button className="btn primary" onClick={onRun} disabled={running}>
            {running ? 'Формируется…' : 'Сформировать'}
          </button>
          <button className="btn needs-edit" onClick={onSave}>
            Сохранить
          </button>
          <button className="btn needs-edit" onClick={onSaveAs}>
            Сохранить как…
          </button>
          <button className="btn" onClick={onExcel} disabled={!cfg.ready}>
            Excel
          </button>
          <button className="btn" onClick={() => window.print()} disabled={!result}>
            Печать
          </button>
        </div>
        <ErrorBox error={error} onClose={() => setError(null)} />
        {paramsUnset(ds) && (
          <div className="msg warn no-print">
            <span>Параметры набора данных (валюта, кодировка, план счетов) ещё не заданы — отчёт может не сформироваться или показать не то, что нужно.</span>
            <button className="btn small" onClick={() => navigate('/settings/params')}>
              Настройки → Параметры
            </button>
          </div>
        )}
        {info && <div className="msg ok no-print">{info}</div>}

        {cfg.ready && !result && !running && !error && (
          <div className="muted pad">Задайте параметры слева и нажмите «Сформировать».</div>
        )}
        {cfg.ready && running && !result && <RunningNote />}

        {result && cfg.ready && (
          <div className={'result-area' + (running ? ' stale' : '')}>
            <div className="print-head">
              <h1>{customTitle || name || result.title || def.title}</h1>
              <div>{paramLine(def, org, ds)}</div>
              {result.subtitle && <div>{result.subtitle}</div>}
            </div>
            {ranKey !== null && ranKey !== keyOf(def) && !running && (
              <div className="stale-hint no-print">Параметры изменены после последнего формирования — таблица показывает прежние данные. Нажмите «Сформировать».</div>
            )}
            <div className="screen-sub no-print">
              <b>{result.title}</b> {result.subtitle && <span className="muted"> — {result.subtitle}</span>}
            </div>
            {result.rows.filter((r) => r.kind !== 'total').length === 0 && (
              <div className="msg warn no-print">По заданным параметрам данных нет: проверьте период, счёт и фильтры. Возможно, за выбранные годы не загружены данные (Настройки → Фирмы и синхронизация).</div>
            )}
            {hasAttrFilter(def) && (
              <div className="msg warn no-print">
                Задан отбор по признакам, поэтому сверка с полным оборотом не выполняется.
                {def.filters.branches?.length && ['analysis', 'ledger', 'chess', 'journal'].includes(def.type)
                  ? ' В корреспонденции учитываются только пары, обе стороны которых относятся к выбранным подразделениям: пары между разными подразделениями не показываются.'
                  : ''}
              </div>
            )}
            {optCols.length > 0 && (
              <div className="no-print" style={{ margin: '4px 0' }}>
                <ColumnsMenu
                  available={optCols.map((c) => c.key)}
                  selected={def.columns.extra ?? []}
                  onChange={(v) => setDef((d) => ({ ...d, columns: { ...d.columns, extra: v } }))}
                />
              </div>
            )}
            <ResultTable
              result={viewResult ?? result}
              isDrillable={isDrillable}
              onCellClick={onCell}
              codeTitle={cfg.codeTitle}
              nameTitle={cfg.nameTitle}
              onOpenCard={def.type === 'card' ? (r) => typeof r.values?.account === 'string' && openAccountCard(r.values.account, { period: def.period, include_closing: def.include_closing, currency: def.currency, scale: def.scale, denominate: def.denominate }) : undefined}
              onOpenFiche={(r) => r.fiche && setFicheOpen({ firm: r.fiche.firm, fiche_id: r.fiche.fiche_id, no: String(r.values?.fiche_no ?? r.fiche.fiche_id) })}
            />
            <ChecksBar checks={result.checks} warnings={result.warnings} />
          </div>
        )}
      </div>
      {ficheOpen && <FicheModal firm={ficheOpen.firm} ficheId={ficheOpen.fiche_id} ficheNo={ficheOpen.no} onClose={() => setFicheOpen(null)} />}
      {saveDlg && (
        <SaveDialog
          initial={{ name: name || def.title || typeLabel(def.type), folder }}
          folders={folders}
          asNew={saveDlg === 'saveas'}
          onSave={doSave}
          onClose={() => setSaveDlg(null)}
        />
      )}
      {drill && <DrillPanel target={drill} onClose={() => setDrill(null)} />}
    </div>
  )
}
