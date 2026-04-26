/**
 * useL3BE — 解析后端返回的 L3BE 二进制 payload
 *
 * 直接从 viewer.html parseL3BE() 提取，逻辑不变。
 * 调用方式：const sections = parseL3BE(arrayBuffer)
 */
export function parseL3BE(ab) {
  const dv = new DataView(ab)
  const magic = String.fromCharCode(
    dv.getUint8(0), dv.getUint8(1), dv.getUint8(2), dv.getUint8(3)
  )
  if (magic !== 'L3BE') throw new Error('Invalid L3BE magic: ' + magic)

  const sectionCount      = dv.getUint32(12, true)
  const sectionTableStart = dv.getUint32(16, true)
  const ENTRY_SIZE        = 80
  const decoder           = new TextDecoder('ascii')

  const sections = {}
  for (let i = 0; i < sectionCount; i++) {
    const base    = sectionTableStart + i * ENTRY_SIZE
    const nameArr = new Uint8Array(ab, base, 32)
    const name    = decoder.decode(nameArr).replace(/\0+$/, '')

    const dtypeCode = dv.getUint16(base + 32, true)
    const ndim      = dv.getUint16(base + 34, true)
    const shape     = []
    for (let d = 0; d < ndim; d++) {
      shape.push(dv.getUint32(base + 36 + d * 4, true))
    }
    const dataOffset = dv.getUint32(base + 52, true)
    const dataNBytes = dv.getUint32(base + 60, true)

    let data
    switch (dtypeCode) {
      case 2:  data = new Uint8Array(ab,   dataOffset, dataNBytes);         break
      case 5:  data = new Int32Array(ab,   dataOffset, dataNBytes / 4);     break
      case 9:  data = new Float32Array(ab, dataOffset, dataNBytes / 4);     break
      default: data = new Uint8Array(ab,   dataOffset, dataNBytes)
    }
    sections[name] = { data, shape, dtype: dtypeCode }
  }
  return sections
}
