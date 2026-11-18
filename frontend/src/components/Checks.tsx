import { useState } from 'react'
import type { Check } from '../api'

export const checkLevel = (c: Check): 'ok' | 'warn' | 'error' => c.level ?? (c.ok ? 'ok' : 'error')

const MARK = { ok: '✓', warn: '!', error: '✗' }
const WORD = { ok: 'в норме', warn: 'замечание', error: 'расхождение' }
const LONG = 110

function CheckItem({ c }: { c: Check }) {
  const lvl = checkLevel(c)
  const full = c.name + (c.detail ? ': ' + c.detail : '')
  const long = full.length > LONG
  const [open, setOpen] = useState(false)
  return (
    <div className={`check ${lvl}${long && !open ? ' clamp' : ''}`} title={WORD[lvl]}>
      <span className="check-mark">{MARK[lvl]}</span>
      <span className="check-text">
        {c.name}
        {c.detail ? <span className="mono">: {c.detail}</span> : null}
      </span>
      {long && (
        <button className="check-more no-print" onClick={() => setOpen(!open)}>
          {open ? 'свернуть' : 'подробнее'}
        </button>
      )}
    </div>
  )
}

/** Полоса контролей: зелёный — норма, жёлтый — замечание, красный — расхождение. */
export default function ChecksBar({ checks, warnings }: { checks?: Check[]; warnings?: string[] }) {
  if (!(checks?.length || warnings?.length)) return null
  const count = { ok: 0, warn: 0, error: 0 }
  checks?.forEach((c) => count[checkLevel(c)]++)
  return (
    <div className="checks">
      {checks && checks.length > 0 && (
        <div className="checks-sum no-print">
          Контроли:{' '}
          <span className="ok">{count.ok} в норме</span>
          {count.warn > 0 && <span className="warn"> · {count.warn} замечаний</span>}
          {count.error > 0 && <span className="bad"> · {count.error} расхождений</span>}
        </div>
      )}
      <div className="checks-list">
        {checks?.map((c, i) => (
          <CheckItem key={i} c={c} />
        ))}
      </div>
      {warnings?.map((w, i) => (
        <div key={i} className="warning">
          ⚠ {w}
        </div>
      ))}
    </div>
  )
}
