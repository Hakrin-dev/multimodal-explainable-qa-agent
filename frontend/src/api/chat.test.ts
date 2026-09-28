import { describe, expect, it, vi } from 'vitest'
import { FIRST_EVENT_TIMEOUT_MS, parseSSEBlock, streamChat } from './chat'

describe('parseSSEBlock', () => {
  it('parses named JSON events', () => {
    expect(parseSSEBlock('event: answer.delta\ndata: {"text":"你好"}')).toEqual({
      event: 'answer.delta',
      data: { text: '你好' },
    })
  })

  it('joins multiline data and ignores comments', () => {
    expect(parseSSEBlock(': keepalive\nevent: note\ndata: first\ndata: second')).toEqual({
      event: 'note',
      data: 'first\nsecond',
    })
  })

  it('keeps the first-event budget explicit instead of imposing a total-turn deadline', () => {
    expect(FIRST_EVENT_TIMEOUT_MS).toBe(12_000)
  })

  it('cancels the timeout after the first SSE event, even while the stream remains open', async () => {
    vi.useFakeTimers()
    let streamController: ReadableStreamDefaultController<Uint8Array> | undefined
    let requestSignal: AbortSignal | null | undefined
    const stream = new ReadableStream<Uint8Array>({ start(controller) { streamController = controller } })
    vi.stubGlobal('fetch', vi.fn(async (_input: RequestInfo, init?: RequestInit) => {
      requestSignal = init?.signal
      return new Response(stream, { headers: { 'content-type': 'text/event-stream' } })
    }))

    try {
      const events: unknown[] = []
      const pending = streamChat('慢查询', 'session', (event) => events.push(event))
      await vi.advanceTimersByTimeAsync(11_999)
      expect(requestSignal?.aborted).toBe(false)

      streamController?.enqueue(new TextEncoder().encode('event: turn.start\ndata: {}\n\n'))
      await vi.advanceTimersByTimeAsync(0)
      expect(events).toHaveLength(1)

      await vi.advanceTimersByTimeAsync(60_000)
      expect(requestSignal?.aborted).toBe(false)
      streamController?.close()
      await pending
    } finally {
      vi.unstubAllGlobals()
      vi.useRealTimers()
    }
  })
})
