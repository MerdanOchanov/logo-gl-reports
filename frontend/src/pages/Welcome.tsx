import type { DatasetInfo } from '../api'
import { useIsAdmin } from '../components/role'
import { useSession } from '../dataset'
import { navigate } from '../router'

/** Первый запуск: три шага вместо пустого списка отчётов. */
export default function Welcome({ ds }: { ds: DatasetInfo }) {
  const isAdmin = useIsAdmin()
  const { globalAdmin } = useSession()
  const connDone = !!ds.configured
  const booksDone = (ds.books_main ?? 0) > 0
  const steps = [
    {
      n: 1,
      title: 'Подключение',
      done: connDone,
      text: connDone ? `Подключено: сервер ${ds.connection?.server ?? ''}, база ${ds.connection?.database ?? ''}.` : 'Укажите сервер с базой Logo, логин и пароль и проверьте подключение.',
      to: '/settings/connection',
      btn: connDone ? 'Изменить подключение' : 'Настроить подключение',
    },
    {
      n: 2,
      title: 'Книги и годы',
      done: booksDone,
      text: booksDone ? `Выбрано основных книг: ${ds.books_main}.` : 'Программа прочитает фирмы и периоды Logo и предложит, какие из них брать для отчётов по годам.',
      to: '/settings/books',
      btn: booksDone ? 'Открыть «Книги и годы»' : 'Выбрать книги',
      disabled: !connDone,
    },
    {
      n: 3,
      title: 'Готово',
      done: connDone && booksDone,
      text: connDone && booksDone ? 'Всё настроено. Данные загружаются; затем можно строить отчёты.' : 'После первых двух шагов здесь появится список отчётов.',
      to: '/reports',
      btn: 'К отчётам',
      disabled: !(connDone && booksDone),
    },
  ]
  return (
    <div className="page">
      <div className="big-empty">
        <h2>Добро пожаловать!</h2>
        <p>Чтобы строить бухгалтерские отчёты, программе нужно знать, откуда брать данные. Это три простых шага.</p>
        {!isAdmin && <div className="msg warn">Настройку выполняет администратор. Обратитесь к нему — после настройки здесь появятся отчёты.</div>}
        <div className="welcome-steps">
          {steps.map((s) => (
            <div key={s.n} className={'welcome-step' + (s.done ? ' done' : '')}>
              <div className="welcome-n">{s.done ? '✓' : s.n}</div>
              <h3>{s.title}</h3>
              <p>{s.text}</p>
              <button className={'btn' + (s.done ? '' : ' primary')} disabled={s.n === 1 && !globalAdmin ? true : !isAdmin && s.n < 3 ? true : s.disabled} onClick={() => navigate(s.to)}>
                {s.btn}
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
