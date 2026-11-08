<template>
  <div class="container chat-wrap"
       @dragenter="onDragEnter"
       @dragover="onDragOver"
       @dragleave="onDragLeave"
       @drop="onDrop">

    <!-- 拖拽遮罩：pointer-events:none 让事件继续落到容器上 -->
    <div v-if="dragOver" class="drop-mask">
      <div class="drop-box">
        <div class="drop-icon">📥</div>
        <div class="drop-title">松手就上传</div>
        <div class="drop-hint">图片、Excel、CSV、文本都行 · 也可以直接 Ctrl+V 粘贴截图</div>
      </div>
    </div>
    <div class="chat-main">
    <div v-if="deniedTip" class="notice denied">
      {{ deniedTip }}
      <button class="link-btn" style="margin-left:8px" @click="deniedTip = ''">知道了</button>
    </div>

    <!-- 首次进入的引导 -->
    <div v-if="!messages.length" class="intro">
      <h1>你的策略，到底靠不靠谱？</h1>
      <p class="muted">
        直接用大白话问我。我会去策略库里找、用真实历史数据跑回测，
        然后把结果翻译成人话。<b>跑输了我会明说。</b>
      </p>
      <div class="quick">
        <button v-for="q in quickQuestions" :key="q" class="chip" @click="ask(q)">{{ q }}</button>
      </div>
      <div class="notice" style="margin-top:18px">
        数据截至 {{ cutoff || '读取中' }} · 看策略库和回测都免费 · AI 深度解读才需积分
      </div>
    </div>

    <!-- 对话区 -->
    <div class="messages" ref="msgBox">
      <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
        <div class="bubble">
          <!-- 用户消息里的附件：图片显示缩略图，其它显示文件名 -->
          <div v-if="(m.atts || []).length" class="msg-atts">
            <template v-for="(a, k) in m.atts" :key="k">
              <img v-if="a.thumb" :src="a.thumb" class="msg-att-img" alt="" />
              <span v-else class="msg-att-file">📄 {{ a.name }}</span>
            </template>
          </div>
          <!-- 过程区：思考内容 + 存活计时。
               它比回答先出现，用来看"模型在想什么、有没有在动"。
               实测首段思考要 13 秒、首段正文要 26 秒，没有这块就是干等。 -->
          <div v-if="m.thinking || m.streaming || m.note" class="proc" :class="{ done: !m.streaming }">
            <button class="proc-head" @click="m.procOpen = !m.procOpen">
              <span class="proc-dot" :class="{ live: m.streaming }"></span>
              <!-- 只用 m 上的字段拼，不调用 setup 里的函数：
                   热更新时旧实例上可能没有新函数，模板一调就会整页崩。 -->
              <span class="proc-title">{{
                !m.streaming
                  ? (m.thinking ? '思考过程' : '处理过程')
                  : (m.content ? '正在写回答…' : (m.toolLabel || '正在思考…'))
              }}</span>
              <span v-if="m.elapsed" class="proc-time">{{ m.elapsed.toFixed(1) }}s</span>
              <span class="proc-caret">{{ m.procOpen ? '收起' : '展开' }}</span>
            </button>
            <div v-if="m.procOpen" class="proc-body">
              <div v-if="m.toolLabel" class="tool-hint">{{ m.toolLabel }}</div>
              <div v-if="m.note" class="proc-note">{{ m.note }}</div>
              <pre v-if="m.thinking" class="proc-text">{{ m.thinking }}</pre>
              <div v-else-if="m.streaming" class="proc-idle">
                正在等模型开口…（这段通常要 10 秒以上，不用管它）
              </div>
            </div>
          </div>

          <div v-if="m.stall" class="stall-tip">
            已经 {{ m.stallFor }} 秒没收到任何数据了，可能网络断了。
            可以点下面的「停止」重发，或者再等等。
          </div>

          <div v-if="m.content" class="content md" v-html="renderMarkdown(m.content)"></div>
          <div v-else-if="m.streaming" class="thinking">{{ thinkingText }}</div>
          <span v-if="m.streaming && m.content" class="cursor"></span>

          <!-- 工具产出的卡片 -->
          <div v-for="(card, j) in m.cards || []" :key="j" class="card-block">
            <FactorSearchCard v-if="card.type==='factor_search'" :data="card.data" />
            <DeployCard v-else-if="card.type==='deploy_guide'" :data="card.data" />
            <WebResultsCard v-else-if="card.type==='web_results'" :data="card.data" />
            <ReportCard v-else-if="card.type==='report'" :data="card.data" />
            <CompareTable v-else-if="card.type==='compare'" :rows="card.data.comparison" />
            <PairAnalysis v-else-if="card.type==='pair_analysis'" :data="card.data" />
            <DesignResult v-else-if="card.type==='design_result'" :data="card.data" />
            <RobustnessCard v-else-if="card.type==='robustness'" :data="card.data" />
            <BacktestCard v-else-if="card.type==='backtest'" :data="card.data" />
            <StrategyMiniList v-else-if="card.type==='strategy_list'" :items="card.data.strategies" />
            <div v-else-if="card.type==='symbol_list'" class="tiny">
              可选标的：{{ (card.data.symbols||[]).map(s=>s.name).join('、') }}
            </div>
            <ParamSuggest v-else-if="card.type==='param_suggest'" :data="card.data" />
            <div v-else-if="card.type==='saved_strategy'" class="saved-ok">
              <b>已存进「我的策略」</b> · {{ card.data.name }}
              <span class="tiny">（{{ card.data.symbol_code }} ·
                收益 {{ card.data.total_return_pct }}% ·
                回撤 {{ card.data.max_drawdown_pct }}%）</span>
              <router-link to="/saved" class="link-btn" style="margin-left:8px">去看看</router-link>
            </div>
            <!-- AI 发布到公共库：策略和因子共用一张卡，按 kind 换文案 -->
            <div v-else-if="card.type==='published'" class="saved-ok pub-ok">
              <b>{{ card.data.kind==='factor' ? '已进公共因子库' : '已发布到公共策略库' }}</b>
              · {{ card.data.name }}
              <span v-if="card.data.kind==='factor'" class="tiny">
                （实测 {{ card.data.valid_points }} 个有效点）
              </span>
              <span v-else class="tiny">
                （入库前验证：{{ card.data.verified_on }} 收益
                {{ card.data.total_return_pct }}% ·
                {{ card.data.trade_count }} 次交易<span v-if="card.data.filters_desc"> ·
                {{ card.data.filters_desc }}</span>）
              </span>
              <router-link :to="card.data.page" class="link-btn" style="margin-left:8px">去看看</router-link>
            </div>
            <!-- AI 存进「我的因子」：只有条件，不带标的 -->
            <div v-else-if="card.type==='saved'" class="saved-ok">
              <b>已存进「我的因子」</b> · {{ card.data.name }}
              <span class="tiny">（{{ card.data.condition_text }}）</span>
              <router-link to="/my-factors" class="link-btn" style="margin-left:8px">去看看</router-link>
            </div>
            <div v-if="m.archived" class="tiny archived-tip">
              历史记录：图表数据已压缩，重新问一次可以看完整曲线
            </div>
          </div>

          <div v-if="m.charged != null" class="tiny" style="margin-top:8px">
            本次实扣 {{ m.charged }} 积分<span v-if="m.refunded">（已退回 {{ m.refunded }}）</span>
            <span v-if="m.compliance?.rounds>1" class="tag" style="margin-left:6px">
              合规审查 {{ m.compliance.rounds }} 轮
            </span>
          </div>

          <!-- 每条消息的操作：复制原文、引用到输入框、分享（优先给报告链接） -->
          <div v-if="m.content && !m.streaming" class="msg-ops">
            <button class="msg-op" title="复制这段内容" @click="copyMsg(m)">复制</button>
            <button class="msg-op" title="引用它，接着往下问" @click="quoteMsg(m)">引用</button>
            <button class="msg-op" title="分享这段对话" @click="shareMsg(m)">分享</button>
          </div>
        </div>
      </div>

      <!-- 上次提问没等到回答，明确告诉用户，并给一键重试 -->
      <div v-if="interrupted && !loading" class="msg assistant">
        <div class="bubble interrupted">
          <div class="content">
            上次这个问题没等到我回答（页面刷新或中途离开了）。要重新问一次吗？
          </div>
          <div class="row" style="margin-top:8px">
            <button class="btn btn-sm" @click="retry">重新提问</button>
            <button class="btn btn-plain btn-sm" @click="interrupted = ''">忽略</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 输入区 -->
    <!-- 已选附件：发送前能删；图片直接缩略图，其他显示文件名 -->
    <div v-if="attachments.length || uploading" class="att-bar">
      <div v-for="(a, i) in attachments" :key="i" class="att-chip">
        <img v-if="a.kind === 'image'" :src="a.data_url" class="att-thumb" alt="" />
        <span class="att-name" :title="a.name">{{ a.name }}</span>
        <button class="att-x" title="移除" @click="attachments.splice(i, 1)">×</button>
      </div>
      <div v-if="uploading" class="att-chip att-loading">正在读取…</div>
    </div>

    <div class="input-bar">
      <button class="icon-btn" title="历史对话" @click="openHistory">历史</button>
      <button v-if="messages.length" class="new-btn" title="开始新对话" @click="newChat">新</button>
      <button class="plus-btn" title="插入策略或因子" @click="pickerOpen = true">＋</button>
      <button class="plus-btn" title="上传图片 / 表格 / 文本" @click="fileInput?.click()">📎</button>
      <input ref="fileInput" type="file" multiple hidden
             accept=".png,.jpg,.jpeg,.webp,.gif,.bmp,.xlsx,.xlsm,.xls,.csv,.txt,.md,.json,.py,.log,.yaml,.yml"
             @change="onFiles" />
      <textarea
        v-model="input"
        rows="2"
        placeholder="直接打字，或把截图/Excel 拖进来、Ctrl+V 粘贴 / 点＋插入策略因子"
        @keydown.enter.exact.prevent="send"
        @paste="onPaste"
      ></textarea>
      <button v-if="!loading" class="btn" :disabled="!input.trim() && !attachments.length" @click="send">发送</button>
      <button v-else class="btn btn-stop" @click="stop">停止</button>
    </div>

    <!-- 历史对话抽屉：切换而不删除 -->
    <div v-if="historyOpen" class="mask" @click.self="historyOpen = false">
      <div class="drawer">
        <div class="between" style="padding:14px 16px;border-bottom:1px solid var(--border)">
          <b>历史对话</b>
          <button class="link-btn" @click="historyOpen = false">关闭</button>
        </div>
        <div class="hist-list">
          <div v-if="!sessions.length" class="empty" style="padding:30px 0">
            还没有历史对话。开一段新对话后，旧的会自动存到这里。
          </div>
          <div v-for="s in sessions" :key="s.id" class="hist-item"
               :class="{ active: s.id === activeSessionId }" @click="switchTo(s)">
            <div class="between">
              <b class="hist-title">{{ s.title }}</b>
              <span class="tiny">{{ timeAgo(s.updatedAt) }}</span>
            </div>
            <div class="tiny">
              {{ s.count || 0 }} 条消息
              <span v-if="s.id === activeSessionId" class="tag" style="margin-left:6px">当前</span>
            </div>
          </div>
        </div>
        <div style="padding:10px 16px;border-top:1px solid var(--border)" class="row">
          <button class="btn btn-plain btn-sm" @click="clearHistory">清空历史</button>
          <span class="tiny">最多保留 30 段</span>
        </div>
      </div>
    </div>
    <PickerModal v-model="pickerOpen" @pick="onPick" />
    <!-- 复制/分享结果的轻提示 -->
    <transition name="fade">
      <div v-if="toast" class="toast">{{ toast }}</div>
    </transition>
    <p class="tiny" style="text-align:center;margin-top:6px">
      历史表现不代表未来，本工具不构成投资建议
    </p>
    </div>

    <!-- 本轮产出：这段对话里生成了哪些文件，一眼能看到、能直接下载 -->
    <aside v-if="artifacts.length" class="artifacts">
      <div class="art-head">
        <b>本轮产出</b>
        <span class="tiny">{{ artifacts.length }} 个文件</span>
      </div>
      <div class="art-list">
        <div v-for="a in artifacts" :key="a.token" class="art-item">
          <div class="art-title" :title="a.title">{{ a.title }}</div>
          <div class="tiny art-meta">
            {{ a.symbol_code }} · {{ a.size_kb }} KB
          </div>
          <div class="art-tags">
            <span v-for="s in (a.sections || []).slice(0, 4)" :key="s" class="art-tag">
              {{ sectionName(s) }}
            </span>
            <span v-if="(a.sections || []).length > 4" class="art-tag">
              +{{ a.sections.length - 4 }}
            </span>
          </div>
          <div class="art-ops">
            <a :href="a.url" target="_blank" rel="noopener" class="art-op">打开网页</a>
            <a :href="a.download_url" class="art-op">下载 HTML</a>
            <a :href="a.script_url" class="art-op">下载 .py</a>
          </div>
        </div>
      </div>
      <p class="tiny art-foot">在这里的都能下载到本地，断网也能打开</p>
    </aside>
  </div>
