// 首屏主题初始化：在 CSS/Vue 加载前写入 <html data-theme>，避免暗色用户看到浅色闪烁。
// 必须是独立文件并通过 <script src> 引入——CSP script-src 'self' 不允许内联脚本。
(function () {
  try {
    var stored = window.localStorage.getItem('lease-manager-theme')
    var theme =
      stored === 'dark' || stored === 'light'
        ? stored
        : window.matchMedia('(prefers-color-scheme: dark)').matches
          ? 'dark'
          : 'light'
    document.documentElement.dataset.theme = theme
  } catch (error) {
    document.documentElement.dataset.theme = 'light'
  }
})()
