import { useEffect, useState } from 'react'
import { api, type YearInfo } from './api'

const FIRST_YEAR = 2003

/** Запасной список лет, пока сервер не ответил: от 2003 до текущего. */
function fallbackYears(): number[] {
  const last = new Date().getFullYear()
  return Array.from({ length: last - FIRST_YEAR + 1 }, (_, i) => FIRST_YEAR + i)
}

let cache: YearInfo[] | null = null
let pending: Promise<YearInfo[]> | null = null

function loadInfos(): Promise<YearInfo[]> {
  if (cache) return Promise.resolve(cache)
  if (!pending) {
    pending = api
      .years()
      .then((r) => {
        cache = [...r].sort((a, b) => a.year - b.year)
        return cache
      })
      .catch(() => [] as YearInfo[])
      .finally(() => {
        pending = null
      })
  }
  return pending
}

/** Сбросить загруженный список лет (после изменения разметки книг). */
export function resetYears() {
  cache = null
}

/** Годы-метки со сведениями о книгах (даты, части). Пока сервер не ответил — пусто. */
export function useYearInfos(): YearInfo[] {
  const [infos, setInfos] = useState<YearInfo[]>(cache ?? [])
  useEffect(() => {
    let alive = true
    loadInfos().then((r) => alive && setInfos(r))
    return () => {
      alive = false
    }
  }, [])
  return infos
}

/** Годы, доступные на сервере (по возрастанию). */
export function useYears(): number[] {
  const infos = useYearInfos()
  return infos.length ? infos.map((x) => x.year) : fallbackYears()
}

/** Даты книг метки: от начала первой части до конца последней. Нет сведений — календарный год. */
export function yearRange(infos: YearInfo[], year: number): { from: string; to: string } {
  const y = infos.find((x) => x.year === year)
  if (y) {
    const parts = y.parts && y.parts.length ? y.parts : []
    const froms = [y.date_from, ...parts.map((p) => p.date_from)].filter(Boolean) as string[]
    const tos = [y.date_to, ...parts.map((p) => p.date_to)].filter(Boolean) as string[]
    if (froms.length && tos.length) return { from: froms.sort()[0], to: tos.sort().slice(-1)[0] }
  }
  return { from: `${year}-01-01`, to: `${year}-12-31` }
}

/** Метка года, которой принадлежит дата; нет подходящей книги — календарный год даты. */
export function labelOfDate(infos: YearInfo[], date: string): number {
  for (const y of infos) {
    const r = yearRange(infos, y.year)
    if (date >= r.from && date <= r.to) return y.year
  }
  return Number(date.slice(0, 4)) || new Date().getFullYear()
}
