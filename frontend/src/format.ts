import type { Definition, ReportType } from './api'

const nf = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2, useGrouping: true })

/** Деньги: пробел-разделитель разрядов, запятая, 2 знака. Ноль/пусто -> пустая строка. */
export function fmtMoney(v: unknown): string {
  if (v === null || v === undefined || v === '') return ''
  const n = typeof v === 'number' ? v : Number(v)
  if (!isFinite(n)) return ''
  if (Math.abs(n) < 0.005) return ''
  // неразрывные пробелы Intl заменяем на обычные тонкие для компактности
  return nf.format(n).replace(/[  ]/g, ' ')
}

export type NumFmt = 'full' | 'thousand' | 'million' | 'compact'
const nf1 = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 0, maximumFractionDigits: 1 })
const nf0 = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 0, maximumFractionDigits: 0 })

/** Компактные числа для широких таблиц: тыс. / млн. / автоматически. */
export function fmtCompact(v: unknown, mode: NumFmt): string {
  if (v === null || v === undefined || v === '') return ''
  const n = typeof v === 'number' ? v : Number(v)
  if (!isFinite(n) || Math.abs(n) < 0.005) return ''
  const a = Math.abs(n)
  const fix = (s: string) => s.replace(/[  ]/g, ' ')
  if (mode === 'thousand') return fix(nf0.format(n / 1e3))
  if (mode === 'million') return fix(nf1.format(n / 1e6))
  if (a >= 1e6) return fix(nf1.format(n / 1e6)) + ' млн'
  if (a >= 1e3) return fix(nf0.format(n / 1e3)) + ' тыс'
  return fix(nf0.format(n))
}

/** Число для вставки в Excel: без разделителей разрядов, запятая как десятичный знак. */
export function fmtPlain(v: unknown): string {
  if (v === null || v === undefined || v === '') return ''
  const n = typeof v === 'number' ? v : Number(v)
  if (!isFinite(n)) return String(v)
  if (Math.abs(n) < 0.005) return ''
  return n.toFixed(2).replace('.', ',')
}

/** Количество: до 3 знаков после запятой без хвостовых нулей, без денежных единиц и без деления на тыс./млн. */
const nfq = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 0, maximumFractionDigits: 3, useGrouping: true })
export function fmtQty(v: unknown): string {
  if (v === null || v === undefined || v === '') return ''
  const n = typeof v === 'number' ? v : Number(v)
  if (!isFinite(n) || n === 0) return ''
  return nfq.format(n).replace(/[  ]/g, ' ')
}

export function fmtInt(v: unknown): string {
  if (v === null || v === undefined || v === '') return ''
  const n = Number(v)
  if (!isFinite(n) || n === 0) return ''
  return Math.round(n).toLocaleString('ru-RU').replace(/[  ]/g, ' ')
}

export function fmtDate(iso: string | undefined | null): string {
  if (!iso) return ''
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso)
  return m ? `${m[3]}.${m[2]}.${m[1]}` : iso
}

export function fmtDateTime(iso: string | undefined | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  return d.toLocaleString('ru-RU')
}

export const REPORT_TYPES: { type: ReportType; label: string; hint: string; ready: boolean }[] = [
  { type: 'osv', label: 'Оборотно-сальдовая ведомость', hint: 'Сальдо на начало, обороты, сальдо на конец по счетам или группам', ready: true },
  { type: 'card', label: 'Карточка счёта', hint: 'Хронология проводок с нарастающим сальдо, итоги по дням или месяцам', ready: true },
  { type: 'analysis', label: 'Анализ счёта', hint: 'Обороты счёта в разрезе корреспондирующих счетов, по месяцам', ready: true },
  { type: 'ledger', label: 'Главная книга', hint: 'Обороты счёта по месяцам с корреспонденцией', ready: true },
  { type: 'chess', label: 'Шахматная ведомость', hint: 'Матрица корреспонденции Дт × Кт', ready: true },
  { type: 'journal', label: 'Журнал-ордер / ведомость', hint: 'Записи по стороне счёта с разнесением по корреспондентам', ready: true },
  { type: 'form', label: 'Форма по строкам (баланс, ОПУ)', hint: 'Строки с формулами по счетам и группам, шаблон Excel', ready: true },
  { type: 'compare_years', label: 'Сравнение лет', hint: 'Один показатель по нескольким годам рядом', ready: true },
  { type: 'carry_gaps', label: 'Расхождения переноса сальдо', hint: 'Где конечное сальдо года не равно начальному следующего', ready: true },
]

export const typeLabel = (t: string) => REPORT_TYPES.find((x) => x.type === t)?.label ?? t

const pad = (n: number) => String(n).padStart(2, '0')
export const iso = (y: number, m: number, d: number) => `${y}-${pad(m)}-${pad(d)}`
export const lastDay = (y: number, m: number) => new Date(y, m, 0).getDate()

/** Валюта по умолчанию — местная валюта текущего набора (заполняется из параметров набора, см. dataset.tsx). */
export const currencyCtl = { local: '' }

