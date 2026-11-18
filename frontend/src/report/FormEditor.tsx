import { useEffect, useRef, useState } from 'react'
import { api, errMsg, ApiError, type FormDef, type FormLine, type TemplateInfo } from '../api'
import { ErrorBox, Modal, useLoad } from '../components/ui'
import { Section, type BlockProps } from './common'

const COLS: [string, string][] = [
  ['begin', 'На начало'],
  ['end', 'На конец'],
  ['period', 'За период'],
  ['prev', 'Прошлый год (на конец)'],
  ['prev_begin', 'Прошлый год (на начало)'],
]

type Check = { ok: boolean; msg?: string } | 'header' | 'na'

const emptyForm = (): FormDef => ({ lines: [], template_id: null, columns: ['begin', 'end'], checks: [], coverage: [] })

function errTextOf(errors?: { message: string; pos?: number | null }[]): string {
  const e = errors?.[0]
  if (!e) return 'Ошибка в формуле'
  return e.message + (e.pos != null ? ` (позиция ${e.pos + 1})` : '')
}

/** Проверка формулы на лету; результат кэшируется по тексту. */
function useFormulaCheck(lines: FormLine[]) {
  const [res, setRes] = useState<Record<number, Check>>({})
  const cache = useRef(new Map<string, Check>())
  const codes = lines.map((l) => l.code).filter(Boolean)
  const key = lines.map((l) => l.formula).join('\u0001') + '|' + codes.join(',')
  useEffect(() => {
    let alive = true
    const timer = setTimeout(async () => {
      for (let i = 0; i < lines.length; i++) {
        const f = lines[i].formula.trim()
        if (!f) {
          if (alive) setRes((r) => ({ ...r, [i]: 'header' }))
          continue
        }
        const k = f + '|' + codes.join(',')
        let c = cache.current.get(k)
        if (!c) {
          try {
            const r = await api.validateFormula(f, codes)
            const bad = r.ok === false || !!r.errors?.length
            c = bad ? { ok: false, msg: errTextOf(r.errors) } : { ok: true }
          } catch (e) {
            if (e instanceof ApiError && (e.status === 400 || e.status === 422)) c = { ok: false, msg: e.message }
            else c = 'na'
          }
          cache.current.set(k, c)
        }
        if (!alive) return
        setRes((r) => ({ ...r, [i]: c! }))
      }
    }, 450)
    return () => {
      alive = false
      clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  return res
}

export default function FormParams({ def, setDef }: BlockProps) {
  const form: FormDef = { ...emptyForm(), ...(def.form ?? {}) }
  const set = (p: Partial<FormDef>) => setDef((d) => ({ ...d, form: { ...emptyForm(), ...(d.form ?? {}), ...p } }))
  const setLine = (i: number, p: Partial<FormLine>) => set({ lines: form.lines.map((l, k) => (k === i ? { ...l, ...p } : l)) })
  const move = (i: number, dir: -1 | 1) => {
    const j = i + dir
    if (j < 0 || j >= form.lines.length) return
    const l = [...form.lines]
    ;[l[i], l[j]] = [l[j], l[i]]
    set({ lines: l })
  }
  const [help, setHelp] = useState(false)
  const [tplOpen, setTplOpen] = useState(false)
  const check = useFormulaCheck(form.lines)
  const { data: tpls, reload: reloadTpl } = useLoad<TemplateInfo[]>(() => api.templates().catch(() => []), [])

  const toggleCol = (k: string) =>
    set({ columns: form.columns.includes(k) ? form.columns.filter((x) => x !== k) : COLS.map(([c]) => c).filter((c) => c === k || form.columns.includes(c)) })

  return (
    <>
      <Section title="Графы формы">
        {COLS.map(([k, l]) => (
          <label key={k} className="chk">
            <input type="checkbox" checked={form.columns.includes(k)} onChange={() => toggleCol(k)} /> {l}
          </label>
        ))}
        <div className="hint">«Прошлый год» — тот же период годом раньше. Графы формы считаются по периоду отчёта.</div>
      </Section>

      <Section title={`Строки формы (${form.lines.length})`}>
        <div className="quick">
          <button className="btn small" onClick={() => setHelp(true)}>
            Как писать формулы
          </button>
        </div>
        {form.lines.map((l, i) => {
          const c = check[i]
          const bad = typeof c === 'object' && !c.ok
          return (
            <div key={i} className={'fl' + (bad ? ' err' : '')}>
              <div className="r1">
                <input type="text" style={{ width: 56 }} value={l.code} placeholder="код" onChange={(e) => setLine(i, { code: e.target.value })} title="Код строки (для ссылок [010])" />
                <input type="text" value={l.name} placeholder="название строки" onChange={(e) => setLine(i, { name: e.target.value })} />
              </div>
              <input
                type="text"
                className="mono"
                value={l.formula}
                placeholder="например: SKD(100*)+SKD(102*)"
                onChange={(e) => setLine(i, { formula: e.target.value })}
              />
              {bad && <div className="ferr">✗ {(c as { msg?: string }).msg}</div>}
              {typeof c === 'object' && c.ok && <div className="fok">✓ формула верна</div>}
              {c === 'na' && <div className="hint">проверка недоступна (сервер не ответил)</div>}
              {c === 'header' && <div className="hint">без формулы — строка-заголовок</div>}
              <div className="r1" style={{ marginTop: 3, alignItems: 'center' }}>
                <label className="chk" style={{ margin: 0 }}>
                  <input type="checkbox" checked={!!l.bold} onChange={(e) => setLine(i, { bold: e.target.checked })} /> жирная
                </label>
                <label className="inline" title="Отступ в таблице">
                  отступ
                  <select value={l.indent ?? 0} onChange={(e) => setLine(i, { indent: Number(e.target.value) })}>
                    {[0, 1, 2, 3, 4].map((n) => (
                      <option key={n}>{n}</option>
                    ))}
                  </select>
                </label>
                <input type="text" style={{ width: 76 }} value={l.cell ?? ''} placeholder="ячейка" title="Ячейка шаблона Excel, например D12" onChange={(e) => setLine(i, { cell: e.target.value || undefined })} />
                <span className="spacer" />
                <button className="btn small ghost" onClick={() => move(i, -1)} disabled={i === 0} title="Выше">
                  ↑
                </button>
                <button className="btn small ghost" onClick={() => move(i, 1)} disabled={i === form.lines.length - 1} title="Ниже">
                  ↓
                </button>
                <button className="btn small ghost" onClick={() => set({ lines: form.lines.filter((_, k) => k !== i) })} title="Удалить строку">
                  ✕
                </button>
              </div>
            </div>
          )
        })}
        <div className="quick">
          <button className="btn small" onClick={() => set({ lines: [...form.lines, { code: String((form.lines.length + 1) * 10).padStart(3, '0'), name: '', formula: '', bold: false, indent: 0 }] })}>
            + Строка
          </button>
        </div>
      </Section>

      <Section title={`Контрольные равенства (${form.checks.length})`} open={form.checks.length > 0}>
        {form.checks.map((c, i) => (
          <div key={i} className="fl">
            <input
              type="text"
              value={c.name}
              placeholder="название, например: Актив = Пассив"
              onChange={(e) => set({ checks: form.checks.map((x, k) => (k === i ? { ...x, name: e.target.value } : x)) })}
              onBlur={() => !c.name.trim() && set({ checks: form.checks.map((x, k) => (k === i ? { ...x, name: `Контроль ${i + 1}` } : x)) })}
              title="Равенство без названия в расчёт не попадает — пустое название заменяется на «Контроль N»"
            />
            <div className="r1" style={{ marginTop: 3 }}>
              <input
                type="text"
                className="mono"
                value={c.left}
                placeholder="[300]"
                onChange={(e) => set({ checks: form.checks.map((x, k) => (k === i ? { ...x, left: e.target.value } : x)) })}
              />
              <span>=</span>
              <input
                type="text"
                className="mono"
                value={c.right}
                placeholder="[600]"
                onChange={(e) => set({ checks: form.checks.map((x, k) => (k === i ? { ...x, right: e.target.value } : x)) })}
              />
              <button className="btn small ghost" onClick={() => set({ checks: form.checks.filter((_, k) => k !== i) })}>
                ✕
              </button>
            </div>
          </div>
        ))}
        <button className="btn small" onClick={() => set({ checks: [...form.checks, { name: `Контроль ${form.checks.length + 1}`, left: '', right: '' }] })}>
          + Равенство
        </button>
        <div className="hint">Обе части — формулы со ссылками на строки: [010]+[020]. Результат — в полосе контролей под таблицей.</div>
      </Section>

      <Section title="Охват счетов" open={!!form.coverage?.length}>
        <input
          type="text"
          value={(form.coverage ?? []).join(' ; ')}
          placeholder="например: 1*|2*|3*|4*|5*"
          onChange={(e) => set({ coverage: e.target.value.split(';').map((x) => x.trim()).filter(Boolean) })}
        />
        <div className="hint">Какие счета форма обязана покрыть. Несколько отборов — через « ; ». Не охваченные строками счета появятся в предупреждениях.</div>
      </Section>

      <Section title="Шаблон Excel" open={!!form.template_id}>
        <label>
          шаблон бланка
          <select value={form.template_id ?? ''} onChange={(e) => set({ template_id: e.target.value ? Number(e.target.value) : null })}>
            <option value="">— без шаблона (простая таблица) —</option>
            {tpls?.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name} ({t.filename})
              </option>
            ))}
          </select>
        </label>
        <button className="btn small" onClick={() => setTplOpen(true)}>
          Загрузить новый шаблон…
        </button>
        <div className="hint">В выгрузке в Excel значения строк подставляются в ячейки шаблона, указанные в поле «ячейка» каждой строки.</div>
      </Section>

      {help && <FormulaHelp onClose={() => setHelp(false)} />}
      {tplOpen && (
        <UploadTemplate
          onClose={() => setTplOpen(false)}
          onDone={(t) => {
            setTplOpen(false)
            reloadTpl()
            set({ template_id: t.id })
          }}
        />
      )}
    </>
  )
}

