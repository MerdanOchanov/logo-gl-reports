// Типизированный клиент API (контракт: docs/ARCHITECTURE.md §4–§5).

export type ReportType = 'osv' | 'card' | 'ledger' | 'analysis' | 'chess' | 'journal' | 'form' | 'compare_years' | 'carry_gaps'

export interface RowSpec {
  kind: 'classifier' | 'account'
  id?: number // классификатор
  depth?: number // глубина классификатора
  max_level?: number // уровень счёта
}

export interface Axis {
  kind: 'account' | 'classifier'
  level?: number
  id?: number
  depth?: number
}

export interface FormLine {
  code: string
  name: string
  formula: string
  cell?: string
  bold?: boolean
  indent?: number
}

export interface FormDef {
  lines: FormLine[]
  template_id: number | null
  columns: string[]
  checks: { name: string; left: string; right: string }[]
  coverage?: string[]
}

export interface Definition {
  type: ReportType
  title: string
  period: { date_from: string; date_to: string }
  /** код валюты отчёта: местная или отчётная валюта набора */
  currency: string
  scale: 1 | 1000 | 1000000
  denominate: boolean
  include_closing: boolean
  filters: {
    accounts: string[]
    groups: { classifier: number; group: number }[]
    centers: string[]
    trcodes: number[]
    text: string
    /** Отбор по признакам проводок и ордеров (пусто или нет поля — без отбора) */
    branches?: number[]
    departments?: number[]
    sources?: number[]
    authors?: number[]
  }
  rows: RowSpec[]
  columns: { balance: 'net' | 'expanded'; by_month: boolean; show_usd: boolean; by_branch?: boolean; qty?: boolean; extra?: string[] }
  options: { hide_zero: boolean; sort: { by: string; dir: 'asc' | 'desc' }; unmapped: boolean; max_graphs?: number }
  account?: string
  card?: { detail: 'entries' | 'day' | 'month'; lines?: boolean }
  analysis?: { corr: Axis }
  ledger?: { corr?: Axis }
  chess?: { rows: Axis; cols: Axis }
  journal?: {
    side: 'credit' | 'debit'
    rows: 'day' | 'fiche'
    corr?: Axis
    fixed_cols?: { sel: string; title?: string }[]
    rest?: 'other' | 'auto' | 'hide'
    with_balance?: boolean
  }
  form?: FormDef
  // сравнение лет (type=compare_years): поля верхнего уровня
  years?: number[]
  target_year?: number
  measures?: string[]
  use_links?: boolean
  mmdd_from?: string
  mmdd_to?: string
  delta?: boolean
  [k: string]: unknown
}

export interface Column {
  key: string
  title: string
  group?: string
  type: 'text' | 'money' | 'int' | 'qty'
  drill?: 'dt' | 'kt' | null
  /** Дополнительная колонка: показывается по выбору пользователя */
  optional?: boolean
  /** Графа подразделения в ОСВ: расшифровка передаёт отбор по этому подразделению */
  branch?: number
  period?: { date_from: string; date_to: string } | null
  name?: string
  corr?: string[]
  corr_groups?: unknown[]
  corr_exclude?: string[]
}

export interface DrillSpec {
  groups?: unknown[]
  corr_groups?: unknown[]
  accounts?: string[]
  group?: { classifier: number; group: number } | number | string
  side?: 'dt' | 'kt'
  corr?: string[]
  corr_exclude?: string[]
  period?: { date_from: string; date_to: string }
}

export interface ResultRow {
  id: string
  parent: string | null
  level: number
  kind: 'group' | 'account' | 'total' | 'line' | 'unmapped' | string
  code?: string
  name?: string
  values: Record<string, number | string | null>
  bold?: boolean
  drill?: DrillSpec
  fiche?: { firm: number; fiche_id: number }
  calc?: string[]
}

export interface Check {
  name: string
  ok: boolean
  /** ok — норма, warn — замечание (не ошибка отчёта), error — расхождение. Если сервер не прислал — по полю ok. */
  level?: 'ok' | 'warn' | 'error'
  detail?: string
}

export interface Result {
  title: string
  subtitle?: string
  columns: Column[]
  rows: ResultRow[]
  totals?: Record<string, number | string | null>
  checks?: Check[]
  warnings?: string[]
}

