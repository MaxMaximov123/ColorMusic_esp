import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/login', name: 'login', component: () => import('./pages/LoginPage.vue') },
  { path: '/', redirect: '/dashboard' },
  { path: '/dashboard', name: 'dashboard', component: () => import('./pages/DashboardPage.vue'), meta: { requiresAuth: true } }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach((to, from, next) => {
  if (to.meta.requiresAuth && !localStorage.getItem('token')) {
    next('/login')
  } else {
    next()
  }
})

export default router
