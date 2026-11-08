<template>
  <div class="container" style="padding:22px 0 70px;max-width:920px">
    <h2 style="margin:0 0 4px">部署上线</h2>
    <p class="muted" style="margin:0 0 6px">
      把你验证过的策略搬到自己的服务器上，每个交易日自动跑一次。
      <b>一台最便宜的服务器就够。</b>
    </p>
    <p class="tiny" style="margin:0 0 22px">
      所有策略共用一套模板，换策略只改参数，部署逻辑不用重来。
    </p>

    <!-- 步骤 1 -->
    <section class="card step">
      <div class="step-head">
        <span class="num">1</span>
        <div>
          <b>买一台服务器</b>
          <p class="tiny" style="margin:2px 0 0">
            程序一次只跑十几秒、占几百 MB 内存。别买贵的，2 核 2G 足够。
          </p>
        </div>
      </div>

      <div class="plat-grid">
        <div v-for="p in platforms" :key="p.name" class="plat">
          <div class="between">
            <b>{{ p.name }}</b>
          </div>
          <div class="tiny" style="margin:4px 0 6px">{{ p.spec }}</div>
          <div class="price">{{ p.price }}</div>
          <p class="tiny" style="margin:6px 0 0">{{ p.note }}</p>
        </div>
      </div>

      <div class="tip">
        <b>买的时候注意三点</b>
        <ul>
          <li>镜像选 <b>Ubuntu 22.04 / 24.04</b>，别的系统要多装东西</li>
          <li>地域选<b>国内</b>（上海/杭州/北京），拉 A 股行情更快</li>
          <li>安全组<b>只开 22 端口</b>，不要开 80/443 —— 这个程序不对外提供服务</li>
        </ul>
      </div>
    </section>

    <!-- 步骤 2 -->
    <section class="card step">
      <div class="step-head">
        <span class="num">2</span>
        <div>
          <b>生成并下载部署包</b>
          <p class="tiny" style="margin:2px 0 0">
            包里是策略代码、Docker 配置、一键脚本和使用说明，<b>不依赖我们的服务器</b>。
          </p>
        </div>
      </div>

      <div class="row wrap" style="margin-top:14px;gap:10px">
        <select v-model="strategyCode" class="inp" style="min-width:200px">
          <option value="">选择策略</option>
          <option v-for="s in strategies" :key="s.code" :value="s.code">{{ s.name }}</option>
        </select>
        <input v-model="symbol" class="inp" style="width:120px" placeholder="股票代码" />
        <select v-model.number="years" class="inp" style="width:110px">
          <option :value="1">近 1 年</option>
          <option :value="3">近 3 年</option>
          <option :value="5">近 5 年</option>
        </select>
        <button class="btn" :disabled="!strategyCode || downloading" @click="download">
          {{ downloading ? '正在打包…' : '下载部署包' }}
        </button>
      </div>

      <div v-if="packInfo" class="pack-info">
        <div class="between">
          <b>包已生成 {{ packInfo.size_kb }} KB</b>
          <span class="tiny">{{ packInfo.files.length }} 个文件</span>
        </div>
        <div class="files">
          <span v-for="f in packInfo.files" :key="f.path" class="file">
            {{ f.path }} <em>{{ f.lines }} 行</em>
          </span>
        </div>
      </div>
      <p v-if="error" class="err">{{ error }}</p>
    </section>

    <!-- 步骤 3 -->
    <section class="card step">
      <div class="step-head">
        <span class="num">3</span>
        <div>
          <b>传上去，跑一条命令</b>
          <p class="tiny" style="margin:2px 0 0">在你自己的电脑上执行第一条，然后登录服务器跑第二条。</p>
        </div>
      </div>

      <div class="code-block">
        <div class="between">
          <span class="code-label">上传（本地电脑）</span>
          <button class="link-btn" @click="copy(cmd1)">复制</button>
        </div>
        <pre>{{ cmd1 }}</pre>
      </div>

      <div class="code-block">
        <div class="between">
          <span class="code-label">部署（服务器上）</span>
          <button class="link-btn" @click="copy(cmd2)">复制</button>
        </div>
        <pre>{{ cmd2 }}</pre>
      </div>

      <p class="tiny" style="margin:8px 0 0">
        `deploy.sh` 会自动装 Docker → 构建镜像 → 先跑一次验证 → 加每个交易日 18:10 的定时任务。
      </p>
    </section>

    <!-- 步骤 4 -->
    <section class="card step">
      <div class="step-head">
        <span class="num">4</span>
        <div>
          <b>看结果</b>
          <p class="tiny" style="margin:2px 0 0">信号写在 <code>data/signals.json</code>，只有 BUY / SELL / HOLD 三种。</p>
        </div>
      </div>
      <div class="code-block">
        <pre>cat data/signals.json
