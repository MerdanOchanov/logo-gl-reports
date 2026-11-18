import { createContext, useContext } from 'react'

/** Роль текущего пользователя в выбранном наборе (при выключенной авторизации сервер отдаёт admin). */
export const RoleCtx = createContext<string>('admin')
export const useCanEdit = () => ['editor', 'admin'].includes(useContext(RoleCtx))
export const useIsAdmin = () => useContext(RoleCtx) === 'admin'