</template>

<script setup>
// 必须和 App.vue 里 keep-alive 的 include 对上，否则缓存不生效
defineOptions({ name: 'Chat' })
import { ref, computed, reactive, nextTick, onMounted, onActivated, onDeactivated, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import api, { syncBalance } from '../api'
import { useUserStore } from '../stores/user'
import { useCreditsStore } from '../stores/credits'
import { showRecharge } from '../components/rechargeBus'
import { renderMarkdown } from '../utils/markdown'
import CompareTable from '../components/CompareTable.vue'
import BacktestCard from '../components/BacktestCard.vue'
import StrategyMiniList from '../components/StrategyMiniList.vue'
import ParamSuggest from '../components/ParamSuggest.vue'
import PairAnalysis from '../components/PairAnalysis.vue'
import FactorSearchCard from '../components/FactorSearchCard.vue'
import DeployCard from '../components/DeployCard.vue'
import WebResultsCard from '../components/WebResultsCard.vue'
import ReportCard from '../components/ReportCard.vue'
import DesignResult from '../components/DesignResult.vue'
import RobustnessCard from '../components/RobustnessCard.vue'
import PickerModal from '../components/PickerModal.vue'
import { guideState, openGuide } from '../components/guideBus'

// 开发时热更新到 Chat.vue 就整页重载。
// 这个组件被 keep-alive 缓存着：HMR 只换模板时，旧实例还是旧 setup，
// 模板若调到新加的函数就会 `_ctx.xxx is not a function`，整页白屏。
// 直接重载最省事（对话内容本来就在 localStorage 里）。
if (import.meta.hot) {
  import.meta.hot.accept(() => { window.location.reload() })
}

const pickerOpen = ref(false)

// 把选中的策略/因子插到光标位置，用户接着写想让它干什么
function onPick(chosen) {
  if (!chosen.length) return
  const tag = chosen.map(x => x.insert).join('')
  input.value = input.value + tag + '帮我组合这些，看看能不能得到更好的策略'
  nextTick(() => {
    const ta = document.querySelector('.input-bar textarea')
    if (ta) {
      ta.focus()
      ta.setSelectionRange(ta.value.length - 20, ta.value.length - 20)
    }
  })
}

// 首次进来就弹模态框问"新手/老手"，不占用整页
onMounted(() => {
  if (!guideState.level) {
    nextTick(() => openGuide())
  }
})

const user = useUserStore()
const credits = useCreditsStore()
const router = useRouter()
const route = useRoute()
const messages = ref([])
const input = ref('')
const loading = ref(false)
const thinkingText = ref('正在理解你的问题…')
const cutoff = ref('')
const msgBox = ref(null)
const conversationId = ref(null)

// ---- 草稿：切换页面、刷新、关标签页都不该丢 ----
// keep-alive 能保住同一次会话内的输入，但刷新和关标签页保不住，
// 所以还落一份 localStorage。按用户隔离，换账号不会看到别人的草稿。
// 草稿也按会话隔离：两个标签页各写各的，互相看不见
const draftKey = () => `chat:draft:${user.userId || 'guest'}:${curSid(false) || 'default'}`
watch(input, (v) => {
  try {
    if (v) localStorage.setItem(draftKey(), v)
    else localStorage.removeItem(draftKey())
  } catch (e) { /* 配额满就放弃，不影响输入 */ }
})

// ---- 对话持久化（按标签页隔离）----
// 老实现：所有标签共用 `chat:{uid}` 一个 key，后保存的覆盖前一个，
// 表现就是「开个新对话，旧的那段被取消了」；两个标签还会互相踩。
//
// 现在拆成三层：
//   正文   localStorage  chat:s:{uid}:{sid}   一个会话一个 key，各写各的
//   索引   localStorage  chat:i:{uid}         只有 id/标题/时间，够列目录
//   当前   sessionStorage chat:cur:{uid}      sessionStorage 天然按标签隔离，刷新还在
// 两个标签页各自持有不同的 sid，正文写到不同 key，物理上不可能覆盖对方。
const uid = () => user.userId || 'guest'
const indexKey = () => `chat:i:${uid()}`
const bodyKey = (sid) => `chat:s:${uid()}:${sid}`
const tabKey = () => `chat:cur:${uid()}`
const interrupted = ref('')     // 上次提问没等到回答（刷新/离开导致），存那条问题
const deniedTip = ref('')       // 被权限守卫挡回来的提示
const sessions = ref([])        // 历史对话，最新在前
const historyOpen = ref(false)
const activeSessionId = ref(null)

// ---- 本轮产出：把这段对话里生成的文件汇总到侧栏 ----
// 用户要的是「我刚生成了什么、去哪拿」，而不是在消息流里翻半天找卡片。
const SECTION_NAMES = {
  kline: '买卖点', equity: '复利', simple: '单利', drawdown: '回撤',
  metrics: '指标', trades: '逐笔', hysteresis: '回滞',
}
function sectionName(s) { return SECTION_NAMES[s] || s }

const artifacts = computed(() => {
  const seen = new Set()
  const out = []
  for (const m of messages.value) {
    for (const c of m.cards || []) {
      const d = c.data
      if (!d) continue
      // 报告：有 token 才能打开/下载
      if (c.type === 'report' && d.token && !seen.has(d.token)) {
        seen.add(d.token)
        out.push({
          token: d.token,
          title: d.title || '策略报告',
          size_kb: d.size_kb || 0,
          symbol_code: d.symbol_code || '',
          sections: d.sections || [],
          // 打开必须指向 /api/reports/{token}（服务端直出 HTML）。
          // 以前的 /reports/{token} 是前端路由，点进去会落到「我的报告」列表页。
          url: d.api_url || `/api/reports/${d.token}`,
          download_url: d.download_url || `/api/reports/${d.token}?download=1`,
          script_url: d.script_url || `/api/reports/${d.token}/script`,
        })
      }
    }
  }
  return out
})

// ---- 附件上传：截图、CSV、Excel 都能直接丢进来 ----
const attachments = ref([])       // 待发送的附件
const fileInput = ref(null)
const uploading = ref(false)

// 图片先压到最长边 1600 / jpeg 0.85：手机截图动辄 3~5MB，
// 压完通常 200KB 内，既省上传时间也省模型 token。
// 表格和文本不压，直接读。
function compressImage(file, maxSide = 1600, quality = 0.85) {
  return new Promise((resolve) => {
    const img = new Image()
    img.onload = () => {
      let { width, height } = img
      const scale = Math.min(1, maxSide / Math.max(width, height))
      width = Math.round(width * scale)
      height = Math.round(height * scale)
      const canvas = document.createElement('canvas')
      canvas.width = width
      canvas.height = height
      canvas.getContext('2d').drawImage(img, 0, 0, width, height)
      canvas.toBlob((blob) => resolve(blob || file), 'image/jpeg', quality)
    }
    img.onerror = () => resolve(file)     // 解不出来就原样传，别把用户卡住
    img.src = URL.createObjectURL(file)
  })
}

// 选文件 / 拖进来 / 粘贴截图，三条路都走这里
const MAX_FILES = 10
const MAX_SIZE_MB = 20

async function uploadFiles(files) {
  files = (files || []).filter(Boolean)
  if (!files.length) return

  if (attachments.value.length + files.length > MAX_FILES) {
    messages.value.push({
      role: 'assistant',
      content: `一次最多带 ${MAX_FILES} 个文件，你这次给了 ${files.length} 个。分两批发吧。`,
    })
    files = files.slice(0, Math.max(0, MAX_FILES - attachments.value.length))
    if (!files.length) return
  }

  uploading.value = true
  for (const f of files) {
    if (f.size > MAX_SIZE_MB * 1024 * 1024) {
      messages.value.push({
        role: 'assistant',
        content: `${f.name || '这个文件'} 有 ${(f.size / 1024 / 1024).toFixed(1)}MB，`
          + `超过 ${MAX_SIZE_MB}MB 上限。大表格建议先在 Excel 里删掉用不上的列再传。`,
      })
      continue
    }
    try {
      let blob = f
      // 粘贴截图时浏览器给的 name 常常是 "image.png" 甚至空串，
      // 重命名成时间戳，否则附件列表里一排同名文件分不清谁是谁
      let name = (f.name || '').trim()
      if (!name || /^image\.(png|jpe?g)$/i.test(name)) {
        const ext = (f.type || 'image/png').split('/')[1] || 'png'
        name = `截图_${new Date().toISOString().slice(0, 19).replace(/[:T-]/g, '')}.${ext}`
      }
      if (/^image\//.test(f.type)) {
        blob = await compressImage(f)
        // 压完是 jpeg，扩展名要跟着改，否则后端按扩展名判 mime 会对不上
        name = name.replace(/\.(png|jpe?g|webp|bmp|gif)$/i, '') + '.jpg'
      }
      const form = new FormData()
      form.append('file', blob, name)
      const r = await fetch('/api/ai/upload', {
        method: 'POST',
        headers: user.token ? { Authorization: `Bearer ${user.token}` } : {},
        body: form,
      })
      const j = await r.json()
      if (!r.ok || j.code !== 0) throw new Error(j.msg || '上传失败')
      attachments.value.push(j.data)
    } catch (err) {
      // "Failed to fetch" 不是文件的问题，是连不上后端。
      // 直接把这个区别告诉用户，否则他会以为是自己的文件坏了。
      const raw = err.message || ''
      const msg = /failed to fetch|networkerror|load failed/i.test(raw)
        ? '连不上后端服务（可能后端没启动或刚崩了），跟你的文件无关。重启后端再试一次。'
        : raw
      messages.value.push({ role: 'assistant', content: `这个文件没读进来：${msg}` })
    }
  }
  uploading.value = false
}

// 原来 📎 按钮走这里
function onFiles(e) {
  const files = Array.from(e.target.files || [])
  e.target.value = ''                   // 清空，允许再选同一个文件
  uploadFiles(files)
}

// ---- 拖拽上传 ----
// 计数器：鼠标划过子元素会连发 dragleave，直接用布尔会闪
const dragOver = ref(false)
let dragDepth = 0

function hasFiles(e) {
  const t = e.dataTransfer
  return !!t && Array.from(t.types || []).includes('Files')
}

function onDragEnter(e) {
  if (!hasFiles(e)) return
  e.preventDefault()
  dragDepth++
  dragOver.value = true
}

function onDragOver(e) {
  if (!hasFiles(e)) return
  e.preventDefault()                  // 不拦的话浏览器会直接把图片当网页打开
  e.dataTransfer.dropEffect = 'copy'
}

function onDragLeave(e) {
  if (!hasFiles(e)) return
  dragDepth = Math.max(0, dragDepth - 1)
  if (dragDepth === 0) dragOver.value = false
}

function onDrop(e) {
  if (!hasFiles(e)) return
  e.preventDefault()
  dragDepth = 0
  dragOver.value = false
  const files = Array.from(e.dataTransfer.files || [])
  if (!files.length) {
    // 拖文件夹进来时 files 是空的（浏览器不给读目录）
    messages.value.push({
      role: 'assistant',
      content: '拖进来的是文件夹，我读不了目录。把它压缩成 zip，或者单独把里面要用的文件拖过来。',
    })
    return
  }
  uploadFiles(files)
}

// ---- 粘贴上传 ----
// 截图直接 Ctrl+V 就能进输入框，不用先存成文件再点📎
// 焦点不在输入框时（比如刚打开页面），Ctrl+V 也该能粘截图。
// 只在对话页激活期间挂这个监听，切到策略库就把监听摘掉，
// 否则在别的页面粘贴也会往对话里塞图。
function onGlobalPaste(e) {
  const tag = (e.target && e.target.tagName) || ''
  if (tag === 'TEXTAREA' || tag === 'INPUT') return   // 输入框自己的 @paste 处理
  onPaste(e)
}

function onPaste(e) {
  const items = e.clipboardData?.items
  if (!items) return
  const files = []
  for (const it of items) {
    if (it.kind !== 'file') continue
    const f = it.getAsFile()
    if (f) files.push(f)
  }
  if (!files.length) return           // 纯文本粘贴，走浏览器默认
  e.preventDefault()                  // 拦掉，否则某些浏览器会把图片也塞进 textarea
  uploadFiles(files)
}

// 历史对话里不保留大数组（K线、净值曲线几百个点），只留结论数字，
// 否则几段对话就把 localStorage 撑爆了。
function slim(v, depth = 0) {
  if (v == null) return v
  if (Array.isArray(v)) return v.length > 40 ? [] : v.map(x => slim(x, depth + 1))
  if (typeof v === 'object') {
    const out = {}
    for (const [k, x] of Object.entries(v)) out[k] = slim(x, depth + 1)
    return out
  }
  return v
}

function plainMessages(list, slimCards = false) {
  return list.slice(-30).map(m => ({
    role: m.role,
    content: m.content,
    charged: m.charged,
    refunded: m.refunded,
    archived: slimCards,
    // 附件只留文件名：缩略图是 base64，存进 localStorage 会瞬间撑爆配额
    atts: (m.atts || []).map(a => ({ kind: a.kind, name: a.name })),
    cards: (m.cards || []).map(c => ({
      type: c.type, tool: c.tool,
      data: slimCards ? slim(c.data) : c.data,
    })),
  }))
}

function newSid() {
  return 's' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6)
}

