<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { NCode, NCollapse, NCollapseItem, NDataTable, NTag } from 'naive-ui'
import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { DataTableColumns } from 'naive-ui'
import type { QueryData } from '../types'

echarts.use([BarChart, LineChart, PieChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

const props = defineProps<{ data: QueryData }>()
const chartEl = ref<HTMLDivElement>()
let chart: echarts.ECharts | undefined
let resizeObserver: ResizeObserver | undefined

const rows = computed(() => (props.data.rows ?? []).map((row, index) => {
  const record: Record<string, unknown> = { __key: index }
  ;(props.data.columns ?? []).forEach((column, columnIndex) => { record[column] = row[columnIndex] })
  return record
}))

const columns = computed<DataTableColumns<Record<string, unknown>>>(() =>
  (props.data.columns ?? []).map((column) => ({ title: column, key: column, minWidth: 120, ellipsis: { tooltip: true } })),
)

function renderChart() {
  if (!chartEl.value || !props.data.chart_hint || !props.data.rows?.length) return
  chart ||= echarts.init(chartEl.value)
  const labels = props.data.rows.map((row) => String(row[0] ?? ''))
  const seriesColumns = (props.data.columns ?? []).slice(1)
  const isPie = props.data.chart_hint === 'pie'
  chart.setOption({
    color: ['#dd5d3f', '#2f756e', '#dca341', '#5f6f8c'],
    tooltip: { trigger: isPie ? 'item' : 'axis' },
    legend: { bottom: 0, textStyle: { color: '#5b5c58' } },
    grid: { left: 44, right: 18, top: 24, bottom: 48, containLabel: true },
    xAxis: isPie ? undefined : { type: 'category', data: labels, axisLabel: { rotate: labels.length > 7 ? 25 : 0 } },
    yAxis: isPie ? undefined : { type: 'value' },
    series: isPie
      ? [{ type: 'pie', radius: ['38%', '68%'], data: props.data.rows.map((row) => ({ name: String(row[0]), value: Number(row[1]) })) }]
      : seriesColumns.map((name, index) => ({
          name,
          type: props.data.chart_hint,
          smooth: props.data.chart_hint === 'line',
          data: props.data.rows?.map((row) => Number(row[index + 1])) ?? [],
        })),
  }, true)
}

watch(() => props.data, () => nextTick(renderChart), { deep: true })
onMounted(() => {
  renderChart()
  if (chartEl.value) {
    resizeObserver = new ResizeObserver(() => chart?.resize())
    resizeObserver.observe(chartEl.value)
  }
})
onBeforeUnmount(() => { resizeObserver?.disconnect(); chart?.dispose() })
</script>

<template>
  <section class="data-card">
    <div class="data-card__head">
      <div>
        <span class="eyebrow">结构化结果</span>
        <strong>{{ data.row_count ?? data.rows?.length ?? 0 }} 行记录</strong>
      </div>
      <n-tag v-if="data.chart_hint" size="small" :bordered="false">{{ data.chart_hint }} 图</n-tag>
    </div>
    <div v-if="data.chart_hint && data.rows?.length" ref="chartEl" class="chart" aria-label="查询结果图表" />
    <n-data-table
      v-if="data.columns?.length"
      :columns="columns"
      :data="rows"
      :row-key="(row: Record<string, unknown>) => row.__key as number"
      :max-height="300"
      size="small"
      striped
    />
    <n-collapse v-if="data.sql" class="sql-collapse">
      <n-collapse-item title="查看生成的 SQL" name="sql">
        <n-code :code="data.sql" language="sql" word-wrap />
      </n-collapse-item>
    </n-collapse>
  </section>
</template>
