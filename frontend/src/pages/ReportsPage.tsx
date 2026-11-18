import { askConfirm } from '../components/confirm'
import { useMemo, useState } from 'react'
import { api, errMsg, type ReportInfo, type ReportVersion } from '../api'
import { ErrorBox, Modal, useLoad } from '../components/ui'
import { REPORT_TYPES, fmtDateTime, typeLabel } from '../format'
import { navigate } from '../router'
import { useDataset } from '../dataset'
import Welcome from './Welcome'

export default function ReportsPage() {
  const { data: ds } = useDataset()
  // новая пустая установка или не настроенное подключение — вместо списка отчётов приветствие с тремя шагами
  if (ds && (!ds.configured || ds.state === 'new' || (ds.books_main ?? 0) === 0)) return <Welcome ds={ds} />
  return <ReportsList />
}

function ReportsList() {
  const { data, error, loading, reload } = useLoad<ReportInfo[]>(() => api.reports(), [])
  const [q, setQ] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [showNew, setShowNew] = useState(false)
  const [hist, setHist] = useState<ReportInfo | null>(null)

  const groups = useMemo(() => {
    const t = q.trim().toLowerCase()
    const m = new Map<string, ReportInfo[]>()
    for (const r of data ?? []) {
      if (t && !(r.name.toLowerCase().includes(t) || (r.folder ?? '').toLowerCase().includes(t) || typeLabel(r.type).toLowerCase().includes(t))) continue
      const k = r.folder || ''
      m.set(k, [...(m.get(k) ?? []), r])
    }
    return [...m.entries()].sort((a, b) => (a[0] === '' ? 1 : b[0] === '' ? -1 : a[0].localeCompare(b[0], 'ru')))
  }, [data, q])

  const act = async (f: () => Promise<unknown>) => {
    try {
      await f()
      reload()
    } catch (e) {
      setErr(errMsg(e))
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <h2>Отчёты</h2>
        <input type="search" placeholder="Поиск по названию или папке…" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 300 }} />
        <div className="spacer" />
        <button className="btn primary needs-edit" onClick={() => setShowNew(true)}>
          + Новый отчёт
        </button>
      </div>
      <ErrorBox error={error || err} onClose={() => setErr(null)} />
      {loading && !data && <div className="muted pad">Загрузка…</div>}
      {data && data.length === 0 && (
        <div className="big-empty">
          <h2>Отчётов пока нет</h2>
          <p>
            Здесь будут ваши сохранённые отчёты: оборотно-сальдовая ведомость, карточка и анализ счёта, шахматка, главная книга, баланс и другие. Данные берутся из главной книги Logo — выберите вид отчёта,
            задайте период и нажмите «Сформировать». Чтобы посмотреть, как это выглядит, создайте готовые примеры.
          </p>
          {err && <div className="msg err">{err}</div>}
          <div className="quick">
            <button
              className="btn primary needs-edit"
              onClick={() =>
                act(async () => {
                  await api.demoSeed()
                })
              }
            >
              Создать примеры
            </button>
            <button className="btn needs-edit" onClick={() => setShowNew(true)}>
              Новый отчёт
            </button>
          </div>
        </div>
      )}
      {groups.map(([folder, items]) => (
        <section key={folder} className="folder">
          <h3>📁 {folder || 'Без папки'}</h3>
          <table className="simple">
            <thead>
              <tr>
                <th>Название</th>
                <th style={{ width: 240 }}>Тип</th>
                <th style={{ width: 150 }}>Изменён</th>
                <th style={{ width: 300 }} />
              </tr>
            </thead>
            <tbody>
              {items.map((r) => (
                <tr key={r.id}>
                  <td>
                    <a href={`#/report/${r.id}`} className="link">
                      {r.name}
                    </a>
                  </td>
                  <td>{typeLabel(r.type)}</td>
                  <td className="nowrap">{fmtDateTime(r.updated_at)}</td>
                  <td className="actions">
                    <button className="btn small" onClick={() => navigate(`/report/${r.id}`)}>
                      Открыть
                    </button>
                    <button className="btn small needs-edit" onClick={() => act(() => api.copyReport(r.id))}>
                      Копировать
                    </button>
                    <button className="btn small" onClick={() => setHist(r)}>
                      История
                    </button>
                    <button
                      className="btn small danger needs-edit"
                      onClick={() => askConfirm(`Удалить отчёт «${r.name}»? Это нельзя отменить.`, { okLabel: 'Удалить', danger: true }).then((ok) => { if (ok) void act(() => api.deleteReport(r.id)) })}
                    >
                      Удалить
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
      {showNew && (
        <Modal title="Новый отчёт — выберите вид" onClose={() => setShowNew(false)}>
          <div className="type-grid">
            {REPORT_TYPES.map((t) => (
              <button key={t.type} className="type-card" onClick={() => (setShowNew(false), navigate(`/report/new/${t.type}`))}>
                <b>{t.label}</b>
                <span>{t.hint}</span>
                {!t.ready && <em>в разработке</em>}
              </button>
            ))}
          </div>
        </Modal>
      )}
      {hist && <History report={hist} onClose={() => setHist(null)} />}
    </div>
  )
}

function History({ report, onClose }: { report: ReportInfo; onClose: () => void }) {
  const { data, error, loading } = useLoad<ReportVersion[]>(() => api.versions(report.id), [report.id])
  const [err, setErr] = useState<string | null>(null)
  const restore = async (v: ReportVersion) => {
    if (!v.definition) return
    try {
      const r = await api.createReport({
        name: `${report.name} (версия от ${fmtDateTime(v.saved_at)})`,
        type: report.type,
        folder: report.folder,
        definition: v.definition,
      })
      onClose()
      navigate(`/report/${r.id}`)
    } catch (e) {
      setErr(errMsg(e))
    }
  }
  return (
    <Modal title={`История версий: ${report.name}`} onClose={onClose}>
      <ErrorBox error={error || err} />
      {loading && <div className="muted pad">Загрузка…</div>}
      {data && data.length === 0 && <div className="muted pad">Версий пока нет.</div>}
      {data && data.length > 0 && (
        <table className="simple">
          <thead>
            <tr>
              <th>Сохранено</th>
              <th>Комментарий</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data.map((v) => (
              <tr key={v.id}>
                <td className="nowrap">{fmtDateTime(v.saved_at)}</td>
                <td>{v.comment}</td>
                <td className="actions">
                  {v.definition && (
                    <button className="btn small" onClick={() => restore(v)} title="Создать новый отчёт с параметрами этой версии">
                      Открыть копией
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Modal>
  )
}
