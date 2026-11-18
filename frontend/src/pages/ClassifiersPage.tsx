import { askConfirm, askText } from '../components/confirm'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, errMsg, type Classifier, type ClassifierInfo, type ClsGroup, type ClsRule, type ResolveResult, type RuleKind } from '../api'
import { ErrorBox, Modal, useLoad } from '../components/ui'
import { fmtMoney } from '../format'
import { navigate, setNavGuard } from '../router'

export default function ClassifiersPage({ editId }: { editId?: number }) {
  return editId !== undefined ? <Editor id={editId} /> : <List />
}

// ---------------------------------------------------------------- список

function List() {
  const { data, error, loading, reload } = useLoad<ClassifierInfo[]>(() => api.classifiers(), [])
  const [err, setErr] = useState<string | null>(null)
  const [imp, setImp] = useState(false)
  const act = async (f: () => Promise<unknown>) => {
    try {
      await f()
      reload()
    } catch (e) {
      setErr(errMsg(e))
    }
  }
  const create = async () => {
    const name = (await askText('Название нового классификатора', { okLabel: 'Создать' }))?.trim()
    if (!name) return
    try {
      const r = await api.createClassifier(name)
      navigate(`/classifiers/${r.id}`)
    } catch (e) {
      setErr(errMsg(e))
    }
  }
  return (
    <div className="page">
      <div className="page-head">
        <h2>Классификаторы</h2>
        <div className="spacer" />
        <button className="btn needs-edit" onClick={() => setImp(true)}>
          Импорт из меток Logo…
        </button>
        <button className="btn primary needs-edit" onClick={create}>
          + Новый классификатор
        </button>
      </div>
      <p className="hint">Классификатор группирует счета (например, по российским кодам) и используется в строках отчётов.</p>
      <ErrorBox error={error || err} onClose={() => setErr(null)} />
      {loading && !data && <div className="muted pad">Загрузка…</div>}
      {data && data.length === 0 && <div className="muted pad">Классификаторов пока нет.</div>}
      {data && data.length > 0 && (
        <table className="simple">
          <thead>
            <tr>
              <th>Название</th>
              <th>Описание</th>
              <th className="r" style={{ width: 80 }}>
                Групп
              </th>
              <th className="r" style={{ width: 80 }}>
                Правил
              </th>
              <th style={{ width: 280 }} />
            </tr>
          </thead>
          <tbody>
            {data.map((c) => (
              <tr key={c.id}>
                <td>
                  <a className="link" href={`#/classifiers/${c.id}`}>
                    {c.name}
                  </a>
                </td>
                <td>{c.description}</td>
                <td className="num">{c.groups_count}</td>
                <td className="num">{c.rules_count}</td>
                <td className="actions">
                  <button className="btn small" onClick={() => navigate(`/classifiers/${c.id}`)}>
                    Открыть
                  </button>
                  <button className="btn small needs-edit" onClick={() => act(() => api.copyClassifier(c.id))}>
                    Копировать
                  </button>
                  <button
                    className="btn small danger needs-edit"
                    onClick={() => askConfirm(`Удалить классификатор «${c.name}»? Отчёты, которые его используют, перестанут формироваться.`, { okLabel: 'Удалить', danger: true }).then((ok) => { if (ok) void act(() => api.deleteClassifier(c.id)) })}
                  >
                    Удалить
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {imp && <ImportModal onClose={() => setImp(false)} />}
    </div>
  )
}

function ImportModal({ onClose }: { onClose: () => void }) {
  const [year, setYear] = useState(new Date().getFullYear())
  const [name, setName] = useState('Российские коды (из меток Logo)')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const go = async () => {
    setBusy(true)
    try {
      const r = await api.importSpecode(year, name)
      onClose()
      navigate(`/classifiers/${r.id}`)
    } catch (e) {
      setErr(errMsg(e))
      setBusy(false)
    }
  }
  return (
    <Modal
      title="Импорт классификатора из меток Logo"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Отмена
          </button>
          <button className="btn primary" onClick={go} disabled={busy || !name}>
            {busy ? 'Импорт…' : 'Импортировать'}
          </button>
        </>
      }
    >
      <ErrorBox error={err} />
      <div className="form">
        <label>
          Год, за который брать метки
          <input type="number" value={year} min={2003} max={2030} onChange={(e) => setYear(Number(e.target.value))} />
        </label>
        <label>
          Название классификатора
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <p className="hint">Будут созданы группы по значениям метки (SPECODE) плана счетов за выбранный год и правила «метка Logo».</p>
      </div>
    </Modal>
  )
}

// ---------------------------------------------------------------- редактор

const KIND_LABEL: Record<RuleKind, string> = {
  mask: 'Маска',
  range: 'Диапазон',
  list: 'Список',
  specode: 'Метка Logo',
}
const KIND_HINT: Record<RuleKind, string> = {
  mask: '329.01*  (без звёздочки — точный код и все его подчинённые)',
  range: '150..157  (по корню/префиксу включительно)',
  list: '100, 102.01, 150  (коды через запятую)',
  specode: 'значение метки Logo, например 76',
}

let tmpId = -1

function Editor({ id }: { id: number }) {
  const [doc, setDoc] = useState<Classifier | null>(null)
  const [dirty, setDirty] = useState(false)
  useEffect(() => {
    if (!dirty) return
    setNavGuard(() => 'В классификаторе есть несохранённые изменения. Уйти без сохранения?')
    const h = (e: BeforeUnloadEvent) => (e.preventDefault(), (e.returnValue = ''))
    window.addEventListener('beforeunload', h)
    return () => {
      window.removeEventListener('beforeunload', h)
      setNavGuard(null)
    }
  }, [dirty])
  const [error, setError] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)
  const [sel, setSel] = useState<number | null>(null)
  const [open, setOpen] = useState<Set<number>>(new Set())
  const [year, setYear] = useState(new Date().getFullYear())
  const [res, setRes] = useState<ResolveResult | null>(null)
  const [checking, setChecking] = useState(false)

  useEffect(() => {
    api
      .classifier(id)
      .then((c) => {
        setDoc(c)
        setOpen(new Set(c.groups.map((g) => g.id)))
        setSel(c.groups.find((g) => g.parent_id == null)?.id ?? null)
      })
      .catch((e) => setError(errMsg(e)))
  }, [id])

  useEffect(() => {
    if (!info) return
    const t = setTimeout(() => setInfo(null), 3000)
    return () => clearTimeout(t)
  }, [info])

  const edit = useCallback((f: (d: Classifier) => Classifier) => {
    setDoc((d) => (d ? f(d) : d))
    setDirty(true)
  }, [])

  const kids = useMemo(() => {
    const m = new Map<number | null, ClsGroup[]>()
    for (const g of doc?.groups ?? []) {
      const l = m.get(g.parent_id ?? null) ?? []
      l.push(g)
      m.set(g.parent_id ?? null, l)
    }
    for (const l of m.values()) l.sort((a, b) => a.sort - b.sort || a.code.localeCompare(b.code, 'ru', { numeric: true }))
    return m
  }, [doc?.groups])

  const groupName = useCallback(
    (gid: number | { id: number; code?: string; name?: string }) => {
      const idn = typeof gid === 'number' ? gid : gid.id
      const g = doc?.groups.find((x) => x.id === idn)
      if (g) return `${g.code} ${g.name}`
      return typeof gid === 'object' ? `${gid.code ?? ''} ${gid.name ?? ''}`.trim() || String(idn) : String(idn)
    },
    [doc?.groups],
  )

  if (!doc) return <div className="page">{error ? <ErrorBox error={error} /> : <div className="muted pad">Загрузка…</div>}</div>

  const group = doc.groups.find((g) => g.id === sel) ?? null
  const rules = doc.rules.filter((r) => r.group_id === sel)

  const descendants = (gid: number): Set<number> => {
    const s = new Set<number>([gid])
    const stack = [gid]
    while (stack.length) {
      const c = stack.pop()!
      for (const k of kids.get(c) ?? []) {
        s.add(k.id)
        stack.push(k.id)
      }
    }
    return s
  }

  const addGroup = (parent: number | null) => {
    const siblings = kids.get(parent) ?? []
    const g: ClsGroup = {
      id: tmpId--,
      parent_id: parent,
      code: String(siblings.length + 1),
      name: 'Новая группа',
      sort: (siblings[siblings.length - 1]?.sort ?? 0) + 1,
    }
    edit((d) => ({ ...d, groups: [...d.groups, g] }))
    if (parent !== null) setOpen((o) => new Set(o).add(parent))
    setSel(g.id)
  }

  const patchGroup = (gid: number, p: Partial<ClsGroup>) => edit((d) => ({ ...d, groups: d.groups.map((g) => (g.id === gid ? { ...g, ...p } : g)) }))

  const delGroup = async (gid: number) => {
    const ds = descendants(gid)
    const nr = doc.rules.filter((r) => ds.has(r.group_id)).length
    if (!(await askConfirm(`Удалить группу «${groupName(gid)}»${ds.size > 1 ? ` вместе с подгруппами (${ds.size - 1})` : ''} и её правила (${nr})?`, { okLabel: 'Удалить', danger: true }))) return
    edit((d) => ({ ...d, groups: d.groups.filter((g) => !ds.has(g.id)), rules: d.rules.filter((r) => !ds.has(r.group_id)) }))
    setSel(null)
  }

  const moveGroup = (gid: number, dir: -1 | 1) => {
    const g = doc.groups.find((x) => x.id === gid)!
    const sib = kids.get(g.parent_id ?? null) ?? []
    const i = sib.findIndex((x) => x.id === gid)
    const j = i + dir
    if (j < 0 || j >= sib.length) return
    const order = sib.map((x) => x.id)
    ;[order[i], order[j]] = [order[j], order[i]]
    edit((d) => ({ ...d, groups: d.groups.map((x) => (order.includes(x.id) ? { ...x, sort: order.indexOf(x.id) + 1 } : x)) }))
  }

  const reparent = (gid: number, parent: number | null) => {
    const siblings = kids.get(parent) ?? []
    patchGroup(gid, { parent_id: parent, sort: (siblings[siblings.length - 1]?.sort ?? 0) + 1 })
    if (parent !== null) setOpen((o) => new Set(o).add(parent))
  }

  const addRule = (gid: number, kind: RuleKind = 'mask', value = '') => {
    const r: ClsRule = { id: tmpId--, group_id: gid, kind, value, year_from: null, year_to: null, priority: 0 }
    edit((d) => ({ ...d, rules: [...d.rules, r] }))
  }
  const patchRule = (rid: number, p: Partial<ClsRule>) => edit((d) => ({ ...d, rules: d.rules.map((r) => (r.id === rid ? { ...r, ...p } : r)) }))
  const delRule = (rid: number) => edit((d) => ({ ...d, rules: d.rules.filter((r) => r.id !== rid) }))

  const save = async (d: Classifier = doc) => {
    try {
      await api.saveClassifier(d)
    } catch (e) {
      setError(errMsg(e))
      return false
    }
    setDirty(false)
    setInfo('Классификатор сохранён.')
    // после сохранения сервер присваивает группам настоящие номера — перечитываем классификатор, чтобы экран не держал временные
    try {
      const fresh = await api.classifier(id)
      const chain = (groups: ClsGroup[], gid: number | null) => {
        const out: string[] = []
        let g = groups.find((x) => x.id === gid)
        while (g) {
          out.unshift(g.code + '' + g.name)
          g = groups.find((x) => x.id === g!.parent_id)
        }
        return out.join('')
      }
      const oldKey = sel != null ? chain(d.groups, sel) : null
      const mapped = oldKey != null ? fresh.groups.find((g) => chain(fresh.groups, g.id) === oldKey) : undefined
      setDoc({ ...fresh, name: d.name, description: d.description ?? fresh.description })
      setSel(mapped?.id ?? fresh.groups.find((g) => g.parent_id == null)?.id ?? null)
      setOpen(new Set(fresh.groups.map((g) => g.id)))
      if (res) setRes(null)
    } catch {
      /* перечитать не удалось — останется то, что на экране */
    }
    return true
  }

  const check = async () => {
    setChecking(true)
    setError(null)
    try {
      setRes(await api.resolve(id, year))
    } catch (e) {
      setError(errMsg(e))
    } finally {
      setChecking(false)
    }
  }

  const saveAndCheck = async () => {
    if (dirty && !(await save())) return
    await check()
  }

  // быстро отнести несопоставленный счёт в группу: добавляет правило-маску на точный код, сохраняет, перепроверяет
  const assign = async (account: string, gid: number) => {
    const r: ClsRule = { id: tmpId--, group_id: gid, kind: 'mask', value: account, year_from: null, year_to: null, priority: 0 }
    const next = { ...doc, rules: [...doc.rules, r] }
    setDoc(next)
    if (await save(next)) {
      // после сохранения ids на сервере могли смениться — перечитываем документ
      try {
        setDoc(await api.classifier(id))
      } catch {
        /* */
      }
      await check()
    }
  }

  const renderTree = (parent: number | null, depth: number): React.ReactNode =>
    (kids.get(parent) ?? []).map((g) => {
      const has = (kids.get(g.id)?.length ?? 0) > 0
      return (
        <div key={g.id}>
          <div className={'gt-row' + (g.id === sel ? ' sel' : '')} style={{ paddingLeft: 4 + depth * 16 }} onClick={() => setSel(g.id)}>
            {has ? (
              <button
                className="tog"
                onClick={(e) => {
                  e.stopPropagation()
                  setOpen((o) => {
                    const s = new Set(o)
                    if (s.has(g.id)) s.delete(g.id)
                    else s.add(g.id)
                    return s
                  })
                }}
              >
                {open.has(g.id) ? '▾' : '▸'}
              </button>
            ) : (
              <span className="tog-sp" />
            )}
            <span className="gt-code">{g.code}</span> <span>{g.name}</span>
            <span className="gt-cnt">{doc.rules.filter((r) => r.group_id === g.id).length || ''}</span>
          </div>
          {has && open.has(g.id) && renderTree(g.id, depth + 1)}
        </div>
      )
    })

  const flat: ClsGroup[] = []
  const walk = (p: number | null, d: number) => {
    for (const g of kids.get(p) ?? []) {
      flat.push({ ...g, name: '  '.repeat(d) + g.code + ' ' + g.name })
      walk(g.id, d + 1)
    }
  }
  walk(null, 0)

  const mappedInSel = sel != null && res ? res.mapped.filter((m) => m.group_id === sel).length : null

  return (
    <div className="page cls-editor">
      <div className="page-head">
        <a href="#/classifiers" className="link">
          ← Классификаторы
        </a>
        <input
          className="title-in"
          type="text"
          value={doc.name}
          onChange={(e) => edit((d) => ({ ...d, name: e.target.value }))}
        />
        <input
          type="text"
          placeholder="Описание"
          value={doc.description ?? ''}
          onChange={(e) => edit((d) => ({ ...d, description: e.target.value }))}
          style={{ width: 300 }}
        />
        <div className="spacer" />
        {dirty && <span className="tag">не сохранено</span>}
        <button className="btn primary needs-edit" onClick={() => save()} disabled={!dirty}>
          Сохранить
        </button>
      </div>
      <ErrorBox error={error} onClose={() => setError(null)} />
      {info && <div className="msg ok">{info}</div>}

      <div className="cls-grid">
        <div className="panel">
          <div className="panel-head">
            <b>Группы</b>
            <span className="spacer" />
            <button className="btn small" onClick={() => addGroup(null)}>
              + Группа
            </button>
            <button className="btn small" onClick={() => addGroup(sel)} disabled={sel == null}>
              + Подгруппа
            </button>
          </div>
          <div className="gt">{renderTree(null, 0)}</div>
          {doc.groups.length === 0 && <div className="muted pad">Групп нет. Добавьте первую.</div>}
        </div>

        <div className="panel">
          {group ? (
            <>
              <div className="panel-head">
                <b>Группа</b>
                <span className="spacer" />
                <button className="btn small" onClick={() => moveGroup(group.id, -1)} title="Выше среди соседних">
                  ↑
                </button>
                <button className="btn small" onClick={() => moveGroup(group.id, 1)} title="Ниже среди соседних">
                  ↓
                </button>
                <button className="btn small danger" onClick={() => delGroup(group.id)}>
                  Удалить
                </button>
              </div>
              <div className="form pad">
                <div className="row2">
                  <label>
                    Код
                    <input type="text" value={group.code} onChange={(e) => patchGroup(group.id, { code: e.target.value })} />
                  </label>
                  <label style={{ flex: 3 }}>
                    Название
                    <input type="text" value={group.name} onChange={(e) => patchGroup(group.id, { name: e.target.value })} />
                  </label>
                </div>
                <label>
                  Родитель (переместить)
                  <select
                    value={group.parent_id ?? ''}
                    onChange={(e) => reparent(group.id, e.target.value === '' ? null : Number(e.target.value))}
                  >
                    <option value="">— верхний уровень —</option>
                    {flat
                      .filter((g) => !descendants(group.id).has(g.id))
                      .map((g) => (
                        <option key={g.id} value={g.id}>
                          {g.name}
                        </option>
                      ))}
                  </select>
                </label>
              </div>
              <div className="panel-head">
                <b>Правила группы</b>
                <span className="spacer" />
                <button className="btn small" onClick={() => addRule(group.id)}>
                  + Правило
                </button>
              </div>
              <table className="simple rules">
                <thead>
                  <tr>
                    <th style={{ width: 120 }}>Тип</th>
                    <th>Значение</th>
                    <th style={{ width: 70 }}>Год с</th>
                    <th style={{ width: 70 }}>Год по</th>
                    <th style={{ width: 70 }} title="Больший приоритет побеждает при пересечении">
                      Приор.
                    </th>
                    <th style={{ width: 30 }} />
                  </tr>
                </thead>
                <tbody>
                  {rules.map((r) => (
                    <tr key={r.id}>
                      <td>
                        <select value={r.kind} onChange={(e) => patchRule(r.id, { kind: e.target.value as RuleKind })}>
                          {(Object.keys(KIND_LABEL) as RuleKind[]).map((k) => (
                            <option key={k} value={k}>
                              {KIND_LABEL[k]}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <input type="text" value={r.value} placeholder={KIND_HINT[r.kind]} onChange={(e) => patchRule(r.id, { value: e.target.value })} />
                      </td>
                      <td>
                        <input type="number" value={r.year_from ?? ''} onChange={(e) => patchRule(r.id, { year_from: e.target.value ? Number(e.target.value) : null })} />
                      </td>
                      <td>
                        <input type="number" value={r.year_to ?? ''} onChange={(e) => patchRule(r.id, { year_to: e.target.value ? Number(e.target.value) : null })} />
                      </td>
                      <td>
                        <input type="number" value={r.priority} onChange={(e) => patchRule(r.id, { priority: Number(e.target.value) || 0 })} />
                      </td>
                      <td>
                        <button className="btn small ghost" onClick={() => delRule(r.id)} title="Удалить правило">
                          ✕
                        </button>
                      </td>
                    </tr>
                  ))}
                  {rules.length === 0 && (
                    <tr>
                      <td colSpan={6} className="muted pad">
                        Правил нет — счета в эту группу не попадут.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
              <div className="hint pad">
                Маска: <code>329.01*</code> · Диапазон: <code>150..157</code> · Список: <code>100,102.01</code> · Метка Logo: значение SPECODE. Годы пустые — правило действует всегда.
                {mappedInSel !== null && (
                  <>
                    {' '}
                    Сопоставлено счетов в этой группе за {year} г.: <b>{mappedInSel}</b>.
                  </>
                )}
              </div>
            </>
          ) : (
            <div className="muted pad">Выберите группу слева.</div>
          )}
        </div>

        <div className="panel">
          <div className="panel-head">
            <b>Проверка</b>
            <span className="spacer" />
            <label className="inline">
              за год
              <input type="number" value={year} min={2003} max={2030} style={{ width: 70 }} onChange={(e) => setYear(Number(e.target.value))} />
            </label>
            <button className="btn small primary" onClick={saveAndCheck} disabled={checking}>
              {checking ? '…' : dirty ? 'Сохранить и проверить' : 'Проверить'}
            </button>
          </div>
          {!res && <div className="muted pad">Нажмите «Проверить», чтобы увидеть, какие счета сопоставлены, а какие нет.</div>}
          {res && (
            <div className="resolve">
              <div className="res-sum">
                <span className="check ok">✓ сопоставлено: {res.mapped.length}</span>
                <span className={'check ' + (res.unmapped.length ? 'bad' : 'ok')}>не сопоставлено: {res.unmapped.length}</span>
                <span className={'check ' + (res.conflicts.length ? 'bad' : 'ok')}>конфликтов: {res.conflicts.length}</span>
              </div>
              {res.conflicts.length > 0 && (
                <>
                  <h4>Конфликты (счёт попал в несколько групп)</h4>
                  <table className="simple">
                    <tbody>
                      {res.conflicts.map((c) => (
                        <tr key={c.account}>
                          <td className="nowrap">{c.account}</td>
                          <td>{c.name}</td>
                          <td>{c.groups.map(groupName).join(' / ')}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div className="hint">Поднимите приоритет у нужного правила или уточните маски.</div>
                </>
              )}
              {res.unmapped.length > 0 && (
                <>
                  <h4>Не сопоставлены (по обороту)</h4>
                  <div className="unm-scroll">
                    <table className="simple">
                      <tbody>
                        {[...res.unmapped]
                          .sort((a, b) => Math.abs(b.turnover) - Math.abs(a.turnover))
                          .map((u) => (
                            <UnmappedRow key={u.account} u={u} groups={flat} defaultGroup={sel} onAssign={assign} />
                          ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function UnmappedRow({
  u,
  groups,
  defaultGroup,
  onAssign,
}: {
  u: { account: string; name: string; turnover: number }
  groups: ClsGroup[]
  defaultGroup: number | null
  onAssign: (account: string, gid: number) => void
}) {
  const [g, setG] = useState<number | ''>(defaultGroup ?? '')
  return (
    <tr>
      <td className="nowrap">{u.account}</td>
      <td title={u.name} className="txt-cell">
        {u.name}
      </td>
      <td className="num">{fmtMoney(u.turnover)}</td>
      <td style={{ width: 150 }}>
        <select value={g} onChange={(e) => setG(e.target.value ? Number(e.target.value) : '')}>
          <option value="">— группа —</option>
          {groups.map((x) => (
            <option key={x.id} value={x.id}>
              {x.name}
            </option>
          ))}
        </select>
      </td>
      <td style={{ width: 60 }}>
        <button className="btn small" disabled={g === ''} onClick={() => g !== '' && onAssign(u.account, g)}>
          Отнести
        </button>
      </td>
    </tr>
  )
}
