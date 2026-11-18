import { useEffect, useState } from 'react'
import { api, errMsg, type Fiche } from '../api'
import { fmtDate, fmtMoney, fmtQty } from '../format'
import { ErrorBox, Modal } from './ui'

type PairRow = { dt_account: string; kt_account: string; amount: number }

const METHOD: Record<string, string> = { PROP: 'пропорционально', CROSS: 'по перекрёстной ссылке', SEQ: 'по порядку', MANUAL: 'вручную' }

export default function FicheModal({
  firm,
  ficheId,
  ficheNo,
  onClose,
  editable,
  onChanged,
}: {
  firm: number
  ficheId: number
  ficheNo: string
  onClose: () => void
  editable?: boolean
  onChanged?: () => void
}) {
  const [f, setF] = useState<Fiche | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [edit, setEdit] = useState<PairRow[] | null>(null)
  const [info, setInfo] = useState<string | null>(null)
  const [tick, setTick] = useState(0)
  useEffect(() => {
    api
      .fiche(firm, ficheId)
      .then(setF)
      .catch((e) => setError(errMsg(e)))
  }, [firm, ficheId, tick])
  const sd = f ? f.lines.reduce((a, l) => a + (l.dt || 0), 0) : 0
  const hasQty = !!f && f.lines.some((l) => l.qty != null && l.qty !== 0)
  const sk = f ? f.lines.reduce((a, l) => a + (l.kt || 0), 0) : 0
  const updPair = (i: number, p: Partial<PairRow>) => setEdit((l) => (l ? l.map((x, k) => (k === i ? { ...x, ...p } : x)) : l))
  const editSum = edit ? edit.reduce((a, p) => a + (Number(p.amount) || 0), 0) : 0
  const save = async () => {
    if (!edit) return
    try {
      await api.saveOverride({ firm, fiche_id: ficheId, pairs: edit.map((p) => ({ ...p, amount: Number(p.amount) || 0 })) })
      setEdit(null)
      setInfo('Пары закреплены вручную.')
      setTick((t) => t + 1)
      onChanged?.()
    } catch (e) {
      setError(errMsg(e))
    }
  }
  const reset = async () => {
    try {
      await api.deleteOverride(firm, ficheId)
      setInfo('Ручное закрепление снято, пары пересчитаны автоматически.')
      setTick((t) => t + 1)
      onChanged?.()
    } catch (e) {
      setError(errMsg(e))
    }
  }
  return (
    <Modal title={`Ордер № ${ficheNo}${f ? ' от ' + fmtDate(f.date) : ''}`} onClose={onClose} wide>
      <ErrorBox error={error} onClose={() => setError(null)} />
      {info && <div className="msg ok">{info}</div>}
      {!f && !error && <div className="muted pad">Загрузка…</div>}
      {f && (
        <>
          {f.text && <p className="fiche-text">{f.text}</p>}
          {f.text2_4 && <p className="fiche-text muted">{f.text2_4}</p>}
          <div className="fiche-attrs">
            {([
              ['Источник', f.source],
              ['Кто ввёл', f.author],
              ['Когда введено', f.entered_at],
              ['№ документа', f.doc_no],
            ] as const)
              .filter(([, v]) => v)
              .map(([k, v]) => (
                <span key={k}>
                  <span className="muted">{k}:</span> <b>{v}</b>
                </span>
              ))}
          </div>
          <h4>Проводки</h4>
          <table className="simple">
            <thead>
              <tr>
                <th>№</th>
                <th>Счёт</th>
                <th>Название</th>
                <th>Подразделение</th>
                <th className="r">Дебет</th>
                <th className="r">Кредит</th>
                <th>Содержание</th>
                {hasQty && <th className="r">Количество</th>}
              </tr>
            </thead>
            <tbody>
              {f.lines.map((l, i) => (
                <tr key={i}>
                  <td>{l.line_no ?? i + 1}</td>
                  <td className="nowrap">{l.account}</td>
                  <td>{l.account_name}</td>
                  <td className="nowrap" title={l.department ? 'Отдел: ' + l.department : undefined}>
                    {l.branch}
                  </td>
                  <td className="num">{fmtMoney(l.dt)}</td>
                  <td className="num">{fmtMoney(l.kt)}</td>
                  <td className="txt-cell">{l.text}</td>
                  {hasQty && <td className="num">{fmtQty(l.qty)}</td>}
                </tr>
              ))}
              <tr className="bold">
                <td colSpan={4}>Итого</td>
                <td className="num">{fmtMoney(sd)}</td>
                <td className="num">{fmtMoney(sk)}</td>
                <td />
                {hasQty && <td />}
              </tr>
            </tbody>
          </table>
          <h4>Корреспонденция (пары Дт/Кт)</h4>
          {!edit && (
            <>
              <table className="simple">
                <thead>
                  <tr>
                    <th>Дебет</th>
                    <th>Кредит</th>
                    <th className="r">Сумма</th>
                    <th>Метод</th>
                  </tr>
                </thead>
                <tbody>
                  {f.pairs.map((p, i) => (
                    <tr key={i}>
                      <td>{p.dt_account}</td>
                      <td>{p.kt_account}</td>
                      <td className="num">{fmtMoney(p.amount)}</td>
                      <td>
                        {METHOD[p.method ?? ''] ?? p.method}
                        {p.disputed ? <span className="tag red"> спорная</span> : null}
                      </td>
                    </tr>
                  ))}
                  {f.pairs.length === 0 && (
                    <tr>
                      <td colSpan={4} className="muted">
                        Пары для этого ордера не рассчитаны.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
              {editable && (
                <div className="toolbar">
                  <button
                    className="btn"
                    onClick={() => setEdit(f.pairs.map((p) => ({ dt_account: p.dt_account, kt_account: p.kt_account, amount: p.amount })))}
                  >
                    Закрепить пары вручную…
                  </button>
                  <button className="btn" onClick={reset}>
                    Снять ручное закрепление
                  </button>
                </div>
              )}
            </>
          )}
          {edit && (
            <>
              <table className="simple">
                <thead>
                  <tr>
                    <th>Дебет</th>
                    <th>Кредит</th>
                    <th className="r">Сумма</th>
                    <th style={{ width: 30 }} />
                  </tr>
                </thead>
                <tbody>
                  {edit.map((p, i) => (
                    <tr key={i}>
                      <td>
                        <input type="text" value={p.dt_account} list="fiche-accs" onChange={(e) => updPair(i, { dt_account: e.target.value })} />
                      </td>
                      <td>
                        <input type="text" value={p.kt_account} list="fiche-accs" onChange={(e) => updPair(i, { kt_account: e.target.value })} />
                      </td>
                      <td>
                        <input type="number" step="0.01" value={p.amount} style={{ textAlign: 'right' }} onChange={(e) => updPair(i, { amount: Number(e.target.value) })} />
                      </td>
                      <td>
                        <button className="btn small ghost" onClick={() => setEdit((l) => (l ? l.filter((_, k) => k !== i) : l))}>
                          ✕
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <datalist id="fiche-accs">
                {[...new Set(f.lines.map((l) => l.account))].map((a) => (
                  <option key={a} value={a} />
                ))}
              </datalist>
              <div className="toolbar">
                <button className="btn small" onClick={() => setEdit((l) => [...(l ?? []), { dt_account: '', kt_account: '', amount: 0 }])}>
                  + Пара
                </button>
                <span className={Math.abs(editSum - sd) < 0.005 ? 'muted' : 'neg'}>
                  Сумма пар {fmtMoney(editSum) || '0,00'} {Math.abs(editSum - sd) < 0.005 ? '= обороту ордера' : `≠ обороту ордера ${fmtMoney(sd)}`}
                </span>
                <div className="spacer" />
                <button className="btn" onClick={() => setEdit(null)}>
                  Отмена
                </button>
                <button className="btn primary" onClick={save}>
                  Закрепить
                </button>
              </div>
            </>
          )}
        </>
      )}
    </Modal>
  )
}