export interface Status {
  logo_ok: boolean
  firms_synced: number
  firms_total: number
  syncing: boolean
  queued?: boolean
  progress?: number | { done?: number; total?: number; firm?: number; text?: string; message?: string } | null
  dataset?: { id: number; name: string; state?: string }
}

export interface Firm {
  firm: number
  year: number
  name: string
  enabled: boolean | number
  synced_at?: string | null
  line_count?: number
}

export interface YearBook {
  firm: number
  name: string
  synced_at?: string | null
  line_count?: number
}

export interface YearPart {
  book_id: number
  firm: number
  date_from: string
  date_to: string
}

export interface YearInfo {
  book_id?: number
  date_from?: string
  date_to?: string
  parts?: YearPart[]
  year: number
  firm: number
  name: string
  enabled: boolean | number
  synced_at?: string | null
  line_count?: number
  divisor?: number
  default_firm: number
  alternatives: YearBook[]
}

export interface Account {
  code: string
  name: string
  level: number
  parent: string | null
  is_leaf: boolean | number
  specode?: string | null
  cyphcode?: string | null
  units?: string
  is_currency?: boolean
  full_name?: string | null
  note?: string | null
  created?: string | null
  created_by?: string | null
  inactive?: boolean
  specode_name?: string | null
}

export interface ClassifierInfo {
  id: number
  name: string
  description?: string
  groups_count: number
  rules_count: number
}

export interface ClsGroup {
  id: number
  parent_id: number | null
  code: string
  name: string
  sort: number
}

export type RuleKind = 'mask' | 'range' | 'list' | 'specode'

export interface ClsRule {
  id: number
  group_id: number
  kind: RuleKind
  value: string
  year_from: number | null
  year_to: number | null
  priority: number
}

export interface Classifier {
  id: number
  name: string
  description?: string
  groups: ClsGroup[]
  rules: ClsRule[]
}

export interface ResolveResult {
  mapped: { account: string; name: string; group_id: number }[]
  unmapped: { account: string; name: string; turnover: number }[]
  conflicts: { account: string; name?: string; groups: (number | { id: number; code?: string; name?: string })[] }[]
}

export interface ReportInfo {
  id: number
  name: string
  type: ReportType
  folder: string | null
  updated_at: string
}

export interface ReportFull extends ReportInfo {
  definition: Definition
}

export interface ReportVersion {
  id: number
  report_id?: number
  saved_at: string
  comment?: string | null
  definition?: Definition
}

export interface DrillLine {
  firm: number
  fiche_id: number
  fiche_no: string
  date: string
  account: string
  account_name?: string
  corr?: string
  dt: number
  kt: number
  text?: string
  method?: string
  branch?: string
  department?: string
  qty?: number | null
  source?: string
  author?: string
  entered_at?: string
  doc_no?: string
  text2_4?: string
  branch_id?: number
  department_id?: number
  source_id?: number
  author_id?: number
}

export interface DrillResult {
  total: number
  sum_dt: number
  sum_kt: number
  lines: DrillLine[]
}

export interface DrillRequest {
  period: { date_from: string; date_to: string }
  accounts?: string[]
  group?: unknown
  groups?: unknown[]
  side?: 'dt' | 'kt'
  corr?: string[]
  corr_exclude?: string[]
  corr_groups?: unknown[]
  include_closing: boolean
  branches?: number[]
  departments?: number[]
  sources?: number[]
  authors?: number[]
  /** turn — обороты (по умолчанию), open/close — проводки, формирующие сальдо на начало/конец */
  mode?: 'turn' | 'open' | 'close'
  limit: number
  offset: number
}

export interface Fiche {
  fiche_no: string
  date: string
  trcode?: number
  text?: string
  source?: string
  author?: string
  entered_at?: string
  doc_no?: string
  text2_4?: string
  source_id?: number
  author_id?: number
  lines: { line_no?: number; account: string; account_name?: string; dt: number; kt: number; text?: string; center?: string; branch?: string; department?: string; qty?: number | null }[]
  pairs: { dt_account: string; kt_account: string; amount: number; method?: string; disputed?: number | boolean }[]
}

export interface Dicts {
  year: number
  firm?: number
  branches: { id: number; name: string; lines: number }[]
  departments: { id: number; name: string; lines: number }[]
  sources: { id: number; name: string; fiches: number; confirmed: boolean }[]
  authors: { id: number; name: string; fiches: number }[]
  warnings?: string[]
}

