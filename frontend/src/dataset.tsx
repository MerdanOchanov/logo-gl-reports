import { createContext, useContext, useEffect, useState } from 'react'
import { api, datasetCtl, type DatasetInfo, type Me } from './api'
import { currencyCtl } from './format'

// Общее состояние «набора данных» (источник — фирма и период Logo; наборов может быть несколько).
let cache: DatasetInfo | null = null
let pending: Promise<DatasetInfo | null> | null = null
let gen = 0
const subs = new Set<(d: DatasetInfo | null) => void>()

function load(): Promise<DatasetInfo | null> {
  if (!pending) {
    const my = gen
    pending = api
      .dataset()
      .then((d) => {
        if (my !== gen) return cache // за время запроса набор сменился
        cache = d
        currencyCtl.local = d?.params?.currency_local?.code ?? ''
        subs.forEach((f) => f(d))
        return d
      })
      .catch(() => cache)
      .finally(() => {
        if (my === gen) pending = null
      })
  }
  return pending
}

/** Заново прочитать сведения о наборе (после сохранения подключения, книг, параметров). */
export function reloadDataset() {
  return load()
}

/** Забыть всё, что запомнено о прежнем наборе (при переключении набора). */
export function resetDatasetCache() {
  gen++
  cache = null
  currencyCtl.local = ''
  pending = null
  subs.forEach((f) => f(null))
}

/** Сведения о наборе одним обещанием (для экранов, которым нужны параметры до первой отрисовки). null — прочитать не удалось. */
export function getDataset(): Promise<DatasetInfo | null> {
  return cache ? Promise.resolve(cache) : load()
}

/** Валюты набора для отчётов: местная и (если включена) отчётная. Значения берутся из параметров набора. */
export function currencyOptions(ds: DatasetInfo | null | undefined): { code: string; label: string }[] {
  const loc = ds?.params?.currency_local
  const rep = ds?.params?.currency_report
  const out: { code: string; label: string }[] = []
  if (loc?.code) out.push({ code: loc.code, label: `${loc.name || 'местная валюта'} (${loc.code})` })
  if (rep?.code && rep.enabled && rep.code !== loc?.code) out.push({ code: rep.code, label: `${rep.name || 'отчётная валюта'} (${rep.code})` })
  return out
}

/** Параметры набора (валюта и др.) ещё не заданы: в наборе стоит валюта-заглушка. */
export function paramsUnset(ds: DatasetInfo | null | undefined): boolean {
  if (!ds) return false
  const code = ds.params?.currency_local?.code
  return ds.configured === false || !code || code === 'LOCAL'
}

export function useDataset(): { data: DatasetInfo | null; reload: () => void } {
  const [d, setD] = useState<DatasetInfo | null>(cache)
  useEffect(() => {
    subs.add(setD)
    if (!cache) load()
    return () => {
      subs.delete(setD)
    }
  }, [])
  return { data: d, reload: () => void load() }
}

/** Сеанс: кто вошёл, какие наборы ему доступны и как переключить набор. Заполняется в App. */
export interface Session {
  me: Me | null
  /** администратор всей установки (наборы, подключения, пользователи) */
  globalAdmin: boolean
  /** номер выбранного набора (если не выбран явно — набор по умолчанию) */
  currentId: number | null
  refreshMe: () => Promise<void>
  selectDataset: (id: number | null) => Promise<void>
}

export const SessionCtx = createContext<Session>({ me: null, globalAdmin: true, currentId: null, refreshMe: async () => {}, selectDataset: async () => {} })
export const useSession = () => useContext(SessionCtx)

/** Переключатель набора данных в шапке. Набор один — компактная подпись; несколько — список. */
export function DatasetSwitcher() {
  const { data } = useDataset()
  const { me, currentId, selectDataset } = useSession()
  const list = me?.datasets ?? []
  const title = data ? data.org_name || data.name : ''
  if (list.length > 1) {
    const cur = currentId ?? datasetCtl.id ?? me?.dataset?.id ?? list[0].id
    const curName = list.find((x) => x.id === cur)?.name ?? data?.name ?? 'Набор №' + cur
    return (
      <label className="ds-switch" title="Набор данных: отдельная база Logo или отдельная организация со своими отчётами и настройками">
        <span>Набор данных:</span>
        <select value={cur} title={curName} onChange={(e) => void selectDataset(Number(e.target.value))}>
          {!list.some((x) => x.id === cur) && <option value={cur}>{data?.name ?? 'Набор №' + cur}</option>}
          {list.map((x) => (
            <option key={x.id} value={x.id}>
              {x.name}
              {x.state === 'new' ? ' (не настроен)' : ''}
            </option>
          ))}
        </select>
      </label>
    )
  }
  if (!data) return null
  return (
    <span className="status" title={'Набор данных: ' + data.name + (data.connection ? ` · ${data.connection.server} / ${data.connection.database}` : '')}>
      {title}
    </span>
  )
}
