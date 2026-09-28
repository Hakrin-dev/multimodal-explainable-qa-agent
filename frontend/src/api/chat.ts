import type { SSEEvent } from '../types'

export const FIRST_EVENT_TIMEOUT_MS = 12_000

export async function streamChat(
  question: string,
  sessionId: string,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const controller = new AbortController()
  const forwardAbort = () => controller.abort()
  signal?.addEventListener('abort', forwardAbort, { once: true })
  // Only guard the pre-first-event phase. Once the server has started the
  // stream, a slow HYBRID/DB turn must be allowed to finish without a total
  // request deadline; the caller can still abort through the supplied signal.
  let firstEventReceived = false
  const firstEventTimeout = setTimeout(() => controller.abort(), FIRST_EVENT_TIMEOUT_MS)
  const markFirstEvent = () => {
    if (!firstEventReceived) {
      firstEventReceived = true
      clearTimeout(firstEventTimeout)
    }
  }

  try {
    const response = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ question, session_id: sessionId }),
      signal: controller.signal,
    })
    if (!response.ok) throw new Error(`请求失败（HTTP ${response.status}）`)
    if (!response.headers.get('content-type')?.includes('text/event-stream')) {
      throw new Error('服务未返回事件流，请检查 /api 反向代理配置')
    }
    if (!response.body) throw new Error('浏览器未返回可读取的数据流')

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n')
      let boundary = buffer.indexOf('\n\n')
      while (boundary !== -1) {
        const parsed = parseSSEBlock(buffer.slice(0, boundary))
        buffer = buffer.slice(boundary + 2)
        if (parsed) {
          markFirstEvent()
          onEvent(parsed)
        }
        boundary = buffer.indexOf('\n\n')
      }
      if (done) break
    }

    const tail = parseSSEBlock(buffer)
    if (tail) {
      markFirstEvent()
      onEvent(tail)
    }
  } catch (error) {
    throw error
  } finally {
    clearTimeout(firstEventTimeout)
    signal?.removeEventListener('abort', forwardAbort)
  }
}

export function parseSSEBlock(block: string): SSEEvent | null {
  let event = 'message'
  const data: string[] = []
  for (const line of block.split('\n')) {
    if (!line || line.startsWith(':')) continue
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    let value = colon === -1 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'event') event = value
    if (field === 'data') data.push(value)
  }
  if (!data.length) return null
  const raw = data.join('\n')
  try {
    return { event, data: JSON.parse(raw) }
  } catch {
    return { event, data: raw }
  }
}

export async function checkHealth(): Promise<boolean> {
  try {
    const response = await fetch('/api/health', { signal: AbortSignal.timeout(3000) })
    return response.ok && response.headers.get('content-type')?.includes('application/json') === true
  } catch {
    return false
  }
}
