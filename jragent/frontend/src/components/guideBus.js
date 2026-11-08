// 新手引导的全局开关：NavBar 的按钮和首页的分流都能触发
import { reactive } from 'vue'

export const guideState = reactive({
  visible: false,
  // 用户是否已经选过"新手/老手"，选过就记在本地，不再反复问
  level: localStorage.getItem('userLevel') || '',
})

export function openGuide() {
  guideState.visible = true
}

export function closeGuide() {
  guideState.visible = false
}

export function setLevel(level) {
  guideState.level = level
  localStorage.setItem('userLevel', level)
}
