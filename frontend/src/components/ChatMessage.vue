<script setup lang="ts">
import { defineAsyncComponent } from 'vue'
import { NButton, NTag } from 'naive-ui'
import CitationList from './CitationList.vue'
import type { ChatMessageModel } from '../types'

const DataCard = defineAsyncComponent(() => import('./DataCard.vue'))

defineProps<{ message: ChatMessageModel }>()
const emit = defineEmits<{ chooseClarify: [value: string] }>()

function optionsOf(message: ChatMessageModel): string[] {
  return Object.values(message.clarify?.options ?? {}).flat().map(String)
}
</script>

<template>
  <article class="message" :class="`message--${message.role}`">
    <div class="message__meta">
      <span>{{ message.role === 'user' ? '你' : '问迹' }}</span>
      <n-tag v-if="message.result?.intent" size="tiny" :bordered="false">{{ message.result.intent }}</n-tag>
      <span v-if="message.result">{{ message.result.latency_ms }} ms</span>
    </div>
    <div class="message__bubble" :class="{ 'is-pending': message.pending }">
      <p v-if="message.text">{{ message.text }}</p>
      <div v-else-if="message.pending" class="thinking"><i /><i /><i /><span>正在梳理问题</span></div>
      <div v-if="message.error" class="message__error">{{ message.error }}</div>
    </div>
    <DataCard v-if="message.result?.data?.columns?.length" :data="message.result.data" />
    <CitationList v-if="message.result?.citations?.length" :citations="message.result.citations" />
    <div v-if="message.clarify" class="clarify">
      <span class="section-kicker">请选择补充信息</span>
      <template v-if="optionsOf(message).length">
        <n-button v-for="option in optionsOf(message)" :key="option" round size="small" @click="emit('chooseClarify', option)">{{ option }}</n-button>
      </template>
      <small v-else>后端暂未给出候选项，请直接在下方输入补充信息。</small>
    </div>
  </article>
</template>
