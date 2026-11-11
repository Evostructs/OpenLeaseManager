import test from 'node:test'
import assert from 'node:assert/strict'
import { translate } from '../src/i18n/translate.js'
import { currencyLabel } from '../src/i18n/currency.js'
import { localizeChartOption } from '../src/i18n/charts.js'

test('currency unit changes labels in both languages without changing numbers', () => {
  assert.equal(translate('元/㎡/天', 'en', 'USD'), 'USD/m²/day')
  assert.equal(translate('金额（元）', 'zh-CN', 'USD'), '金额（USD）')
  assert.equal(translate('1,000.25 元', 'zh-CN', 'USD'), '1,000.25 USD')
  assert.equal(translate('万元', 'en', 'USD'), 'USD 10k')
  assert.equal(translate('万元', 'en', 'CNY'), 'CNY 10k')
  assert.equal(translate('¥1,000.25', 'en', 'USD'), 'USD 1,000.25')
  assert.equal(translate(1000.25, 'en', 'USD'), 1000.25)
  assert.equal(currencyLabel('单元 / 多元 / 美元 / 元素', 'USD'), '单元 / 多元 / 美元 / 元素')
})

test('chart currency unit preserves data and updates tooltip units', () => {
  const data = [{ name: 'CNY Research', value: 1000.25 }]
  const original = { series: [{ data }], yAxis: { name: '万元' }, tooltip: { valueFormatter: n => `${n} 元` } }
  for (const language of ['en', 'zh-CN']) {
    const localized = localizeChartOption(original, language, 'USD')
    assert.equal(localized.series[0].data, data)
    assert.match(localized.yAxis.name, /USD/)
    assert.equal(localized.tooltip.valueFormatter(1000.25), '1000.25 USD')
  }
  assert.equal(original.yAxis.name, '万元')
})
