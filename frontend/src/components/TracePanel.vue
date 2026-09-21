<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { NEmpty, NTabPane, NTabs } from 'naive-ui'
import { Graph } from '@antv/x6'
import dagre from '@dagrejs/dagre'
import type { TraceNode } from '../types'

const props = defineProps<{ nodes: TraceNode[] }>()
const graphEl = ref<HTMLDivElement>()
const selected = ref<TraceNode>()
let graph: Graph | undefined
let resizeObserver: ResizeObserver | undefined

const labels: Record<string, string> = {
  turn: '本轮请求', intent: '意图识别', plan: '任务规划', tool_call: '工具调用',
  llm_call: '模型调用', fuse: '结果融合', clarify: '信息澄清', step: '处理步骤',
}
const colors: Record<string, string> = {
  intent: '#7b5bd6', plan: '#b47724', tool_call: '#28746d', llm_call: '#496b9b',
  fuse: '#d85a3b', clarify: '#b45367', step: '#687169', turn: '#4b4c48',
}

const timelineNodes = computed(() => props.nodes.filter((node) => node.type !== 'turn'))

function nodeColor(node: TraceNode) {
  if (node.status === 'error') return '#c73f3f'
  if (node.status === 'degraded') return '#c78224'
  return colors[node.type] ?? '#687169'
}

function renderGraph() {
  if (!graphEl.value) return
  graph?.dispose()
  graph = new Graph({
    container: graphEl.value,
    background: { color: 'transparent' },
    grid: false,
    panning: true,
    mousewheel: { enabled: true, modifiers: ['ctrl', 'meta'], minScale: 0.5, maxScale: 1.8 },
    interacting: { nodeMovable: false, edgeMovable: false },
  })
  if (!props.nodes.length) return

  const layout = new dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}))
  layout.setGraph({ rankdir: 'LR', nodesep: 26, ranksep: 64, marginx: 24, marginy: 24 })
  props.nodes.forEach((node) => layout.setNode(node.id, { width: 156, height: 58 }))
  props.nodes.forEach((node) => {
    if (node.parent_id && props.nodes.some((candidate) => candidate.id === node.parent_id)) layout.setEdge(node.parent_id, node.id)
  })
  dagre.layout(layout)

  props.nodes.forEach((node) => {
    const position = layout.node(node.id)
    graph?.addNode({
      id: node.id,
      x: position.x - 78,
      y: position.y - 29,
      width: 156,
      height: 58,
      data: node,
      attrs: {
        body: { fill: '#fffefa', stroke: nodeColor(node), strokeWidth: 1.5, rx: 10, ry: 10 },
        label: { text: `${node.label}\n${node.latency_ms} ms`, fill: '#30312e', fontSize: 12, lineHeight: 18 },
      },
    })
  })
  props.nodes.forEach((node) => {
    if (node.parent_id && graph?.getCellById(node.parent_id)) {
      graph.addEdge({
        source: { cell: node.parent_id, anchor: 'right' },
        target: { cell: node.id, anchor: 'left' },
        attrs: { line: { stroke: '#b8b8af', strokeWidth: 1.2, targetMarker: { name: 'block', width: 7, height: 5 } } },
        connector: { name: 'rounded' },
        router: { name: 'manhattan', args: { padding: 12 } },
      })
    }
  })
  graph.on('node:click', ({ node }) => { selected.value = node.getData<TraceNode>() })
  graph.centerContent()
}

watch(() => props.nodes, () => nextTick(renderGraph), { deep: true })
onMounted(() => {
  renderGraph()
  if (graphEl.value) {
    resizeObserver = new ResizeObserver(() => graph?.resize(graphEl.value?.clientWidth, graphEl.value?.clientHeight))
    resizeObserver.observe(graphEl.value)
  }
})
onBeforeUnmount(() => { resizeObserver?.disconnect(); graph?.dispose() })
</script>

<template>
  <section class="trace-panel">
    <div class="trace-panel__head">
      <div>
        <span class="eyebrow">Explainability trace</span>
        <strong>这条答案如何得到</strong>
      </div>
      <span>{{ timelineNodes.length }} 个步骤</span>
    </div>
    <n-tabs type="segment" animated>
      <n-tab-pane name="timeline" tab="时间线">
        <div v-if="timelineNodes.length" class="timeline">
          <button v-for="(node, index) in timelineNodes" :key="node.id" type="button" class="timeline__item" @click="selected = node">
            <span class="timeline__rail"><i :style="{ background: nodeColor(node) }" /><b v-if="index < timelineNodes.length - 1" /></span>
            <span class="timeline__content">
              <span class="timeline__top"><strong>{{ node.label }}</strong><small>{{ node.latency_ms }} ms</small></span>
              <span>{{ labels[node.type] ?? node.type }} · {{ node.status }}</span>
            </span>
          </button>
        </div>
        <n-empty v-else description="等待推理步骤" />
      </n-tab-pane>
      <n-tab-pane name="dag" tab="DAG">
        <div ref="graphEl" class="trace-graph" />
      </n-tab-pane>
    </n-tabs>
    <div v-if="selected" class="trace-detail">
      <button type="button" aria-label="关闭节点详情" @click="selected = undefined">×</button>
      <span class="eyebrow">节点详情</span>
      <strong>{{ selected.label }}</strong>
      <pre>{{ JSON.stringify({ input: selected.input, output: selected.output, detail: selected.detail }, null, 2) }}</pre>
    </div>
  </section>
</template>
