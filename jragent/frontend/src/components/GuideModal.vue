<template>
  <div v-if="visible" class="mask" @click.self="close">
    <div class="panel">
      <!-- 第一次进来：先问熟不熟 -->
      <template v-if="stage === 'choose'">
        <div class="between" style="margin-bottom:14px">
          <b>欢迎来到全域策略研究所</b>
          <button class="tiny" @click="close">跳过</button>
        </div>
        <p class="muted" style="margin-bottom:16px">先告诉我你熟不熟，我换个方式跟你讲。</p>
        <div class="choose-grid">
          <div class="choose-card" @click="startBeginner">
            <div class="ct">我是新手</div>
            <p>先带我看懂策略是什么</p>
            <span class="tiny">4 张图讲清金叉死叉，不需要任何基础</span>
          </div>
          <div class="choose-card" @click="startPro">
            <div class="ct">我是老手</div>
            <p>直接开始，别跟我讲概念</p>
            <span class="tiny">选股票、改参数、看回测结果</span>
          </div>
        </div>
        <p class="tiny" style="margin-top:14px;text-align:center">
          之后想再看图解，点右上角的「新手指南」
        </p>
      </template>

      <!-- 新手：4 步图解 -->
      <template v-else>
        <div class="between" style="margin-bottom:6px">
          <b>新手引导 · {{ step + 1 }} / {{ steps.length }}</b>
          <button class="tiny" @click="close">跳过</button>
        </div>

        <div class="step-body">
          <h3>{{ steps[step].title }}</h3>
          <p class="muted" v-html="steps[step].text"></p>
          <div class="fig" v-html="steps[step].svg"></div>
          <div v-if="steps[step].point" class="notice">{{ steps[step].point }}</div>
        </div>

        <div class="between" style="margin-top:16px">
          <button class="btn btn-plain btn-sm" :disabled="step===0" @click="step--">上一步</button>
          <button v-if="step < steps.length-1" class="btn btn-sm" @click="step++">下一步</button>
          <button v-else class="btn btn-sm" @click="goTry">去跑一次真实回测</button>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { setLevel } from './guideBus'

const props = defineProps({ modelValue: Boolean })
const emit = defineEmits(['update:modelValue'])
const router = useRouter()

const stage = ref('choose')     // choose | steps
const step = ref(0)
const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

// 从「新手指南」按钮打开时直接进图解，不再问一遍
watch(() => props.modelValue, (v) => {
  if (v && localStorage.getItem('userLevel')) stage.value = 'steps'
  if (!v) { stage.value = 'choose'; step.value = 0 }
})

// ---- 示意图全部内联 SVG，离线可看 ----
// 画法参照手绘习惯：线粗、颜色对比强、交叉点放大加白边、文字大字标出
// 所有交叉点坐标都用插值算过，不是目测的

// 图1：完整一轮涨跌。起点先横盘让快线在慢线下方，上穿过程才看得出来
const svgMA = `
<svg viewBox="0 0 520 200" width="100%" xmlns="http://www.w3.org/2000/svg">
  <rect width="520" height="200" fill="#fff"/>
  <polyline points="40,154 80,152 120,148 160,130 200,100 240,74 280,56 320,54 360,72 400,104 440,138 480,156"
    fill="none" stroke="#b9b9b0" stroke-width="2.4" stroke-dasharray="6 4"/>
  <polyline points="40,158 80,158 120,156 160,150 200,110 240,80 280,60 320,57 360,75 400,107 440,139 480,156"
    fill="none" stroke="#f0b429" stroke-width="3.6" stroke-linecap="round"/>
  <polyline points="40,150 80,150 120,150 160,148 200,142 240,128 280,108 320,90 360,80 400,92 440,116 480,138"
    fill="none" stroke="#7c4dbc" stroke-width="3.6" stroke-linecap="round"/>
  <circle cx="162.4" cy="147.6" r="9" fill="#e0352b" stroke="#fff" stroke-width="3"/>
  <circle cx="370" cy="83" r="9" fill="#1a9c5b" stroke="#fff" stroke-width="3"/>
  <text x="150" y="130" font-size="15" fill="#e0352b" font-weight="bold">买入</text>
  <text x="384" y="76" font-size="15" fill="#1a9c5b" font-weight="bold">卖出</text>
  <text x="392" y="44" font-size="13" fill="#f0b429" font-weight="bold">快线 MA7</text>
  <text x="392" y="112" font-size="13" fill="#7c4dbc" font-weight="bold">慢线 MA25</text>
  <rect x="34" y="30" width="168" height="52" rx="7" fill="#fff" stroke="#e8e8e2"/>
  <line x1="44" y1="46" x2="66" y2="46" stroke="#b9b9b0" stroke-width="2.2" stroke-dasharray="5 3"/>
  <text x="72" y="50" font-size="12" fill="#6b6b64">灰虚线＝股价</text>
  <line x1="44" y1="68" x2="66" y2="68" stroke="#f0b429" stroke-width="3"/>
  <text x="72" y="72" font-size="12" fill="#6b6b64">黄＝快线，紫＝慢线</text>
  <text x="40" y="188" font-size="11" fill="#9a9a90">股价先涨后跌走了一轮，两条均线跟着它慢慢拐弯</text>
</svg>`

