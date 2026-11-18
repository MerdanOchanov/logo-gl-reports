import { useState } from 'react'
import { api, type Account } from '../api'
import AccountTree from '../components/AccountTree'
import { ErrorBox, NotConfigured, useLoad } from '../components/ui'
import { useDataset } from '../dataset'
import { useYearInfos } from '../years'

export default function AccountsPage() {
  const infos = useYearInfos()
  const { data: ds } = useDataset()
  const years = infos.map((x) => x.year)
  const [picked, setPicked] = useState<number | null>(null)
  // год по умолчанию — последний из имеющихся книг (а не текущий календарный: книги за него может не быть)
  const now = new Date().getFullYear()
  const year = picked ?? (years.includes(now) ? now : years[years.length - 1] ?? null)
  const noBooks = !!ds && (ds.state === 'new' || ds.books_total === 0) && years.length === 0
  const { data, error, loading } = useLoad<Account[]>(() => (year == null ? Promise.resolve([]) : api.accounts(year)), [year])
  return (
    <div className="page">
      <div className="page-head">
        <h2>План счетов</h2>
        {year != null && (
          <label className="inline">
            за год
            <select value={year} onChange={(e) => setPicked(Number(e.target.value))}>
              {[...years].reverse().map((y) => (
                <option key={y}>{y}</option>
              ))}
            </select>
          </label>
        )}
      </div>
      {noBooks && <NotConfigured text="Планы счетов читаются из книг Logo — добавьте книги за нужные годы." />}
      {!noBooks && year == null && ds && <div className="muted pad">Нет загруженных лет: план счетов появится после загрузки данных (Настройки → Книги и годы).</div>}
      <ErrorBox error={error} />
      {loading && year != null && <div className="muted pad">Загрузка…</div>}
      {data && data.length > 0 && <AccountTree accounts={data} showSpecode extraCols />}
    </div>
  )
}