// 本标签页当前会话 id。ensure=true 时没有就建一个。
function curSid(ensure = true) {
  let sid = sessionStorage.getItem(tabKey())
  if (!sid && ensure) {
    sid = activeSessionId.value || newSid()
    sessionStorage.setItem(tabKey(), sid)
  }
  return sid
}

function readIndex() {
  try {
    const raw = localStorage.getItem(indexKey())
    const list = raw ? JSON.parse(raw) : []
    return Array.isArray(list) ? list : []
  } catch (e) { return [] }
}

// 索引只放元数据，更新方式是读-改-写。
// 两个标签同时写最多丢一条标题，正文在各自的 key 里，不会丢。
function upsertIndex(sid, title, count = 0) {
  const list = readIndex().filter(x => x.id !== sid)
  list.unshift({ id: sid, title: title || '未命名对话', count, updatedAt: Date.now() })
  const trimmed = list.slice(0, 60)
  try { localStorage.setItem(indexKey(), JSON.stringify(trimmed)) } catch (e) {}
  return trimmed
}

// 老版本只有 `chat:{uid}` 和 `chat:sessions:{uid}`，升级后要把内容搬进新结构，
// 否则用户一刷新，之前的对话全"消失"了。
function migrateLegacyOnce() {
  const flag = `chat:migrated:${uid()}`
  if (localStorage.getItem(flag)) return
  let touched = false
  try {
    const oldBody = localStorage.getItem(`chat:${uid()}`)
    if (oldBody) {
      const d = JSON.parse(oldBody)
      const sid = 's' + Date.now().toString(36)
      localStorage.setItem(bodyKey(sid), JSON.stringify({ ...d, updatedAt: Date.now() }))
      sessionStorage.setItem(tabKey(), sid)
      if (d.messages?.length) {
        const first = d.messages.find(m => m.role === 'user')
        upsertIndex(sid, titleOf(d.messages))
      }
      localStorage.removeItem(`chat:${uid()}`)
      touched = true
    }

    const oldList = localStorage.getItem(`chat:sessions:${uid()}`)
    if (oldList) {
      const arr = JSON.parse(oldList)
      if (Array.isArray(arr)) {
        for (const it of arr.slice(0, 30)) {
          if (!it?.id) continue
          if (!localStorage.getItem(bodyKey(it.id))) {
            try {
              localStorage.setItem(bodyKey(it.id), JSON.stringify({
                conversationId: it.conversationId,
                updatedAt: it.updatedAt || Date.now(),
                messages: it.messages || [],
              }))
            } catch (e) { /* 单条存不下就跳过，不影响其它 */ }
          }
          if (it.title) upsertIndex(it.id, it.title)
        }
      }
      localStorage.removeItem(`chat:sessions:${uid()}`)
      touched = true
    }
    localStorage.setItem(flag, '1')
  } catch (e) {
    // 迁移失败不阻塞使用，新结构照常工作
    console.warn('旧对话迁移失败', e)
  }
  return touched
}

