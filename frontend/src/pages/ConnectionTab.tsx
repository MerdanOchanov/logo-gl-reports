import { askConfirm } from '../components/confirm'
import { useEffect, useState } from 'react'
import { api, errMsg, type ConnTest, type Connection } from '../api'
import { ErrorBox, useLoad } from '../components/ui'
import { fmtDateTime } from '../format'
import { reloadDataset, useDataset } from '../dataset'
import { resetYears } from '../years'

export default function ConnectionTab() {
  const { data, error, loading, reload } = useLoad<Connection[]>(() => api.connections(), [])
  const { data: ds } = useDataset()
  const [sel, setSel] = useState<number | 'new' | null>(null)
  const [drivers, setDrivers] = useState<{ drivers: string[]; auto: string } | null>(null)
  useEffect(() => {
    api
      .connectionDrivers()
      .then(setDrivers)
      .catch(() => setDrivers(null))
  }, [])
  const list = data ?? []
  const curId = sel ?? ds?.connection?.id ?? list[0]?.id ?? null
  const conn = sel === 'new' ? null : list.find((c) => c.id === curId) ?? list[0] ?? null
  return (
    <>
      <p className="hint">
        Здесь задаётся, откуда программа читает данные: сервер SQL с базой Logo. Программа только читает данные — ничего в Logo не записывает. Подключений может быть несколько: каждому набору данных назначается своё (или общее — тогда
        это одна база Logo, из которой взяты разные фирмы).
      </p>
      <ErrorBox error={error} />
      {loading && !data && <div className="muted pad">Загрузка…</div>}
      {data && (
        <div className="toolbar">
          <label className="inline">
            Подключение
            <select style={{ width: 280 }} value={sel === 'new' ? 'new' : conn?.id ?? ''} onChange={(e) => setSel(e.target.value === 'new' ? 'new' : Number(e.target.value))}>
              {list.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} — {c.server} / {c.database}
                </option>
              ))}
              {sel === 'new' && <option value="new">Новое подключение</option>}
            </select>
          </label>
          <button className="btn" disabled={sel === 'new'} onClick={() => setSel('new')}>
            + Добавить подключение
          </button>
        </div>
      )}
      {data && (
        <ConnForm
          key={conn?.id ?? 'new'}
          conn={conn}
          drivers={drivers}
          onSaved={(id) => {
            if (id != null) setSel(id)
            reload()
          }}
          onDeleted={() => {
            setSel(null)
            reload()
          }}
        />
      )}
    </>
  )
}

