import { askConfirm } from '../components/confirm'
import { useState } from 'react'
import { api, errMsg, saveFile, type ImportReport } from '../api'
import { ErrorBox } from '../components/ui'
import { reloadDataset, useDataset } from '../dataset'
import { resetYears } from '../years'
import { useIsAdmin } from '../components/role'
import { SETTING_PARTS } from './DatasetsTab'

const ALL = SETTING_PARTS.map((p) => p.k)
const PART_RU = Object.fromEntries(SETTING_PARTS.map((p) => [p.k, p.label])) as Record<string, string>
const PART_SHORT: Record<string, string> = {
  reports: 'отчёты',
  classifiers: 'классификаторы',
  names: 'названия счетов',
  closing: 'правила закрывающих ордеров',
  links: 'связи счетов',
  params: 'параметры набора',
  templates: 'шаблоны Excel',
}

function Parts({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  return (
    <div style={{ margin: '4px 0 8px' }}>
      {SETTING_PARTS.map((p) => (
        <label key={p.k} className="chk">
          <input type="checkbox" checked={value.includes(p.k)} onChange={(e) => onChange(e.target.checked ? [...value, p.k] : value.filter((x) => x !== p.k))} />
          {p.label}
        </label>
      ))}
    </div>
  )
}

/** Перенос настроек набора в файл и из файла: между наборами, на другой компьютер, для резервной копии. */
export default function TransferTab() {
  const isAdmin = useIsAdmin()
  const { data: ds } = useDataset()
  const [expParts, setExpParts] = useState<string[]>(ALL)
  const [impParts, setImpParts] = useState<string[]>(ALL)
  const [mode, setMode] = useState<'merge' | 'replace'>('merge')
  const [file, setFile] = useState<File | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [rep, setRep] = useState<ImportReport | null>(null)

  const doExport = async () => {
    setBusy('exp')
    setErr(null)
    try {
      const r = await api.exportSettings(expParts)
      saveFile(r.blob, r.filename)
      setInfo('Файл настроек сохранён. В нём нет паролей, подключений, пользователей и разметки книг.')
    } catch (e) {
      setErr(errMsg(e))
    } finally {
      setBusy(null)
    }
  }
  const doImport = async () => {
    if (!file) return setErr('Выберите файл настроек (.json), сохранённый этой программой.')
    if (mode === 'replace' && !(await askConfirm('Режим «заменить»: одноимённые отчёты, классификаторы, шаблоны и параметры набора будут заменены данными из файла, правила закрывающих ордеров — заменены целиком. Продолжить?', { okLabel: 'Заменить', danger: true }))) return
    setBusy('imp')
    setErr(null)
    setRep(null)
    try {
      const r = await api.importSettings(file, mode, impParts.length === ALL.length ? [] : impParts)
      setRep(r)
      setInfo('Настройки загружены.')
      resetYears()
      void reloadDataset()
    } catch (e) {
      setErr(errMsg(e))
    } finally {
      setBusy(null)
    }
  }
  const keys = rep ? [...new Set([...Object.keys(rep.created), ...Object.keys(rep.updated), ...Object.keys(rep.skipped)])] : []
  return (
    <>
      <p className="hint">
        Настройки набора «{ds?.name ?? ''}» можно сохранить в файл и загрузить в другой набор или на другой компьютер. В файл не попадают пароли, подключения, пользователи, разметка книг, ручная корреспонденция и загруженные данные.
      </p>
      <ErrorBox error={err} onClose={() => setErr(null)} />
      {info && <div className="msg ok">{info}</div>}

      <h3>Сохранить настройки в файл</h3>
      <p className="hint">Что включить в файл:</p>
      <Parts value={expParts} onChange={setExpParts} />
      <div className="toolbar">
        <button className="btn primary" onClick={doExport} disabled={busy !== null || expParts.length === 0}>
          {busy === 'exp' ? 'Готовим файл…' : 'Сохранить настройки в файл'}
        </button>
      </div>

      <h3 style={{ marginTop: 24 }}>Загрузить настройки из файла</h3>
      {!isAdmin && <div className="msg warn">Загружать настройки может администратор набора.</div>}
      <div className="toolbar">
        <input type="file" style={{ width: 340 }} accept=".json,application/json" disabled={!isAdmin} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
      </div>
      <label className="chk">
        <input type="radio" name="imode" checked={mode === 'merge'} disabled={!isAdmin} onChange={() => setMode('merge')} />
        Дополнить: добавить то, чего нет; одноимённые отчёты, классификаторы и шаблоны получат пометку «(2)», совпадающие названия счетов, связи, правила и уже заданные параметры не меняются
      </label>
      <label className="chk">
        <input type="radio" name="imode" checked={mode === 'replace'} disabled={!isAdmin} onChange={() => setMode('replace')} />
        Заменить: одноимённые отчёты, классификаторы, шаблоны и параметры заменяются данными из файла, правила закрывающих ордеров заменяются целиком
      </label>
      <p className="hint">Что загрузить из файла (чего в файле нет — пропустится):</p>
      <Parts value={impParts} onChange={setImpParts} />
      <div className="toolbar">
        <button className={'btn ' + (mode === 'replace' ? 'danger' : 'primary')} onClick={doImport} disabled={!isAdmin || busy !== null || !file || impParts.length === 0}>
          {busy === 'imp' ? 'Загружаем…' : mode === 'replace' ? 'Заменить настройки из файла' : 'Дополнить настройки из файла'}
        </button>
      </div>

      {rep && (
        <>
          <h4>Что сделано</h4>
          <table className="simple" style={{ maxWidth: 640 }}>
            <thead>
              <tr>
                <th>Раздел</th>
                <th className="r">Создано</th>
                <th className="r">Обновлено</th>
                <th className="r">Пропущено</th>
              </tr>
            </thead>
            <tbody>
              {keys.map((k) => (
                <tr key={k}>
                  <td>{PART_SHORT[k] ?? PART_RU[k] ?? k}</td>
                  <td className="num">{rep.created[k] ?? 0}</td>
                  <td className="num">{rep.updated[k] ?? 0}</td>
                  <td className="num">{rep.skipped[k] ?? 0}</td>
                </tr>
              ))}
              {keys.length === 0 && (
                <tr>
                  <td colSpan={4} className="muted">
                    В файле нет выбранных разделов.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
          {rep.resync_required && <div className="msg warn">Изменилась кодировка текстов: названия будут перечитаны из Logo при ближайшей проверке данных.</div>}
          {rep.plan_rebuild && <div className="msg warn">Изменился профиль плана счетов: планы счетов перечитываются в фоне.</div>}
          {rep.warnings.map((w, i) => (
            <div key={i} className="msg warn">
              {w}
            </div>
          ))}
        </>
      )}
    </>
  )
}
