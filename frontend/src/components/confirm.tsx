import { useEffect, useState } from 'react'
import { Modal } from './ui'

// Собственные диалоги приложения вместо window.confirm / window.prompt (во встроенных браузерах они могут быть подавлены).

interface ConfirmReq {
  kind: 'confirm'
  text: string
  title?: string
  okLabel?: string
  danger?: boolean
  resolve: (v: boolean) => void
}
interface PromptReq {
  kind: 'prompt'
  text: string
  title?: string
  initial?: string
  readOnly?: boolean
  okLabel?: string
  resolve: (v: string | null) => void
}
type Req = ConfirmReq | PromptReq

let push: ((r: Req) => void) | null = null
const queue: Req[] = []

function enqueue(r: Req) {
  if (push) push(r)
  else queue.push(r)
}

/** Спросить подтверждение. Возвращает true, если пользователь согласился. */
export function askConfirm(text: string, opts: { title?: string; okLabel?: string; danger?: boolean } = {}): Promise<boolean> {
  return new Promise((resolve) => enqueue({ kind: 'confirm', text, resolve, ...opts }))
}

/** Спросить строку текста. null — отмена. */
export function askText(text: string, opts: { title?: string; initial?: string; okLabel?: string; readOnly?: boolean } = {}): Promise<string | null> {
  return new Promise((resolve) => enqueue({ kind: 'prompt', text, resolve, ...opts }))
}

/** Место, где рисуются диалоги: один раз в корне приложения. */
export function ConfirmHost() {
  const [list, setList] = useState<Req[]>([])
  useEffect(() => {
    push = (r) => setList((l) => [...l, r])
    if (queue.length) setList((l) => [...l, ...queue.splice(0)])
    return () => {
      push = null
    }
  }, [])
  const cur = list[0]
  if (!cur) return null
  const done = () => setList((l) => l.slice(1))
  return cur.kind === 'confirm' ? (
    <ConfirmBox key={list.length + cur.text} req={cur} onDone={done} />
  ) : (
    <PromptBox key={list.length + cur.text} req={cur} onDone={done} />
  )
}

function ConfirmBox({ req, onDone }: { req: ConfirmReq; onDone: () => void }) {
  const close = (v: boolean) => {
    req.resolve(v)
    onDone()
  }
  return (
    <Modal
      title={req.title ?? 'Подтвердите действие'}
      onClose={() => close(false)}
      footer={
        <>
          <button className="btn" onClick={() => close(false)}>
            Отмена
          </button>
          <button className={'btn ' + (req.danger ? 'danger' : 'primary')} autoFocus onClick={() => close(true)}>
            {req.okLabel ?? 'Да'}
          </button>
        </>
      }
    >
      <div style={{ whiteSpace: 'pre-line' }}>{req.text}</div>
    </Modal>
  )
}

function PromptBox({ req, onDone }: { req: PromptReq; onDone: () => void }) {
  const [v, setV] = useState(req.initial ?? '')
  const close = (r: string | null) => {
    req.resolve(r)
    onDone()
  }
  return (
    <Modal
      title={req.title ?? req.text}
      onClose={() => close(null)}
      footer={
        <>
          <button className="btn" onClick={() => close(null)}>
            {req.readOnly ? 'Закрыть' : 'Отмена'}
          </button>
          {!req.readOnly && (
            <button className="btn primary" onClick={() => close(v)}>
              {req.okLabel ?? 'Готово'}
            </button>
          )}
        </>
      }
    >
      {req.title && <div style={{ marginBottom: 6 }}>{req.text}</div>}
      <input
        type="text"
        style={{ width: '100%' }}
        autoFocus
        value={v}
        readOnly={req.readOnly}
        onFocus={(e) => req.readOnly && e.target.select()}
        onChange={(e) => setV(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && !req.readOnly && close(v)}
      />
    </Modal>
  )
}
