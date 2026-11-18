import { useEffect, useState } from 'react'
import { api, errMsg, type AccountName, type ClosingRule, type Firm } from '../api'
import { ErrorBox, useLoad } from '../components/ui'
import { askConfirm } from '../components/confirm'
import { fmtDateTime } from '../format'
import { Templates, Users } from './SettingsExtra'
import AccountLinks from './AccountLinks'
import { navigate, useRoute } from '../router'
import ConnectionTab from './ConnectionTab'
import BooksTab from './BooksTab'
import ParamsTab from './ParamsTab'
import DatasetsTab from './DatasetsTab'
import TransferTab from './TransferTab'
import { useSession } from '../dataset'
import { useCanEdit, useIsAdmin } from '../components/role'

const TABS = [
  { k: 'datasets', label: 'Наборы данных' },
  { k: 'connection', label: 'Подключение к Logo' },
  { k: 'books', label: 'Книги и годы' },
  { k: 'params', label: 'Параметры набора' },
  { k: 'firms', label: 'Фирмы и синхронизация' },
  { k: 'closing', label: 'Закрывающие ордера' },
  { k: 'names', label: 'Названия счетов' },
  { k: 'templates', label: 'Шаблоны Excel' },
  { k: 'links', label: 'Связи счетов между годами' },
  { k: 'transfer', label: 'Перенос настроек' },
  { k: 'users', label: 'Пользователи' },
]

const ADMIN_TABS = ['connection', 'books', 'params', 'firms', 'closing', 'names', 'users']
/** Вкладки, доступные только администратору всей установки (подключения, наборы данных, пользователи). */
const GLOBAL_TABS = ['datasets', 'connection', 'users']

export default function SettingsPage() {
  const isAdmin = useIsAdmin()
  const canEdit = useCanEdit()
  const { globalAdmin } = useSession()
  const tabs = TABS.filter((t) => (!GLOBAL_TABS.includes(t.k) || globalAdmin) && (isAdmin || globalAdmin || (canEdit && !ADMIN_TABS.includes(t.k))))
  const route = useRoute()
  const fromRoute = /^\/settings\/(\w+)/.exec(route)?.[1]
  const [tab, setTabState] = useState(fromRoute && TABS.some((t) => t.k === fromRoute) ? fromRoute : isAdmin ? 'books' : globalAdmin ? 'datasets' : 'templates')
  // переход по ссылке вида #/settings/books открывает нужную вкладку
  useEffect(() => {
    if (fromRoute && TABS.some((t) => t.k === fromRoute)) setTabState(fromRoute)
  }, [fromRoute])
  const setTab = (k: string) => {
    setTabState(k)
    navigate('/settings/' + k, true)
  }
  const [seedMsg, setSeedMsg] = useState<string | null>(null)
  const [seedErr, setSeedErr] = useState<string | null>(null)
  const seed = async () => {
    try {
      const r = await api.demoSeed()
      setSeedErr(null)
      setSeedMsg(r.created && r.created.length ? `Создано: ${r.created.join('; ')}. Примеры лежат в папке «Примеры» раздела «Отчёты».` : 'Примеры уже были созданы раньше — ничего нового не добавлено.')
    } catch (e) {
      setSeedMsg(null)
      setSeedErr(errMsg(e))
    }
  }
  return (
    <div className="page">
      <div className="page-head">
        <h2>Настройки</h2>
        <div className="spacer" />
        <button className="btn needs-admin" disabled={!isAdmin} onClick={seed} title="Добавить образцы отчётов и классификатор, чтобы было с чего начать">
          Создать примеры
        </button>
      </div>
      <ErrorBox error={seedErr} onClose={() => setSeedErr(null)} />
      {seedMsg && (
        <div className="msg ok">
          <span>{seedMsg}</span>
          <button className="btn small" onClick={() => navigate('/reports')}>
            Открыть отчёты
          </button>
        </div>
      )}
      <div className="tabs">
        {tabs.length === 0 && <div className="msg warn">Раздел «Настройки» доступен редактору и администратору.</div>}
        {tabs.map((t) => (
          <button key={t.k} className={tab === t.k ? 'active' : ''} onClick={() => setTab(t.k)}>
            {t.label}
          </button>
        ))}
      </div>
      {tabs.length > 0 && tab === 'datasets' && globalAdmin && <DatasetsTab />}
      {tabs.length > 0 && tab === 'connection' && globalAdmin && <ConnectionTab />}
      {tabs.length > 0 && tab === 'books' && isAdmin && <BooksTab />}
      {tabs.length > 0 && tab === 'params' && isAdmin && <ParamsTab />}
      {tabs.length > 0 && tab === 'firms' && <Firms />}
      {tabs.length > 0 && tab === 'closing' && <Closing />}
      {tabs.length > 0 && tab === 'names' && <Names />}
      {tabs.length > 0 && tab === 'templates' && <Templates />}
      {tabs.length > 0 && tab === 'links' && <AccountLinks />}
      {tabs.length > 0 && tab === 'transfer' && <TransferTab />}
      {tabs.length > 0 && tab === 'users' && globalAdmin && <Users />}
    </div>
  )
}