export interface ClosingRule {
  id?: number
  kind: 'text' | 'accounts'
  value: string
  enabled: boolean | number
}

export interface AccountName {
  code: string
  year_from: number | null
  year_to: number | null
  name: string
}

export interface CorrStats {
  methods?: Record<string, number | { amount?: number; share?: number; count?: number }>
  years?: { year: number; methods?: Record<string, number | { amount?: number; share?: number }>; disputed?: number; total?: number }[]
  disputed?: number
  total?: number
  note?: string
}

export interface DisputedFiche {
  firm: number
  year?: number
  fiche_id: number
  fiche_no: string
  date: string
  text?: string
  amount?: number
}

export interface TemplateInfo {
  id: number
  name: string
  filename: string
  uploaded_at?: string
}

export interface AccountLink {
  id?: number
  year_from: number
  code_from: string
  year_to: number
  code_to: string
  method?: string
  confidence?: number
  note?: string
}

export interface AutoItem {
  code_from: string
  name_from?: string
  code_to?: string | null
  name_to?: string | null
  turnover?: number
  confidence?: number
  method?: string
  status?: string
  reason?: string
  candidates?: { code: string; name: string; score: number }[]
}

export interface AutoAgg {
  total: number
  matched: number
  disputed: number
  unmatched: number
  matched_turnover_share?: number | null
  disputed_turnover_share?: number | null
  unmatched_turnover_share?: number | null
}

export interface AutoReport {
  summary: { year_from: number; year_to: number; accounts_from: number; accounts_to: number; all: AutoAgg; leaves: AutoAgg; leaves_with_turnover: AutoAgg }
  disputed: AutoItem[]
  unmatched: AutoItem[]
  matched_sample: AutoItem[]
}

export interface MeDataset {
  id: number
  name: string
  role?: string
  state?: string
  is_default?: boolean
}

export interface Me {
  login: string
  role: string
  auth?: boolean
  global_admin?: boolean
  datasets?: MeDataset[]
  dataset?: { id: number; name: string }
}

export interface Connection {
  id: number
  name: string
  server: string
  database: string
  login: string
  has_password: boolean
  driver: string
  trust_cert: boolean
  timeout_sec: number
  origin: 'config' | 'ui'
  last_ok_at?: string | null
  last_error?: string | null
  env_fields: string[]
  password_plain?: boolean
  warnings: string[]
}

export interface ConnectionInput {
  name: string
  server: string
  database: string
  login: string
  password?: string | null
  driver?: string
  trust_cert?: boolean
  timeout_sec?: number
}

export interface ConnTest {
  ok: boolean
  server_version?: string
  database?: string
  driver?: string
  logo?: { firms: number; periods: number; tables_firm_period?: number }
  collation?: string
  ansi_cp?: string
  warnings?: string[]
}

export interface CurrencyLocal {
  code: string
  name: string
  symbol?: string
  gen_pl?: string
  prep_pl?: string
}
export interface CurrencyReport {
  code: string
  name: string
  symbol?: string
  enabled: boolean
}
export interface PlanProfile {
  template: 'turkish' | 'none' | string
  mode: 'sep' | 'width' | string
  sep?: string
  root_len?: number
  widths?: number[]
  balance_roots?: string[]
  pnl_roots?: string[]
  noncarried?: string[]
}
export interface CarryNote {
  from_label?: number | null
  to_label?: number | null
  from_firm?: number | null
  to_firm?: number | null
  text: string
  denom_suffix?: string
}
export interface DatasetParams {
  currency_local?: CurrencyLocal
  currency_report?: CurrencyReport
  charset?: { ansi: string; oem: string }
  denomination?: { before: string; divisor: number }[]
  plan_profile?: PlanProfile
  carry_notes?: CarryNote[]
  sync?: { ttl_current: number; ttl_old: number; old_after_days: number }
}

export interface DatasetInfo {
  id: number
  name: string
  org_name: string
  is_default: boolean
  archived: boolean
  state: 'new' | 'ready' | string
  role?: string
  books_main: number
  books_total: number
  last_sync_at?: string | null
  logo_ok: boolean
  syncing?: boolean
  queued?: boolean
  progress?: { done?: number; total?: number; message?: string } | null
  connection?: { id: number; name: string; server: string; database: string } | null
  params?: DatasetParams
  books_count?: number
  cache_bytes?: number
  configured?: boolean
}

