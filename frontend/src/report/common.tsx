import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, type Account, type ClassifierInfo, type Definition, type Dicts, type Result } from '../api'
import AccountTree from '../components/AccountTree'
import { ErrorBox, Modal, useLoad } from '../components/ui'
import { iso, lastDay } from '../format'
import { currencyOptions, useDataset } from '../dataset'
import { labelOfDate, useYearInfos, useYears, yearRange } from '../years'

export type SetDef = (f: (d: Definition) => Definition) => void
export interface BlockProps {
  def: Definition
  setDef: SetDef
  /** Текущий результат отчёта (если уже сформирован) */
  result?: Result | null
}

export function Section({ title, children, open = true }: { title: ReactNode; children: ReactNode; open?: boolean }) {
  const [o, setO] = useState(open)
  return (
    <section className="psec">
      <h3 onClick={() => setO(!o)}>
        <span className="tog">{o ? '▾' : '▸'}</span> {title}
      </h3>
      {o && <div className="psec-body">{children}</div>}
    </section>
  )
}

/** Год справочников для отбора: год конца периода (для сравнения лет — целевой год). */
export function dictYear(def: Definition): number {
  if (def.type === 'compare_years') return def.target_year ?? (def.years ?? []).slice(-1)[0] ?? new Date().getFullYear()
  return Number(def.period.date_to.slice(0, 4)) || new Date().getFullYear()
}

type AttrKey = 'branches' | 'departments' | 'sources' | 'authors'
const ATTR_KEYS: AttrKey[] = ['branches', 'departments', 'sources', 'authors']

export function hasAttrFilter(def: Definition): boolean {
  return ATTR_KEYS.some((k) => (def.filters[k]?.length ?? 0) > 0)
}

interface PickItem {
  id: number
  label: string
  count: number
  unit: string
}

/** Список флажков с поиском для длинных списков. */
function MultiPick({ title, items, sel, onChange }: { title: string; items: PickItem[]; sel: number[]; onChange: (v: number[]) => void }) {
  const [q, setQ] = useState('')
  const known = new Set(items.map((i) => i.id))
  const all: PickItem[] = [...items, ...sel.filter((id) => !known.has(id)).map((id) => ({ id, label: `№ ${id} (нет в этом году)`, count: 0, unit: '' }))]
  const shown = q.trim() ? all.filter((i) => i.label.toLowerCase().includes(q.trim().toLowerCase())) : all
  const toggle = (id: number) => onChange(sel.includes(id) ? sel.filter((x) => x !== id) : [...sel, id])
  return (
    <div className="attr-pick">
      <h4>
        {title}
        {sel.length > 0 && <span className="tag"> выбрано: {sel.length}</span>}
      </h4>
      {all.length > 8 && <input type="search" placeholder="Найти…" value={q} onChange={(e) => setQ(e.target.value)} />}
      <div className="attr-list">
        {shown.map((i) => (
          <label key={i.id} className="chk">
            <input type="checkbox" checked={sel.includes(i.id)} onChange={() => toggle(i.id)} />
            <span className="attr-name">{i.label}</span>
            {i.count > 0 && (
              <span className="muted attr-cnt">
                {i.count.toLocaleString('ru-RU')} {i.unit}
              </span>
            )}
          </label>
        ))}
        {shown.length === 0 && <div className="muted">Нет значений</div>}
      </div>
    </div>
  )
}