function Firms() {
  const { data, error, loading, reload } = useLoad<Firm[]>(() => api.firms(), [])
  const [err, setErr] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)
  const act = async (f: () => Promise<unknown>, msg?: string) => {
    try {
      await f()
      if (msg) setInfo(msg)
      reload()
    } catch (e) {
      setErr(errMsg(e))
    }
  }
  // пока идёт синхронизация — обновляем список
  useEffect(() => {
    const t = setInterval(() => reload(), 5000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  return (
    <>
      <div className="toolbar">
        <button className="btn primary" onClick={() => act(() => api.sync(), 'Синхронизация запущена. Ход виден вверху справа.')}>
          Синхронизировать включённые
        </button>
        <button
          className="btn danger"
          onClick={() =>
            askConfirm('Перезагрузить ВСЕ включённые годы из Logo заново? Это может занять много времени.', { okLabel: 'Перезагрузить', danger: true }).then(
              (ok) => { if (ok) void act(() => api.sync(undefined, true), 'Полная пересинхронизация запущена.') },
            )
          }
        >
          Пересинхронизировать всё принудительно
        </button>
      </div>
      <p className="hint">Изменения в Logo подхватываются автоматически перед расчётом отчёта; кнопки нужны для первой загрузки и для принудительного обновления.</p>
      <ErrorBox error={error || err} onClose={() => setErr(null)} />
      {info && <div className="msg ok">{info}</div>}
      {loading && !data && <div className="muted pad">Загрузка…</div>}
      {data && (
        <table className="simple">
          <thead>
            <tr>
              <th style={{ width: 60 }}>Вкл.</th>
              <th style={{ width: 80 }}>Фирма</th>
              <th style={{ width: 70 }}>Год</th>
              <th>Название</th>
              <th style={{ width: 160 }}>Загружено</th>
              <th className="r" style={{ width: 110 }}>
                Проводок
              </th>
              <th style={{ width: 230 }} />
            </tr>
          </thead>
          <tbody>
            {data.map((f) => (
              <tr key={f.firm} className={f.enabled ? '' : 'dim'}>
                <td>
                  <input type="checkbox" checked={!!f.enabled} onChange={(e) => act(() => api.setFirm(f.firm, e.target.checked))} />
                </td>
                <td>{f.firm}</td>
                <td>{f.year}</td>
                <td>{f.name}</td>
                <td className="nowrap">{f.synced_at ? fmtDateTime(f.synced_at) : <span className="muted">не загружено</span>}</td>
                <td className="num">{f.line_count?.toLocaleString('ru-RU')}</td>
                <td className="actions">
                  <button className="btn small" disabled={!f.enabled} onClick={() => act(() => api.sync([f.firm]), `Синхронизация фирмы ${f.firm} запущена.`)}>
                    Обновить
                  </button>
                  <button className="btn small" disabled={!f.enabled} onClick={() => act(() => api.sync([f.firm], true), `Принудительная загрузка фирмы ${f.firm} запущена.`)}>
                    Перезагрузить
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  )
}

function Closing() {
  const { data, error, loading } = useLoad<ClosingRule[]>(() => api.closingRules(), [])
  const [rules, setRules] = useState<ClosingRule[]>([])
  const [err, setErr] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)
  useEffect(() => { if (data) setRules(data) }, [data])
  const upd = (i: number, p: Partial<ClosingRule>) => setRules((r) => r.map((x, k) => (k === i ? { ...x, ...p } : x)))
  const save = async () => {
    try {
      await api.saveClosingRules(rules)
      setInfo('Правила сохранены. Закрывающие ордера пересчитаны.')
    } catch (e) {
      setErr(errMsg(e))
    }
  }
  return (
    <>
      <p className="hint">
        Закрывающие ордера — ордера закрытия года (списание 7xx → 6xx, 691 → 590 и т. п.). Отчёт можно строить «с ними» и «без них». Ордер считается закрывающим, если подходит под любое включённое правило.
      </p>
      <ErrorBox error={error || err} onClose={() => setErr(null)} />
      {info && <div className="msg ok">{info}</div>}
      {loading && <div className="muted pad">Загрузка…</div>}
      <table className="simple">
        <thead>
          <tr>
            <th style={{ width: 60 }}>Вкл.</th>
            <th style={{ width: 220 }}>Вид правила</th>
            <th>Значение</th>
            <th style={{ width: 40 }} />
          </tr>
        </thead>
        <tbody>
          {rules.map((r, i) => (
            <tr key={i}>
              <td>
                <input type="checkbox" checked={!!r.enabled} onChange={(e) => upd(i, { enabled: e.target.checked })} />
              </td>
              <td>
                <select value={r.kind} onChange={(e) => upd(i, { kind: e.target.value as ClosingRule['kind'] })}>
                  <option value="text">Текст в содержании ордера</option>
                  <option value="accounts">Счета: ордер целиком на этих счетах</option>
                </select>
              </td>
              <td>
                <input
                  type="text"
                  value={r.value}
                  placeholder={r.kind === 'text' ? 'например: PEYDA-ZYYAN' : 'маски через запятую: 570*,590*,690*,691*'}
                  onChange={(e) => upd(i, { value: e.target.value })}
                />
              </td>
              <td>
                <button className="btn small ghost" onClick={() => setRules((l) => l.filter((_, k) => k !== i))}>
                  ✕
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="toolbar">
        <button className="btn" onClick={() => setRules((l) => [...l, { kind: 'text', value: '', enabled: true }])}>
          + Правило
        </button>
        <button className="btn primary" onClick={save}>
          Сохранить
        </button>
      </div>
    </>
  )
}

function Names() {
  const { data, error, loading } = useLoad<AccountName[]>(() => api.accountNames(), [])
  const [rows, setRows] = useState<AccountName[]>([])
  const [err, setErr] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)
  useEffect(() => { if (data) setRows(data) }, [data])
  const upd = (i: number, p: Partial<AccountName>) => setRows((r) => r.map((x, k) => (k === i ? { ...x, ...p } : x)))
  const save = async () => {
    try {
      await api.saveAccountNames(rows.filter((r) => r.code && r.name))
      setInfo('Названия сохранены.')
    } catch (e) {
      setErr(errMsg(e))
    }
  }
  const num = (v: string) => (v ? Number(v) : null)
  return (
    <>
      <p className="hint">Переопределяет название счёта в отчётах (например, вместо турецкого — русское). Годы пустые — действует всегда.</p>
      <ErrorBox error={error || err} onClose={() => setErr(null)} />
      {info && <div className="msg ok">{info}</div>}
      {loading && <div className="muted pad">Загрузка…</div>}
      <table className="simple">
        <thead>
          <tr>
            <th style={{ width: 180 }}>Код счёта</th>
            <th style={{ width: 90 }}>Год с</th>
            <th style={{ width: 90 }}>Год по</th>
            <th>Название</th>
            <th style={{ width: 40 }} />
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td>
                <input type="text" value={r.code} onChange={(e) => upd(i, { code: e.target.value })} />
              </td>
              <td>
                <input type="number" value={r.year_from ?? ''} onChange={(e) => upd(i, { year_from: num(e.target.value) })} />
              </td>
              <td>
                <input type="number" value={r.year_to ?? ''} onChange={(e) => upd(i, { year_to: num(e.target.value) })} />
              </td>
              <td>
                <input type="text" value={r.name} onChange={(e) => upd(i, { name: e.target.value })} />
              </td>
              <td>
                <button className="btn small ghost" onClick={() => setRows((l) => l.filter((_, k) => k !== i))}>
                  ✕
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="toolbar">
        <button className="btn" onClick={() => setRows((l) => [...l, { code: '', year_from: null, year_to: null, name: '' }])}>
          + Название
        </button>
        <button className="btn primary" onClick={save}>
          Сохранить
        </button>
      </div>
    </>
  )
}