export type BookRole = 'main' | 'alt' | 'off'

export interface Book {
  id?: number
  slot?: number
  label: number
  part: number
  src?: number
  src_firm: number
  src_period: number
  date_from: string
  date_to: string
  role: BookRole
  name?: string
  divisor: number
  oem: boolean
  reason?: string
  note?: string
  firm_name?: string
  synced_at?: string | null
  line_count?: number
  rows_outside?: number
  carry_next?: unknown
}

export interface BooksResp {
  books: Book[]
  coverage?: { date_from: string | null; date_to: string | null; gaps: unknown[]; labels: number[] }
  state?: string
  sync_needed?: number[]
  warnings?: string[]
}

export interface ScanState {
  state: 'idle' | 'running' | 'done' | 'error'
  done: number
  total: number
  current?: string | null
  message?: string
  scanned_at?: string | null
  error?: string | null
}

/** Статистика источника (фирма и период Logo) по результатам чтения. */
export interface SourceStat {
  src: number
  firm: number
  period: number
  firm_name?: string
  title?: string
  begdate?: string | null
  enddate?: string | null
  lines?: number
  open_lines?: number
  turn_lines?: number
  fiches?: number
  date_min?: string | null
  date_max?: string | null
  turn_date_min?: string | null
  turn_date_max?: string | null
  turn_dt?: number
  turn_kt?: number
  open_dt?: number
  open_kt?: number
  has_open?: boolean
  oem_rows?: number
}

export interface ProposalBook extends SourceStat {
  slot: number
  label: number
  part: number
  date_from: string
  date_to: string
  role: BookRole
  divisor: number
  oem: boolean
  oem_note?: string
  name: string
  confidence?: string
  reason: string
  warnings: string[]
}

export interface ProposalSource extends SourceStat {
  in_logo?: boolean
  has_tables?: boolean
  has_plan?: boolean
  assigned?: boolean
  can_be_book?: boolean
  cannot_reason?: string
  reasons?: string[]
}

export interface ParamSuggestion {
  value: unknown
  source?: string
  configured?: boolean
  current?: unknown
  differs?: boolean
}

export interface Proposal {
  scanned_at: string
  sources_total: number
  sources_in_logo: number
  params: DatasetParams
  params_suggested?: Record<string, ParamSuggestion>
  books: ProposalBook[]
  off: (SourceStat & { reasons: string[] })[]
  gaps_between_labels: { after: number; before: number; days: number }[]
  unassigned_data: (SourceStat & { date_from?: string; date_to?: string; reasons: string[] })[]
  sources?: ProposalSource[]
}

export interface ValidateResp {
  ok: boolean
  errors: string[]
  warnings: string[]
}

export interface UserInfo {
  id: number
  login: string
  role: string
  datasets?: { id: number; role: string }[]
}

export interface PlanDetect {
  profile: PlanProfile
  separator?: string
  root_len?: number
  mode?: string
  template?: string
  checked?: { accounts: number; books: number; sources: number[] }
  discrepancies?: string[]
  current?: PlanProfile
  differs?: boolean
}

export interface ImportReport {
  created: Record<string, number>
  updated: Record<string, number>
  skipped: Record<string, number>
  warnings: string[]
  resync_required?: boolean
  plan_rebuild?: boolean
}

export interface DatasetCreate {
  name: string
  connection_id: number
  org_name?: string
  plan_template: 'turkish' | 'none'
  copy_from?: number
  copy_parts?: string[]
}

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

const GENERIC: Record<number, string> = {
  400: 'Сервер не принял запрос: проверьте параметры отчёта.',
  401: 'Требуется вход в систему.',
  403: 'Недостаточно прав для этого действия (у роли «просмотр» изменение данных недоступно).',
  404: 'Запрошенные данные не найдены (возможно, их уже удалили).',
  405: 'Это действие сервером не поддерживается.',
  409: 'Действие сейчас выполнить нельзя: сервер занят другой операцией. Повторите чуть позже.',
  413: 'Файл слишком большой.',
  422: 'Некорректные параметры запроса. Проверьте заполнение полей.',
  500: 'На сервере произошла внутренняя ошибка. Повторите действие; если ошибка повторяется — сообщите администратору.',
  501: 'Этот вид отчёта сервер пока не поддерживает.',
  502: 'Сервер временно недоступен. Повторите чуть позже.',
  503: 'Сервер временно недоступен. Повторите чуть позже.',
  504: 'Сервер не успел ответить. Сузьте период или повторите позже.',
}