function saveChat() {
  const sid = curSid()
  if (!sid) return
  const payload = {
    conversationId: conversationId.value,
    // 正在等 AI 时也记下来，回来才知道上次是被中断的
    pending: loading.value,
    updatedAt: Date.now(),
    messages: plainMessages(messages.value),
  }
  const write = (obj) => localStorage.setItem(bodyKey(sid), JSON.stringify(obj))
  try {
    write(payload)
  } catch (e) {
    // 配额不够时逐步降级：先丢卡片数据，再只留文本
    try {
      write({ ...payload, messages: payload.messages.map(m => ({ role: m.role, content: m.content })) })
    } catch (e2) {
      try {
        write({
          ...payload,
          messages: payload.messages.slice(-20).map(m => ({ role: m.role, content: m.content })),
        })
      } catch (e3) { /* 实在存不下就放弃，不影响使用 */ }
    }
  }
  if (messages.value.length) upsertIndex(sid, titleOf(messages.value), messages.value.length)
}

function titleOf(list) {
  const first = list.find(m => m.role === 'user')
  const t = (first?.content || '未命名对话').replace(/\s+/g, ' ').trim()
  return t.length > 24 ? t.slice(0, 24) + '…' : t
}

function loadChat() {
  migrateLegacyOnce()
  const sid = curSid(false)
  if (!sid) return false
  activeSessionId.value = sid
  try {
    const raw = localStorage.getItem(bodyKey(sid))
    if (!raw) return false
    const d = JSON.parse(raw)
    if (!d.messages?.length) return false
    messages.value = d.messages
    conversationId.value = d.conversationId || null

    // 上次停在"用户问了、AI 没答"的状态，说明中途刷新或离开了
    if (d.pending) {
      const lastUser = [...d.messages].reverse().find(m => m.role === 'user')
      if (lastUser) interrupted.value = lastUser.content
    }
    return true
  } catch (e) { return false }
}