// 图2：金叉特写，只看上涨那一段
const svgGolden = `
<svg viewBox="0 0 520 200" width="100%" xmlns="http://www.w3.org/2000/svg">
  <rect width="520" height="200" fill="#fff"/>
  <polyline points="40,152 100,152 160,150 220,144 280,130 340,110 400,88 460,72"
    fill="none" stroke="#7c4dbc" stroke-width="3.6" stroke-linecap="round"/>
  <polyline points="40,160 100,158 160,148 220,120 280,88 340,66 400,54 460,48"
    fill="none" stroke="#f0b429" stroke-width="3.6" stroke-linecap="round"/>
  <circle cx="145" cy="150.5" r="10" fill="#e0352b" stroke="#fff" stroke-width="3"/>
  <text x="104" y="132" font-size="16" fill="#e0352b" font-weight="bold">金叉 → 买入</text>
  <text x="170" y="186" font-size="12" fill="#6b6b64">黄色快线从下往上穿过紫色慢线</text>
  <text x="40" y="186" font-size="12" fill="#9a9a90">短期比长期强了</text>
  <text x="392" y="74" font-size="13" fill="#7c4dbc" font-weight="bold">慢线 MA25</text>
  <text x="392" y="40" font-size="13" fill="#f0b429" font-weight="bold">快线 MA7</text>
</svg>`

// 图3：死叉特写，只看下跌那一段
const svgDead = `
<svg viewBox="0 0 520 200" width="100%" xmlns="http://www.w3.org/2000/svg">
  <rect width="520" height="200" fill="#fff"/>
  <polyline points="40,48 100,48 160,50 220,54 280,66 340,86 400,110 460,132"
    fill="none" stroke="#7c4dbc" stroke-width="3.6" stroke-linecap="round"/>
  <polyline points="40,40 100,42 160,47 220,56 280,72 340,102 400,134 460,154"
    fill="none" stroke="#f0b429" stroke-width="3.6" stroke-linecap="round"/>
  <circle cx="196" cy="52.4" r="10" fill="#1a9c5b" stroke="#fff" stroke-width="3"/>
  <text x="212" y="42" font-size="16" fill="#1a9c5b" font-weight="bold">死叉 → 卖出</text>
  <text x="40" y="186" font-size="12" fill="#9a9a90">短期比长期弱了，可能要接着跌</text>
  <text x="392" y="128" font-size="13" fill="#f0b429" font-weight="bold">快线 MA7</text>
  <text x="392" y="94" font-size="13" fill="#7c4dbc" font-weight="bold">慢线 MA25</text>
</svg>`

