import { useState } from 'react'
import { api, errMsg, type DatasetInfo, type TemplateInfo, type UserInfo } from '../api'
import { ErrorBox, Modal, useLoad } from '../components/ui'
import { askConfirm } from '../components/confirm'
import { fmtDateTime } from '../format'

export function Templates() {
  const { data, error, loading, reload } = useLoad<TemplateInfo[]>(() => api.templates(), [])
  const [name, setName] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)
  const upload = async () => {
    if (!file) return setErr('Выберите файл Excel (.xlsx).')
    try {
      await api.uploadTemplate(name.trim() || file.name.replace(/\.xlsx?$/i, ''), file)
      setInfo('Шаблон загружен.')
      setErr(null)
      setName('')
      setFile(null)
      reload()
    } catch (e) {
      setErr(errMsg(e))
    }
  }
  return (
    <>
      <p className="hint">
        Шаблоны — бланки Excel (баланс, ОПУ и т. п.). В отчёте «Форма по строкам» выбирается шаблон, а для каждой строки задаётся ячейка, куда подставится значение при выгрузке в Excel.
      </p>
      <ErrorBox error={error || err} onClose={() => setErr(null)} />
      {info && <div className="msg ok">{info}</div>}
      {loading && !data && <div className="muted pad">Загрузка…</div>}
      <table className="simple" style={{ maxWidth: 900 }}>
        <thead>
          <tr>
            <th>Название</th>
            <th>Файл</th>
            <th style={{ width: 150 }}>Загружен</th>
            <th style={{ width: 90 }} />
          </tr>
        </thead>
        <tbody>
          {data?.map((t) => (
            <tr key={t.id}>
              <td>{t.name}</td>
              <td>{t.filename}</td>
              <td className="nowrap">{fmtDateTime(t.uploaded_at)}</td>
              <td className="actions">
                <button
                  className="btn small danger"
                  onClick={async () => {
                    if (!(await askConfirm(`Удалить шаблон «${t.name}»?`, { okLabel: 'Удалить', danger: true }))) return
                    try {
                      await api.deleteTemplate(t.id)
                      reload()
                    } catch (e) {
                      setErr(errMsg(e))
                    }
                  }}
                >
                  Удалить
                </button>
              </td>
            </tr>
          ))}
          {data?.length === 0 && (
            <tr>
              <td colSpan={4} className="muted">
                Шаблонов пока нет.
              </td>
            </tr>
          )}
        </tbody>
      </table>
      <h4>Загрузить шаблон</h4>
      <div className="toolbar">
        <input type="text" style={{ width: 260 }} value={name} onChange={(e) => setName(e.target.value)} placeholder="название шаблона" />
        <input type="file" style={{ width: 300 }} accept=".xlsx,.xlsm" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        <button className="btn primary" onClick={upload}>
          Загрузить
        </button>
      </div>
    </>
  )
}

const ROLE_RU: Record<string, string> = { viewer: 'просмотр', editor: 'редактор', admin: 'администратор' }

