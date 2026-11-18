import { Component, type ReactNode } from 'react'

/** Страховка: при ошибке отрисовки вместо белого экрана — понятное сообщение и кнопка «На главную». */
export default class ErrorBoundary extends Component<{ children: ReactNode; onHome?: () => void }, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  componentDidCatch(error: unknown, info: unknown) {
    console.error('Ошибка отрисовки страницы:', error, info)
  }

  render() {
    if (!this.state.failed) return this.props.children
    return (
      <div className="page">
        <div className="msg err">
          <span>
            <b>Не удалось показать страницу.</b> Что-то пошло не так при выводе данных. Обновите страницу (F5); если ошибка повторяется — сообщите администратору.
          </span>
          <button
            className="btn small"
            onClick={() => {
              this.setState({ failed: false })
              if (this.props.onHome) this.props.onHome()
              else window.location.hash = '#/reports'
            }}
          >
            На главную
          </button>
        </div>
      </div>
    )
  }
}
