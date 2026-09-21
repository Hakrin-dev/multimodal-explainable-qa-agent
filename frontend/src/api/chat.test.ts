import { describe, expect, it } from 'vitest'
import { parseSSEBlock } from './chat'

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
})
