/** Ссылка на новую карточку счёта за тот же период (открывается в новой вкладке, текущий отчёт не теряется). */
export function openAccountCard(
  account: string,
  ctx: { period: { date_from: string; date_to: string }; include_closing: boolean; currency?: string; scale?: number; denominate?: boolean },
) {
  const p = new URLSearchParams({
    account,
    from: ctx.period.date_from,
    to: ctx.period.date_to,
    closing: ctx.include_closing ? '1' : '0',
    cur: ctx.currency ?? '',
    scale: String(ctx.scale ?? 1),
    den: ctx.denominate === false ? '0' : '1',
  })
  window.open(window.location.pathname + '#/report/new/card?' + p.toString(), '_blank')
}

