import { useCallback, useEffect, useRef, useState } from 'react'
import { StatusBadge, ErrorBox } from './components/ui'
import { api, authHooks, datasetCtl, errMsg, setSelectedDataset, ApiError, type Me } from './api'
import { confirmLeave, navigate, useRoute } from './router'
import ErrorBoundary from './components/ErrorBoundary'
import { ConfirmHost } from './components/confirm'
import ReportsPage from './pages/ReportsPage'
import ReportScreen from './report/ReportScreen'
import ClassifiersPage from './pages/ClassifiersPage'
import AccountsPage from './pages/AccountsPage'
import CorrPage from './pages/CorrPage'
import SettingsPage from './pages/SettingsPage'
import PerfPage from './pages/PerfPage'
import FaqPage from './pages/FaqPage'
import { RoleCtx, useCanEdit } from './components/role'
import { DatasetSwitcher, SessionCtx, resetDatasetCache, type Session } from './dataset'
import { resetYears } from './years'
import type { ReportType } from './api'
import { REPORT_TYPES } from './format'

const ROLE_RU: Record<string, string> = { viewer: 'просмотр', editor: 'редактор', admin: 'администратор' }

const NAV = [
  { path: '/reports', label: 'Отчёты', match: /^\/(reports|report)/ },
  { path: '/classifiers', label: 'Классификаторы', match: /^\/classifiers/ },
  { path: '/accounts', label: 'План счетов', match: /^\/accounts/ },
  { path: '/corr', label: 'Корреспонденция', match: /^\/corr/ },
  { path: '/settings', label: 'Настройки', match: /^\/settings/ },
  { path: '/faq', label: 'Вопросы и ответы', match: /^\/faq/ },
]

function Login({ onDone }: { onDone: (m: Me) => void }) {
  const [login, setLogin] = useState('')
  const [pass, setPass] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const go = async () => {
    try {
      onDone(await api.login(login.trim(), pass))
    } catch (e) {
      setErr(e instanceof ApiError && e.status === 401 ? 'Неверный логин или пароль.' : errMsg(e))
    }
  }
  return (
    <div className="login-box" onKeyDown={(e) => e.key === 'Enter' && go()}>
      <h2>Вход</h2>
      <ErrorBox error={err} />
      <label>
        логин
        <input type="text" autoFocus value={login} onChange={(e) => setLogin(e.target.value)} />
      </label>
      <label>
        пароль
        <input type="password" value={pass} onChange={(e) => setPass(e.target.value)} />
      </label>
      <button className="btn primary" onClick={go} disabled={!login || !pass}>
        Войти
      </button>
    </div>
  )
}

