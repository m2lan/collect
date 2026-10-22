import { reactive } from 'vue'
import { me } from './api'

const ROLES = { viewer: 1, operator: 2, admin: 3 }

export const auth = reactive({ user: null })

export async function loadUser() {
  auth.user = await me()
  return auth.user
}

/** 当前用户是否具备某角色（及以上）权限 */
export function can(role) {
  return (ROLES[auth.user?.role] || 0) >= ROLES[role]
}
