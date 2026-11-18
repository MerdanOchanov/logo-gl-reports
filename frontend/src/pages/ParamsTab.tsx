import { askConfirm } from '../components/confirm'
import { useEffect, useState } from 'react'
import { api, errMsg, type CarryNote, type DatasetParams, type PlanDetect, type PlanProfile } from '../api'
import { ErrorBox } from '../components/ui'
import { reloadDataset, useDataset } from '../dataset'
import { resetYears } from '../years'
import { navigate } from '../router'

const list = (s: string) =>
  s
    .split(/[,;\s]+/)
    .map((x) => x.trim())
    .filter(Boolean)

const CODEPAGES = ['cp1250', 'cp1251', 'cp1252', 'cp1253', 'cp1254', 'cp1257', 'cp437', 'cp850', 'cp852', 'cp857', 'cp866']

interface Form {
  name: string
  org_name: string
  local: { code: string; name: string; symbol: string; gen_pl: string; prep_pl: string }
  report: { code: string; name: string; enabled: boolean }
  ansi: string
  oem: string
  denom: { before: string; divisor: number }[]
  plan: { template: string; mode: string; sep: string; root_len: number; widths: string; balance: string; pnl: string; noncarried: string }
  notes: CarryNote[]
}

function toForm(name: string, org: string, p: DatasetParams): Form {
  const pl = p.plan_profile
  return {
    name,
    org_name: org,
    local: { code: p.currency_local?.code ?? '', name: p.currency_local?.name ?? '', symbol: p.currency_local?.symbol ?? '', gen_pl: p.currency_local?.gen_pl ?? '', prep_pl: p.currency_local?.prep_pl ?? '' },
    report: { code: p.currency_report?.code ?? '', name: p.currency_report?.name ?? '', enabled: !!p.currency_report?.enabled },
    ansi: p.charset?.ansi ?? 'cp1254',
    oem: p.charset?.oem ?? 'cp857',
    denom: (p.denomination ?? []).map((d) => ({ ...d })),
    plan: {
      template: pl?.template ?? 'none',
      mode: pl?.mode ?? 'sep',
      sep: pl?.sep ?? '.',
      root_len: pl?.root_len ?? 3,
      widths: (pl?.widths ?? []).join(', '),
      balance: (pl?.balance_roots ?? []).join(', '),
      pnl: (pl?.pnl_roots ?? []).join(', '),
      noncarried: (pl?.noncarried ?? []).join(', '),
    },
    notes: (p.carry_notes ?? []).map((n) => ({ ...n })),
  }
}

function planOf(f: Form['plan']): PlanProfile {
  return {
    template: f.template,
    mode: f.mode,
    sep: f.sep,
    root_len: f.root_len,
    widths: list(f.widths).map(Number).filter((n) => n > 0),
    balance_roots: list(f.balance),
    pnl_roots: list(f.pnl),
    noncarried: list(f.noncarried),
  }
}