function FormulaHelp({ onClose }: { onClose: () => void }) {
  const rows: [string, string][] = [
    ['SKD(100*)', 'сальдо конечное дебетовое (развёрнуто по подсчетам: берутся только дебетовые остатки)'],
    ['SKK(320*)', 'сальдо конечное кредитовое'],
    ['SND(…) / SNK(…)', 'сальдо на начало, дебетовое / кредитовое'],
    ['SN(…) / SK(…)', 'свёрнутое сальдо Дт − Кт на начало / на конец'],
    ['OBD(…) / OBK(…)', 'обороты периода по дебету / по кредиту'],
    ['OBDK(60*, 100*)', 'оборот в корреспонденции: Дт 60*, Кт 100*'],
    ['[010]', 'ссылка на другую строку формы по её коду'],
    ['+  −  *  /  ( )', 'арифметика и числа: [010]+[020]-1000'],
  ]
  const sel: [string, string][] = [
    ['100*', 'счёт 100 и все подчинённые'],
    ['150..157', 'диапазон корней, включительно'],
    ['100*|102*|103.01*', 'несколько отборов через «|»'],
    ['@1:76', 'группа классификатора: номер классификатора : код группы'],
    ['1* ~ 129*', 'исключение: всё, что после «~», в отбор не входит'],
  ]
  const ex: [string, string][] = [
    ['SKD(100*)+SKD(102*)', 'денежные средства: касса и банк'],
    ['SKD(120*)-SKK(120*)', 'чистая дебиторка'],
    ['OBK(601*)', 'выручка за период'],
    ['[010]-[020]', 'разность двух строк'],
  ]
  const tbl = (r: [string, string][]) => (
    <table>
      <tbody>
        {r.map(([a, b]) => (
          <tr key={a}>
            <td>
              <code>{a}</code>
            </td>
            <td>{b}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
  return (
    <Modal title="Как писать формулы" onClose={onClose} wide>
      <div className="help">
        <h4>Функции</h4>
        {tbl(rows)}
        <h4>Отбор счетов (то, что в скобках)</h4>
        {tbl(sel)}
        <h4>Примеры</h4>
        {tbl(ex)}
        <p className="hint">Формула проверяется сервером при вводе: ошибка показывается под строкой. Скрипты и произвольный код не поддерживаются.</p>
      </div>
    </Modal>
  )
}

function UploadTemplate({ onClose, onDone }: { onClose: () => void; onDone: (t: TemplateInfo) => void }) {
  const [name, setName] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const go = async () => {
    if (!file) return setErr('Выберите файл .xlsx')
    setBusy(true)
    try {
      onDone(await api.uploadTemplate(name.trim() || file.name.replace(/\.xlsx?$/i, ''), file))
    } catch (e) {
      setErr(errMsg(e))
      setBusy(false)
    }
  }
  return (
    <Modal
      title="Загрузка шаблона Excel"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Отмена
          </button>
          <button className="btn primary" onClick={go} disabled={busy}>
            Загрузить
          </button>
        </>
      }
    >
      <ErrorBox error={err} />
      <label>
        название шаблона
        <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="например: Баланс (бланк)" />
      </label>
      <label>
        файл
        <input type="file" accept=".xlsx,.xlsm" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
      </label>
    </Modal>
  )
}