/** Текст похож на технический (трассировка, исключение, SQL, англоязычная служебная фраза). */
function looksTechnical(m: string): boolean {
  if (/Traceback|File ".*", line|at \w+\.\w+|Exception|Error:|pyodbc|sqlite3|SQL|None|KeyError|TypeError|ValueError|<html|<!DOCTYPE|\{.*\}/i.test(m)) return true
  // сообщение без единой русской буквы — служебное
  return !/[А-Яа-яЁё]/.test(m)
}

/** Ответ сервера -> понятное русское сообщение (без трассировок и технических деталей). */
async function errText(resp: Response, trusted = false): Promise<string> {
  let detail = ''
  try {
    const j = await resp.json()
    if (j && typeof j.detail === 'string') detail = j.detail
    else if (j && Array.isArray(j.detail)) {
      const fields = j.detail.map((d: { loc?: unknown[] }) => (d.loc ? d.loc.filter((x) => typeof x === 'string' && x !== 'body').join('.') : '')).filter(Boolean)
      detail = 'Некорректные параметры' + (fields.length ? ': ' + [...new Set(fields)].join(', ') : '') + '.'
    }
  } catch {
    /* не JSON */
  }
  const generic = GENERIC[resp.status] ?? `Ошибка сервера (код ${resp.status}).`
  if (!detail) return generic
  if (resp.status === 403) detail = detail.replace(/«viewer»/g, '«просмотр»').replace(/«editor»/g, '«редактор»').replace(/«admin»/g, '«администратор»')
  if (resp.status === 403 && /^Нет доступа к набору данных/.test(detail)) detail = detail.replace(/\.$/, '') + '. Выберите другой набор вверху справа или попросите администратора дать вам доступ.'
  // «Не удалось открыть файл: Error -3 while …» — отбрасываем англоязычный технический хвост
  detail = detail.replace(/:\s+(?![^]*[А-Яа-яЁё])[^]*[A-Za-z]{4,}[^]*$/, '.').replace(/\.\.$/, '.')
  // технические пометки в скобках без русских слов — «(date_to)», «(years: [2024, 2025])»
  detail = detail.replace(/\s*\([^()А-Яа-яЁё]*[A-Za-z_][^()А-Яа-яЁё]*\)/g, '')
  if (resp.status >= 500 || (!trusted && looksTechnical(detail))) return generic
  if (resp.status === 501) return generic + ' ' + detail
  return detail
}

/** Вызывается при ответе 401 (кроме /me и /login) — приложение показывает экран входа. */
export const authHooks = { onUnauthorized: () => {}, onDatasetLost: () => {} }

const DS_KEY = 'buhlogo.dataset'

function readStoredDataset(): number | null {
  try {
    const v = Number(window.localStorage.getItem(DS_KEY))
    return Number.isInteger(v) && v > 0 ? v : null
  } catch {
    return null
  }
}

/** Выбранный набор данных: хранится в браузере и уходит заголовком X-Dataset во все запросы. Не задан — набор по умолчанию. */
export const datasetCtl: { id: number | null } = { id: readStoredDataset() }

export function setSelectedDataset(id: number | null) {
  datasetCtl.id = id
  try {
    if (id == null) window.localStorage.removeItem(DS_KEY)
    else window.localStorage.setItem(DS_KEY, String(id))
  } catch {
    /* хранилище браузера недоступно — выбор действует до закрытия вкладки */
  }
}

/** Единственное место, где формируются заголовки всех запросов к серверу (сюда же добавляется X-Dataset). */
function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers: Record<string, string> = { ...((init.headers as Record<string, string>) ?? {}) }
  if (datasetCtl.id != null) headers['X-Dataset'] = String(datasetCtl.id)
  return fetch('/api' + path, { ...init, headers })
}

/** Ответы этих разделов — тексты сервера, написанные для человека: не заменять общими фразами. */
const TRUSTED = /^\/(connections|books|dataset|users)/

