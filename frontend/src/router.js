import { createRouter, createWebHistory } from 'vue-router'
import { auth, can, loadUser } from './store'

export const MENU = [
  { group: '概览', items: [{ path: '/dashboard', title: '采集看板', icon: 'DataBoard' }] },
  {
    group: '数据',
    items: [
      { path: '/lots', title: '拍品数据', icon: 'Goods' },
      { path: '/analytics', title: '行情分析', icon: 'TrendCharts' },
    ],
  },
  {
    group: '采集',
    items: [
      { path: '/sources', title: '站点管理', icon: 'Link' },
      { path: '/runs', title: '采集记录', icon: 'Document' },
    ],
  },
  { group: '提醒', items: [{ path: '/subscriptions', title: '订阅提醒', icon: 'Bell' }] },
  { group: '系统', items: [{ path: '/settings', title: '系统设置', icon: 'Setting', role: 'admin' }] },
]

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('./views/Login.vue') },
    {
      path: '/',
      component: () => import('./views/Layout.vue'),
      redirect: '/dashboard',
      children: [
        { path: 'dashboard', name: 'dashboard', component: () => import('./views/Dashboard.vue'), meta: { title: '采集看板' } },
        { path: 'lots', name: 'lots', component: () => import('./views/Lots.vue'), meta: { title: '拍品数据' } },
        { path: 'analytics', name: 'analytics', component: () => import('./views/Analytics.vue'), meta: { title: '行情分析' } },
        { path: 'sources', name: 'sources', component: () => import('./views/Sources.vue'), meta: { title: '站点管理' } },
        { path: 'runs', name: 'runs', component: () => import('./views/Runs.vue'), meta: { title: '采集记录' } },
        { path: 'subscriptions', name: 'subscriptions', component: () => import('./views/Subscriptions.vue'), meta: { title: '订阅提醒' } },
        { path: 'settings', name: 'settings', component: () => import('./views/Settings.vue'), meta: { title: '系统设置', role: 'admin' } },
      ],
    },
  ],
})

router.beforeEach(async (to) => {
  if (to.name === 'login') return true
  if (!localStorage.getItem('token')) return { name: 'login' }
  if (!auth.user) {
    try {
      await loadUser()
    } catch {
      return { name: 'login' }
    }
  }
  if (to.meta.role && !can(to.meta.role)) return { name: 'dashboard' }
  return true
})

export default router
