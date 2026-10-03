export interface ManagedDocument {
  doc_id: string
  name: string
  file?: string
  size?: number
  pdf_url?: string
  source?: 'knowledge_base' | 'upload'
}

export interface DocumentQuality {
  doc_id?: string
  original?: Record<string, unknown>
  repaired?: Record<string, unknown> | null
  [key: string]: unknown
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) {
    const detail = await response.text().catch(() => '')
    throw new Error(detail || `请求失败（HTTP ${response.status}）`)
  }
  return response.json() as Promise<T>
}

export async function listDocuments(): Promise<ManagedDocument[]> {
  const payload = await request<{ docs?: ManagedDocument[] }>('/api/docs')
  return payload.docs ?? []
}

export function documentPdfUrl(docId: string, version: 'original' | 'repaired' = 'original'): string {
  return `/api/docs/${encodeURIComponent(docId)}/pdf?version=${version}`
}

export function getDocumentQuality(docId: string): Promise<DocumentQuality> {
  return request<DocumentQuality>(`/api/docs/${encodeURIComponent(docId)}/quality`)
}

export function repairDocument(docId: string, options: Record<string, boolean>): Promise<Record<string, unknown>> {
  return request<Record<string, unknown>>(`/api/docs/${encodeURIComponent(docId)}/repair`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(options),
  })
}

export async function uploadDocument(file: File): Promise<ManagedDocument> {
  const form = new FormData()
  form.append('file', file)
  return request<ManagedDocument>('/api/docs/upload', { method: 'POST', body: form })
}