async function realRequest<T>(method: string, path: string, body?: unknown): Promise<T> {
  let resp: Response
  try {
    resp = await apiFetch(path, {
      method,
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw new ApiError('Сервер не отвечает. Проверьте, что он запущен.', 0)
  }
  if (!resp.ok) {
    if (resp.status === 401 && path !== '/me' && path !== '/login') authHooks.onUnauthorized()
    const text = await errText(resp, TRUSTED.test(path))
    // выбранный ранее набор исчез, закрыт или в архиве — молча возвращаемся к набору по умолчанию
    if (datasetCtl.id != null && !/^\/(me|login|logout|users|connections)/.test(path) && /набор/i.test(text) && (resp.status === 404 || resp.status === 403 || (resp.status === 409 && /в архиве/.test(text)))) {
      setSelectedDataset(null)
      authHooks.onDatasetLost()
    }
    throw new ApiError(text, resp.status)
  }
  if (resp.status === 204) return undefined as T
  return (await resp.json()) as T
}

const request = realRequest

const qs = (o: Record<string, string | number | undefined>) => {
  const p = Object.entries(o)
    .filter(([, v]) => v !== undefined && v !== '')
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`)
  return p.length ? '?' + p.join('&') : ''
}

export const api = {
  status: () => request<Status>('GET', '/status'),
  firms: () => request<Firm[]>('GET', '/firms'),
  setFirm: (firm: number, enabled: boolean) => request<unknown>('PUT', `/firms/${firm}`, { enabled }),
  sync: (firms?: number[], force?: boolean) => request<unknown>('POST', '/sync', { firms, force }),
  years: () => request<YearInfo[]>('GET', '/years'),
  setYearBook: (year: number, firm: number) =>
    request<{ year: number; firm: number; previous: number; changed: boolean; sync_started: boolean }>('PUT', `/years/${year}/book`, { firm }),
  accounts: (year: number) => request<Account[]>('GET', '/accounts' + qs({ year })),

  classifiers: () => request<ClassifierInfo[]>('GET', '/classifiers'),
  createClassifier: (name: string, description = '') =>
    request<{ id: number } & Partial<Classifier>>('POST', '/classifiers', { name, description }),
  classifier: (id: number) => request<Classifier>('GET', `/classifiers/${id}`),
  saveClassifier: (c: Classifier) => request<unknown>('PUT', `/classifiers/${c.id}`, c),
  deleteClassifier: (id: number) => request<unknown>('DELETE', `/classifiers/${id}`),
  importSpecode: (year: number, name: string) =>
    request<{ id: number }>('POST', '/classifiers/import-specode', { year, name }),
  copyClassifier: (id: number) => request<{ id: number }>('POST', `/classifiers/${id}/copy`),
  resolve: (id: number, year: number) => request<ResolveResult>('GET', `/classifiers/${id}/resolve` + qs({ year })),

  reports: () => request<ReportInfo[]>('GET', '/reports'),
  report: (id: number) => request<ReportFull>('GET', `/reports/${id}`),
  createReport: (r: { name: string; type: ReportType; folder: string | null; definition: Definition; comment?: string }) =>
    request<{ id: number }>('POST', '/reports', r),
  saveReport: (id: number, r: { name: string; type: ReportType; folder: string | null; definition: Definition; comment?: string }) =>
    request<unknown>('PUT', `/reports/${id}`, r),
  deleteReport: (id: number) => request<unknown>('DELETE', `/reports/${id}`),
  versions: (id: number) => request<ReportVersion[]>('GET', `/reports/${id}/versions`),
  copyReport: (id: number) => request<{ id: number }>('POST', `/reports/${id}/copy`),

  run: (definition: Definition) => request<Result>('POST', '/run', { definition }),
  drill: (r: DrillRequest) => request<DrillResult>('POST', '/drill', r),
  dicts: (year: number) => request<Dicts>('GET', '/dicts' + qs({ year })),
  fiche: (firm: number, id: number) => request<Fiche>('GET', '/fiche' + qs({ firm, id })),

  corrStats: (year?: number) => request<CorrStats>('GET', '/corr/stats' + qs({ year })),
  corrDisputed: (year: number) => request<DisputedFiche[]>('GET', '/corr/disputed' + qs({ year })),
  saveOverride: (o: { firm: number; fiche_id: number; pairs: { dt_account: string; kt_account: string; amount: number }[] }) =>
    request<unknown>('PUT', '/corr/override', o),
  deleteOverride: (firm: number, fiche_id: number) => request<unknown>('DELETE', '/corr/override' + qs({ firm, fiche_id })),

  validateFormula: (formula: string, codes: string[]) =>
    request<{ ok?: boolean; errors?: { message: string; pos?: number | null }[] }>('POST', '/forms/validate', { formula, codes }),
  templates: () => request<TemplateInfo[]>('GET', '/templates'),
  deleteTemplate: (id: number) => request<unknown>('DELETE', `/templates/${id}`),
  uploadTemplate: async (name: string, file: File): Promise<TemplateInfo> => {
    const fd = new FormData()
    fd.append('name', name)
    fd.append('file', file)
    let resp: Response
    try {
      resp = await apiFetch('/templates', { method: 'POST', body: fd })
    } catch {
      throw new ApiError('Сервер не отвечает.', 0)
    }
    if (!resp.ok) throw new ApiError(await errText(resp), resp.status)
    return resp.json()
  },
  /** Связи счетов: сервер может отдать массив или объект {total, items} — принимаем оба вида. */
  accountLinks: async (p: { year_from?: number; year_to?: number; q?: string; limit?: number }): Promise<{ total: number; items: AccountLink[] }> => {
    const r = await request<AccountLink[] | { total?: number; items?: AccountLink[] } | null>('GET', '/account-links' + qs(p))
    if (Array.isArray(r)) return { total: r.length, items: r }
    const items = Array.isArray(r?.items) ? r!.items! : []
    return { total: typeof r?.total === 'number' ? r.total : items.length, items }
  },
  createLinks: (items: Partial<AccountLink>[]) => request<AccountLink[]>('POST', '/account-links', items),
  updateLink: (id: number, l: Partial<AccountLink>) => request<AccountLink>('PUT', `/account-links/${id}`, l),
  deleteLink: (id: number) => request<unknown>('DELETE', `/account-links/${id}`),
  autoLinks: (year_from: number, year_to: number) => request<AutoReport>('POST', '/account-links/auto', { year_from, year_to }),
  applyAutoLinks: (year_from: number, year_to: number) =>
    request<AutoReport & { applied: number }>('POST', '/account-links/auto/apply', { year_from, year_to }),

  dataset: () => request<DatasetInfo>('GET', '/dataset'),
  datasets: () => request<DatasetInfo[]>('GET', '/datasets'),
  saveDataset: (id: number, p: { name?: string; org_name?: string; connection_id?: number; is_default?: boolean; archived?: boolean; note?: string }) =>
    request<DatasetInfo>('PUT', `/datasets/${id}`, p),
  createDataset: (p: DatasetCreate) => request<DatasetInfo>('POST', '/datasets', p),
  deleteDataset: (id: number, confirm: string) => request<{ ok: boolean }>('DELETE', `/datasets/${id}` + qs({ confirm })),
  detectPlanProfile: () => request<PlanDetect>('POST', '/dataset/plan-profile/detect', {}),
  exportSettings: async (parts: string[]): Promise<{ blob: Blob; filename: string }> => {
    let resp: Response
    try {
      resp = await apiFetch('/dataset/export' + qs({ parts: parts.join(',') }))
    } catch {
      throw new ApiError('Сервер не отвечает.', 0)
    }
    if (!resp.ok) throw new ApiError(await errText(resp, true), resp.status)
    const cd = resp.headers.get('Content-Disposition') ?? ''
    const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd)
    return { blob: await resp.blob(), filename: m ? decodeURIComponent(m[1]) : 'buhlogo-settings-v1.json' }
  },
  importSettings: async (file: File, mode: 'merge' | 'replace', parts: string[]): Promise<ImportReport> => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('mode', mode)
    if (parts.length) fd.append('parts', parts.join(','))
    let resp: Response
    try {
      resp = await apiFetch('/dataset/import', { method: 'POST', body: fd })
    } catch {
      throw new ApiError('Сервер не отвечает.', 0)
    }
    if (!resp.ok) throw new ApiError(await errText(resp, true), resp.status)
    return resp.json()
  },
  saveParams: (p: DatasetParams) => request<{ resync_required: boolean; plan_rebuild: boolean; params: DatasetParams }>('PUT', '/dataset/params', p),

  connections: () => request<Connection[]>('GET', '/connections'),
  createConnection: (c: ConnectionInput) => request<Connection>('POST', '/connections', c),
  saveConnection: (id: number, c: ConnectionInput) => request<Connection>('PUT', `/connections/${id}`, c),
  testConnection: (c: { id?: number } & Partial<ConnectionInput>) => request<ConnTest>('POST', '/connections/test', c),
  connectionDatabases: (c: { id?: number; server: string; login: string; password?: string | null; driver?: string }) =>
    request<string[]>('POST', '/connections/databases', c),
  connectionDrivers: () => request<{ drivers: string[]; auto: string }>('GET', '/connections/drivers'),
  deleteConnection: (id: number) => request<unknown>('DELETE', `/connections/${id}`),

  books: () => request<BooksResp>('GET', '/books'),
  scanState: () => request<ScanState>('GET', '/books/scan'),
  startScan: () => request<{ started: boolean }>('POST', '/books/scan', {}),
  proposal: () => request<Proposal>('GET', '/books/proposal'),
  validateBooks: (books: Book[]) => request<ValidateResp>('POST', '/books/validate', { books }),
  saveBooks: (books: Book[]) => request<BooksResp>('PUT', '/books', { books }),
  applyProposal: (include_params: boolean, overwrite?: string[]) => request<BooksResp>('POST', '/books/apply-proposal', { include_params, overwrite }),
  makeMain: (id: number) => request<{ changed: boolean; sync_started: boolean }>('POST', `/books/${id}/make-main`),

  me: () => request<Me>('GET', '/me'),
  login: (login: string, password: string) => request<Me>('POST', '/login', { login, password }),
  logout: () => request<unknown>('POST', '/logout'),
  users: () => request<UserInfo[]>('GET', '/users'),
  createUser: (u: { login: string; password: string; role: string; datasets?: { dataset_id: number; role: string }[] }) => request<unknown>('POST', '/users', u),
  setUserDatasets: (id: number, d: { dataset_id: number; role: string }[]) => request<unknown>('PUT', `/users/${id}/datasets`, d),
  updateUser: (id: number, u: { login?: string; password?: string; role?: string }) => request<unknown>('PUT', `/users/${id}`, u),
  deleteUser: (id: number) => request<unknown>('DELETE', `/users/${id}`),
  demoSeed: () => request<{ created?: string[]; skipped?: string[] }>('POST', '/demo/seed'),

  closingRules: () => request<ClosingRule[]>('GET', '/closing-rules'),
  saveClosingRules: (r: ClosingRule[]) => request<unknown>('PUT', '/closing-rules', r),
  accountNames: () => request<AccountName[]>('GET', '/account-names'),
  saveAccountNames: (r: AccountName[]) => request<unknown>('PUT', '/account-names', r),

  /** Расшифровка в Excel: тело как у drill + выбранные дополнительные колонки. */
  exportDrill: async (r: Omit<DrillRequest, 'limit' | 'offset'> & { extra: string[]; limit?: number; title?: string }): Promise<Blob> => {
    let resp: Response
    try {
      resp = await apiFetch('/export/drill', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...r, limit: r.limit ?? 50000, offset: 0 }),
      })
    } catch {
      throw new ApiError('Сервер не отвечает.', 0)
    }
    if (!resp.ok) throw new ApiError(await errText(resp), resp.status)
    return resp.blob()
  },

  /** Скачивание xlsx: возвращает blob, сохранение — в downloadXlsx. */
  exportXlsx: async (definition: Definition, title?: string): Promise<Blob> => {
    let resp: Response
    try {
      resp = await apiFetch('/export/xlsx', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(title ? { definition, title } : { definition }),
      })
    } catch {
      throw new ApiError('Сервер не отвечает.', 0)
    }
    if (!resp.ok) {
      throw new ApiError(await errText(resp), resp.status)
    }
    return resp.blob()
  },
}

export async function downloadXlsx(definition: Definition, filename: string, title?: string) {
  saveBlob(await api.exportXlsx(definition, title), filename)
}

export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename.replace(/[\\/:*?"<>|]+/g, '_') + '.xlsx'
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 5000)
}

/** Сохранить файл на компьютер (имя — как есть, с расширением). */
export function saveFile(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 5000)
}

export const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e))
