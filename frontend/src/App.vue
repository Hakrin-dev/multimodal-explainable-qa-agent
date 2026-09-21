<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onMounted, reactive, ref } from 'vue'
import { NButton, NConfigProvider, NInput, NMessageProvider, NTag, type GlobalThemeOverrides } from 'naive-ui'
import ChatMessage from './components/ChatMessage.vue'
import { checkHealth, streamChat } from './api/chat'
import type { ChatMessageModel, ClarifyPayload, SSEEvent, TraceNode, TraceTree, TurnResult } from './types'

const TracePanel = defineAsyncComponent(() => import('./components/TracePanel.vue'))

const themeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#d85a3b', primaryColorHover: '#bb4930', primaryColorPressed: '#9d3e2b',
    borderRadius: '8px', fontFamily: 'Inter, "Noto Sans SC", system-ui, sans-serif',
  },
}

const examples = ['销量前十的曲目是哪些？', '一共有几种曲风？', '员工每年有多少天年假？', '销售额增长率是多少？']
const question = ref('')
const sending = ref(false)
const backendOnline = ref<boolean>()
const activeTrace = ref<TraceNode[]>([])
const feedEl = ref<HTMLElement>()
const sessionId = ref(localStorage.getItem('mqa.session-id') || crypto.randomUUID())
const aborter = ref<AbortController>()
const messages = ref<ChatMessageModel[]>([])

const sessionLabel = computed(() => sessionId.value.slice(0, 8))

onMounted(async () => {
  localStorage.setItem('mqa.session-id', sessionId.value)
  backendOnline.value = await checkHealth()
})

function flattenTrace(tree?: TraceTree): TraceNode[] {
  if (!tree?.root) return []
  const result: TraceNode[] = []
  const walk = (node: TraceNode) => { result.push(node); node.children?.forEach(walk) }
  walk(tree.root)
  return result
}

function scrollToBottom() {
  nextTick(() => feedEl.value?.scrollTo({ top: feedEl.value.scrollHeight, behavior: 'smooth' }))
}

function handleEvent(event: SSEEvent, assistant: ChatMessageModel) {
  const data = event.data as Record<string, unknown>
  if (event.event === 'trace.node') {
    const node = data as unknown as TraceNode
    const existing = activeTrace.value.findIndex((item) => item.id === node.id)
    if (existing === -1) activeTrace.value.push(node)
    else activeTrace.value.splice(existing, 1, node)
    assistant.traceNodes = [...activeTrace.value]
  } else if (event.event === 'answer.delta') {
    assistant.text += String(data.text ?? '')
  } else if (event.event === 'clarify.request') {
    assistant.clarify = data as unknown as ClarifyPayload
  } else if (event.event === 'answer.done') {
    const result = data as unknown as TurnResult
    assistant.result = result
    assistant.text = result.answer || assistant.text
    assistant.clarify = result.status === 'clarify' ? result.clarify : assistant.clarify
    activeTrace.value = flattenTrace(result.trace)
    assistant.traceNodes = [...activeTrace.value]
  } else if (event.event === 'turn.end') {
    assistant.pending = false
  } else if (event.event === 'error') {
    assistant.error = String(data.message ?? '服务处理失败')
    assistant.pending = false
  }
  scrollToBottom()
}

async function send(value = question.value) {
  const text = value.trim()
  if (!text || sending.value) return
  question.value = ''
  activeTrace.value = []
  const stamp = Date.now().toString(36)
  const user: ChatMessageModel = { id: `u-${stamp}`, role: 'user', text }
  const assistant = reactive<ChatMessageModel>({ id: `a-${stamp}`, role: 'assistant', text: '', pending: true, traceNodes: [] })
  messages.value.push(user, assistant)
  sending.value = true
  aborter.value = new AbortController()
  scrollToBottom()
  try {
    await streamChat(text, sessionId.value, (event) => handleEvent(event, assistant), aborter.value.signal)
    backendOnline.value = true
  } catch (error) {
    assistant.pending = false
    assistant.error = error instanceof DOMException && error.name === 'AbortError'
      ? '连接后端超时，请确认服务已启动后重试'
      : error instanceof Error ? error.message : '连接失败，请确认后端已启动'
    backendOnline.value = false
  } finally {
    assistant.pending = false
    sending.value = false
    aborter.value = undefined
    scrollToBottom()
  }
}

function newSession() {
  aborter.value?.abort()
  sessionId.value = crypto.randomUUID()
  localStorage.setItem('mqa.session-id', sessionId.value)
  messages.value = []
  activeTrace.value = []
  question.value = ''
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    void send()
  }
}
</script>

<template>
  <n-config-provider :theme="null" :theme-overrides="themeOverrides">
    <n-message-provider>
      <div class="app-shell">
        <header class="topbar">
          <a class="brand" href="#" aria-label="问迹首页">
            <span class="brand__mark">问</span>
            <span><strong>问迹</strong><small>EXPLAINABLE DATA AGENT</small></span>
          </a>
          <div class="topbar__center">Chinook 数据工作台 <span>/</span> 对话分析</div>
          <div class="topbar__actions">
            <n-tag round size="small" :type="backendOnline === true ? 'success' : backendOnline === false ? 'error' : 'default'">
              <span class="status-dot" />{{ backendOnline === true ? '服务在线' : backendOnline === false ? '服务离线' : '检测中' }}
            </n-tag>
            <n-button quaternary size="small" @click="newSession">＋ 新会话</n-button>
          </div>
        </header>

        <main class="workspace">
          <section class="conversation">
            <div ref="feedEl" class="feed">
              <div v-if="!messages.length" class="welcome">
                <span class="welcome__index">01 / ASK</span>
                <h1>从问题到依据，<br><em>每一步都看得见。</em></h1>
                <p>查询业务数据、检索制度文档，或让智能体联合两个数据源回答。结果、SQL 与推理链路会同步呈现。</p>
                <div class="examples">
                  <button v-for="(example, index) in examples" :key="example" type="button" @click="send(example)">
                    <span>0{{ index + 1 }}</span>{{ example }}<b>↗</b>
                  </button>
                </div>
              </div>
              <ChatMessage v-for="message in messages" :key="message.id" :message="message" @choose-clarify="send" />
            </div>

            <footer class="composer-wrap">
              <div class="composer" :class="{ 'is-busy': sending }">
                <n-input
                  v-model:value="question"
                  type="textarea"
                  placeholder="问一个关于数据或文档的问题…"
                  :autosize="{ minRows: 1, maxRows: 4 }"
                  :disabled="sending"
                  @keydown="handleKeydown"
                />
                <n-button circle type="primary" :loading="sending" :disabled="!question.trim() && !sending" aria-label="发送" @click="send()">↑</n-button>
              </div>
              <div class="composer-meta"><span>Enter 发送 · Shift + Enter 换行</span><span>会话 {{ sessionLabel }}</span></div>
            </footer>
          </section>

          <aside class="inspector">
            <TracePanel :nodes="activeTrace" />
            <div class="inspector-note">
              <span>LIVE TRACE</span>
              <p>节点会随回答过程逐步出现。点击任一节点查看输入、输出和结构化细节。</p>
            </div>
          </aside>
        </main>
      </div>
    </n-message-provider>
  </n-config-provider>
</template>
