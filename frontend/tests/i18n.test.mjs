import test from 'node:test'
import assert from 'node:assert/strict'
import { translate, readLocale, persistLocale } from '../src/i18n/translate.js'
import { localizeChartOption, restoreChartEvent } from '../src/i18n/charts.js'

test('English is the default, saved Chinese survives, and blocked storage is safe', () => {
  const saved = new Map()
  const storage = { getItem: key => saved.get(key), setItem: (key, value) => saved.set(key, value) }
  assert.equal(readLocale(storage), 'en')
  assert.equal(persistLocale('zh-CN', storage), 'zh-CN')
  assert.equal(readLocale(storage), 'zh-CN')
  assert.equal(persistLocale('unknown', storage), 'en')
  const blocked = { getItem() { throw Error('blocked') }, setItem() { throw Error('blocked') } }
  assert.equal(readLocale(blocked), 'en')
  assert.equal(persistLocale('zh-CN', blocked), 'zh-CN')
})

test('presentation strings translate without modifying values or unknown business names', () => {
  assert.equal(translate('收款核销'), 'Payment allocation')
  assert.equal(translate('在租', 'zh-CN'), '在租')
  assert.equal(translate('  保存 '), '  Save ')
  assert.equal(translate('张氏贸易有限公司'), '张氏贸易有限公司')
  assert.equal(translate(123.45), 123.45)
  assert.equal(translate(null), null)
  assert.equal(translate('房号 张氏贸易有限公司 存在 2 份有效合同').includes('张氏贸易有限公司'), true)
  assert.equal(translate('4间 · 4租'), '4 units · 4 leased')
})

test('chart localization leaves data, getters and event identities intact', () => {
  let reads = 0
  const source = { series: [{ name: '应收', get data() { reads++; return [{ name: '保存', value: 10 }] } }], yAxis: { name: '万元' } }
  const result = localizeChartOption(source, 'en')
  assert.equal(reads, 0)
  assert.equal(source.series[0].name, '应收')
  assert.equal(result.series[0].name, 'Receivable')
  assert.equal(result.series[0].data[0].name, '保存')
  assert.equal(result.yAxis.name, 'CNY 10k')
  assert.deepEqual(restoreChartEvent({ seriesIndex: 0, seriesName: 'Receivable', value: 10 }, source), { seriesIndex: 0, seriesName: '应收', value: 10 })
  assert.equal(localizeChartOption(source, 'zh-CN'), source)
})

test('area terminology preserves leased versus leasable definitions', () => {
  assert.equal(translate('在租面积（㎡）'), 'Leased area (m²)')
  assert.equal(translate('可出租面积减去在租面积。'), 'Leasable area minus leased area.')
  assert.equal(translate('在租面积 / 可出租面积'), 'Leased area / leasable area')
  assert.equal(translate('统计日有效在租面积去重合计。'), 'Total leased area on the reporting date, with duplicates removed.')
})