function openHistory() {
  saveChat()                // 先把当前这段存起来
  sessions.value = readIndex()
  historyOpen.value = true
}

function switchTo(s) {
  if (s.id === activeSessionId.value) { historyOpen.value = false; return }
  if (loading.value) {
    alert('AI 还在回答，等它说完再切换，否则这次回答会丢。')
    return
  }
  saveChat()                // 切换前先存当前这段
  let d = null
  try { d = JSON.parse(localStorage.getItem(bodyKey(s.id)) || 'null') } catch (e) {}
  if (!d) { alert('这段对话的内容找不到了，可能被浏览器清理过。'); return }

  messages.value = d.messages || []
  conversationId.value = d.conversationId || null
  activeSessionId.value = s.id
  sessionStorage.setItem(tabKey(), s.id)   // 只改本标签页，不碰别的标签
  interrupted.value = ''
  historyOpen.value = false
  saveChat()
  nextTick(scrollBottom)
}

function clearHistory() {
  if (!window.confirm('清空这个账号下所有历史对话？当前这段也会清掉，无法恢复。')) return
  for (const it of readIndex()) {
    try { localStorage.removeItem(bodyKey(it.id)) } catch (e) {}
  }
  try { localStorage.removeItem(indexKey()) } catch (e) {}

  const sid = newSid()
  sessionStorage.setItem(tabKey(), sid)
  activeSessionId.value = sid
  messages.value = []
  conversationId.value = null
  interrupted.value = ''
  sessions.value = []
}

function timeAgo(ts) {
  if (!ts) return ''
  const min = Math.floor((Date.now() - ts) / 60000)
  if (min < 1) return '刚刚'
  if (min < 60) return `${min} 分钟前`
  const h = Math.floor(min / 60)
  if (h < 24) return `${h} 小时前`
  return `${Math.floor(h / 24)} 天前`
}

// 取走待填入的内容（sessionStorage 一次性使用）
function takePrefill() {
  const s = sessionStorage.getItem('aiPrefill')
  if (!s) return ''
  sessionStorage.removeItem('aiPrefill')
  input.value = s
  return s
}

function focusInputEnds() {
  nextTick(() => {
    const ta = document.querySelector('.input-bar textarea')
    if (!ta) return
    ta.focus()
    ta.setSelectionRange(ta.value.length, ta.value.length)
  })
}

// 已经在这个页面上时点「做成网页」，路由不会重建组件，
// 靠 query 里那个时间戳触发一次填入
watch(() => route.query.prefill, (v) => {
  if (!v) return
  if (takePrefill()) {
    focusInputEnds()
    router.replace({ path: '/' })     // 清掉时间戳，刷新不会重复填
  }
})

// 新对话 = 换一个新的会话 id，旧的那段原样留在历史里。
// 绝不删旧 key —— 之前正是这里把旧对话"取消"掉的。
function newChat() {
  if (loading.value) {
    if (!window.confirm('AI 还在回答，现在开新对话会中断这次回答。确定吗？')) return
    stop()
  }
  saveChat()                        // 旧对话存好，不删
  const sid = newSid()
  sessionStorage.setItem(tabKey(), sid)
  activeSessionId.value = sid
  messages.value = []
  conversationId.value = null
  interrupted.value = ''
  sessions.value = readIndex()
  nextTick(() => focusInputEnds())
}

// 重新发一次被中断的问题
function retry() {
  const q = interrupted.value
  interrupted.value = ''
  input.value = q
  nextTick(() => send())
}

// 流式期间每个 token 都会变，逐个写 localStorage 会卡，等结束再存
watch(messages, () => { if (!loading.value) saveChat() }, { deep: true })
watch(loading, saveChat)

// 注意：这里刻意不做「离开拦截」。
// 用户想去别的页面看策略就让他去，拦着只会烦人。
// 如果回答被中断，回来会有「上次没答完，要重试吗」的提示，事后能补救。

const quickQuestions = [
  '我是新手，给我一个能用的策略',
  '什么是金叉和死叉？',
  '均线交叉在贵州茅台上市值怎么样',
  '为什么回测好看实盘却亏钱',
]

const thinkingSteps = [
  '正在理解你的问题…',
  '正在查策略库…',
  '正在跑真实历史回测…',
  '正在把结果翻译成人话…',
]
let stepTimer = null
let controller = null     // 用来主动中止流式请求

onMounted(async () => {
  // 先确定本标签页的会话 id：草稿 key 依赖它，
  // 晚了会出现"输入框已经有内容但草稿存到了 default"的错位
  curSid(true)
  loadChat()          // 再把这段会话的内容恢复出来

  // 恢复上次没发出去的草稿（刷新/关标签页前写的内容）
  try {
    const dv = localStorage.getItem(draftKey())
    if (dv && !input.value) input.value = dv
  } catch (e) {}

  // 被路由守卫挡回来的：告诉他为什么，而不是静默跳走
  if (route.query.denied === 'admin') {
    deniedTip.value = '经营看板只对管理员账号开放，你当前的账号没有权限。'
    router.replace({ path: '/' })
  }
  // 从策略详情页 / 结果卡片"做成网页"跳过来时，把上下文填进输入框
  const prefill = takePrefill()

  nextTick(() => {
    scrollBottom()
    if (prefill) focusInputEnds()
  })
  try {
    const d = await api.get('/symbols/meta/cutoff')
    cutoff.value = d.latest_trade_date
  } catch (e) {}
})

function scrollBottom() {
  nextTick(() => {
    if (msgBox.value) msgBox.value.scrollTop = msgBox.value.scrollHeight
  })
}

// keep-alive 下从别的页面切回来时触发：刷新余额、回到底部。
// 不加的话，切走期间流式收到的内容会堆在上面看不到。
// 拦住浏览器「把文件拖进窗口就直接打开」的默认行为。
// .chat-wrap 只占中间那一块，用户拖到两边的留白也会松手 ——
// 不拦的话当前页面会被一张图片顶掉，输入的内容全没了。
function blockDragDefault(e) {
  if (!hasFiles(e)) return
  e.preventDefault()
}

onActivated(() => {
  credits.refresh()
  scrollBottom()
  window.addEventListener('paste', onGlobalPaste)
  window.addEventListener('dragover', blockDragDefault)
  window.addEventListener('drop', blockDragDefault)
})

onDeactivated(() => {
  // 切走时摘掉，避免在别的页面粘贴也往这里塞图
  window.removeEventListener('paste', onGlobalPaste)
  window.removeEventListener('dragover', blockDragDefault)
  window.removeEventListener('drop', blockDragDefault)
  dragOver.value = false
  dragDepth = 0
})

// ---- 消息操作：复制 / 引用 / 分享 ----
const toast = ref('')
let toastTimer = null
function flashToast(msg) {
  toast.value = msg
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toast.value = '' }, 1800)
}

async function copyText(t) {
  try {
    await navigator.clipboard.writeText(t)
    return true
  } catch (e) {
    // http 或旧浏览器没有 clipboard API，退回老办法
    try {
      const ta = document.createElement('textarea')
      ta.value = t
      ta.style.cssText = 'position:fixed;opacity:0;top:0'
      document.body.appendChild(ta)
      ta.select()
      const ok = document.execCommand('copy')
      document.body.removeChild(ta)
      return ok
    } catch (e2) { return false }
  }
}

