export type Intent = 'CHAT' | 'DB_QUERY' | 'DOC_QUERY' | 'HYBRID' | 'AMBIGUOUS' | string
export type TurnStatus = 'ok' | 'clarify' | 'degraded' | 'error'
export type NodeStatus = 'pending' | 'ok' | 'error' | 'degraded' | 'skipped'

export interface Citation {
  doc: string
  doc_id?: string
  page?: number
  breadcrumb?: string
  snippet?: string
  score?: number
}

export interface QueryData {
  sql?: string
  columns?: string[]
  rows?: unknown[][]
  row_count?: number
  chart_hint?: 'bar' | 'line' | 'pie' | null
  status?: string
  [key: string]: unknown
}

export interface TraceNode {
  id: string
  parent_id?: string | null
  type: string
  label: string
  input?: unknown
  output?: unknown
  latency_ms: number
  status: NodeStatus
  detail?: Record<string, unknown>
  started_at?: string
  children?: TraceNode[]
}

export interface TraceTree {
  trace_version: string
  turn_id: string
  question: string
  created_at: string
  root: TraceNode
}

export interface ClarifyPayload {
  missing_slots?: string[]
  options?: Record<string, unknown[]>
  question?: string
}

export interface TurnResult {
  question: string
  answer: string
  intent: Intent
  status: TurnStatus
  data: QueryData
  citations: Citation[]
  clarify: ClarifyPayload
  cost_rmb: number
  latency_ms: number
  trace: TraceTree
}

export interface ChatMessageModel {
  id: string
  role: 'user' | 'assistant'
  text: string
  pending?: boolean
  result?: TurnResult
  traceNodes?: TraceNode[]
  clarify?: ClarifyPayload
  error?: string
}

export interface SSEEvent<T = unknown> {
  event: string
  data: T
}