// 图4：不是示意图，是真实回测数据
// 贵州茅台 5/20 均线交叉，近 5 年：策略 -27.68%，买入持有 -9.03%
// 两条线用同一个 y 轴值域（0.607~1.234），高低关系是真的
const svgWarn = `
<svg viewBox="0 0 520 200" width="100%" xmlns="http://www.w3.org/2000/svg">
  <rect width="520" height="200" fill="#fff"/>
  <line x1="46" y1="152" x2="486" y2="152" stroke="#eee"/>
  <polyline points="46,83.1 63,77.9 80,92.9 97,77.5 114,72.4 131,122.4 148,75.5 164,84.0 181,96.8 198,75.4 215,90.3 232,92.7 249,86.4 266,92.7 283,113.5 300,101.6 317,109.0 334,94.4 351,101.4 368,105.5 384,104.9 401,110.3 418,102.0 435,117.7 452,114.7 469,113.6 486,98.9"
    fill="none" stroke="#b9b9b0" stroke-width="2.2" stroke-dasharray="6 4"/>
  <polyline points="46,83.1 63,75.5 80,84.0 97,79.8 114,70.8 131,88.4 148,54.4 164,68.6 181,83.8 198,76.0 215,79.4 232,90.1 249,98.0 266,102.1 283,111.3 300,120.4 317,136.4 334,127.9 351,132.9 368,132.6 384,133.2 401,139.6 418,144.3 435,151.7 452,143.6 469,143.3 486,131.6"
    fill="none" stroke="#e0352b" stroke-width="2.8" stroke-linecap="round"/>
  <text x="372" y="128" font-size="13" fill="#e0352b" font-weight="bold">用策略 −27.68%</text>
  <text x="372" y="88" font-size="13" fill="#9a9a90">一直拿着 −9.03%</text>
  <text x="46" y="182" font-size="12" fill="#9a9a90">贵州茅台近五年真实回测：策略明显不如拿着不动</text>
</svg>`

const steps = [
  {
    title: '先搞懂：均线是什么',
    text: '把最近几天的收盘价平均一下，就是均线。<br><b>MA7</b> 是最近 7 天的平均价，<b>MA25</b> 是最近 25 天的平均价。<br>天数少的叫<b>快线</b>，反应快；天数多的叫<b>慢线</b>，更平稳。',
    svg: svgMA,
    point: '记住一件事就够了：快线看"最近"，慢线看"这段日子"。',
  },
  {
    title: '金叉：快线上穿慢线 → 买入',
    text: '当 MA7 从<b>下面</b>穿到 MA25 的<b>上面</b>，说明最近的价格比过去一段时间要强。<br>很多人把这当作"可能要涨了"的信号，于是买入。',
    svg: svgGolden,
    point: '在回测图上，这种买入点会标成红色三角。',
  },
  {
    title: '死叉：快线下穿慢线 → 卖出',
    text: '反过来，MA7 从<b>上面</b>跌破 MA25，说明最近的价格开始走弱。<br>这时候卖出，躲开可能的下跌。',
    svg: svgDead,
    point: '卖出点会标成绿色三角。',
  },
  {
    title: '最重要的一步：别信感觉，看数据',
    text: '金叉死叉流传很广，但<b>它在很多股票上其实是亏钱的</b>。<br>比如我们用贵州茅台五年真实数据跑过，这套方法亏了 27.68%，比拿着不动还惨。<br><b>所以一定要用真实历史数据验证，而不是听别人说了算。</b>',
    svg: svgWarn,
    point: '接下来你可以自己选一只股票、改改参数，亲眼看一次回测结果。',
  },
]

function close() { visible.value = false }

function startBeginner() {
  setLevel('beginner')
  stage.value = 'steps'
  step.value = 0
}

function startPro() {
  setLevel('pro')
  close()
}

function goTry() {
  close()
  router.push('/strategies')
}
</script>

<style scoped>
.mask {
  position: fixed; inset: 0; background: rgba(0,0,0,.42);
  display: flex; align-items: center; justify-content: center; z-index: 200; padding: 16px;
}
.panel {
  background: #fff; border-radius: 16px; padding: 24px;
  width: 600px; max-width: 100%; max-height: 90vh; overflow-y: auto;
  box-shadow: 0 20px 60px rgba(0,0,0,.18);
}
.choose-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 14px; }
@media (max-width: 560px) { .choose-grid { grid-template-columns: 1fr; } }
.choose-card {
  border: 1px solid var(--border); border-radius: 12px; padding: 18px;
  cursor: pointer; transition: all .15s;
}
.choose-card:hover { border-color: var(--primary); background: #fbfaff; }
.ct { font-size: 16px; font-weight: 600; margin-bottom: 6px; }
.choose-card p { color: var(--text-2); font-size: 13px; margin-bottom: 6px; }
.step-body h3 { font-size: 17px; margin-bottom: 10px; }
.step-body p { font-size: 14px; line-height: 1.8; }
.fig { margin: 14px 0; border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }
</style>
