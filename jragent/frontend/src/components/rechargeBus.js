// 充值弹窗的全局总线：api 拦截器在 402 时触发，App.vue 里监听
import { reactive } from 'vue'

export const rechargeState = reactive({
  visible: false,
  need: 0,
  have: 0,
})

export function showRecharge({ need = 0, have = 0 } = {}) {
  rechargeState.need = need
  rechargeState.have = have
  rechargeState.visible = true
}

export function hideRecharge() {
  rechargeState.visible = false
}
