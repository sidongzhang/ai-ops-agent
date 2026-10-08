/**
 * 把诊断回答拆成 结论 / 影响 / 关键证据 / 建议 / 待确认。
 *
 * 与 SVOM 浮窗（AiopsAssistant.vue）保持同一套结构与措辞约定，
 * 让两个入口的展示骨架一致。不符合约定时返回 structured=false，
 * 由调用方回退为整段 markdown 渲染。
 */
const LABELS = {
  结论: 'conclusion',
  影响: 'impact',
  关键证据: 'evidence',
  证据链: 'evidence',
  证据: 'evidence',
  建议: 'advice',
  处置建议: 'advice',
  待确认: 'pending',
  待核实: 'pending',
  待观察: 'pending',
}

const LABEL_PATTERN = /^[\s>*#-]*(结论|影响|关键证据|证据链|证据|建议|处置建议|待确认|待核实|待观察)[\s*]*[:：]?\s*(.*)$/

export function parseAnswer(answer) {
  const empty = {
    structured: false,
    conclusion: '',
    impact: '',
    evidence: '',
    advice: '',
    pending: '',
    confidence: '',
    confidenceText: '',
  }
  const text = String(answer || '').trim()
  if (!text) return empty

  const buckets = {}
  let current = null
  text.split(/\r?\n/).forEach((line) => {
    const matched = line.match(LABEL_PATTERN)
    if (matched) {
      current = LABELS[matched[1]]
      if (!buckets[current]) buckets[current] = []
      if (matched[2]) buckets[current].push(matched[2])
      return
    }
    if (current) buckets[current].push(line)
  })

  const join = (key) => (buckets[key] || []).join('\n').trim()
  const conclusion = join('conclusion')
  if (!conclusion) return empty

  const confidence = (conclusion.match(/置信度[\s:：]*([高中低])/) || [])[1] || ''
  const level = confidence === '高' ? 'high' : (confidence === '低' ? 'low' : (confidence === '中' ? 'medium' : ''))
  return {
    structured: true,
    conclusion,
    impact: join('impact'),
    evidence: join('evidence'),
    advice: join('advice'),
    pending: join('pending'),
    confidence: level,
    confidenceText: confidence ? `置信度 ${confidence}` : '',
  }
}
