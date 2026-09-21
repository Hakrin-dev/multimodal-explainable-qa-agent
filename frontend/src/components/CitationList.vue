<script setup lang="ts">
import { NTag, NTooltip } from 'naive-ui'
import type { Citation } from '../types'

defineProps<{ citations: Citation[] }>()

function openCitation(citation: Citation) {
  if (!citation.doc_id) return
  const page = citation.page ? `#page=${citation.page}` : ''
  window.open(`/api/docs/${encodeURIComponent(citation.doc_id)}/pdf${page}`, '_blank', 'noopener,noreferrer')
}
</script>

<template>
  <section class="citations">
    <div class="section-kicker">引用溯源 · {{ citations.length }}</div>
    <n-tooltip v-for="(citation, index) in citations" :key="`${citation.doc_id}-${citation.page}-${index}`">
      <template #trigger>
        <button class="citation" type="button" :disabled="!citation.doc_id" @click="openCitation(citation)">
          <span class="citation__index">{{ index + 1 }}</span>
          <span class="citation__body">
            <strong>{{ citation.breadcrumb || citation.doc }}</strong>
            <small>第 {{ citation.page ?? '—' }} 页<span v-if="citation.score != null"> · 相关度 {{ citation.score.toFixed(3) }}</span></small>
            <span v-if="citation.snippet" class="citation__snippet">{{ citation.snippet }}</span>
          </span>
          <span class="citation__arrow">↗</span>
        </button>
      </template>
      PDF 服务端点接入后可定位原文
    </n-tooltip>
  </section>
</template>
