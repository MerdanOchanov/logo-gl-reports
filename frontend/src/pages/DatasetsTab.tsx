import { askConfirm } from '../components/confirm'
import { useEffect, useState } from 'react'
import { api, errMsg, type Connection, type DatasetInfo } from '../api'
import { ErrorBox, Modal, useLoad } from '../components/ui'
import { fmtDateTime } from '../format'
import { useSession } from '../dataset'
import { navigate } from '../router'

/** Части настроек, которые можно перенести из другого набора или в файл. */
export const SETTING_PARTS: { k: string; label: string }[] = [
  { k: 'reports', label: 'Отчёты' },
  { k: 'classifiers', label: 'Классификаторы' },
  { k: 'names', label: 'Названия счетов' },
  { k: 'closing', label: 'Закрывающие ордера' },
  { k: 'links', label: 'Связи счетов между годами' },
  { k: 'params', label: 'Параметры набора (валюта, кодировка, план счетов)' },
  { k: 'templates', label: 'Шаблоны Excel' },
]

function stateOf(d: DatasetInfo): { text: string; cls: string } {
  if (d.archived) return { text: 'в архиве', cls: 'gray' }
  if (d.queued) return { text: 'в очереди на загрузку', cls: '' }
  if (d.syncing) {
    const p = d.progress
    return { text: 'синхронизация' + (p?.total ? `: ${p.done ?? 0} из ${p.total}` : '…'), cls: '' }
  }
  if (d.state === 'new' || d.books_total === 0) return { text: 'не настроен', cls: 'red' }
  if (!d.logo_ok) return { text: 'нет связи с Logo', cls: 'red' }
  return { text: 'готов', cls: 'green' }
}

