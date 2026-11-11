import { createRouter, createWebHistory } from 'vue-router'

// 视图切换仍由 App.vue 的 KeepAlive 动态组件驱动（懒加载/预加载/滚动记忆体系不变）；
// 本路由表仅承担 URL ↔ 页签的双向同步：深链接、刷新保持、浏览器前进/后退。
// 后端 app/main.py 的 spa_fallback 已将非 /api 路径回退到 index.html，history 模式可用。
const TAB_ROUTES = [
  ['dashboard', '/'],
  ['overview', '/overview'],
  ['operations', '/operations'],
  ['analytics', '/analytics'],
  ['evidence', '/evidence'],
  ['exchange', '/snapshot-exchange'],
  ['ai-assist', '/ai-assist'],
  ['risks', '/risks'],
  ['contracts', '/contracts'],
  ['customers', '/customers'],
  ['rooms', '/rooms'],
  ['receivables', '/receivables'],
  ['audit', '/audit'],
  ['tools', '/tools']
]

const TAB_BY_PATH = new Map(TAB_ROUTES.map(([key, path]) => [path, key]))
const PATH_BY_TAB = new Map(TAB_ROUTES)

export function tabForPath(path) {
  return TAB_BY_PATH.get(path) || 'dashboard'
}

export function pathForTab(key) {
  return PATH_BY_TAB.get(key) || '/'
}

// 占位组件：路由表只用于路径匹配与导航监听，实际渲染由 App.vue 的动态组件完成
const routePlaceholder = { render: () => null }

const router = createRouter({
  history: createWebHistory(),
  routes: [
    ...TAB_ROUTES.map(([, path]) => ({ path, component: routePlaceholder })),
    { path: '/:pathMatch(.*)*', redirect: '/' }
  ]
})

export default router