function AttrBody({ def, setDef }: BlockProps) {
  const year = dictYear(def)
  const { data, error, loading } = useLoad<Dicts>(() => api.dicts(year), [year])
  const set = (k: AttrKey, v: number[]) =>
    setDef((d) => {
      const f = { ...d.filters }
      if (v.length) f[k] = v
      else delete f[k]
      return { ...d, filters: f }
    })
  const needsPairs = ['analysis', 'ledger', 'chess', 'journal'].includes(def.type)
  return (
    <>
      <div className="hint">
        Отчёт строится только по проводкам с выбранными признаками. Пусто — без отбора. Внутри одного признака значения складываются («или»), между признаками действует «и». Список — за {year} год.
      </div>
      <ErrorBox error={error} />
      {loading && !data && <div className="muted" style={{ minHeight: 150 }}>Загрузка…</div>}
      {data && (
        <>
          <MultiPick title="Подразделение" sel={def.filters.branches ?? []} onChange={(v) => set('branches', v)} items={data.branches.map((b) => ({ id: b.id, label: b.name, count: b.lines, unit: 'пров.' }))} />
          <MultiPick title="Отдел" sel={def.filters.departments ?? []} onChange={(v) => set('departments', v)} items={data.departments.map((b) => ({ id: b.id, label: b.name, count: b.lines, unit: 'пров.' }))} />
          <MultiPick title="Источник ордера" sel={def.filters.sources ?? []} onChange={(v) => set('sources', v)} items={data.sources.map((b) => ({ id: b.id, label: b.name, count: b.fiches, unit: 'орд.' }))} />
          <MultiPick title="Кто ввёл" sel={def.filters.authors ?? []} onChange={(v) => set('authors', v)} items={data.authors.map((b) => ({ id: b.id, label: b.name, count: b.fiches, unit: 'орд.' }))} />
        </>
      )}
      {needsPairs && (def.filters.branches?.length ?? 0) > 0 && (
        <div className="hint">
          В этом отчёте учитываются только пары проводок, обе стороны которых относятся к выбранным подразделениям. Пары между разными подразделениями не показываются, поэтому итог может быть меньше оборота ОСВ с тем же отбором.
        </div>
      )}
    </>
  )
}

/** Блок «Отбор по признакам»: по умолчанию свёрнут; при заданном отборе в заголовке видно «задан отбор» и «Сбросить». */
export function AttrFilterSection({ def, setDef }: BlockProps) {
  const on = hasAttrFilter(def)
  const reset = () =>
    setDef((d) => {
      const f = { ...d.filters }
      for (const k of ATTR_KEYS) delete f[k]
      return { ...d, filters: f }
    })
  return (
    <Section
      title={
        <>
          Отбор по признакам
          {on && <span className="tag attr-on"> задан отбор</span>}
          {on && (
            <button
              className="btn small ghost"
              style={{ marginLeft: 6 }}
              onClick={(e) => {
                e.stopPropagation()
                reset()
              }}
            >
              Сбросить
            </button>
          )}
        </>
      }
      open={false}
    >
      <AttrBody def={def} setDef={setDef} />
    </Section>
  )
}

const MN = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь']