export default function DatasetsTab() {
  const { data, error, loading, reload } = useLoad<DatasetInfo[]>(() => api.datasets(), [])
  const { refreshMe, selectDataset, currentId } = useSession()
  const [err, setErr] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)
  const [showNew, setShowNew] = useState(false)
  const [rename, setRename] = useState<DatasetInfo | null>(null)
  const [del, setDel] = useState<DatasetInfo | null>(null)

  const busy = !!data?.some((d) => d.syncing || d.queued)
  useEffect(() => {
    const t = window.setInterval(reload, busy ? 3000 : 12000)
    return () => window.clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busy])

  const act = async (f: () => Promise<unknown>, msg?: string) => {
    try {
      setErr(null)
      await f()
      if (msg) setInfo(msg)
      reload()
      await refreshMe()
    } catch (e) {
      setErr(errMsg(e))
    }
  }
  const open = async (d: DatasetInfo, to?: string) => {
    try {
      setErr(null)
      await selectDataset(d.id)
      if (to) navigate(to)
    } catch (e) {
      setErr(errMsg(e))
    }
  }

  return (
    <>
      <p className="hint">
        Набор данных — это отдельная организация или отдельная база Logo со своими книгами, отчётами, классификаторами и настройками. Наборы никак не влияют друг на друга. Переключатель набора — вверху справа; доступ пользователей к
        наборам настраивается на вкладке «Пользователи».
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
        <button className="btn primary" onClick={() => setShowNew(true)}>
          + Создать набор данных
        </button>
      </div>
      {loading && !data && <div className="muted pad">Загрузка…</div>}
      {data && (
        <table className="simple">
          <thead>
            <tr>
              <th>Набор данных</th>
              <th style={{ width: 190 }}>Состояние</th>
              <th className="r" style={{ width: 90 }}>
                Книг
              </th>
              <th style={{ width: 150 }}>Последняя загрузка</th>
              <th style={{ width: 230 }}>Подключение</th>
              <th style={{ width: 330 }} />
            </tr>
          </thead>
          <tbody>
            {data.map((d) => {
              const st = stateOf(d)
              return (
                <tr key={d.id} className={d.archived ? 'dim' : ''}>
                  <td>
                    <b>{d.name}</b>
                    {d.is_default && <span className="tag green"> по умолчанию</span>}
                    {d.id === currentId && <span className="tag"> выбран</span>}
                    {d.org_name && <div className="muted small">{d.org_name}</div>}
                  </td>
                  <td>
                    <span className={'tag ' + st.cls}>{st.text}</span>
                    {d.syncing && d.progress?.message && <div className="muted small">{d.progress.message}</div>}
                  </td>
                  <td className="num">
                    {d.books_main} <span className="muted">из {d.books_total}</span>
                  </td>
                  <td className="nowrap">{d.last_sync_at ? fmtDateTime(d.last_sync_at) : <span className="muted">нет</span>}</td>
                  <td>{d.connection ? <span title={d.connection.server}>{d.connection.name}<div className="muted small">{d.connection.database}</div></span> : <span className="muted">—</span>}</td>
                  <td className="actions">
                    {!d.archived && (
                      <button className="btn small" disabled={d.id === currentId} onClick={() => open(d)}>
                        Открыть
                      </button>
                    )}
                    {!d.archived && (
                      <button className="btn small" onClick={() => open(d, '/settings/books')}>
                        Книги и годы
                      </button>
                    )}
                    <button className="btn small" onClick={() => setRename(d)}>
                      Переименовать
                    </button>
                    {!d.archived && !d.is_default && (
                      <button className="btn small" onClick={() => act(() => api.saveDataset(d.id, { is_default: true }), `Набор «${d.name}» теперь открывается по умолчанию.`)}>
                        Сделать основным
                      </button>
                    )}
                    {!d.archived && (
                      <button
                        className="btn small"
                        disabled={d.is_default}
                        title={d.is_default ? 'Набор по умолчанию нельзя отправить в архив: сначала назначьте основным другой' : 'Спрятать набор: данные сохраняются, доступ закрывается'}
                        onClick={() => askConfirm(`Отправить набор «${d.name}» в архив? Он пропадёт из переключателя, данные и настройки сохранятся.`, { okLabel: 'В архив' }).then((ok) => { if (ok) void act(() => api.saveDataset(d.id, { archived: true }), `Набор «${d.name}» отправлен в архив.`) })}
                      >
                        В архив
                      </button>
                    )}
                    {d.archived && (
                      <button className="btn small" onClick={() => act(() => api.saveDataset(d.id, { archived: false }), `Набор «${d.name}» возвращён из архива.`)}>
                        Вернуть из архива
                      </button>
                    )}
                    {d.archived && (
                      <button className="btn small danger" onClick={() => setDel(d)}>
                        Удалить…
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}

      {showNew && data && (
        <NewDataset
          datasets={data}
          onClose={() => setShowNew(false)}
          onCreated={async (d) => {
            setShowNew(false)
            setInfo(`Набор «${d.name}» создан. Теперь выберите для него книги.`)
            reload()
            await refreshMe()
            try {
              await selectDataset(d.id)
              navigate('/settings/books')
            } catch (e) {
              setErr(errMsg(e))
            }
          }}
        />
      )}
      {rename && (
        <RenameDialog
          d={rename}
          onClose={() => setRename(null)}
          onSave={async (name, org) => {
            await api.saveDataset(rename.id, { name, org_name: org })
            setRename(null)
            setInfo('Название сохранено.')
            reload()
            await refreshMe()
          }}
        />
      )}
      {del && (
        <DeleteDialog
          d={del}
          onClose={() => setDel(null)}
          onDelete={async (confirm) => {
            await api.deleteDataset(del.id, confirm)
            setDel(null)
            setInfo(`Набор «${del.name}» удалён. Его файлы не стёрты, а перенесены в служебную папку data\\ds\\_deleted на сервере: при необходимости администратор сервера сможет их вернуть.`)
            reload()
            await refreshMe()
          }}
        />
      )}
    </>
  )
}

function RenameDialog({ d, onClose, onSave }: { d: DatasetInfo; onClose: () => void; onSave: (name: string, org: string) => Promise<void> }) {
  const [name, setName] = useState(d.name)
  const [org, setOrg] = useState(d.org_name ?? '')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const go = async () => {
    setBusy(true)
    try {
      await onSave(name.trim(), org.trim())
    } catch (e) {
      setErr(errMsg(e))
      setBusy(false)
    }
  }
  return (
    <Modal
      title="Переименовать набор данных"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Отмена
          </button>
          <button className="btn primary" disabled={!name.trim() || busy} onClick={go}>
            Сохранить
          </button>
        </>
      }
    >
      <ErrorBox error={err} />
      <div className="form">
        <label>
          Название набора (виден в переключателе)
          <input type="text" autoFocus value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          Название организации (подставляется в заголовки отчётов; пусто — из Logo)
          <input type="text" value={org} onChange={(e) => setOrg(e.target.value)} />
        </label>
      </div>
    </Modal>
  )
}

function DeleteDialog({ d, onClose, onDelete }: { d: DatasetInfo; onClose: () => void; onDelete: (confirm: string) => Promise<void> }) {
  const [txt, setTxt] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const go = async () => {
    setBusy(true)
    try {
      await onDelete(txt)
    } catch (e) {
      setErr(errMsg(e))
      setBusy(false)
    }
  }
  return (
    <Modal
      title={`Удалить набор «${d.name}»?`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Отмена
          </button>
          <button className="btn danger" disabled={txt !== d.name || busy} onClick={go}>
            Удалить набор
          </button>
        </>
      }
    >
      <ErrorBox error={err} />
      <p>
        Набор перестанет существовать для пользователей: его отчёты, классификаторы, настройки и загруженные данные станут недоступны. В Logo ничего не меняется. Файлы набора не стираются, а переносятся в служебную папку на сервере.
      </p>
      <label>
        Для подтверждения введите название набора: <b>{d.name}</b>
        <input type="text" autoFocus value={txt} onChange={(e) => setTxt(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && txt === d.name && go()} />
      </label>
    </Modal>
  )
}

function NewDataset({ datasets, onClose, onCreated }: { datasets: DatasetInfo[]; onClose: () => void; onCreated: (d: DatasetInfo) => void | Promise<void> }) {
  const { data: conns } = useLoad<Connection[]>(() => api.connections(), [])
  const { data: drivers } = useLoad(() => api.connectionDrivers(), [])
  const [f, setF] = useState({ name: '', org_name: '', plan: 'turkish' as 'turkish' | 'none', connection: '' as string, copy_from: '' })
  const [nc, setNc] = useState({ name: '', server: '', database: '', login: '', password: '', driver: '', trust_cert: true })
  const [parts, setParts] = useState<string[]>(SETTING_PARTS.filter((p) => p.k !== 'params').map((p) => p.k))
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [testMsg, setTestMsg] = useState<string | null>(null)
  const connId = f.connection === '' && conns?.length ? String(conns[0].id) : f.connection
  const useNew = connId === 'new'

  const test = async () => {
    setTestMsg(null)
    try {
      const r = await api.testConnection({ server: nc.server, database: nc.database, login: nc.login, password: nc.password, driver: nc.driver })
      setTestMsg(`Подключение работает${r.logo ? `: фирм ${r.logo.firms}, периодов ${r.logo.periods}` : ''}.`)
      setErr(null)
    } catch (e) {
      setErr(errMsg(e))
    }
  }
  const go = async () => {
    if (!f.name.trim()) return setErr('Введите название.')
    if (useNew && !(nc.server && nc.database && nc.login)) return setErr('Для нового подключения укажите сервер, базу и логин.')
    if (!useNew && connId === '') return setErr('Выберите подключение к Logo.')
    setBusy(true)
    setErr(null)
    try {
      let cid = Number(connId)
      if (useNew) {
        const c = await api.createConnection({ name: nc.name.trim() || `${nc.server} / ${nc.database}`, server: nc.server, database: nc.database, login: nc.login, password: nc.password, driver: nc.driver, trust_cert: nc.trust_cert })
        cid = c.id
      }
      const d = await api.createDataset({
        name: f.name.trim(),
        org_name: f.org_name.trim() || undefined,
        connection_id: cid,
        plan_template: f.plan,
        ...(f.copy_from ? { copy_from: Number(f.copy_from), copy_parts: parts } : {}),
      })
      await onCreated(d)
    } catch (e) {
      setErr(errMsg(e))
      setBusy(false)
    }
  }
  return (
    <Modal
      title="Новый набор данных"
      wide
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            Отмена
          </button>
          <button className="btn primary" disabled={busy} onClick={go}>
            {busy ? 'Создаём…' : 'Создать и перейти к выбору книг'}
          </button>
        </>
      }
    >
      <ErrorBox error={err} />
      <div className="form">
        <div className="row2">
          <label>
            Название набора
            <input type="text" autoFocus value={f.name} placeholder="например: Филиал Ашхабад" onChange={(e) => setF({ ...f, name: e.target.value })} />
          </label>
          <label>
            Название организации (необязательно)
            <input type="text" value={f.org_name} onChange={(e) => setF({ ...f, org_name: e.target.value })} />
          </label>
        </div>
        <div className="row2">
          <label>
            Подключение к Logo
            <select value={connId} onChange={(e) => setF({ ...f, connection: e.target.value })}>
              {conns?.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} — {c.server} / {c.database}
                </option>
              ))}
              <option value="new">Новое подключение…</option>
            </select>
          </label>
          <label>
            План счетов
            <select value={f.plan} onChange={(e) => setF({ ...f, plan: e.target.value as 'turkish' | 'none' })}>
              <option value="turkish">Турецкий план (как в Logo Tiger)</option>
              <option value="none">Свой план (без шаблона)</option>
            </select>
          </label>
        </div>
        {useNew && (
          <div className="msg" style={{ display: 'block', background: '#f7f9fc', border: '1px solid var(--line)' }}>
            <div className="conn-grid">
              <label>
                Название подключения
                <input type="text" value={nc.name} onChange={(e) => setNc({ ...nc, name: e.target.value })} />
              </label>
              <label>
                Сервер
                <input type="text" value={nc.server} placeholder="например: 192.168.1.10 или SERVER\LOGO" onChange={(e) => setNc({ ...nc, server: e.target.value })} />
              </label>
              <label>
                База Logo
                <input type="text" value={nc.database} placeholder="например: LOGO_DB" onChange={(e) => setNc({ ...nc, database: e.target.value })} />
              </label>
              <label>
                Логин
                <input type="text" autoComplete="off" value={nc.login} onChange={(e) => setNc({ ...nc, login: e.target.value })} />
              </label>
              <label>
                Пароль
                <input type="password" autoComplete="new-password" value={nc.password} onChange={(e) => setNc({ ...nc, password: e.target.value })} />
              </label>
              <label>
                Драйвер
                <select value={nc.driver} onChange={(e) => setNc({ ...nc, driver: e.target.value })}>
                  <option value="">Подобрать автоматически{drivers?.auto ? `: ${drivers.auto}` : ''}</option>
                  {drivers?.drivers.map((x) => (
                    <option key={x}>{x}</option>
                  ))}
                </select>
              </label>
            </div>
            <div className="toolbar">
              <button className="btn" onClick={test} disabled={!nc.server || !nc.database || !nc.login}>
                Проверить подключение
              </button>
              {testMsg && <span className="tag green">{testMsg}</span>}
            </div>
          </div>
        )}
        <label>
          Скопировать настройки из набора (необязательно)
          <select value={f.copy_from} onChange={(e) => setF({ ...f, copy_from: e.target.value })}>
            <option value="">Не копировать — начать с пустого набора</option>
            {datasets
              .filter((d) => !d.archived)
              .map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
          </select>
        </label>
        {f.copy_from && (
          <div>
            <div className="hint">Что скопировать (книги и подключение не копируются — их вы выберете для нового набора):</div>
            {SETTING_PARTS.map((p) => (
              <label key={p.k} className="chk">
                <input type="checkbox" checked={parts.includes(p.k)} onChange={(e) => setParts(e.target.checked ? [...parts, p.k] : parts.filter((x) => x !== p.k))} />
                {p.label}
              </label>
            ))}
          </div>
        )}
        <p className="hint">После создания откроется «Книги и годы» нового набора: прочитайте фирмы из Logo, примите предложение программы и сохраните разметку.</p>
      </div>
    </Modal>
  )
}