function ConnForm({ conn, drivers, onSaved, onDeleted }: { conn: Connection | null; drivers: { drivers: string[]; auto: string } | null; onSaved: (id?: number) => void; onDeleted: () => void }) {
  const envFields = conn?.env_fields ?? []
  const fromConfig = conn?.origin === 'config'
  const [f, setF] = useState({
    name: conn?.name ?? 'Основное подключение',
    server: conn?.server ?? '',
    database: conn?.database ?? '',
    login: conn?.login ?? '',
    password: '',
    driver: conn?.driver ?? '',
    trust_cert: conn?.trust_cert ?? true,
  })
  const [dbs, setDbs] = useState<string[] | null>(null)
  const [test, setTest] = useState<ConnTest | null>(null)
  const [testErr, setTestErr] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const set = (p: Partial<typeof f>) => {
    setF((x) => ({ ...x, ...p }))
    setTest(null)
    setTestErr(null)
  }
  const locked = (k: string) => envFields.includes(k)
  const body = () => ({
    name: f.name,
    server: f.server,
    database: f.database,
    login: f.login,
    password: f.password === '' ? undefined : f.password,
    driver: f.driver,
    trust_cert: f.trust_cert,
  })
  const run = async (what: string, fn: () => Promise<void>) => {
    setBusy(what)
    setErr(null)
    try {
      await fn()
    } catch (e) {
      setErr(errMsg(e))
    } finally {
      setBusy(null)
    }
  }
  const doTest = () =>
    run('test', async () => {
      setTest(null)
      setTestErr(null)
      try {
        setTest(await api.testConnection(conn ? { id: conn.id, ...body() } : body()))
      } catch (e) {
        setTestErr(errMsg(e))
      }
    })
  const showDbs = () =>
    run('dbs', async () => {
      // для сохранённого подключения без нового пароля сервер возьмёт сохранённый
      setDbs(await api.connectionDatabases({ id: conn?.id, server: f.server, login: f.login, password: f.password === '' ? undefined : f.password, driver: f.driver }))
    })
  const save = () =>
    run('save', async () => {
      let id = conn?.id
      if (conn) await api.saveConnection(conn.id, body())
      else id = (await api.createConnection(body())).id
      setInfo('Подключение сохранено.')
      setF((x) => ({ ...x, password: '' }))
      resetYears()
      reloadDataset()
      onSaved(id)
    })
  const remove = () =>
    run('del', async () => {
      if (!conn || !(await askConfirm(`Удалить подключение «${conn.name}»? Удалить можно только подключение, которое не используется ни одним набором данных.`, { okLabel: 'Удалить', danger: true }))) return
      await api.deleteConnection(conn.id)
      onDeleted()
    })

  return (
    <div className="form conn-form">
      {fromConfig && (
        <div className="msg warn">
          <span>
            Подключение создано из файла настроек программы (config.ini). Менять его можно здесь.
            {envFields.length > 0 && ' Поля, помеченные «задано в окружении», здесь менять нельзя — их меняет администратор сервера в переменных окружения и перезапускает программу.'}
          </span>
        </div>
      )}
      {conn?.warnings?.map((w, i) => (
        <div key={i} className="msg warn">
          {w}
        </div>
      ))}
      {conn?.password_plain && <div className="msg warn">Пароль подключения хранится без шифрования. Сохраните подключение заново — программа попробует зашифровать его.</div>}
      {conn?.last_error && <div className="msg err">Последняя попытка подключения не удалась: {conn.last_error}</div>}
      <ErrorBox error={err} onClose={() => setErr(null)} />
      {info && <div className="msg ok">{info}</div>}

      <div className="conn-grid">
        <Field label="Название подключения">
          <input type="text" value={f.name} onChange={(e) => set({ name: e.target.value })} />
        </Field>
        <Field label="Сервер" locked={locked('server')}>
          <input type="text" value={f.server} disabled={locked('server')} placeholder="например: 192.168.1.10 или SERVER\LOGO" onChange={(e) => set({ server: e.target.value })} />
        </Field>
        <Field label="База Logo" locked={locked('database')}>
          <div className="row2">
            {dbs ? (
              <select value={f.database} disabled={locked('database')} onChange={(e) => set({ database: e.target.value })}>
                {!dbs.includes(f.database) && <option value={f.database}>{f.database || '— выберите —'}</option>}
                {dbs.map((d) => (
                  <option key={d}>{d}</option>
                ))}
              </select>
            ) : (
              <input type="text" value={f.database} disabled={locked('database')} placeholder="например: LOGO_DB" onChange={(e) => set({ database: e.target.value })} />
            )}
            <button className="btn" style={{ flex: 'none', alignSelf: 'flex-end', marginBottom: 6 }} disabled={!f.server || !f.login || (!f.password && !conn?.has_password) || busy !== null} title={f.password || conn?.has_password ? '' : 'Чтобы получить список баз, введите пароль'} onClick={showDbs}>
              {busy === 'dbs' ? 'Ищем…' : 'Показать базы'}
            </button>
          </div>
        </Field>
        <Field label="Логин" locked={locked('login')}>
          <input type="text" value={f.login} disabled={locked('login')} autoComplete="off" onChange={(e) => set({ login: e.target.value })} />
        </Field>
        <Field label="Пароль" locked={locked('password')}>
          <input
            type="password"
            value={f.password}
            disabled={locked('password')}
            autoComplete="new-password"
            placeholder={conn?.has_password ? 'Сохранён. Оставьте пустым, чтобы не менять' : 'Введите пароль'}
            onChange={(e) => set({ password: e.target.value })}
          />
        </Field>
        <Field label="Драйвер подключения" locked={locked('driver')}>
          <select value={f.driver} disabled={locked('driver')} onChange={(e) => set({ driver: e.target.value })}>
            <option value="">Подобрать автоматически{drivers?.auto ? `: ${drivers.auto}` : drivers ? ' (подходящий драйвер не найден)' : ''}</option>
            {f.driver && !drivers?.drivers.includes(f.driver) && <option value={f.driver}>{f.driver}</option>}
            {drivers?.drivers.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <label className="chk">
        <input type="checkbox" checked={f.trust_cert} onChange={(e) => set({ trust_cert: e.target.checked })} />
        Доверять сертификату сервера (нужно для серверов без доверенного сертификата)
      </label>

      <div className="toolbar">
        <button className="btn" onClick={doTest} disabled={busy !== null || !f.server || !f.database || !f.login}>
          {busy === 'test' ? 'Проверяем…' : 'Проверить'}
        </button>
        <button className="btn primary" onClick={save} disabled={busy !== null || !f.server || !f.database || !f.login}>
          Сохранить
        </button>
        {conn && (
          <button className="btn danger" onClick={remove} disabled={busy !== null}>
            Удалить
          </button>
        )}
        {conn?.last_ok_at && <span className="muted">последнее успешное подключение: {fmtDateTime(conn.last_ok_at)}</span>}
      </div>
      {!f.password && !conn?.has_password && <p className="hint">Список баз сервера можно получить, введя пароль в поле выше.</p>}
      {!test && !testErr && <p className="hint">Перед сохранением нажмите «Проверить»: программа подключится к серверу и сообщит, найдена ли в базе Logo. Если пароль не введён, используется сохранённый.</p>}

      {test && (
        <div className="msg ok" style={{ display: 'block' }}>
          Подключение работает: {test.server_version ?? 'сервер ответил'}
          {test.database ? `, база ${test.database}` : ''}
          {test.logo ? `, фирм ${test.logo.firms}, периодов ${test.logo.periods}` : ''}.
          {test.collation && <div className="hint">Кодировка сервера: {test.collation}{test.ansi_cp ? ` (${test.ansi_cp})` : ''}.</div>}
        </div>
      )}
      {test?.warnings?.map((w, i) => (
        <div key={i} className="msg warn">
          {w}
        </div>
      ))}
      {testErr && <div className="msg err">{testErr}</div>}
    </div>
  )
}

function Field({ label, locked, children }: { label: string; locked?: boolean; children: React.ReactNode }) {
  return (
    <label>
      <span>
        {label}
        {locked && <span className="tag"> задано в окружении</span>}
      </span>
      {children}
    </label>
  )
}
