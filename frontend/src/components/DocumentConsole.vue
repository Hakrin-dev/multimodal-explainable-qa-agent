<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NButton, NTag } from 'naive-ui'
import { documentPdfUrl, getDocumentQuality, listDocuments, repairDocument, uploadDocument, type DocumentQuality, type ManagedDocument } from '../api/documents'

const emit = defineEmits<{ close: [] }>()
const documents = ref<ManagedDocument[]>([])
const selected = ref<string>()
const quality = ref<DocumentQuality>()
const loading = ref(false)
const uploading = ref(false)
const repairing = ref(false)
const error = ref('')
const fileInput = ref<HTMLInputElement>()

async function refresh() {
  loading.value = true; error.value = ''
  try { documents.value = await listDocuments() } catch (e) { error.value = e instanceof Error ? e.message : '文档列表加载失败' }
  finally { loading.value = false }
}

async function inspect(doc: ManagedDocument) {
  selected.value = doc.doc_id; quality.value = undefined; error.value = ''
  try { quality.value = await getDocumentQuality(doc.doc_id) } catch (e) { error.value = e instanceof Error ? e.message : '质量报告加载失败' }
}

async function upload(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  uploading.value = true; error.value = ''
  try {
    const uploaded = await uploadDocument(file)
    await refresh()
    await inspect(uploaded)
  } catch (e) { error.value = e instanceof Error ? e.message : '上传失败' }
  finally { uploading.value = false; if (fileInput.value) fileInput.value.value = '' }
}

async function repair() {
  if (!selected.value) return
  repairing.value = true; error.value = ''
  try { await repairDocument(selected.value, { correct_orientation: true, enhance_clarity: true, run_ocr: true, normalize_traditional: true }); await inspect({ doc_id: selected.value, name: selected.value }) }
  catch (e) { error.value = e instanceof Error ? e.message : '修复失败' }
  finally { repairing.value = false }
}

function pretty(value: unknown): string { return JSON.stringify(value, null, 2) }
function openPdf(version: 'original' | 'repaired' = 'original') {
  if (selected.value) window.open(documentPdfUrl(selected.value, version), '_blank', 'noopener,noreferrer')
}
onMounted(refresh)
</script>

<template>
  <section class="doc-console">
    <header class="doc-console__head">
      <div><span class="eyebrow">04 / DOCUMENTS</span><h1>文档管理台</h1><p>上传、查看质量报告、修复并预览知识库文档。</p></div>
      <div class="doc-console__actions"><input ref="fileInput" type="file" accept="application/pdf" hidden @change="upload"><n-button secondary :loading="uploading" @click="fileInput?.click()">上传 PDF</n-button><n-button quaternary @click="emit('close')">返回对话</n-button></div>
    </header>
    <div v-if="error" class="doc-console__error">{{ error }}</div>
    <div class="doc-console__body">
      <div class="doc-list">
        <div class="doc-list__meta"><span>{{ loading ? '正在加载…' : `${documents.length} 份文档` }}</span><n-button text size="small" @click="refresh">刷新</n-button></div>
        <button v-for="doc in documents" :key="doc.doc_id" class="doc-item" :class="{ selected: selected === doc.doc_id }" @click="inspect(doc)">
          <span class="doc-item__icon">PDF</span><span><strong>{{ doc.name || doc.doc_id }}</strong><small>{{ doc.doc_id }} · {{ doc.source === 'upload' ? '已上传' : '知识库' }} · {{ doc.size ? `${Math.round(doc.size / 1024)} KB` : '已入库' }}</small></span><span>›</span>
        </button>
        <p v-if="!loading && !documents.length" class="doc-empty">暂无文档，请上传 PDF。</p>
      </div>
      <div class="doc-detail" v-if="selected">
        <div class="doc-detail__head"><div><span class="eyebrow">QUALITY REPORT</span><h2>{{ selected }}</h2></div><n-tag v-if="quality?.repaired" type="success">已生成修复件</n-tag></div>
        <div class="doc-detail__actions"><n-button size="small" @click="openPdf()">查看原件</n-button><n-button v-if="quality?.repaired" size="small" @click="openPdf('repaired')">查看修复件</n-button><n-button size="small" type="primary" :loading="repairing" @click="repair">执行修复</n-button></div>
        <pre v-if="quality">{{ pretty(quality) }}</pre><p v-else class="doc-empty">正在读取质量报告…</p>
      </div>
      <div v-else class="doc-detail doc-empty">选择一份文档查看质量、复杂度和修复状态。</div>
    </div>
  </section>
</template>
