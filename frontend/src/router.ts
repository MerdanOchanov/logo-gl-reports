import { useEffect, useState } from 'react'
import { askConfirm } from './components/confirm'

const read = () => window.location.hash.replace(/^#/, '') || '/reports'

/** Защита от потери несохранённых изменений: экран отчёта ставит функцию, возвращающую текст предупреждения (или null). */
let guard: (() => string | null) | null = null
let lastRoute = read()
export function setNavGuard(g: (() => string | null) | null) {
  guard = g
}

/** Спросить, можно ли уйти (собственный диалог приложения). Нет предупреждения — сразу «да». */
export function confirmLeave(): Promise<boolean> {
  const msg = guard?.()
  if (!msg) return Promise.resolve(true)
  return askConfirm(msg, { title: 'Несохранённые изменения', okLabel: 'Уйти без сохранения', danger: true })
}

function go(path: string, replace: boolean) {
  lastRoute = path
  if (replace) window.history.replaceState(null, '', '#' + path)
  else window.location.hash = path
  window.dispatchEvent(new HashChangeEvent('hashchange'))
}

export function navigate(path: string, replace = false) {
  if (path === lastRoute && !replace) return
  if (replace || !guard?.()) return go(path, replace)
  void confirmLeave().then((ok) => ok && go(path, replace))
}

export function useRoute(): string {
  const [r, setR] = useState(read())
  useEffect(() => {
    const h = () => {
      const now = read()
      if (now !== lastRoute) {
        // переход кнопками «назад/вперёд» браузера или по ссылке
        if (guard?.()) {
          window.history.replaceState(null, '', '#' + lastRoute)
          void confirmLeave().then((ok) => ok && go(now, false))
          return
        }
        lastRoute = now
      }
      setR(now)
    }
    window.addEventListener('hashchange', h)
    return () => window.removeEventListener('hashchange', h)
  }, [])
  return r
}