async function copyMsg(m) {
  const ok = await copyText(m.content || '')
  flashToast(ok ? '已复制' : '复制失败，请手动选中')
}

// 引用：把这句话作为上下文带进输入框，用户接着往下问。
// 用「> 」开头，模型和用户都能一眼看出哪段是引用。
function quoteMsg(m) {
  const raw = (m.content || '').trim()
  if (!raw) return
  // 长回答只引前 200 字，避免输入框被撑满
  const short = raw.length > 200 ? raw.slice(0, 200) + '…' : raw
  const quoted = short.split('\n').map(l => `> ${l}`).join('\n')
  const prefix = `关于你上面这段：\n${quoted}\n\n我的追问：`
  input.value = input.value ? `${prefix}\n${input.value}` : prefix
  nextTick(() => {
    focusInputEnds()
    flashToast('已引用，接着写你的问题')
  })
}

// 分享：这条消息里如果有报告，优先给报告链接（别人能直接看网页）；
// 否则把「问 + 答」整理成一段可粘贴的文本。
async function shareMsg(m) {
  const report = (m.cards || []).find(c => c.type === 'report' && c.data?.token)
  if (report) {
    const url = location.origin + (report.data.api_url || `/api/reports/${report.data.token}`)
    const ok = await copyText(url)
    flashToast(ok ? '报告链接已复制，发给别人就能看' : '复制失败')
    return
  }
  // 找这条回答前面最近的一条用户提问
  const idx = messages.value.indexOf(m)
  let question = ''
  for (let k = idx - 1; k >= 0; k--) {
    if (messages.value[k].role === 'user') { question = messages.value[k].content || ''; break }
  }
  const parts = []
  if (question) parts.push(`问：${question}`)
  if (m.content) parts.push(`答：${m.content}`)
  parts.push('—— 来自全域策略研究所（历史表现不代表未来，不构成投资建议）')
  const ok = await copyText(parts.join('\n\n'))
  flashToast(ok ? '内容已复制，可直接粘贴分享' : '复制失败')
}

function ask(q) {
  input.value = q
  send()
}

async function send() {
  const text = input.value.trim()
  const atts = attachments.value.slice()
  // 只有附件、没有文字也算有效提问（用户可能就想让我看张图）
  if ((!text && !atts.length) || loading.value) return
  if (!user.isLogin) {
    messages.value.push({ role: 'user', content: text })
    messages.value.push({
      role: 'assistant',
      content: '我需要先帮你建个账号才能开始跑回测。点击右上角登录，注册就送 100 积分，够你试很多次。',
    })
    setTimeout(() => router.push('/login'), 1200)
    return
  }

  // 余额不足就别发请求：预扣 5 分，少于这个数必然失败，
  // 与其让用户看到一串报错，不如直接说清 + 给充值入口。
  // 注意先刷新一次，页面开着很久时本地余额可能是过时的。
  if (credits.balance < 5) {
    await credits.refresh()
    if (credits.balance < 5) {
      messages.value.push({ role: 'user', content: text })
      messages.value.push({
        role: 'assistant',
        content: `你的积分只剩 **${credits.balance}**，不够跑一次问答（一次预扣 5 分，按实际用量结算，用不完会退回）。\n\n`
          + '充值入口在右上角「N 积分」或「我的 → 积分中心」。充完直接把这句发出来就行，内容我留着。',
      })
      showRecharge({ need: Math.max(0, 5 - credits.balance), have: credits.balance })
      scrollBottom()
      return
    }
  }

  // 消息里只留附件名和缩略图，不留 base64——localStorage 存不下
  messages.value.push({
    role: 'user',
    content: text,
    atts: atts.map(a => ({
      kind: a.kind, name: a.name,
      thumb: a.kind === 'image' ? a.data_url : '',
    })),
  })
  input.value = ''
  attachments.value = []
  loading.value = true
  interrupted.value = ''

  // 先插入一个空的助手气泡，流式内容往里写
  const reply = ref2({
    role: 'assistant', content: '', cards: [], streaming: true,
    thinking: '', note: '', toolLabel: '',
    t0: Date.now(), elapsed: 0, lastEvtAt: Date.now(),
    stall: false, stallFor: 0, procOpen: true,
  })
  messages.value.push(reply)

  let step = 0
  thinkingText.value = thinkingSteps[0]
  let ticks = 0
  stepTimer = setInterval(() => {
    ticks += 1
    // 这个计时器本身就是"没卡住"的证明：数字在走，说明页面和请求都还活着。
    // 没有它的话，模型思考的这 10~25 秒里界面是静止的，只能靠猜。
    reply.elapsed = (Date.now() - reply.t0) / 1000
    const silent = (Date.now() - reply.lastEvtAt) / 1000
    reply.stallFor = Math.floor(silent)
    reply.stall = reply.streaming && silent > 20
    // 3.5 秒换一句提示（250ms × 14），换太快反而眼花
    if (ticks % 14 === 0) {
      step = (step + 1) % thinkingSteps.length
      thinkingText.value = thinkingSteps[step]
    }
  }, 250)
  scrollBottom()

  try {
    await runStream(text, reply, atts)
  } catch (e) {
    if (e.name === 'AbortError') {
      // 用户主动停止：保留已经收到的内容，标注一下
      reply.streaming = false
      reply.toolLabel = ''
      if (!reply.content) reply.content = '（已停止）'
      else reply.content += '\n\n（已停止生成）'
    } else if (!reply.content) {
      reply.content = `这次没跑成：${e.message}`
    } else {
      reply.content += `\n\n（回答中断：${e.message}）`
    }
    reply.streaming = false
  } finally {
    loading.value = false
    controller = null
    clearInterval(stepTimer)
    reply.streaming = false
    reply.stall = false
    reply.elapsed = (Date.now() - reply.t0) / 1000
    // 答完了就把过程收起来，留一行「思考过程 · 12.3s」可以点开回看
    if (reply.content) reply.procOpen = false
    saveChat()
    scrollBottom()
  }
}

// 注意：过程区的标题不要在模板里调用 setup 里的函数。
// 曾经踩过一次：Chat 被 keep-alive 缓存，热更新换了模板但旧实例还是旧 setup，
// 模板去调新函数 procTitle → "_ctx.procTitle is not a function" → 整页崩。
// 现在标题直接在模板里用 m.* 拼，不会再出现这种问题。

// 思考动辄两三千字。写入时就截断到 2400 字，
// 免得每个 chunk 都重排一大段文本（长回答会明显卡）。
const THINK_KEEP = 2400

// 让普通对象也能被 Vue 追踪
function ref2(obj) { return reactive(obj) }

function stop() {
  if (controller) controller.abort()
}

async function runStream(text, reply, atts = []) {
  controller = new AbortController()
  const resp = await fetch('/api/ai/chat/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(user.token ? { Authorization: `Bearer ${user.token}` } : {}),
    },
    body: JSON.stringify({
      message: text || '帮我看看这个附件',
      conversation_id: conversationId.value,
      // 图片带 data_url，表格/文本带 content，后端拼成多模态消息
      attachments: atts.map(a => ({
        kind: a.kind, name: a.name,
        data_url: a.data_url, content: a.content,
      })),
    }),
    signal: controller.signal,
  })

  if (!resp.ok) {
    let msg = `请求失败（${resp.status}）`
    try {
      const err = await resp.json()
      msg = err.msg || msg
    } catch (e) { /* 非 JSON 响应 */ }
    throw new Error(msg)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })

    const parts = buf.split('\n\n')
    buf = parts.pop()          // 最后一段可能不完整，留着下次拼
    for (const part of parts) {
      if (!part.startsWith('data: ')) continue
      let evt
      try { evt = JSON.parse(part.slice(6)) } catch (e) { continue }
      handleEvent(evt, reply)
    }
  }
}

