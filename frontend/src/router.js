import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('./views/Login.vue') },
    {
      path: '/',
      component: () => import('./views/Layout.vue'),
      redirect: '/lots',
      children: [
        { path: 'lots', name: 'lots', component: () => import('./views/Lots.vue'), meta: { title: '拍品数据' } },
        { path: 'sources', name: 'sources', component: () => import('./views/Sources.vue'), meta: { title: '站点管理' } },
        { path: 'runs', name: 'runs', component: () => import('./views/Runs.vue'), meta: { title: '采集记录' } },
        { path: 'settings', name: 'settings', component: () => import('./views/Settings.vue'), meta: { title: '系统设置' } },
      ],
    },
  ],
})

router.beforeEach((to) => {
  if (to.name !== 'login' && !localStorage.getItem('token')) return { name: 'login' }
})

export default router
