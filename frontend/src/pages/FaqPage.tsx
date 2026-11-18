import { askText } from '../components/confirm'
import { useEffect, useMemo, useState } from 'react'
import { FAQ, FAQ_COUNT, type FaqItem } from '../faq'
import { navigate } from '../router'

const ALL_IDS = FAQ.flatMap((t) => t.items.map((i) => i.id))

const haystack = (i: FaqItem) => [i.q, ...i.text, ...(i.steps ?? []), ...(i.links ?? []).map((l) => l.label)].join(' ').toLowerCase()

export default function FaqPage({ anchor }: { anchor?: string }) {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState<Set<string>>(() => new Set(anchor && ALL_IDS.includes(anchor) ? [anchor] : []))
  const [copied, setCopied] = useState<string | null>(null)

  // переход по ссылке на конкретный ответ: раскрыть и прокрутить
  useEffect(() => {
    if (!anchor || !ALL_IDS.includes(anchor)) return
    setOpen((s) => new Set(s).add(anchor))
    const t = window.setTimeout(() => document.getElementById('faq-' + anchor)?.scrollIntoView({ block: 'start', behavior: 'smooth' }), 50)
    return () => window.clearTimeout(t)
  }, [anchor])

  const term = q.trim().toLowerCase()
  const topics = useMemo(
    () => FAQ.map((t) => ({ ...t, items: term ? t.items.filter((i) => haystack(i).includes(term)) : t.items })).filter((t) => t.items.length > 0),
    [term],
  )
  const found = topics.reduce((n, t) => n + t.items.length, 0)
  const toggle = (id: string) =>
    setOpen((s) => {
      const n = new Set(s)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })

  const copyLink = async (id: string) => {
    const url = window.location.href.split('#')[0] + '#/faq/' + id
    try {
      await navigator.clipboard.writeText(url)
      setCopied(id)
      window.setTimeout(() => setCopied((c) => (c === id ? null : c)), 2000)
    } catch {
      void askText('Скопируйте адрес ответа (Ctrl+C):', { title: 'Адрес ответа', initial: url, readOnly: true })
    }
  }

  return (
    <div className="page faq">
      <div className="page-head no-print">
        <h2>Вопросы и ответы</h2>
        <input type="search" placeholder="Поиск по вопросам и ответам…" value={q} onChange={(e) => setQ(e.target.value)} className="faq-search" autoFocus />
        <div className="spacer" />
        <button className="btn small" onClick={() => setOpen(new Set(topics.flatMap((t) => t.items.map((i) => i.id))))}>
          Раскрыть все
        </button>
        <button className="btn small" onClick={() => setOpen(new Set())}>
          Свернуть все
        </button>
      </div>
      <div className="hint no-print">{term ? `Найдено вопросов: ${found} из ${FAQ_COUNT}` : `Вопросов: ${FAQ_COUNT}. У каждого ответа есть ссылка: её можно отправить коллеге.`}</div>
      {found === 0 && <div className="muted pad">Ничего не найдено. Попробуйте другое слово, например «сальдо», «Excel» или «классификатор».</div>}
      {topics.map((t) => (
        <section key={t.id} className="faq-topic">
          <h3>{t.title}</h3>
          {t.items.map((i) => {
            const isOpen = open.has(i.id)
            return (
              <div key={i.id} id={'faq-' + i.id} className={'faq-item' + (isOpen ? ' open' : '')}>
                <button className="faq-q" aria-expanded={isOpen} onClick={() => toggle(i.id)}>
                  <span className="faq-arrow">{isOpen ? '▾' : '▸'}</span>
                  {i.q}
                </button>
                <div className="faq-a">
                  {i.text.map((p, k) => (
                    <p key={k}>{p}</p>
                  ))}
                  {i.steps && (
                    <ol>
                      {i.steps.map((s, k) => (
                        <li key={k}>{s}</li>
                      ))}
                    </ol>
                  )}
                  <div className="faq-actions no-print">
                    {i.links?.map((l) => (
                      <a
                        key={l.to + l.label}
                        className="btn small"
                        href={'#' + l.to}
                        onClick={(e) => {
                          e.preventDefault()
                          navigate(l.to)
                        }}
                      >
                        {l.label} →
                      </a>
                    ))}
                    <span className="spacer" />
                    <a className="faq-anchor" href={'#/faq/' + i.id} onClick={(e) => (e.preventDefault(), copyLink(i.id))} title="Скопировать адрес этого ответа">
                      {copied === i.id ? 'Адрес скопирован' : '🔗 Ссылка на ответ'}
                    </a>
                  </div>
                </div>
              </div>
            )
          })}
        </section>
      ))}
    </div>
  )
}