docker compose logs -f
docker compose run --rm strategy</pre>
      </div>
    </section>

    <!-- 诚实边界 -->
    <section class="card bound">
      <b>这个部署包不做什么</b>
      <ul>
        <li><b>不自动下单</b>：自动交易需要券商接口权限和风控，程序刻意不碰钱。</li>
        <li><b>不替你执行远程命令</b>：SSH 操作不可逆，一次误操作可能弄坏你已有的服务。所以做成「生成包 + 分步引导」。</li>
        <li><b>不保证赚钱</b>：它只是把你在平台上验证过的规则，原样搬到服务器每天执行一遍。</li>
        <li><b>没有告警</b>：出错了要自己看日志。需要通知可以自己接个 webhook。</li>
      </ul>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import api, { getUserToken } from '../api'

const strategies = ref([])
const platforms = ref([])
const strategyCode = ref('')
const symbol = ref('600519')
const years = ref(3)
const downloading = ref(false)
const packInfo = ref(null)
const error = ref('')

const cmd1 = computed(() =>
  `scp -r jragent-strategy root@你的服务器IP:/opt/`)
const cmd2 = computed(() =>
  `ssh root@你的服务器IP\ncd /opt/jragent-strategy\nbash deploy.sh`)

onMounted(async () => {
  try {
    const [s, g] = await Promise.all([
      api.get('/strategies', { params: { page_size: 50 } }),
      api.get('/deploy/guide'),
    ])
    strategies.value = s.items || []
    platforms.value = g.platforms || []
  } catch (e) {
    error.value = e.message
  }
})

async function download() {
  downloading.value = true
  error.value = ''
  try {
    // 先用 preview 拿到文件清单，让用户知道包里有啥
    packInfo.value = await api.post('/deploy/preview', {
      strategy_code: strategyCode.value,
      symbol_code: symbol.value,
      years: years.value,
    })

    const resp = await fetch('/api/deploy/package', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(getUserToken() ? { Authorization: `Bearer ${getUserToken()}` } : {}),
      },
      body: JSON.stringify({
        strategy_code: strategyCode.value,
        symbol_code: symbol.value,
        years: years.value,
      }),
    })
    if (!resp.ok) {
      const j = await resp.json().catch(() => ({}))
      throw new Error(j.msg || `下载失败（HTTP ${resp.status}）`)
    }
    const blob = await resp.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `jragent-${strategyCode.value}-${symbol.value}.zip`
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    error.value = e.message
  } finally {
    downloading.value = false
  }
}

function copy(text) {
  navigator.clipboard?.writeText(text)
}
</script>

<style scoped>
.step { margin-bottom: 14px; }
.step-head { display: flex; gap: 12px; align-items: flex-start; }
.num {
  width: 24px; height: 24px; flex-shrink: 0; border-radius: 50%;
  background: var(--primary); color: #fff; font-size: 13px;
  display: flex; align-items: center; justify-content: center;
}
.plat-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 14px; }
.plat {
  border: 1px solid var(--border); border-radius: 10px; padding: 12px;
  background: var(--bg-2, #fafaf7);
}
.price { color: #c0362c; font-size: 13px; font-weight: 600; }
.tip {
  margin-top: 12px; padding: 11px 13px; border-radius: 8px;
  background: #fffbf2; border: 1px solid #f0e2c2; font-size: 12.5px;
}
.tip ul { margin: 6px 0 0; padding-left: 18px; line-height: 1.8; }
.inp {
  height: 36px; padding: 0 10px; border: 1px solid var(--border);
  border-radius: 8px; background: #fff; font-size: 13px;
}
.pack-info {
  margin-top: 12px; padding: 10px 12px; border-radius: 8px;
  background: var(--primary-soft, #eeecfa); font-size: 12.5px;
}
.files { display: flex; flex-wrap: wrap; gap: 6px 12px; margin-top: 7px; }
.file { color: var(--text-2); font-size: 11.5px; }
.file em { color: var(--text-3); font-style: normal; }
.code-block { margin-top: 12px; background: #2a2a26; border-radius: 8px; padding: 10px 12px; }
.code-label { color: #b9b9b0; font-size: 11px; }
.link-btn {
  border: none; background: none; color: #c9c9c0;
  font-size: 11px; cursor: pointer;
}
pre {
  margin: 6px 0 0; color: #eaeae3; font-size: 12.5px;
  font-family: ui-monospace, Menlo, Consolas, monospace;
  white-space: pre-wrap; line-height: 1.7;
}
.bound { background: #fafaf7; }
.bound ul { margin: 8px 0 0; padding-left: 18px; font-size: 12.5px; line-height: 1.9; color: var(--text-2); }
.err { color: #c0362c; font-size: 12.5px; margin-top: 8px; }
@media (max-width: 760px) {
  .plat-grid { grid-template-columns: 1fr; }
}
</style>
