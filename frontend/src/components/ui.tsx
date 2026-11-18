import { useEffect, useRef, useState, type ReactNode } from 'react'
import { api, errMsg, type Status } from '../api'
import { paramsUnset, useDataset } from '../dataset'

const modalStack: number[] = []
let modalSeq = 0

export function Modal({
  title,
  onClose,
  children,
  wide,
  footer,
}: {
  title: string
  onClose: () => void
  children: ReactNode
  wide?: boolean
  footer?: ReactNode
}) {
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  useEffect(() => {
    // Escape закрывает только самое верхнее из открытых окон
    const id = ++modalSeq
    modalStack.push(id)
    const h = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || modalStack[modalStack.length - 1] !== id || e.defaultPrevented) return
      e.preventDefault()
      closeRef.current()
    }
    window.addEventListener('keydown', h)
    return () => {
      window.removeEventListener('keydown', h)
      const i = modalStack.indexOf(id)
      if (i >= 0) modalStack.splice(i, 1)
    }
  }, [])
  return (
    <div className="modal-back no-print" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={'modal' + (wide ? ' wide' : '')}>
        <div className="modal-head">
          <b>{title}</b>
          <button className="btn small ghost" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  )
}

/** Сообщение сервера «набор ещё не настроен» — к нему добавляется кнопка перехода к книгам. */
export const isNotConfigured = (m: string | null | undefined) => !!m && /ещё не настроен/i.test(m)

export function ErrorBox({ error, onClose }: { error: string | null; onClose?: () => void }) {
  if (!error) return null
  return (
    <div className="msg err no-print">
      <span>{error}</span>
      {isNotConfigured(error) && (
        <button className="btn small" onClick={() => (window.location.hash = '#/settings/books')}>
          Перейти к книгам
        </button>
      )}
      {onClose && (
        <button className="btn small ghost" onClick={onClose}>
          ✕
        </button>
      )}
    </div>
  )
}

/** Набор данных без книг: понятное сообщение и переход к настройке книг. */
export function NotConfigured({ text }: { text?: string }) {
  const { data: ds } = useDataset()
  const both = paramsUnset(ds)
  return (
    <div className="msg warn">
      <span>
        <b>Набор ещё не настроен.</b> {text ?? 'В нём нет книг Logo, поэтому показывать пока нечего.'}
        {both && (
          <ol style={{ margin: '6px 0 0', paddingLeft: 20 }}>
            <li>Задайте параметры набора: валюту, кодировку и план счетов (Настройки → Параметры набора).</li>
            <li>Добавьте книги Logo за нужные годы (Настройки → Книги и годы).</li>
          </ol>
        )}
      </span>
      <button className="btn small" onClick={() => (window.location.hash = '#/settings/books')}>
        Перейти к книгам
      </button>
      {both && (
        <button className="btn small" onClick={() => (window.location.hash = '#/settings/params')}>
          Перейти к параметрам
        </button>
      )}
    </div>
  )
}

export function Spinner({ text = 'Загрузка…' }: { text?: string }) {
  return <div className="muted pad">{text}</div>
}

/** Простой хук загрузки данных. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [tick, setTick] = useState(0)
  useEffect(() => {
    let alive = true
    setLoading(true)
    fn()
      .then((d) => {
        if (alive) {
          setData(d)
          setError(null)
        }
      })
      .catch((e) => alive && setError(errMsg(e)))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick])
  return { data, error, loading, reload: () => setTick((t) => t + 1), setData }
}

/** Индикатор состояния синхронизации; пока идёт синхронизация — опрос раз в 1.5 с. */
export function StatusBadge() {
  const [st, setSt] = useState<Status | null>(null)
  const [err, setErr] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => {
    let alive = true
    const tick = async () => {
      let next = 8000
      try {
        const s = await api.status()
        if (!alive) return
        setSt(s)
        setErr(false)
        if (s.syncing) next = 1500
      } catch {
        if (alive) setErr(true)
      }
      if (alive) timer.current = window.setTimeout(tick, next)
    }
    tick()
    return () => {
      alive = false
      window.clearTimeout(timer.current)
    }
  }, [])
  if (err) return <span className="status bad">Нет связи с сервером</span>
  if (!st) return <span className="status">…</span>
  let pct: number | null = null
  const p = st.progress
  if (typeof p === 'number') pct = p <= 1 ? Math.round(p * 100) : Math.round(p)
  else if (p && p.total) pct = Math.round(((p.done ?? 0) / p.total) * 100)
  return (
    <span className={'status ' + (!st.logo_ok ? 'warn' : (st.syncing || st.queued) ? 'sync' : 'ok')}>
      {st.queued ? (
        <>Синхронизация: в очереди</>
      ) : st.syncing ? (
        <>
          Синхронизация{pct !== null ? ` ${pct}%` : '…'}
          {pct !== null && (
            <span className="bar">
              <i style={{ width: pct + '%' }} />
            </span>
          )}
        </>
      ) : (
        <>
          Данные: {st.firms_synced} из {st.firms_total} лет загружено
        </>
      )}
      {!st.logo_ok && ' · нет связи с Logo'}
    </span>
  )
}

/** Диалог сохранения отчёта: название, папка, комментарий версии. */
export function SaveDialog({
  initial,
  folders,
  asNew,
  onSave,
  onClose,
}: {
  initial: { name: string; folder: string | null }
  folders: string[]
  asNew: boolean
  onSave: (v: { name: string; folder: string | null; comment: string }) => Promise<void>
  onClose: () => void
}) {
  const [name, setName] = useState(initial.name)
  const [folder, setFolder] = useState(initial.folder ?? '')
  const [comment, setComment] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const go = async () => {
    if (!name.trim()) return setErr('Введите название отчёта.')
    setBusy(true)
    try {
      await onSave({ name: name.trim(), folder: folder.trim() || null, comment: comment.trim() })
    } catch (e) {
      setErr(errMsg(e))
      setBusy(false)
    }
  }
  return (
    <Modal
      title={asNew ? 'Сохранить отчёт как…' : 'Сохранить отчёт'}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Отмена
          </button>
          <button className="btn primary" onClick={go} disabled={busy}>
            Сохранить
          </button>
        </>
      }
    >
      <ErrorBox error={err} />
      <div className="form" onKeyDown={(e) => e.key === 'Enter' && go()}>
        <label>
          название
          <input type="text" autoFocus value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          папка (можно оставить пустой)
          <input type="text" list="save-folders" value={folder} onChange={(e) => setFolder(e.target.value)} />
          <datalist id="save-folders">
            {folders.map((f) => (
              <option key={f} value={f} />
            ))}
          </datalist>
        </label>
        {!asNew && (
          <label>
            комментарий к версии (попадёт в историю)
            <input type="text" value={comment} onChange={(e) => setComment(e.target.value)} placeholder="например: добавил счёт 320" />
          </label>
        )}
      </div>
    </Modal>
  )
}
