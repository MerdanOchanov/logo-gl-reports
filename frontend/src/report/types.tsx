import { askConfirm } from '../components/confirm'
import { useEffect, useState } from 'react'
import { api, type Axis, type ClassifierInfo, type Classifier, type Definition, type ReportType } from '../api'
import { useLoad } from '../components/ui'
import { useYears } from '../years'
import { AccountInput, AccountPicker, CarryGapsParams, ColumnsBlock, OptionsBlock, OsvParams, RowsBlock, Section, type BlockProps, type TypeConfig } from './common'
import FormParams from './FormEditor'

const ALL = { period: true, currency: true, accounts: true }
const NO_ACC = { period: true, currency: true, accounts: false }

const yearOf = (def: Definition) => Number(def.period.date_to.slice(0, 4)) || new Date().getFullYear()

/** Счёт / маски / группа классификатора — для карточки, анализа, главной книги и журнала. */
function AccountBlock({ def, setDef, multi = true }: BlockProps & { multi?: boolean }) {
  const [pick, setPick] = useState(false)
  const { data: cls } = useLoad<ClassifierInfo[]>(() => api.classifiers(), [])
  const g = def.filters.groups[0]
  const { data: full } = useLoad<Classifier | null>(() => (g ? api.classifier(g.classifier) : Promise.resolve(null)), [g?.classifier])
  const setGroup = (classifier: number | null, group: number | null) =>
    setDef((d) => ({ ...d, filters: { ...d.filters, groups: classifier && group ? [{ classifier, group }] : [] } }))
  const text = def.account ?? ''
  const commit = (t: string) => setDef((d) => ({ ...d, account: t }))
  const setText = commit
  return (
    <Section title="Счёт">
      <AccountInput year={yearOf(def)} value={text} onChange={setText} multi={multi} placeholder="начните вводить код или название счёта" />
      <div className="hint">Код счёта (счёт и все подчинённые) или маска со звёздочкой{multi ? ', несколько через запятую' : ''}. Диапазон: 150..157. Подсказки — из плана счетов {yearOf(def)} года.</div>
      <button className="btn small" onClick={() => setPick(true)}>
        Выбрать из плана счетов…
      </button>
      {pick && (
        <AccountPicker
          year={yearOf(def)}
          current={(def.account ?? '').split(/[,;\s]+/).filter(Boolean)}
          onClose={() => setPick(false)}
          onApply={(l) => {
            commit(l.join(', '))
            setPick(false)
          }}
        />
      )}
      <h4>Или группа классификатора</h4>
      <label>
        классификатор
        <select value={g?.classifier ?? ''} onChange={(e) => setGroup(e.target.value ? Number(e.target.value) : null, null)}>
          <option value="">— не выбрана —</option>
          {cls?.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      </label>
      {g && (
        <label>
          группа
          <select value={g.group || ''} onChange={(e) => (e.target.value ? setGroup(g.classifier, Number(e.target.value)) : setGroup(null, null))}>
            <option value="">— выберите —</option>
            {full?.groups.map((x) => (
              <option key={x.id} value={x.id}>
                {x.code} {x.name}
              </option>
            ))}
          </select>
        </label>
      )}
    </Section>
  )
}

/** Ось: уровень счёта 0–4 или классификатор и глубина. */
function AxisEditor({ axis, onChange }: { axis: Axis; onChange: (a: Axis) => void }) {
  const { data: cls } = useLoad<ClassifierInfo[]>(() => api.classifiers(), [])
  return (
    <div className="row2">
      <label>
        по
        <select
          value={axis.kind === 'classifier' ? 'c' + (axis.id ?? '') : 'a'}
          onChange={(e) => {
            const v = e.target.value
            if (v === 'a') onChange({ kind: 'account', level: axis.level ?? 0 })
            else onChange({ kind: 'classifier', id: Number(v.slice(1)), depth: axis.depth ?? 1 })
          }}
        >
          <option value="a">уровню счёта</option>
          {cls?.map((c) => (
            <option key={c.id} value={'c' + c.id}>
              классификатор: {c.name}
            </option>
          ))}
        </select>
      </label>
      {axis.kind === 'classifier' ? (
        <label>
          глубина
          <select value={axis.depth ?? 1} onChange={(e) => onChange({ ...axis, depth: Number(e.target.value) })}>
            {[1, 2, 3, 4, 5, 6].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <label>
          уровень
          <select value={axis.level ?? 0} onChange={(e) => onChange({ ...axis, level: Number(e.target.value) })}>
            {[0, 1, 2, 3, 4].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
      )}
    </div>
  )
}

/** Предел числа граф: остальные сворачиваются в «Прочие». */
function MaxGraphs({ def, setDef, def_ }: BlockProps & { def_: number }) {
  return (
    <label>
      максимум граф (остальные — «Прочие»)
      <input
        type="number"
        min={5}
        max={200}
        value={def.options.max_graphs ?? def_}
        onChange={(e) => setDef((d) => ({ ...d, options: { ...d.options, max_graphs: Number(e.target.value) || def_ } }))}
      />
    </label>
  )
}

function CardParams(p: BlockProps) {
  const c = p.def.card ?? { detail: 'entries' as const }
  return (
    <>
      <AccountBlock {...p} />
      <Section title="Детализация">
        {(
          [
            ['entries', 'Все проводки (нарастающее сальдо)'],
            ['day', 'Итоги по дням'],
            ['month', 'Итоги по месяцам'],
          ] as const
        ).map(([k, l]) => (
          <label key={k} className="chk">
            <input type="radio" name="card-detail" checked={c.detail === k} onChange={() => p.setDef((d) => ({ ...d, card: { detail: k, lines: c.lines } }))} /> {l}
          </label>
        ))}
        <label className="chk">
          <input
            type="checkbox"
            checked={c.lines !== false}
            onChange={(e) => p.setDef((d) => ({ ...d, card: { detail: c.detail, lines: e.target.checked } }))}
          />{' '}
          Показывать проводки (иначе только итоги)
        </label>
        <div className="hint">Если выбрана маска или группа — движения по всем подчинённым счетам.</div>
      </Section>
      <Section title="Параметры" open={false}>
        <OptionsBlock {...p} />
      </Section>
    </>
  )
}

function AnalysisParams(p: BlockProps) {
  const corr: Axis = p.def.analysis?.corr ?? { kind: 'account', level: 1 }
  return (
    <>
      <AccountBlock {...p} />
      <Section title="Корреспонденты">
        <div className="hint">Как сворачивать корреспондирующие счета:</div>
        <AxisEditor axis={corr} onChange={(a) => p.setDef((d) => ({ ...d, analysis: { corr: a } }))} />
      </Section>
      <Section title="Графы">
        <label className="chk">
          <input
            type="checkbox"
            checked={p.def.columns.by_month}
            onChange={(e) => p.setDef((d) => ({ ...d, columns: { ...d.columns, by_month: e.target.checked } }))}
          />{' '}
          Обороты по месяцам
        </label>
      </Section>
      <Section title="Параметры" open={false}>
        <OptionsBlock {...p} />
      </Section>
    </>
  )
}

function LedgerParams(p: BlockProps) {
  const corr: Axis = p.def.ledger?.corr ?? { kind: 'account', level: 0 }
  return (
    <>
      <AccountBlock {...p} />
      <Section title="Графы корреспонденции">
        <div className="hint">Обороты по дебету раскладываются по кредитуемым счетам:</div>
        <AxisEditor axis={corr} onChange={(a) => p.setDef((d) => ({ ...d, ledger: { corr: a } }))} />
        <MaxGraphs {...p} def_={40} />
      </Section>
      <Section title="Параметры" open={false}>
        <OptionsBlock {...p} />
        <div className="hint">Главная книга строится по месяцам: обороты Дт/Кт и сальдо на конец месяца. Без счёта — раздел на каждый корневой счёт.</div>
      </Section>
    </>
  )
}

function ChessParams(p: BlockProps) {
  const ch = p.def.chess ?? { rows: { kind: 'account' as const, level: 0 }, cols: { kind: 'account' as const, level: 0 } }
  return (
    <>
      <Section title="Строки (Дебет)">
        <AxisEditor axis={ch.rows as Axis} onChange={(a) => p.setDef((d) => ({ ...d, chess: { ...ch, rows: a } }))} />
      </Section>
      <Section title="Графы (Кредит)">
        <AxisEditor axis={ch.cols as Axis} onChange={(a) => p.setDef((d) => ({ ...d, chess: { ...ch, cols: a } }))} />
        <div className="hint">Уровень 0 — корневые счета (3 знака); уровень 2–4 даёт сотни граф: удобны «закрепить граф» и компактные числа над таблицей.</div>
      </Section>
      <Section title="Параметры" open={false}>
        <OptionsBlock {...p} />
      </Section>
    </>
  )
}

type FixedCol = { sel: string; title?: string }

const cleanCols = (list: FixedCol[]) => list.filter((c) => c.sel.trim()).map((c) => (c.title?.trim() ? { sel: c.sel.trim(), title: c.title.trim() } : { sel: c.sel.trim() }))

function JournalParams(p: BlockProps) {
  const j = p.def.journal ?? { side: 'credit' as const, rows: 'day' as const }
  const set = (x: Partial<typeof j>) => p.setDef((d) => ({ ...d, journal: { ...(d.journal ?? j), ...x } }))
  const saved = j.fixed_cols
  const [mode, setMode] = useState<'auto' | 'fixed'>(saved && saved.length ? 'fixed' : 'auto')
  const [cols, setCols] = useState<FixedCol[]>(saved && saved.length ? saved.map((c) => ({ sel: c.sel, title: c.title })) : [])
  const [pickIdx, setPickIdx] = useState<number | null>(null)
  // сохранённый отчёт мог загрузиться позже первого показа панели
  const savedKey = JSON.stringify(saved ?? null)
  useEffect(() => {
    if (saved && saved.length && JSON.stringify(saved) !== JSON.stringify(cleanCols(cols))) {
      setCols(saved.map((c) => ({ sel: c.sel, title: c.title })))
      setMode('fixed')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [savedKey])

  const write = (list: FixedCol[], m: 'auto' | 'fixed' = mode) =>
    p.setDef((d) => {
      const cur = { ...(d.journal ?? j) }
      const clean = cleanCols(list)
      if (m === 'fixed' && clean.length) cur.fixed_cols = clean
      else {
        delete cur.fixed_cols
        delete cur.rest
      }
      return { ...d, journal: cur }
    })
  const upd = (list: FixedCol[]) => (setCols(list), write(list))
  const changeMode = (m: 'auto' | 'fixed') => {
    setMode(m)
    write(cols, m)
  }
  const move = (i: number, d: number) => {
    const l = [...cols]
    ;[l[i], l[i + d]] = [l[i + d], l[i]]
    upd(l)
  }
  // графы корреспондентов текущего результата (формировался в автоматическом режиме)
  const auto: FixedCol[] =
    p.result && !saved?.length
      ? p.result.columns
          .filter((c) => c.group && /^(В дебет счетов|С кредита счетов)$/.test(c.group) && c.title !== 'Прочие')
          .map((c) => ({ sel: c.title + '*', title: c.name ? `${c.title} ${c.name}` : c.title }))
      : []
  const year = Number(p.def.period.date_to.slice(0, 4)) || new Date().getFullYear()
  return (
    <>
      <AccountBlock {...p} />
      <Section title="Журнал-ордер">
        <label>
          сторона счёта
          <select value={j.side} onChange={(e) => set({ side: e.target.value as 'credit' | 'debit' })}>
            <option value="credit">по кредиту (классический журнал-ордер)</option>
            <option value="debit">по дебету (ведомость)</option>
          </select>
        </label>
        <label>
          строки
          <select value={j.rows} onChange={(e) => set({ rows: e.target.value as 'day' | 'fiche' })}>
            <option value="day">по дням</option>
            <option value="fiche">по ордерам</option>
          </select>
        </label>
        <label className="chk">
          <input type="checkbox" checked={j.with_balance === true} onChange={(e) => set({ with_balance: e.target.checked || undefined })} /> Показывать сальдо на начало и конец
        </label>
        <div className="hint">Графы — корреспондирующие счета, итог — оборот выбранной стороны. Сальдо добавляет строки «Сальдо на начало», «Оборот по противоположной стороне», «Сальдо на конец».</div>
      </Section>
      <Section title="Графы журнала">
        <label className="chk">
          <input type="radio" name="jr-mode" checked={mode === 'auto'} onChange={() => changeMode('auto')} /> Автоматически (по фактическим корреспондентам)
        </label>
        <label className="chk">
          <input type="radio" name="jr-mode" checked={mode === 'fixed'} onChange={() => changeMode('fixed')} /> Заданный набор граф
        </label>
        {mode === 'auto' && (
          <>
            <AxisEditor axis={j.corr ?? { kind: 'account', level: 0 }} onChange={(a) => set({ corr: a })} />
            <MaxGraphs {...p} def_={60} />
          </>
        )}
        {mode === 'fixed' && (
          <>
            {cols.map((c, i) => (
              <div key={i} className="fline">
                <div className="row2">
                  <label>
                    счета
                    <input type="text" value={c.sel} placeholder="102*   320*|321*   150..157" onChange={(e) => upd(cols.map((x, k) => (k === i ? { ...x, sel: e.target.value } : x)))} />
                  </label>
                  <label>
                    заголовок (необязательно)
                    <input type="text" value={c.title ?? ''} onChange={(e) => upd(cols.map((x, k) => (k === i ? { ...x, title: e.target.value } : x)))} />
                  </label>
                </div>
                <div className="quick">
                  <button className="btn small ghost" onClick={() => setPickIdx(i)} title="Выбрать из плана счетов">
                    из плана…
                  </button>
                  <span className="spacer" />
                  <button className="btn small ghost" onClick={() => move(i, -1)} disabled={i === 0} title="Выше">
                    ↑
                  </button>
                  <button className="btn small ghost" onClick={() => move(i, 1)} disabled={i === cols.length - 1} title="Ниже">
                    ↓
                  </button>
                  <button className="btn small ghost" onClick={() => upd(cols.filter((_, k) => k !== i))} title="Удалить графу">
                    ✕
                  </button>
                </div>
              </div>
            ))}
            <div className="quick">
              <button className="btn small" onClick={() => upd([...cols, { sel: '', title: '' }])}>
                + Графа
              </button>
              <button
                className="btn small"
                disabled={!auto.length}
                title={auto.length ? 'Графы корреспондентов из текущего результата' : 'Сначала сформируйте отчёт'}
                onClick={async () => {
                  if (cols.some((c) => c.sel.trim()) && !(await askConfirm('Заменить текущий список граф?', { okLabel: 'Заменить' }))) return
                  upd(auto)
                }}
              >
                Заполнить по фактическим корреспондентам
              </button>
            </div>
            <div className="hint">Маска со звёздочкой (102*), диапазон (150..157), несколько счетов через | или запятую. Графы идут слева направо в заданном порядке.</div>
            <label>
              остальные счета
              <select value={j.rest ?? 'other'} onChange={(e) => set({ rest: e.target.value as 'other' | 'auto' | 'hide' })}>
                <option value="other">одной графой «Прочие»</option>
                <option value="auto">отдельными графами</option>
                <option value="hide">не показывать</option>
              </select>
            </label>
            {pickIdx !== null && (
              <AccountPicker
                year={year}
                current={(cols[pickIdx]?.sel ?? '').split(/[|,;\s]+/).filter(Boolean)}
                onClose={() => setPickIdx(null)}
                onApply={(l) => {
                  upd(cols.map((x, k) => (k === pickIdx ? { ...x, sel: l.join('|') } : x)))
                  setPickIdx(null)
                }}
              />
            )}
          </>
        )}
      </Section>
    </>
  )
}

const MEASURES: [string, string][] = [
  ['open', 'Сальдо на начало (Дт − Кт)'],
  ['turn_dt', 'Оборот по дебету'],
  ['turn_kt', 'Оборот по кредиту'],
  ['close', 'Сальдо на конец (Дт − Кт)'],
  ['close_dt', 'Сальдо на конец, дебет'],
  ['close_kt', 'Сальдо на конец, кредит'],
]

function CompareParams(p: BlockProps) {
  const years = p.def.years ?? []
  const measures = p.def.measures ?? ['turn_dt']
  const YEARS = useYears()
  const toggleYear = (y: number) =>
    p.setDef((d) => {
      const cur = d.years ?? []
      const ys = cur.includes(y) ? cur.filter((x) => x !== y) : [...cur, y].sort()
      const t = d.target_year && ys.includes(d.target_year) ? d.target_year : ys[ys.length - 1]
      return { ...d, years: ys, target_year: t }
    })
  const toggleMeasure = (m: string) =>
    p.setDef((d) => {
      const cur = d.measures ?? []
      return { ...d, measures: MEASURES.map(([k]) => k).filter((k) => (k === m ? !cur.includes(m) : cur.includes(k))) }
    })
  return (
    <>
      <Section title="Годы">
        <div className="years-grid">
          {YEARS.map((y) => (
            <label key={y} className="chk">
              <input type="checkbox" checked={years.includes(y)} onChange={() => toggleYear(y)} /> {y}
            </label>
          ))}
        </div>
        <label>
          плана счетов какого года держаться
          <select value={p.def.target_year ?? ''} onChange={(e) => p.setDef((d) => ({ ...d, target_year: Number(e.target.value) }))}>
            {years.map((y) => (
              <option key={y}>{y}</option>
            ))}
          </select>
        </label>
        <div className="hint">Нужно выбрать от двух до десяти лет. Счета прошлых лет приводятся к плану выбранного года.</div>
        <label className="chk">
          <input type="checkbox" checked={p.def.use_links !== false} onChange={(e) => p.setDef((d) => ({ ...d, use_links: e.target.checked }))} /> Учитывать связи счетов между годами
        </label>
        <div className="hint">Связи — в «Настройки → Связи счетов между годами».</div>
      </Section>
      <Section title="Показатели">
        <label className="chk">
          <input type="checkbox" checked={p.def.delta === true} onChange={(e) => p.setDef((d) => ({ ...d, delta: e.target.checked }))} /> Добавить графу «Изменение, %» (первый год → последний)
        </label>
        {MEASURES.map(([k, l]) => (
          <label key={k} className="chk">
            <input type="checkbox" checked={measures.includes(k)} onChange={() => toggleMeasure(k)} /> {l}
          </label>
        ))}
      </Section>
      <Section title="Часть года">
        <div className="row2">
          <label>
            с (ММ-ДД)
            <input type="text" value={p.def.mmdd_from ?? '01-01'} onChange={(e) => p.setDef((d) => ({ ...d, mmdd_from: e.target.value }))} />
          </label>
          <label>
            по (ММ-ДД)
            <input type="text" value={p.def.mmdd_to ?? '12-31'} onChange={(e) => p.setDef((d) => ({ ...d, mmdd_to: e.target.value }))} />
          </label>
        </div>
        <div className="hint">Один и тот же отрезок года в каждом году: 01-01 … 12-31 — год целиком, 01-01 … 09-30 — девять месяцев.</div>
      </Section>
      <Section title="Строки отчёта">
        <RowsBlock {...p} />
      </Section>
      <Section title="Графы и параметры" open={false}>
        <ColumnsBlock {...p} />
        <OptionsBlock {...p} />
      </Section>
    </>
  )
}

export const TYPE_CONFIG: Record<ReportType, TypeConfig> = {
  osv: { ready: true, common: ALL, codeTitle: 'Счёт', Params: (p) => <OsvParams {...p} /> },
  carry_gaps: { ready: true, common: ALL, codeTitle: 'Счёт', Params: (p) => <CarryGapsParams {...p} /> },
  card: { ready: true, common: NO_ACC, codeTitle: 'Дата', nameTitle: 'Содержание операции', Params: (p) => <CardParams {...p} /> },
  analysis: { ready: true, common: NO_ACC, codeTitle: 'Счёт', nameTitle: 'Наименование / корреспондент', Params: (p) => <AnalysisParams {...p} /> },
  ledger: { ready: true, common: NO_ACC, codeTitle: 'Счёт / месяц', nameTitle: 'Наименование', Params: (p) => <LedgerParams {...p} /> },
  chess: { ready: true, common: ALL, codeTitle: 'Дебет счёта', nameTitle: 'Наименование', Params: (p) => <ChessParams {...p} /> },
  journal: { ready: true, common: NO_ACC, codeTitle: 'Дата / №', nameTitle: 'Содержание', Params: (p) => <JournalParams {...p} /> },
  form: { ready: true, common: NO_ACC, codeTitle: 'Код строки', nameTitle: 'Показатель', Params: (p) => <FormParams {...p} /> },
  compare_years: { ready: true, common: { currency: true, accounts: true }, codeTitle: 'Счёт', Params: (p) => <CompareParams {...p} /> },
}