function handleEvent(evt, reply) {
  // 任何事件都算"还活着"。卡住告警的计时靠它重置。
  reply.lastEvtAt = Date.now()

  switch (evt.type) {
    case 'start':
      conversationId.value = evt.conversation_id
      break

    case 'thinking':
      // 模型的原生思考内容，边想边显示。
      // 只留最后一段，避免长思考把内存和渲染拖垮
      reply.thinking = (reply.thinking + evt.text).slice(-THINK_KEEP)
      break

    case 'note':
      // 后端的额外说明（比如"预算用完了，正在重试"）
      reply.note = evt.text
      break

    case 'delta':
      reply.content += evt.text
      scrollBottom()
      break

    case 'reset':
      // 这一轮其实是工具调用，之前的文字是思考过程
      reply.content = ''
      break

    case 'tool':
      thinkingText.value = evt.label
      reply.toolLabel = evt.label
      // 在过程日志里插一条分隔，看得出"想完 → 去查数据 → 继续想"
      reply.thinking = (reply.thinking + `\n\n▶ ${evt.label}\n`).slice(-THINK_KEEP)
      break

    case 'card':
      reply.cards.push(evt.card)
      scrollBottom()
      break

    case 'replace':
      reply.content = evt.text
      break

    case 'done':
      reply.streaming = false
      reply.toolLabel = ''
      reply.charged = evt.charged
      reply.refunded = evt.refunded
      reply.compliance = evt.compliance
      syncBalance(evt.balance)
      break

    case 'error':
      reply.streaming = false
      reply.toolLabel = ''
      reply.content = evt.msg
      break
  }
}
</script>

<style scoped>
.chat-wrap {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  /* 给右侧「本轮产出」留位置：主区还是 1080，整体加宽到 1380 */
  max-width: 1380px;
  height: calc(100vh - 56px);
  padding-top: 12px;
}
.chat-main { display: flex; flex-direction: column; min-width: 0; min-height: 0; }