export default function App() {
  // экран входа — только если /api/me ответил 401
  const [me, setMe] = useState<Me | null>(null)
  const [needLogin, setNeedLogin] = useState(false)
  const [ready, setReady] = useState(false)
  // «эпоха» набора: при смене набора всё содержимое окна создаётся заново, чтобы не осталось данных прежнего набора
  const [epoch, setEpoch] = useState(0)
  const lost = useRef(false)

  /** Прочитать сведения о пользователе; если сохранённый набор недоступен — молча вернуться к набору по умолчанию. */
  const fetchMe = useCallback(async (): Promise<Me> => {
    let m: Me
    try {
      m = await api.me()
    } catch (e) {
      if (datasetCtl.id != null && e instanceof ApiError && (e.status === 403 || e.status === 404 || e.status === 409)) {
        setSelectedDataset(null)
        return api.me()
      }
      throw e
    }
    const list = m.datasets ?? []
    if (datasetCtl.id != null && list.length > 0 && !list.some((x) => x.id === datasetCtl.id)) {
      setSelectedDataset(null)
      m = await api.me()
    }
    return m
  }, [])

  const refreshMe = useCallback(async () => {
    setMe(await fetchMe())
  }, [fetchMe])

  const selectDataset = useCallback(
    async (id: number | null) => {
      // открытый отчёт с несохранёнными правками: спрашиваем, как и при уходе со страницы
      if (!(await confirmLeave())) return
      const prev = datasetCtl.id
      setSelectedDataset(id)
      resetYears()
      resetDatasetCache()
      try {
        setMe(await api.me())
      } catch (e) {
        setSelectedDataset(prev)
        resetDatasetCache()
        throw e
      }
      setEpoch((x) => x + 1)
      // открытый отчёт или классификатор принадлежат прежнему набору
      if (/^\/(report|classifiers\/\d+)/.test(window.location.hash.replace(/^#/, ''))) navigate('/reports', true)
    },
    [],
  )

  useEffect(() => {
    authHooks.onUnauthorized = () => setNeedLogin(true)
    authHooks.onDatasetLost = () => {
      if (lost.current) return
      lost.current = true
      resetYears()
      resetDatasetCache()
      fetchMe()
        .then(setMe)
        .catch(() => {})
        .finally(() => {
          setEpoch((x) => x + 1)
          lost.current = false
        })
    }
    fetchMe()
      .then(setMe)
      .catch((e) => e instanceof ApiError && e.status === 401 && setNeedLogin(true))
      .finally(() => setReady(true))
  }, [fetchMe])

  const session: Session = {
    me,
    globalAdmin: me ? me.global_admin ?? !me.auth : true,
    currentId: datasetCtl.id ?? me?.dataset?.id ?? null,
    refreshMe,
    selectDataset,
  }

  if (!ready) return <div className="muted pad">Загрузка…</div>
  if (needLogin)
    return (
      <Login
        onDone={(m) => {
          setMe(m)
          setNeedLogin(false)
          setEpoch((x) => x + 1)
        }}
      />
    )
  return (
    <SessionCtx.Provider value={session}>
      <ConfirmHost />
      <RoleCtx.Provider value={me?.role ?? 'admin'}>
        <Shell
          key={epoch}
          me={me}
          onLogout={() =>
            api
              .logout()
              .catch(() => {})
              .then(() => api.me())
              .then(setMe)
              .catch(() => (setMe(null), setNeedLogin(true)))
          }
        />
      </RoleCtx.Provider>
    </SessionCtx.Provider>
  )
}

function Shell({ me, onLogout }: { me: Me | null; onLogout: () => void }) {
  const route = useRoute()
  const canEdit = useCanEdit()
  let body
  let m: RegExpExecArray | null
  if ((m = /^\/report\/new\/(\w+)(?:\?(.*))?/.exec(route)))
    body = REPORT_TYPES.some((t) => t.type === m![1]) ? (
      <ReportScreen key={'new' + route} newType={m[1] as ReportType} preset={new URLSearchParams(m[2] ?? '')} />
    ) : (
      <div className="page">
        <div className="msg warn">
          <span>
            <b>Неизвестный вид отчёта.</b> Такого вида отчёта в программе нет — выберите его в списке на странице «Отчёты».
          </span>
          <button className="btn small" onClick={() => navigate('/reports')}>
            К отчётам
          </button>
        </div>
      </div>
    )
  else if ((m = /^\/report\/(\d+)/.exec(route))) body = <ReportScreen key={m[1]} reportId={Number(m[1])} />
  else if ((m = /^\/classifiers\/(\d+)/.exec(route))) body = <ClassifiersPage key={m[1]} editId={Number(m[1])} />
  else if (route.startsWith('/classifiers')) body = <ClassifiersPage />
  else if (route.startsWith('/accounts')) body = <AccountsPage />
  else if (route.startsWith('/corr')) body = <CorrPage />
  else if (route.startsWith('/settings')) body = <SettingsPage />
  else if ((m = /^\/faq(?:\/([\w-]+))?/.exec(route))) body = <FaqPage anchor={m[1]} />
  else if (import.meta.env.DEV && route === '/perf') body = <PerfPage />
  else body = <ReportsPage />

  return (
    <div className="app">
      <header className="topbar no-print">
        <div className="brand">Бухгалтерские отчёты</div>
        <nav>
          {NAV.filter((n) => canEdit || n.path !== '/settings').map((n) => (
            <a
              key={n.path}
              href={'#' + n.path}
              className={n.match.test(route) ? 'active' : ''}
              onClick={(e) => {
                e.preventDefault()
                navigate(n.path)
              }}
            >
              {n.label}
            </a>
          ))}
        </nav>
        <div className="spacer" />
        <a href="#/faq" className="help-link" title="Вопросы и ответы" onClick={(e) => (e.preventDefault(), navigate('/faq'))}>
          ? Справка
        </a>
        <DatasetSwitcher />
        <StatusBadge />
        {me && (
          <span className="status">
            {me.login}
            {me.auth && <span className="muted" style={{ color: '#9fb0c6' }}> ({ROLE_RU[me.role] ?? me.role})</span>}
            {me.auth && (
              <button className="btn small ghost" style={{ color: '#c9d3e0' }} onClick={onLogout} title="Выйти">
                выйти
              </button>
            )}
          </span>
        )}
      </header>
      <main className={'content' + (canEdit ? '' : ' readonly')}>
        {!canEdit && <div className="msg warn no-print">Режим просмотра: вы можете формировать отчёты, раскрывать их и выгружать в Excel; сохранять и менять данные — нет.</div>}
        <ErrorBoundary key={route} onHome={() => navigate('/reports', true)}>
          {body}
        </ErrorBoundary>
      </main>
    </div>
  )
}
