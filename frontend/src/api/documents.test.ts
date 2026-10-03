import { afterEach, describe, expect, it, vi } from 'vitest'
import { documentPdfUrl, getDocumentQuality, listDocuments, repairDocument, uploadDocument } from './documents'

afterEach(() => vi.unstubAllGlobals())

describe('document management API contract', () => {
  it('maps the B document list response', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ docs: [{ doc_id: 'a', name: 'a', source: 'upload' }] }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await expect(listDocuments()).resolves.toEqual([{ doc_id: 'a', name: 'a', source: 'upload' }])
    expect(fetchMock).toHaveBeenCalledWith('/api/docs', undefined)
  })

  it('keeps PDF version query parameters compatible with the preview API', () => {
    expect(documentPdfUrl('bad_scan')).toBe('/api/docs/bad_scan/pdf?version=original')
    expect(documentPdfUrl('bad_scan', 'repaired')).toBe('/api/docs/bad_scan/pdf?version=repaired')
  })

  it('sends repair options as the B request body', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ state: 'repaired' }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await repairDocument('bad_scan', { correct_orientation: true, run_ocr: true })
    expect(fetchMock).toHaveBeenCalledWith('/api/docs/bad_scan/repair', expect.objectContaining({
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ correct_orientation: true, run_ocr: true }),
    }))
  })

  it('uses multipart upload without overriding Content-Type', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ doc_id: 'new-doc' }), { status: 201 }))
    vi.stubGlobal('fetch', fetchMock)
    await uploadDocument(new File(['%PDF-test'], 'test.pdf', { type: 'application/pdf' }))
    const [, init] = fetchMock.mock.calls[0] as unknown as [RequestInfo, RequestInit]
    expect(init?.method).toBe('POST')
    expect(init?.body).toBeInstanceOf(FormData)
    expect((init?.headers as Record<string, string> | undefined)?.['Content-Type']).toBeUndefined()
  })

  it('requests quality reports through the stable doc_id path', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ doc_id: 'doc-1', original: {} }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await getDocumentQuality('doc-1')
    expect(fetchMock).toHaveBeenCalledWith('/api/docs/doc-1/quality', undefined)
  })
})
