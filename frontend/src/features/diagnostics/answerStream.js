
/** fetch streaming keeps the existing header-based ALGP login (no token in URLs). */
export function subscribeDiagnosis(url, onReport, onUnavailable) {
  const controller = new AbortController()
  let cursor = ''
  let finished = false
  let latest = null
  let draft = null
  const acceptDraft = value => {
    if (value && (!draft || value.stream_id !== draft.stream_id || value.sequence > draft.sequence)) draft = value
  }
  const deliver = report => {
    acceptDraft(report.business_context && report.business_context.answer_stream)
    latest = { ...report, business_context: { ...(report.business_context || {}), ...(draft ? { answer_stream: draft } : {}) } }
    onReport(latest)
  }
  const run = async () => {
    for (let attempt = 0; attempt < 3 && !controller.signal.aborted; attempt += 1) {
      const connection = new AbortController()
      const abortConnection = () => connection.abort()
      controller.signal.addEventListener('abort', abortConnection)
      let idleTimer
      const touch = () => {
        clearTimeout(idleTimer)
        idleTimer = setTimeout(abortConnection, 45000)
      }
      try {
        const headers = { Accept: 'text/event-stream', Authorization: `Bearer ${localStorage.getItem('token') || ''}` }
        if (cursor) headers['Last-Event-ID'] = cursor
        touch()
        const response = await fetch(url, { headers, credentials: 'include', signal: connection.signal })
        if (!response.ok || !response.body || !(response.headers.get('content-type') || '').includes('text/event-stream')) {
          throw new Error(`事件连接失败：${response.status}`)
        }
        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        try {
          while (!controller.signal.aborted) {
            const chunk = await reader.read()
            if (chunk.done) break
            touch()
            buffer += decoder.decode(chunk.value, { stream: true })
            if (buffer.length > 4 * 1024 * 1024) throw new Error('事件数据过大')
            let boundary
            while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
              const frame = buffer.slice(0, boundary.index)
              buffer = buffer.slice(boundary.index + boundary[0].length)
              let event = 'message', id = ''
              const data = []
              frame.split(/\r?\n/).forEach(line => {
                if (line.startsWith('event:')) event = line.slice(6).trim()
                if (line.startsWith('id:')) id = line.slice(3).trim()
                if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''))
              })
              if (event === 'report') {
                const report = JSON.parse(data.join('\n'))
                if (id) cursor = id
                attempt = 0 // Healthy long runs may reconnect many times; bound consecutive failures.
                deliver(report)
                if (['success', 'failed', 'cancelled'].includes(report.status)) { finished = true; return }
              } else if (event === 'answer') {
                const payload = JSON.parse(data.join('\n'))
                if (latest && payload.id === latest.id && latest.status === 'running') {
                  acceptDraft(payload.answer_stream)
                  deliver(latest)
                }
              } else if (event === 'done') { finished = true; return }
              else if (event === 'unavailable') throw new Error('诊断报告已不可用')
            }
          }
        } finally { await reader.cancel().catch(() => {}); reader.releaseLock() }
      } catch (error) {
        if (controller.signal.aborted) return
      } finally {
        clearTimeout(idleTimer)
        controller.signal.removeEventListener('abort', abortConnection)
      }
      if (attempt < 2 && !controller.signal.aborted) await new Promise(resolve => setTimeout(resolve, 1000 * (attempt + 1)))
    }
    if (!finished && !controller.signal.aborted) onUnavailable()
  }
  run().catch(() => { if (!controller.signal.aborted) onUnavailable() })
  return () => controller.abort()
}