export function defaultDefinition(type: ReportType, title?: string, currency?: string): Definition {
  const y = new Date().getFullYear()
  const m = new Date().getMonth() + 1
  const d = baseDefinition(type, title, y, m, currency ?? currencyCtl.local)
  switch (type) {
    case 'card':
      d.card = { detail: 'entries' }
      d.rows = []
      break
    case 'analysis':
      d.analysis = { corr: { kind: 'account', level: 1 } }
      d.columns.by_month = true
      d.rows = []
      break
    case 'ledger':
      d.rows = []
      break
    case 'chess':
      d.chess = { rows: { kind: 'account', level: 0 }, cols: { kind: 'account', level: 0 } }
      d.rows = []
      break
    case 'journal':
      d.journal = { side: 'credit', rows: 'day' }
      d.rows = []
      break
    case 'form':
      d.form = {
        lines: [
          { code: '010', name: 'Денежные средства', formula: 'SKD(100*)+SKD(102*)', bold: false, indent: 1 },
          { code: '020', name: 'Итого', formula: '[010]', bold: true, indent: 0 },
        ],
        template_id: null,
        columns: ['begin', 'end'],
        checks: [],
        coverage: [],
      }
      d.rows = []
      break
    case 'compare_years':
      d.years = [y - 1, y]
      d.target_year = y
      d.measures = ['turn_dt', 'turn_kt', 'close']
      d.use_links = true
      d.mmdd_from = '01-01'
      d.mmdd_to = '12-31'
      break
  }
  return d
}

function baseDefinition(type: ReportType, title: string | undefined, y: number, m: number, currency: string): Definition {
  return {
    type,
    title: title ?? typeLabel(type),
    period: { date_from: iso(y, 1, 1), date_to: iso(y, m, lastDay(y, m)) },
    currency,
    scale: 1,
    denominate: true,
    include_closing: true,
    filters: { accounts: [], groups: [], centers: [], trcodes: [], text: '' },
    rows: [{ kind: 'account', max_level: 2 }],
    columns: { balance: 'expanded', by_month: false, show_usd: false },
    options: { hide_zero: true, sort: { by: 'code', dir: 'asc' }, unmapped: true },
  }
}

export const download = (name: string, text: string, mime = 'text/plain') => {
  const url = URL.createObjectURL(new Blob([text], { type: mime }))
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 3000)
}

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)

/**
 * Привести определение отчёта (из базы, из шаблона, из импорта) к полному виду: недостающие разделы
 * (период, фильтры, колонки, параметры, строки, разделы вида отчёта) берутся по умолчанию для вида отчёта.
 * Примеры и старые отчёты могут не содержать часть разделов — экран не должен из-за этого падать.
 */
export function normalizeDefinition(raw: unknown, currency?: string): Definition {
  const r: Record<string, unknown> = isObj(raw) ? raw : {}
  const known = REPORT_TYPES.some((t) => t.type === r.type)
  const type = (known ? r.type : 'osv') as ReportType
  const base = defaultDefinition(type, typeof r.title === 'string' ? r.title : undefined, currency)
  const out: Record<string, unknown> = { ...(base as Record<string, unknown>) }
  for (const [k, v] of Object.entries(r)) if (v !== null && v !== undefined) out[k] = v
  // разделы с вложенными значениями по умолчанию
  for (const k of ['period', 'filters', 'columns', 'options', 'card', 'analysis', 'ledger', 'chess', 'journal'] as const) {
    const b = (base as Record<string, unknown>)[k]
    const v = r[k]
    if (isObj(b) || isObj(v)) out[k] = { ...(isObj(b) ? b : {}), ...(isObj(v) ? v : {}) }
  }
  const o = out.options as Record<string, unknown>
  if (!isObj(o.sort)) o.sort = { by: 'code', dir: 'asc' }
  const f = out.filters as Record<string, unknown>
  for (const k of ['accounts', 'groups', 'centers', 'trcodes']) if (!Array.isArray(f[k])) f[k] = []
  if (typeof f.text !== 'string') f.text = ''
  if (!Array.isArray(out.rows)) out.rows = (base as Record<string, unknown>).rows ?? []
  if (typeof out.title !== 'string' || !out.title) out.title = typeLabel(type)
  if (!out.currency) out.currency = currency ?? currencyCtl.local
  if (type === 'form') {
    const fm = isObj(r.form) ? r.form : {}
    const bf = (base as Record<string, unknown>).form as Record<string, unknown>
    out.form = {
      ...bf,
      ...fm,
      lines: Array.isArray(fm.lines) ? fm.lines : [],
      columns: Array.isArray(fm.columns) ? fm.columns : bf.columns,
      checks: Array.isArray(fm.checks) ? fm.checks : [],
      coverage: Array.isArray(fm.coverage) ? fm.coverage : [],
      template_id: fm.template_id ?? null,
    }
  }
  if (type === 'compare_years') {
    if (!Array.isArray(out.years)) out.years = (base as Record<string, unknown>).years
    if (!Array.isArray(out.measures)) out.measures = (base as Record<string, unknown>).measures
  }
  return out as unknown as Definition
}

/** Контрольные равенства формы без названия сервер не считает: подставляем «Контроль N». */
export function fillCheckNames(def: Definition): Definition {
  const checks = def.form?.checks
  if (!def.form || !checks?.some((c) => !c.name?.trim())) return def
  return { ...def, form: { ...def.form, checks: checks.map((c, i) => (c.name?.trim() ? c : { ...c, name: `Контроль ${i + 1}` })) } }
}
