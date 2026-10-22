// 后台脚本：安装后设置角标颜色；插件的主要逻辑在 popup.js 中
chrome.runtime.onInstalled.addListener(() => {
  chrome.action.setBadgeBackgroundColor({ color: '#2563eb' })
})