/** Период: две даты, быстрые кнопки, выбор года. Год и «от начала» считаются по датам книги года (не обязательно календарного). */
export function PeriodBlock({ def, setDef }: BlockProps) {
  const infos = useYearInfos()
  const YEARS = useYears()
  const { date_from, date_to } = def.period
  const curYear = labelOfDate(infos, date_to)
  const book = yearRange(infos, curYear)
  const sy = Number(book.from.slice(0, 4))
  const sm = Number(book.from.slice(5, 7))
  // календарный месяц -> (год, месяц) внутри книги
  const ymOf = (m: number) => ({ y: m >= sm ? sy : sy + 1, m })
  const initM = Number(date_to.slice(5, 7)) || sm
  const [month, setMonth] = useState(initM)
  const set = (f: string, t: string) => setDef((d) => ({ ...d, period: { date_from: f, date_to: t } }))
  const mi = (y: number, m: number) => y * 12 + (m - 1)
  const fromMi = (n: number) => ({ y: Math.floor(n / 12), m: (n % 12) + 1 })
  const cur = ymOf(month)
  const k = Math.max(0, mi(cur.y, cur.m) - mi(sy, sm)) // номер месяца от начала книги
  const clampTo = (t: string) => (t > book.to ? book.to : t)
  const span = (startOff: number, len: number) => {
    const s = fromMi(mi(sy, sm) + startOff)
    const e = fromMi(mi(sy, sm) + startOff + len - 1)
    set(iso(s.y, s.m, 1), clampTo(iso(e.y, e.m, lastDay(e.y, e.m))))
  }
  const shiftYear = (ny: number) => {
    const r = yearRange(infos, ny)
    set(r.from, r.to)
    setMonth(Number(r.from.slice(5, 7)))
  }
  return (
    <>
      <div className="row2">
        <label>
          с
          <input type="date" value={date_from} onChange={(e) => set(e.target.value, date_to)} />
        </label>
        <label>
          по
          <input type="date" value={date_to} onChange={(e) => set(date_from, e.target.value)} />
        </label>
      </div>
      <div className="row2">
        <label>
          год
          <select value={curYear} onChange={(e) => shiftYear(Number(e.target.value))}>
            {(YEARS.includes(curYear) ? YEARS : [...YEARS, curYear].sort((a, b) => a - b)).map((x) => (
              <option key={x}>{x}</option>
            ))}
          </select>
        </label>
        <label>
          месяц
          <select value={month} onChange={(e) => setMonth(Number(e.target.value))}>
            {MN.map((n, i) => (
              <option key={i} value={i + 1}>
                {n}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="quick">
        <button className="btn small" onClick={() => set(iso(cur.y, cur.m, 1), iso(cur.y, cur.m, lastDay(cur.y, cur.m)))}>
          Месяц
        </button>
        <button className="btn small" title="Квартал считается от начала года (книги)" onClick={() => span(Math.floor(k / 3) * 3, 3)}>
          Квартал
        </button>
        <button className="btn small" title="Полугодие считается от начала года (книги)" onClick={() => span(Math.floor(k / 6) * 6, 6)}>
          Полугодие
        </button>
        <button className="btn small" title="С начала года (книги) по 9-й месяц" onClick={() => span(0, 9)}>
          9 мес.
        </button>
        <button className="btn small" title={`Весь год: ${book.from.split('-').reverse().join('.')} — ${book.to.split('-').reverse().join('.')}`} onClick={() => set(book.from, book.to)}>
          Год
        </button>
      </div>
    </>
  )
}

export function CurrencyBlock({ def, setDef }: BlockProps) {
  const { data: ds } = useDataset()
  const opts = currencyOptions(ds)
  if (def.currency && !opts.some((o) => o.code === def.currency)) opts.push({ code: def.currency, label: def.currency })
  return (
    <>
      <div className="row2">
        <label>
          валюта
          <select value={def.currency} onChange={(e) => setDef((d) => ({ ...d, currency: e.target.value }))}>
            {opts.map((o) => (
              <option key={o.code} value={o.code}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          единицы
          <select value={def.scale} onChange={(e) => setDef((d) => ({ ...d, scale: Number(e.target.value) as Definition['scale'] }))}>
            <option value={1}>единицы</option>
            <option value={1000}>тысячи</option>
            <option value={1000000}>миллионы</option>
          </select>
        </label>
      </div>
      <label className="chk" title="Суммы за годы до 2009 делятся на 5000 (деноминация)">
        <input type="checkbox" checked={def.denominate} onChange={(e) => setDef((d) => ({ ...d, denominate: e.target.checked }))} />
        Деноминация (годы до 2009 ÷ 5000)
      </label>
      <label className="chk" title="Закрывающие ордера (списание 7xx→6xx, 691→590 и т. п.)">
        <input type="checkbox" checked={def.include_closing} onChange={(e) => setDef((d) => ({ ...d, include_closing: e.target.checked }))} />
        С закрывающими ордерами
      </label>
    </>
  )
}

/** Фильтр счетов: маски через запятую + выбор из дерева плана счетов. */
export function AccountFilterBlock({ def, setDef }: BlockProps) {
  const [pick, setPick] = useState(false)
  const [text, setText] = useState(def.filters.accounts.join(', '))
  const [prev, setPrev] = useState(def.filters.accounts.join(','))
  // внешнее изменение (загрузка отчёта, выбор из плана) — обновить текст
  const ext = def.filters.accounts.join(',')
  if (ext !== prev) {
    setPrev(ext)
    setText(def.filters.accounts.join(', '))
  }
  const commit = (t: string) => {
    const list = t.split(/[,;\s]+/).filter(Boolean)
    setPrev(list.join(','))
    setDef((d) => ({ ...d, filters: { ...d.filters, accounts: list } }))
  }
  const year = Number(def.period.date_to.slice(0, 4)) || new Date().getFullYear()
  return (
    <>
      <input
        type="text"
        placeholder="например: 1*, 320.01*, 150..157"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={() => commit(text)}
        onKeyDown={(e) => e.key === 'Enter' && commit(text)}
      />
      <div className="hint">Маски через запятую. Пусто — все счета. Звёздочка в конце: 320* — счёт и все подчинённые.</div>
      {def.type === 'chess' && (
        <div className="hint">
          Отбор по счёту оставляет только проводки, у которых счёт из отбора есть и по дебету, и по кредиту (в отбор должны попадать обе стороны проводки). Если нужна «шахматка по одному счёту» — его оборот в разрезе корреспондентов, используйте «Анализ счёта» или «Журнал-ордер».
        </div>
      )}
      <button className="btn small" onClick={() => setPick(true)}>
        Выбрать из плана счетов…
      </button>
      {pick && (
        <AccountPicker
          year={year}
          current={def.filters.accounts}
          onClose={() => setPick(false)}
          onApply={(l) => {
            commit(l.join(','))
            setPick(false)
          }}
        />
      )}
    </>
  )
}

export function AccountPicker({ year, current, onClose, onApply }: { year: number; current: string[]; onClose: () => void; onApply: (l: string[]) => void }) {
  const { data, error, loading } = useLoad<Account[]>(() => api.accounts(year), [year])
  const [sel, setSel] = useState<string[]>(current.filter(Boolean))
  const has = (code: string) => sel.includes(code + '*') || sel.includes(code)
  const toggle = (code: string) => setSel((s) => (has(code) ? s.filter((x) => x !== code + '*' && x !== code) : [...s, code + '*']))
  return (
    <Modal
      title={`План счетов за ${year} год — отметьте счета для отчёта`}
      onClose={onClose}
      wide
      footer={
        <>
          <span className="muted" style={{ flex: 1 }}>
            Выбрано: {sel.join(', ') || 'ничего (все счета)'}
          </span>
          <button className="btn" onClick={() => setSel([])}>
            Очистить
          </button>
          <button className="btn primary" onClick={() => onApply(sel)}>
            Применить
          </button>
        </>
      }
    >
      <ErrorBox error={error} />
      {loading && <div className="muted pad">Загрузка…</div>}
      {data && (
        <AccountTree
          accounts={data}
          renderAction={(a) => (
            <label className="chk">
              <input type="checkbox" checked={has(a.code)} onChange={() => toggle(a.code)} /> выбрать
            </label>
          )}
        />
      )}
    </Modal>
  )
}

/** Редактор строк отчёта: упорядоченный список уровней. */
export function RowsBlock({ def, setDef }: BlockProps) {
  const { data: cls } = useLoad<ClassifierInfo[]>(() => api.classifiers(), [])
  const rows = def.rows
  const upd = (i: number, patch: Partial<Definition['rows'][number]>) =>
    setDef((d) => ({ ...d, rows: d.rows.map((r, k) => (k === i ? { ...r, ...patch } : r)) }))
  const move = (i: number, dir: -1 | 1) =>
    setDef((d) => {
      const r = [...d.rows]
      const j = i + dir
      if (j < 0 || j >= r.length) return d
      ;[r[i], r[j]] = [r[j], r[i]]
      return { ...d, rows: r }
    })
  const del = (i: number) => setDef((d) => ({ ...d, rows: d.rows.filter((_, k) => k !== i) }))
  return (
    <>
      <ol className="rows-list">
        {rows.map((r, i) => (
          <li key={i}>
            {r.kind === 'classifier' ? (
              <>
                <select value={r.id ?? ''} onChange={(e) => upd(i, { id: Number(e.target.value) })} title="Классификатор">
                  <option value="">— классификатор —</option>
                  {cls?.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
                <select value={r.depth ?? 1} onChange={(e) => upd(i, { depth: Number(e.target.value) })} title="Глубина">
                  {[1, 2, 3, 4, 5, 6].map((n) => (
                    <option key={n} value={n}>
                      гл. {n}
                    </option>
                  ))}
                </select>
              </>
            ) : (
              <>
                <span className="rows-label">Счета до уровня</span>
                <select value={r.max_level ?? 2} onChange={(e) => upd(i, { max_level: Number(e.target.value) })}>
                  <option value={0}>0 (корень)</option>
                  {[1, 2, 3, 4].map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </>
            )}
            <span className="rows-btns">
              <button className="btn small ghost" onClick={() => move(i, -1)} disabled={i === 0} title="Выше">
                ↑
              </button>
              <button className="btn small ghost" onClick={() => move(i, 1)} disabled={i === rows.length - 1} title="Ниже">
                ↓
              </button>
              <button className="btn small ghost" onClick={() => del(i)} title="Удалить">
                ✕
              </button>
            </span>
          </li>
        ))}
        {rows.length === 0 && <li className="muted">Нет уровней — будут показаны все листовые счета.</li>}
      </ol>
      <div className="quick">
        <button className="btn small" onClick={() => setDef((d) => ({ ...d, rows: [...d.rows, { kind: 'classifier', id: cls?.[0]?.id, depth: 1 }] }))}>
          + Классификатор
        </button>
        <button className="btn small" onClick={() => setDef((d) => ({ ...d, rows: [...d.rows, { kind: 'account', max_level: 2 }] }))}>
          + Уровень счёта
        </button>
      </div>
    </>
  )
}

export function ColumnsBlock({ def, setDef }: BlockProps) {
  const c = def.columns
  const { data: ds } = useDataset()
  const rep = ds?.params?.currency_report
  const set = (p: Partial<Definition['columns']>) => setDef((d) => ({ ...d, columns: { ...d.columns, ...p } }))
  return (
    <>
      <label className="chk">
        <input type="radio" name="bal" checked={c.balance === 'net'} onChange={() => set({ balance: 'net' })} /> Свёрнутое сальдо
      </label>
      <label className="chk">
        <input type="radio" name="bal" checked={c.balance === 'expanded'} onChange={() => set({ balance: 'expanded' })} /> Развёрнутое сальдо
      </label>
      <div className="hint">
        Свёрнутое — по счёту одно сальдо (дебет минус кредит). Развёрнутое — дебетовые и кредитовые остатки подчинённых счетов показываются отдельно, как в «бумажной» ОСВ.
      </div>
      <label className="chk">
        <input type="checkbox" checked={c.by_month} onChange={(e) => set(e.target.checked && c.by_branch ? { by_month: true, by_branch: false } : { by_month: e.target.checked })} /> Обороты по месяцам
      </label>
      {def.type === 'osv' && (
        <>
          <label className="chk" title="Обороты по дебету и кредиту отдельными графами для каждого подразделения. Вместе с разбивкой по месяцам не включается.">
            <input type="checkbox" checked={!!c.by_branch} onChange={(e) => set(e.target.checked ? { by_branch: true, by_month: false } : { by_branch: false })} /> Обороты по подразделениям
          </label>
          <label className="chk" title="Графы «Количество» по дебету и кредиту: количество из проводок за период">
            <input type="checkbox" checked={!!c.qty} onChange={(e) => set({ qty: e.target.checked })} /> Количество
          </label>
          {c.qty && <div className="hint">Единицы измерения у проводки не хранятся: количество осмысленно по конечному счёту. На уровне групп и в итоге складываются разные единицы.</div>}
        </>
      )}
      <label className="chk">
        <input type="checkbox" checked={c.show_usd} onChange={(e) => set({ show_usd: e.target.checked })} /> {rep?.code ? `Графы в валюте отчёта (${rep.code})` : 'Графы в отчётной валюте'}
      </label>
    </>
  )
}

export function OptionsBlock({ def, setDef }: BlockProps) {
  const o = def.options
  const set = (p: Partial<Definition['options']>) => setDef((d) => ({ ...d, options: { ...d.options, ...p } }))
  return (
    <>
      <label className="chk">
        <input type="checkbox" checked={o.hide_zero} onChange={(e) => set({ hide_zero: e.target.checked })} /> Скрывать нулевые строки
      </label>
      <label className="chk">
        <input type="checkbox" checked={o.unmapped} onChange={(e) => set({ unmapped: e.target.checked })} /> Показывать «не сопоставлено»
      </label>
      <div className="row2">
        <label>
          сортировка
          <select value={o.sort.by} onChange={(e) => set({ sort: { ...o.sort, by: e.target.value } })}>
            <option value="code">по коду</option>
            <option value="name">по названию</option>
            <option value="turnover">по обороту</option>
            <option value="close">по сальдо на конец</option>
          </select>
        </label>
        <label>
          порядок
          <select value={o.sort.dir} onChange={(e) => set({ sort: { ...o.sort, dir: e.target.value as 'asc' | 'desc' } })}>
            <option value="asc">по возрастанию</option>
            <option value="desc">по убыванию</option>
          </select>
        </label>
      </div>
    </>
  )
}

// ---------- Точка расширения: параметры конкретного типа отчёта ----------

/** Блок параметров типа osv. */
export function OsvParams(p: BlockProps) {
  return (
    <>
      <Section title="Строки отчёта">
        <RowsBlock {...p} />
      </Section>
      <Section title="Графы">
        <ColumnsBlock {...p} />
      </Section>
      <Section title="Параметры">
        <OptionsBlock {...p} />
      </Section>
    </>
  )
}

/** Блок параметров типа carry_gaps. */
export function CarryGapsParams({ def, setDef }: BlockProps) {
  return (
    <Section title="Параметры">
      <label className="chk">
        <input
          type="checkbox"
          checked={def.options.hide_zero}
          onChange={(e) => setDef((d) => ({ ...d, options: { ...d.options, hide_zero: e.target.checked } }))}
        />{' '}
        Показывать только расхождения
      </label>
      <div className="hint">Сверяется конечное сальдо каждого года из периода с начальным (açılış) следующего года.</div>
    </Section>
  )
}

export interface TypeConfig {
  ready: boolean
  /** Общие блоки параметров, нужные типу */
  common: { period?: boolean; currency?: boolean; accounts?: boolean }
  /** Заголовки двух левых граф таблицы */
  codeTitle?: string
  nameTitle?: string
  Params: (p: BlockProps) => ReactNode
}

/**
 * Поле выбора счёта с автоподсказкой по коду и названию из плана счетов выбранного года.
 * Можно ввести несколько счетов через запятую — подсказка работает для последнего вводимого.
 */
export function AccountInput({
  year,
  value,
  onChange,
  multi = true,
  placeholder,
}: {
  year: number
  value: string
  onChange: (v: string) => void
  multi?: boolean
  placeholder?: string
}) {
  const [accounts, setAccounts] = useState<Account[] | null>(null)
  const [open, setOpen] = useState(false)
  const [act, setAct] = useState(0)
  useEffect(() => {
    let alive = true
    setAccounts(null)
    api
      .accounts(year)
      .then((l) => alive && setAccounts(l))
      .catch(() => alive && setAccounts([]))
    return () => {
      alive = false
    }
  }, [year])
  const cut = multi ? value.lastIndexOf(',') : -1
  const head = cut >= 0 ? value.slice(0, cut + 1) + ' ' : ''
  const rawToken = value.slice(cut + 1).trim()
  // часть до «*» — это начало кода; маска или набор цифр с точками ищется только по началу кода, а не по названиям
  const token = rawToken.replace(/\*.*$/, '')
  const codeOnly = rawToken.includes('*') || /^[\d.]+$/.test(token)
  const t = token.toLowerCase()
  const list = useMemo(() => {
    if (!accounts || !t) return []
    const byCode: Account[] = []
    const byName: Account[] = []
    for (const a of accounts) {
      if (a.code.toLowerCase().startsWith(t)) byCode.push(a)
      else if (!codeOnly && a.name.toLowerCase().includes(t)) byName.push(a)
    }
    return [...byCode, ...byName].slice(0, 14)
  }, [accounts, t, codeOnly])
  const pick = (a: Account) => {
    onChange(head + a.code)
    setOpen(false)
  }
  return (
    <div className="ac-wrap">
      <input
        type="text"
        value={value}
        placeholder={placeholder}
        autoComplete="off"
        onChange={(e) => {
          onChange(e.target.value)
          setOpen(true)
          setAct(0)
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (!open || !list.length) return
          if (e.key === 'ArrowDown') (e.preventDefault(), setAct((a) => Math.min(list.length - 1, a + 1)))
          else if (e.key === 'ArrowUp') (e.preventDefault(), setAct((a) => Math.max(0, a - 1)))
          else if (e.key === 'Enter') (e.preventDefault(), pick(list[act]))
          else if (e.key === 'Escape') setOpen(false)
        }}
      />
      {open && list.length > 0 && (
        <div className="ac-list">
          {list.map((a, i) => (
            <div key={a.code} className={'ac-item' + (i === act ? ' act' : '')} onMouseDown={(e) => (e.preventDefault(), pick(a))}>
              <b>{a.code}</b>
              <span>{a.name}</span>
            </div>
          ))}
        </div>
      )}
      {open && token && accounts && accounts.length > 0 && list.length === 0 && <div className="ac-list"><div className="ac-item"><span>В плане счетов {year} года нет счёта «{token}»</span></div></div>}
    </div>
  )
}