/* 窄屏不显示侧栏，避免挤压对话区 */
.artifacts { display: none; }
@media (min-width: 1180px) {
  .chat-wrap { grid-template-columns: minmax(0, 1fr) 252px; gap: 18px; }
  .artifacts {
    display: flex; flex-direction: column; min-height: 0;
    margin-top: 4px; padding: 0 2px 8px;
  }
  .art-head {
    display: flex; align-items: baseline; justify-content: space-between;
    padding: 0 2px 8px; border-bottom: 1px solid var(--border);
  }
  .art-list { overflow-y: auto; padding-top: 10px; }
  .art-item {
    background: #fff; border: 1px solid var(--border);
    border-radius: 9px; padding: 9px 10px; margin-bottom: 9px;
  }
  .art-title {
    font-size: 12.5px; font-weight: 500; line-height: 1.4;
    /* 最多两行，超出省略，避免长标题把侧栏撑乱 */
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .art-meta { margin-top: 2px; }
  .art-tags { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
  .art-tag {
    font-size: 10px; color: var(--text-3); background: var(--bg-2, #f5f5f1);
    border-radius: 4px; padding: 1px 5px;
  }
  .art-ops { display: flex; gap: 8px; margin-top: 8px; }
  .art-op { font-size: 11.5px; color: var(--primary); }
  .art-op:hover { text-decoration: underline; }
  .art-foot { margin-top: 4px; line-height: 1.6; }
}
.intro { text-align: center; padding: 46px 16px 20px; }
.intro h1 { font-size: 24px; font-weight: 600; margin-bottom: 12px; }
.intro p { max-width: 520px; margin: 0 auto; }
.quick { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; margin-top: 20px; }
.level-grid {
  display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px;
  max-width: 640px; margin: 26px auto 0;
}
.level-card {
  background: #fff; border: 1px solid var(--border); border-radius: 14px;
  padding: 22px; cursor: pointer; text-align: left; transition: border-color .15s;
}
.level-card:hover { border-color: var(--primary); }
.level-title { font-size: 17px; font-weight: 600; margin-bottom: 6px; }
.level-card p { color: var(--text-2); margin-bottom: 6px; font-size: 14px; }
@media (max-width: 560px) { .level-grid { grid-template-columns: 1fr; } }
.chip {
  border: 1px solid var(--border); background: #fff; border-radius: 20px;
  padding: 7px 14px; font-size: 13px; color: var(--text-2);
}
.chip:hover { border-color: var(--primary); color: var(--primary); }
.messages { flex: 1; overflow-y: auto; padding: 10px 0 16px; }
.msg { display: flex; margin-bottom: 14px; }
.msg.user { justify-content: flex-end; }
/* 消息操作条：hover 才出现，平时不占视觉 */
.msg-ops {
  position: absolute; top: -11px; right: -4px;
  display: none; gap: 2px;
  background: #fff; border: 1px solid var(--border);
  border-radius: 7px; padding: 2px;
  box-shadow: 0 3px 10px rgba(30, 30, 25, 0.09);
  z-index: 3;
}
.bubble:hover .msg-ops { display: flex; }
.msg-op {
  border: none; background: none; cursor: pointer;
  font-size: 11px; color: var(--text-2);
  padding: 2px 6px; border-radius: 5px; white-space: nowrap;
}
.msg-op:hover { background: var(--primary-soft); color: var(--primary); }

.saved-ok {
  margin-top: 8px; padding: 9px 11px; font-size: 12.5px;
  background: #f2f8f3; border: 1px solid #cfe6d5; border-radius: 8px;
  color: #1a7f4b;
}
/* 进公共库用紫色，和「只存我自己」区分开 —— 这是会影响别人的操作 */
.pub-ok {
  background: var(--primary-soft, #eeecfa);
  border-color: #d9d4f5;
  color: var(--primary);
}
.toast {
  position: fixed; left: 50%; bottom: 96px; transform: translateX(-50%);
  background: rgba(40, 40, 36, 0.92); color: #fff;
  font-size: 12.5px; padding: 7px 14px; border-radius: 8px;
  z-index: 60; pointer-events: none;
}
.fade-enter-active, .fade-leave-active { transition: opacity 0.18s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
.bubble {
  position: relative;
  max-width: 82%; padding: 12px 15px; border-radius: 12px;
  background: #fff; border: 1px solid var(--border);
}
.msg.user .bubble { background: var(--primary); color: #fff; border-color: var(--primary); }
.msg.user .content :deep(b) { color: #fff; }
.content { word-break: break-word; }
.content :deep(table) { width: 100%; border-collapse: collapse; margin: 8px 0; font-size: 12px; }
.card-block { margin-top: 12px; }
.thinking { color: var(--text-3); }

/* ---- 过程区：思考内容 + 计时 ---- */
.proc {
  margin-bottom: 10px; border-radius: 9px;
  background: #faf9f6; border: 1px solid var(--border);
  overflow: hidden;
}
.proc.done { background: #fbfbfa; }
.proc-head {
  width: 100%; display: flex; align-items: center; gap: 8px;
  padding: 7px 10px; background: none; border: 0;
  font-size: 12px; color: var(--text-2); text-align: left; cursor: pointer;
}
.proc-head:hover { background: rgba(0,0,0,.02); }
.proc-dot {
  width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0;
  background: var(--text-3);
}
.proc-dot.live { background: var(--primary); animation: pulse 1s infinite; }
.proc-title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.proc-time {
  font-size: 11px; color: var(--text-3);
  font-variant-numeric: tabular-nums;
}
.proc-caret { font-size: 11px; color: var(--text-3); flex-shrink: 0; }
.proc-body {
  padding: 2px 10px 9px; border-top: 1px dashed var(--border);
  max-height: 220px; overflow-y: auto;
}
.proc-text {
  margin: 6px 0 0; font-size: 11.5px; line-height: 1.65;
  color: var(--text-3); white-space: pre-wrap; word-break: break-word;
  font-family: inherit;
}
.proc-idle { font-size: 11.5px; color: var(--text-3); padding: 6px 0 2px; }
.proc-note {
  margin-top: 6px; padding: 5px 8px; border-radius: 6px;
  background: #fffbf2; border: 1px solid #f0d9a8;
  font-size: 11.5px; color: #92610a;
}
.stall-tip {
  margin-bottom: 8px; padding: 7px 10px; border-radius: 8px;
  background: #fff5f4; border: 1px solid #f3c9c4;
  font-size: 12px; color: #b33a2f;
}

.interrupted { background: #fffbf2; border-color: #f0d9a8; }
.interrupted .content { color: var(--warn); font-size: 13px; }
.tool-hint {
  font-size: 12px; color: var(--primary); margin-bottom: 6px;
  display: inline-flex; align-items: center; gap: 6px;
}
.tool-hint::before {
  content: ''; width: 6px; height: 6px; border-radius: 50%;
  background: var(--primary); animation: pulse 1s infinite;
}
@keyframes pulse { 0%,100% { opacity: .3 } 50% { opacity: 1 } }
.cursor {
  display: inline-block; width: 2px; height: 14px; margin-left: 2px;
  background: var(--primary); vertical-align: text-bottom;
  animation: blink .9s steps(2) infinite;
}
@keyframes blink { 0%,50% { opacity: 1 } 51%,100% { opacity: 0 } }
.input-bar { display: flex; gap: 10px; align-items: flex-end; padding: 10px 0 4px; }
.icon-btn {
  height: 38px; padding: 0 11px; flex-shrink: 0; border-radius: 10px;
  border: 1px solid var(--border); background: #fff;
  font-size: 12px; color: var(--text-2); white-space: nowrap;
}
.icon-btn:hover { border-color: var(--primary); color: var(--primary); }
.link-btn {
  border: none; background: none; color: var(--text-3);
  font-size: 12px; cursor: pointer; padding: 0;
}
.link-btn:hover { color: var(--primary); }

/* 历史对话抽屉 */
.mask {
  position: fixed; inset: 0; background: rgba(30, 30, 25, 0.28);
  z-index: 60; display: flex; justify-content: flex-start;
}
.drawer {
  width: 340px; max-width: 88vw; height: 100%; background: #fff;
  display: flex; flex-direction: column; box-shadow: 4px 0 24px rgba(0,0,0,0.08);
}
.hist-list { flex: 1; overflow-y: auto; }
.hist-item {
  padding: 11px 16px; border-bottom: 1px solid var(--border-soft); cursor: pointer;
}
.hist-item:hover { background: var(--bg-2, #fafaf7); }
.hist-item.active { background: var(--primary-soft, #eeecfa); }
.hist-title { font-size: 13px; font-weight: 500; }
.archived-tip { color: var(--text-3); font-size: 11px; margin-top: 4px; }
.denied { background: #fffbf2; border-color: #f0d9a8; color: #8a6100; }
.new-btn {
  width: 38px; height: 38px; flex-shrink: 0; border-radius: 10px;
  border: 1px solid var(--border); background: #fff;
  font-size: 12px; color: var(--text-2); line-height: 1;
}
.new-btn:hover { border-color: var(--primary); color: var(--primary); }
.plus-btn {
  width: 38px; height: 38px; flex-shrink: 0; border-radius: 10px;
  border: 1px solid var(--border); background: #fff;
  font-size: 19px; color: var(--text-2); line-height: 1;
}
.plus-btn:hover { border-color: var(--primary); color: var(--primary); background: var(--primary-soft); }

/* 待发送附件条 */
.att-bar { display: flex; flex-wrap: wrap; gap: 8px; padding: 8px 0 2px; }
.att-chip {
  display: flex; align-items: center; gap: 6px; max-width: 220px;
  background: #fff; border: 1px solid var(--border); border-radius: 8px;
  padding: 4px 6px 4px 4px;
}
.att-thumb { width: 30px; height: 30px; object-fit: cover; border-radius: 5px; }
.att-name {
  font-size: 11.5px; color: var(--text-2);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.att-x {
  border: none; background: none; color: var(--text-3);
  font-size: 15px; line-height: 1; padding: 0 2px; cursor: pointer;
}
.att-x:hover { color: var(--down); }
.att-loading { font-size: 11.5px; color: var(--text-3); padding: 6px 10px; }

/* 拖拽上传遮罩：覆盖整个视口，pointer-events 关掉，
   否则遮罩会吃掉 drop 事件，容器收不到 */
.drop-mask {
  position: fixed; inset: 0; z-index: 60;
  display: flex; align-items: center; justify-content: center;
  background: rgba(83, 74, 183, 0.10);
  backdrop-filter: blur(1.5px);
  pointer-events: none;
}
.drop-box {
  display: flex; flex-direction: column; align-items: center; gap: 6px;
  padding: 30px 44px; border-radius: 16px;
  background: #fff; border: 2px dashed var(--primary);
  box-shadow: 0 12px 40px rgba(40, 35, 90, 0.16);
}
.drop-icon { font-size: 30px; line-height: 1; }
.drop-title { font-size: 16px; font-weight: 600; color: var(--primary); }
.drop-hint { font-size: 12px; color: var(--text-3); }

/* 消息气泡里的附件 */
.msg-atts { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; }
.msg-att-img {
  max-width: 190px; max-height: 140px; border-radius: 7px;
  border: 1px solid var(--border); object-fit: cover;
}
.msg-att-file {
  font-size: 11.5px; color: var(--text-2);
  background: rgba(255, 255, 255, 0.7); border: 1px solid var(--border);
  border-radius: 6px; padding: 3px 7px;
}
.btn-stop { background: #6b6b64; }
.btn-stop:hover { background: #55554f; }
.input-bar textarea {
  flex: 1; resize: none; border: 1px solid var(--border); border-radius: 10px;
  padding: 10px 13px; background: #fff;
}
.input-bar textarea:focus { outline: none; border-color: var(--primary); }

/* ---- AI 回答的 Markdown 样式 ---- */
.md :deep(h3), .md :deep(h4), .md :deep(h5) {
  font-size: 15px; font-weight: 600; margin: 14px 0 6px; line-height: 1.4;
}
.md :deep(p) { margin: 6px 0; }
.md :deep(ul), .md :deep(ol) { margin: 6px 0 6px 18px; }
.md :deep(li) { margin: 3px 0; }
.md :deep(b) { font-weight: 600; }
.md :deep(code) {
  background: rgba(0,0,0,.05); padding: 1px 5px; border-radius: 4px;
  font-family: ui-monospace, monospace; font-size: 12px;
}
.md :deep(pre) {
  background: #f7f7f5; padding: 10px 12px; border-radius: 8px;
  overflow-x: auto; margin: 8px 0;
}
.md :deep(pre code) { background: none; padding: 0; }
.md :deep(hr) { border: none; border-top: 1px solid var(--border); margin: 12px 0; }
.md :deep(blockquote) {
  border-left: 3px solid var(--primary-soft); padding-left: 10px;
  color: var(--text-2); margin: 8px 0;
}
.md :deep(.md-table) { overflow-x: auto; margin: 10px 0; }
.md :deep(table) { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.md :deep(th), .md :deep(td) {
  padding: 7px 10px; border-bottom: 1px solid var(--border);
  text-align: left; white-space: nowrap;
}
.md :deep(th) { background: var(--bg-soft); color: var(--text-2); font-weight: 500; }
.msg.user .md :deep(code) { background: rgba(255,255,255,.18); }
</style>