export function Users() {
  const { data, error, loading, reload } = useLoad<UserInfo[]>(() => api.users(), [])
  const { data: dss } = useLoad<DatasetInfo[]>(() => api.datasets(), [])
  const [login, setLogin] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState('viewer')
  const [newDs, setNewDs] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)
  const [pwFor, setPwFor] = useState<UserInfo | null>(null)
  const [accFor, setAccFor] = useState<UserInfo | null>(null)
  const [newPw, setNewPw] = useState('')
  const act = async (f: () => Promise<unknown>, msg: string) => {
    try {
      await f()
      setInfo(msg)
      setErr(null)
      reload()
    } catch (e) {
      setErr(errMsg(e))
    }
  }
  const active = (dss ?? []).filter((d) => !d.archived)
  const dsName = (id: number) => dss?.find((d) => d.id === id)?.name ?? `набор №${id}`
  const newDsId = newDs !== '' ? Number(newDs) : active.find((d) => d.is_default)?.id ?? active[0]?.id
  return (
    <>
      <p className="hint">
        Роли: «просмотр» — только смотреть отчёты; «редактор» — менять отчёты и классификаторы; «администратор» — ещё и настройки набора. Роль даётся отдельно по каждому набору данных («Доступ к наборам»). «Администратор установки» видит все
        наборы, подключения и пользователей. Экран входа показывается, только если на сервере включена авторизация.
      </p>
      <ErrorBox error={error || err} onClose={() => setErr(null)} />
      {info && <div className="msg ok">{info}</div>}
      {loading && !data && <div className="muted pad">Загрузка…</div>}
      {data && data.length === 0 && (
        <div className="msg warn">
          <span>
            <b>Пользователей нет — программа открыта всем, кто может зайти на этот компьютер по сети.</b> Вход по паролю включается при запуске программы: администратор сервера включает авторизацию и задаёт первого администратора (раздел «Вход по паролю» в инструкции по установке). После этого остальных пользователей можно добавлять здесь.
          </span>
        </div>
      )}
      <table className="simple" style={{ maxWidth: 1000 }}>
        <thead>
          <tr>
            <th>Логин</th>
            <th style={{ width: 190 }} title="Роль администратора установки даёт доступ ко всем наборам">
              Роль
            </th>
            <th>Доступ к наборам данных</th>
            <th style={{ width: 330 }} />
          </tr>
        </thead>
        <tbody>
          {data?.map((u) => (
            <tr key={u.id}>
              <td>{u.login}</td>
              <td>
                <select value={u.role} onChange={(e) => act(() => api.updateUser(u.id, { role: e.target.value }), 'Роль изменена: она перенесена на все наборы пользователя.')}>
                  <option value="viewer">просмотр</option>
                  <option value="editor">редактор</option>
                  <option value="admin">администратор установки</option>
                </select>
              </td>
              <td>
                {u.role === 'admin' ? (
                  <span className="muted">все наборы, полные права</span>
                ) : u.datasets && u.datasets.length ? (
                  u.datasets.map((d) => (
                    <div key={d.id}>
                      {dsName(d.id)} — <b>{ROLE_RU[d.role] ?? d.role}</b>
                    </div>
                  ))
                ) : (
                  <span className="tag red">нет доступа ни к одному набору</span>
                )}
              </td>
              <td className="actions">
                <button className="btn small" disabled={u.role === 'admin'} title={u.role === 'admin' ? 'Администратор установки имеет доступ ко всем наборам' : ''} onClick={() => setAccFor(u)}>
                  Доступ к наборам…
                </button>
                <button
                  className="btn small"
                  onClick={() => {
                    setNewPw('')
                    setPwFor(u)
                  }}
                >
                  Сменить пароль
                </button>
                <button className="btn small danger" onClick={() => askConfirm(`Удалить пользователя «${u.login}»?`, { okLabel: 'Удалить', danger: true }).then((ok) => { if (ok) void act(() => api.deleteUser(u.id), 'Пользователь удалён.') })}>
                  Удалить
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {accFor && dss && (
        <AccessDialog
          user={accFor}
          datasets={dss}
          onClose={() => setAccFor(null)}
          onSave={async (list) => {
            const u = accFor
            setAccFor(null)
            await act(() => api.setUserDatasets(u.id, list), `Доступ пользователя «${u.login}» к наборам сохранён.`)
          }}
        />
      )}
      {pwFor && (
        <Modal
          title={`Новый пароль для «${pwFor.login}»`}
          onClose={() => setPwFor(null)}
          footer={
            <>
              <button className="btn" onClick={() => setPwFor(null)}>
                Отмена
              </button>
              <button
                className="btn primary"
                disabled={!newPw}
                onClick={() => {
                  const u = pwFor
                  setPwFor(null)
                  act(() => api.updateUser(u.id, { password: newPw }), 'Пароль изменён.')
                }}
              >
                Сменить пароль
              </button>
            </>
          }
        >
          <div className="form">
            <label>
              новый пароль
              <input type="password" autoFocus autoComplete="new-password" value={newPw} onChange={(e) => setNewPw(e.target.value)} />
            </label>
          </div>
        </Modal>
      )}
      <h4>Новый пользователь</h4>
      <div className="toolbar">
        <input type="text" style={{ width: 180 }} placeholder="логин" value={login} onChange={(e) => setLogin(e.target.value)} />
        <input type="password" style={{ width: 180 }} placeholder="пароль" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        <select style={{ width: 200 }} value={role} onChange={(e) => setRole(e.target.value)}>
          <option value="viewer">просмотр</option>
          <option value="editor">редактор</option>
          <option value="admin">администратор установки</option>
        </select>
        {role !== 'admin' && active.length > 0 && (
          <select style={{ width: 220 }} value={newDsId ?? ''} onChange={(e) => setNewDs(e.target.value)} title="К какому набору данных у пользователя будет доступ (остальные можно добавить позже)">
            {active.map((d) => (
              <option key={d.id} value={d.id}>
                Набор: {d.name}
              </option>
            ))}
          </select>
        )}
        <button
          className="btn primary"
          disabled={!login.trim() || !password}
          onClick={() =>
            act(async () => {
              await api.createUser({ login: login.trim(), password, role, ...(role !== 'admin' && newDsId != null ? { datasets: [{ dataset_id: newDsId, role }] } : {}) })
              setLogin('')
              setPassword('')
            }, 'Пользователь добавлен.')
          }
        >
          Добавить
        </button>
      </div>
    </>
  )
}

function AccessDialog({ user, datasets, onClose, onSave }: { user: UserInfo; datasets: DatasetInfo[]; onClose: () => void; onSave: (l: { dataset_id: number; role: string }[]) => Promise<void> }) {
  const [v, setV] = useState<Record<number, string>>(() => Object.fromEntries((user.datasets ?? []).map((d) => [d.id, d.role])))
  const shown = datasets.filter((d) => !d.archived || v[d.id])
  return (
    <Modal
      title={`Доступ к наборам: «${user.login}»`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Отмена
          </button>
          <button
            className="btn primary"
            onClick={() =>
              onSave(
                Object.entries(v)
                  .filter(([, r]) => r)
                  .map(([id, r]) => ({ dataset_id: Number(id), role: r })),
              )
            }
          >
            Сохранить
          </button>
        </>
      }
    >
      <p className="hint">Выберите, к каким наборам данных есть доступ и с какой ролью. «Нет доступа» — набор пользователь не увидит.</p>
      <table className="simple">
        <tbody>
          {shown.map((d) => (
            <tr key={d.id}>
              <td>
                {d.name}
                {d.archived && <span className="tag gray"> в архиве</span>}
              </td>
              <td style={{ width: 200 }}>
                <select value={v[d.id] ?? ''} onChange={(e) => setV({ ...v, [d.id]: e.target.value })}>
                  <option value="">нет доступа</option>
                  <option value="viewer">просмотр</option>
                  <option value="editor">редактор</option>
                  <option value="admin">администратор</option>
                </select>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Modal>
  )
}
