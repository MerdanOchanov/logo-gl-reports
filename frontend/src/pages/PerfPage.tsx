import { useMemo } from 'react'
import type { Column, Result, ResultRow } from '../api'
import ResultTable from '../components/ResultTable'

/** Только для разработки (#/perf): синтетический результат 3000 строк × 120 граф для проверки скорости таблицы. */
export default function PerfPage() {
  const result = useMemo<Result>(() => {
    const columns: Column[] = Array.from({ length: 120 }, (_, i) => ({ key: 'c' + i, title: `Кт ${100 + i}`, type: 'money' as const, group: i < 60 ? 'Группа А' : 'Группа Б' }))
    const rows: ResultRow[] = []
    for (let i = 0; i < 3000; i++) {
      const values: Record<string, number> = {}
      for (let c = 0; c < 120; c++) values[columns[c].key] = ((i * 7 + c * 13) % 17) * 12345.67 * (c % 5 === 0 ? -1 : 1)
      rows.push({ id: 'r' + i, parent: null, level: 0, kind: 'account', code: String(100 + (i % 900)) + '.' + i, name: 'Счёт ' + i, values })
    }
    return { title: 'perf', columns, rows, totals: { c0: 1 } }
  }, [])
  return (
    <div className="page" style={{ height: '100%', maxWidth: 'none', display: 'flex', flexDirection: 'column' }}>
      <ResultTable result={result} />
    </div>
  )
}