export default function ParamsTab() {
  const { data: ds } = useDataset()
  const [f, setF] = useState<Form | null>(null)
  const [orig, setOrig] = useState<string>('')
  const [err, setErr] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [detected, setDetected] = useState<PlanDetect | null>(null)
  useEffect(() => {
    if (ds && !f) {
      const x = toForm(ds.name, ds.org_name, ds.params ?? {})
      setF(x)
      setOrig(JSON.stringify(x))
    }
  }, [ds, f])
  if (!f || !ds) return <div className="muted pad">Загрузка…</div>
  const set = (p: Partial<Form>) => setF({ ...f, ...p })
  const o: Form = JSON.parse(orig)
  const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

  const detect = async () => {
    try {
      setErr(null)
      const r = await api.detectPlanProfile()
      const x = toForm(f.name, f.org_name, { plan_profile: r.profile })
      set({ plan: x.plan })
      setDetected(r)
      setInfo(
        r.differs === false
          ? 'Профиль плана счетов по загруженным данным совпадает с текущим.'
          : 'Профиль плана счетов подставлен по загруженным планам счетов. Проверьте поля и нажмите «Сохранить параметры».',
      )
    } catch (e) {
      const m = errMsg(e)
      setErr(/ещё не загружены/.test(m) ? m + ' Загрузка начинается после сохранения книг на вкладке «Книги и годы».' : m)
    }
  }

  const save = async () => {
    const planChanged = !same(f.plan, o.plan)
    const charsetChanged = f.ansi !== o.ansi || f.oem !== o.oem
    let ask = ''
    if (planChanged) ask += 'Смена профиля плана счетов приведёт к повторному чтению планов счетов всех лет (проводки не затрагиваются).\n'
    if (charsetChanged) ask += 'Смена кодировки текстов приведёт к повторному чтению названий из Logo.\n'
    if (ask && !(await askConfirm(ask + '\nСохранить параметры?', { okLabel: 'Сохранить' }))) return
    setBusy(true)
    try {
      setErr(null)
      const body: DatasetParams = {}
      if (!same(f.local, o.local)) body.currency_local = f.local
      if (!same(f.report, o.report)) body.currency_report = { ...f.report }
      if (charsetChanged) body.charset = { ansi: f.ansi, oem: f.oem }
      if (!same(f.denom, o.denom)) body.denomination = f.denom
      if (planChanged) body.plan_profile = planOf(f.plan)
      if (!same(f.notes, o.notes)) body.carry_notes = f.notes.filter((n) => n.text.trim() || n.denom_suffix)
      let msg = ''
      if (f.name !== o.name || f.org_name !== o.org_name) await api.saveDataset(ds.id, { name: f.name, org_name: f.org_name })
      if (Object.keys(body).length) {
        const r = await api.saveParams(body)
        if (r.resync_required) msg += ' Названия будут перечитаны из Logo при ближайшей проверке данных.'
        if (r.plan_rebuild) msg += ' Планы счетов перечитываются в фоне.'
      }
      setInfo('Параметры сохранены.' + msg)
      resetYears()
      await reloadDataset()
      const x = { ...f }
      setOrig(JSON.stringify(x))
    } catch (e) {
      setErr(errMsg(e))
    } finally {
      setBusy(false)
    }
  }
  const dirty = JSON.stringify(f) !== orig
  const loc = f.local
  const example = `Подпись графы: «Сумма, ${loc.code || '…'}». ` + (loc.gen_pl ? `В тексте о деноминации: «суммы за годы до ${f.denom[0] ? new Date(f.denom[0].before).getFullYear() : '…'} г. указаны в старых ${loc.gen_pl}».` : 'Формы названия валюты не заданы — в текстах будет использовано только название.')

  return (
    <div className="form params-form">
      <ErrorBox error={err} onClose={() => setErr(null)} />
      {info && <div className="msg ok">{info}</div>}

      <h3>Набор данных</h3>
      <div className="row2">
        <label>
          Название набора (виден в шапке программы)
          <input type="text" value={f.name} onChange={(e) => set({ name: e.target.value })} />
        </label>
        <label>
          Название организации (подставляется в заголовки отчётов; пусто — из Logo)
          <input type="text" value={f.org_name} onChange={(e) => set({ org_name: e.target.value })} />
        </label>
      </div>

      <h3>Валюты</h3>
      <div className="row2">
        <label>
          Валюта учёта: код
          <input type="text" value={loc.code} onChange={(e) => set({ local: { ...loc, code: e.target.value.toUpperCase() } })} />
        </label>
        <label>
          название
          <input type="text" value={loc.name} onChange={(e) => set({ local: { ...loc, name: e.target.value } })} />
        </label>
        <label>
          символ
          <input type="text" value={loc.symbol} onChange={(e) => set({ local: { ...loc, symbol: e.target.value } })} />
        </label>
      </div>
      <div className="row2">
        <label>
          множественное число («много чего?»)
          <input type="text" value={loc.gen_pl} placeholder="манатов" onChange={(e) => set({ local: { ...loc, gen_pl: e.target.value } })} />
        </label>
        <label>
          «в чём?» во множественном числе
          <input type="text" value={loc.prep_pl} placeholder="манатах" onChange={(e) => set({ local: { ...loc, prep_pl: e.target.value } })} />
        </label>
      </div>
      <p className="hint">{example}</p>
      <div className="row2">
        <label>
          Отчётная валюта: код
          <input type="text" value={f.report.code} onChange={(e) => set({ report: { ...f.report, code: e.target.value.toUpperCase() } })} />
        </label>
        <label>
          название
          <input type="text" value={f.report.name} onChange={(e) => set({ report: { ...f.report, name: e.target.value } })} />
        </label>
      </div>
      <label className="chk">
        <input type="checkbox" checked={f.report.enabled} onChange={(e) => set({ report: { ...f.report, enabled: e.target.checked } })} />В базе есть суммы в отчётной валюте (если выключить, графы в этой валюте в отчётах недоступны)
      </label>

      <h3>Кодировка текстов</h3>
      <div className="row2">
        <label>
          Основная
          <select value={f.ansi} onChange={(e) => set({ ansi: e.target.value })}>
            {[...new Set([f.ansi, ...CODEPAGES])].map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </label>
        <label>
          Старая (для книг, где в названиях «абракадабра»)
          <select value={f.oem} onChange={(e) => set({ oem: e.target.value })}>
            {[...new Set([f.oem, ...CODEPAGES])].map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </label>
      </div>
      {(f.ansi !== o.ansi || f.oem !== o.oem) && <div className="msg warn">Изменение кодировки потребует заново прочитать названия из Logo.</div>}
      <p className="hint">Какие книги читаются в старой кодировке, отмечается на вкладке «Книги и годы» (колонка «Стар. код.»).</p>

      <h3>Деноминация</h3>
      <table className="simple" style={{ maxWidth: 520 }}>
        <thead>
          <tr>
            <th>Для дат до</th>
            <th>Делитель сумм</th>
            <th style={{ width: 40 }} />
          </tr>
        </thead>
        <tbody>
          {f.denom.map((d, i) => (
            <tr key={i}>
              <td>
                <input type="date" value={d.before} onChange={(e) => set({ denom: f.denom.map((x, k) => (k === i ? { ...x, before: e.target.value } : x)) })} />
              </td>
              <td>
                <input type="number" min={1} value={d.divisor} onChange={(e) => set({ denom: f.denom.map((x, k) => (k === i ? { ...x, divisor: Number(e.target.value) } : x)) })} />
              </td>
              <td>
                <button className="btn small ghost" onClick={() => set({ denom: f.denom.filter((_, k) => k !== i) })}>
                  ✕
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="toolbar">
        <button className="btn" onClick={() => set({ denom: [...f.denom, { before: '', divisor: 1000 }] })}>
          + Строка
        </button>
        <span className="hint">Деноминация — когда валюту «обнулили» (например, 1 новый = 5000 старых). Делитель каждой книги — на вкладке «Книги и годы».</span>
      </div>

      <h3>План счетов</h3>
      <div className="row2">
        <label>
          Шаблон
          <select value={f.plan.template} onChange={(e) => set({ plan: { ...f.plan, template: e.target.value } })}>
            <option value="turkish">Турецкий план (как в Logo Tiger)</option>
            <option value="none">Свой план (без шаблона)</option>
          </select>
        </label>
        <label>
          Как устроены коды счетов
          <select value={f.plan.mode} onChange={(e) => set({ plan: { ...f.plan, mode: e.target.value } })}>
            <option value="sep">Уровни разделены знаком (например 100.01.02)</option>
            <option value="width">Уровни заданы длиной (например 1001002)</option>
          </select>
        </label>
      </div>
      <div className="row2">
        {f.plan.mode === 'sep' ? (
          <label>
            Знак-разделитель уровней
            <input type="text" maxLength={2} value={f.plan.sep} onChange={(e) => set({ plan: { ...f.plan, sep: e.target.value } })} />
          </label>
        ) : (
          <label>
            Длины уровней через запятую
            <input type="text" value={f.plan.widths} placeholder="3, 2, 2" onChange={(e) => set({ plan: { ...f.plan, widths: e.target.value } })} />
          </label>
        )}
        <label>
          Длина корневого счёта
          <input type="number" min={1} value={f.plan.root_len} onChange={(e) => set({ plan: { ...f.plan, root_len: Number(e.target.value) } })} />
        </label>
      </div>
      <div className="row2">
        <label>
          Корни счетов баланса
          <input type="text" value={f.plan.balance} onChange={(e) => set({ plan: { ...f.plan, balance: e.target.value } })} />
        </label>
        <label>
          Корни счетов прибылей и убытков
          <input type="text" value={f.plan.pnl} onChange={(e) => set({ plan: { ...f.plan, pnl: e.target.value } })} />
        </label>
        <label>
          Счета, остатки которых не переносятся
          <input type="text" value={f.plan.noncarried} onChange={(e) => set({ plan: { ...f.plan, noncarried: e.target.value } })} />
        </label>
      </div>
      <div className="toolbar">
        <button className="btn" onClick={detect}>
          Определить профиль плана счетов
        </button>
        <span className="hint">Профиль определяется по уже загруженным планам счетов основных книг. Смена профиля заставит программу заново прочитать планы счетов (проводки не трогаются).</span>
      </div>
      {detected && (
        <div className={'msg ' + (detected.discrepancies?.length ? 'warn' : 'ok')} style={{ display: 'block' }}>
          Проверено счетов: {detected.checked?.accounts ?? 0}, книг: {detected.checked?.books ?? 0}.
          {detected.discrepancies && detected.discrepancies.length > 0 && (
            <ul style={{ margin: '4px 0 0', paddingLeft: 20 }}>
              {detected.discrepancies.map((d, i) => (
                <li key={i}>{d}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      <h3>Закрывающие ордера</h3>
      <p className="hint">
        Правила закрывающих ордеров настраиваются на отдельной вкладке.{' '}
        <button className="btn small" onClick={() => navigate('/settings/closing')}>
          Открыть «Закрывающие ордера»
        </button>
      </p>

      <h3>Известные разрывы переноса остатков</h3>
      <p className="hint">Пометки показываются в отчёте «Разрывы переноса остатков»: между какими годами расхождение ожидаемо и почему.</p>
      <table className="simple">
        <thead>
          <tr>
            <th style={{ width: 90 }}>С года</th>
            <th style={{ width: 90 }}>На год</th>
            <th>Пояснение</th>
            <th style={{ width: 40 }} />
          </tr>
        </thead>
        <tbody>
          {f.notes.map((n, i) => {
            const up = (p: Partial<CarryNote>) => set({ notes: f.notes.map((x, k) => (k === i ? { ...x, ...p } : x)) })
            const num = (v: string) => (v ? Number(v) : null)
            return (
              <tr key={i}>
                <td>
                  <input type="number" value={n.from_label ?? ''} onChange={(e) => up({ from_label: num(e.target.value) })} />
                </td>
                <td>
                  <input type="number" value={n.to_label ?? ''} onChange={(e) => up({ to_label: num(e.target.value) })} />
                </td>
                <td>
                  <input type="text" value={n.text} onChange={(e) => up({ text: e.target.value })} />
                  {n.denom_suffix && <div className="muted small">Дополнение к тексту о деноминации: {n.denom_suffix}</div>}
                </td>
                <td>
                  <button className="btn small ghost" onClick={() => set({ notes: f.notes.filter((_, k) => k !== i) })}>
                    ✕
                  </button>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <div className="toolbar">
        <button className="btn" onClick={() => set({ notes: [...f.notes, { from_label: null, to_label: null, text: '' }] })}>
          + Пометка
        </button>
      </div>

      <div className="toolbar" style={{ borderTop: '1px solid var(--line)', marginTop: 12 }}>
        <button className="btn primary" onClick={save} disabled={busy || !dirty}>
          {busy ? 'Сохраняем…' : 'Сохранить параметры'}
        </button>
        {dirty && <span className="tag">есть несохранённые изменения</span>}
      </div>
    </div>
  )
}
