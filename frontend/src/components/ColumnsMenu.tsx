import { useEffect, useRef, useState } from 'react'

/** Названия дополнительных колонок (ключи — из контракта). Порядок — порядок в меню. */
export const EXTRA_LABELS: Record<string, string> = {
  branch: 'Подразделение',
  department: 'Отдел',
  qty: 'Количество',
  source: 'Источник',
  author: 'Кто ввёл',
  entered_at: 'Когда введено',
  doc_no: '№ документа',
  text2_4: 'Доп. описание',
}
export const EXTRA_KEYS = Object.keys(EXTRA_LABELS)

/** Меню «Колонки»: флажки дополнительных колонок. */
export default function ColumnsMenu({
  available = EXTRA_KEYS,
  selected,
  onChange,
}: {
  available?: string[]
  selected: string[]
  onChange: (v: string[]) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [open])
  const keys = EXTRA_KEYS.filter((k) => available.includes(k))
  const toggle = (k: string) => onChange(keys.filter((x) => (x === k ? !selected.includes(k) : selected.includes(x))))
  return (
    <div className="cols-menu" ref={ref}>
      <button className="btn small" onClick={() => setOpen(!open)} title="Показать дополнительные колонки">
        Колонки{selected.length ? ` (+${selected.length})` : ''} ▾
      </button>
      {open && (
        <div className="cols-pop">
          {keys.map((k) => (
            <label key={k} className="chk">
              <input type="checkbox" checked={selected.includes(k)} onChange={() => toggle(k)} /> {EXTRA_LABELS[k]}
            </label>
          ))}
          {selected.length > 0 && (
            <button className="btn small ghost" onClick={() => onChange([])}>
              Убрать все
            </button>
          )}
        </div>
      )}
    </div>
  )
}
