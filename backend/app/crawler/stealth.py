"""浏览器指纹伪装：抹掉无头浏览器最常被检测的特征。"""

STEALTH_JS = r"""
(() => {
  const patch = (obj, prop, value) => { try { Object.defineProperty(obj, prop, { get: () => value, configurable: true }); } catch (e) {} };
  patch(Navigator.prototype, 'webdriver', undefined);
  patch(Navigator.prototype, 'languages', ['zh-CN', 'zh', 'en-US', 'en']);
  patch(Navigator.prototype, 'hardwareConcurrency', 8);
  patch(Navigator.prototype, 'deviceMemory', 8);
  patch(Navigator.prototype, 'platform', 'Win32');
  // 无头 Chrome 的 plugins 为空
  const plugins = [
    { name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
    { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
    { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
  ];
  patch(Navigator.prototype, 'plugins', Object.assign(plugins, { item: i => plugins[i], namedItem: n => plugins.find(p => p.name === n) }));
  if (!window.chrome) { window.chrome = { runtime: {}, app: { isInstalled: false }, csi: () => ({}), loadTimes: () => ({}) }; }
  const query = window.navigator.permissions && window.navigator.permissions.query;
  if (query) {
    window.navigator.permissions.query = (p) => p && p.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission, onchange: null }) : query.call(window.navigator.permissions, p);
  }
  // WebGL 厂商信息（无头模式下为 SwiftShader / Google Inc.）
  const getParameter = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function (p) {
    if (p === 37445) return 'Intel Inc.';
    if (p === 37446) return 'Intel Iris OpenGL Engine';
    return getParameter.call(this, p);
  };
  patch(window, 'outerWidth', window.innerWidth);
  patch(window, 'outerHeight', window.innerHeight + 85);
})();
"""

LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-default-browser-check",
    "--disable-dev-shm-usage",
]
