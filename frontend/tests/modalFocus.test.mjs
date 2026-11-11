import assert from 'node:assert/strict'
import test from 'node:test'
import { createModalFocusManager } from '../src/lib/modalFocus.js'

function fixture() {
  const listeners = new Map()
  const doc = { activeElement: null, addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name) }
  globalThis.getComputedStyle = () => ({ visibility: 'visible' })
  const element = (name, parent = null, tabIndex = -1) => {
    const attrs = new Map(), node = { name, children: [], parent, tagName: 'DIV', tabIndex, isConnected: true, disabled: false,
      getAttribute: key => attrs.get(key) ?? null, setAttribute: (key, value) => attrs.set(key, value), removeAttribute: key => attrs.delete(key),
      contains(other) { return other === this || this.children.some(child => child.contains(other)) },
      closest() { return attrs.has('inert') || attrs.has('hidden') ? this : parent?.closest() || null },
      matches() { return this.disabled }, getClientRects() { return this.isConnected ? [{}] : [] },
      querySelectorAll(selector) { return this.children.flatMap(child => [child, ...child.querySelectorAll(selector)])
        .filter(child => selector === '[aria-describedby]' ? child.getAttribute('aria-describedby') !== null : child.tabIndex >= 0) },
      focus() { doc.activeElement = this; listeners.get('focusin')?.({ target: this }) }
    }
    parent?.children.push(node)
    return node
  }
  doc.body = element('body')
  const app = element('app', doc.body), opener = element('open', app, 0)
  opener.focus()
  const backdrop = element('backdrop', doc.body), panel = element('panel', backdrop)
  const first = element('first', panel, 0), last = element('last', panel, 0)
  const manager = createModalFocusManager(doc)
  const tab = (shiftKey = false) => { let prevented = false; listeners.get('keydown')?.({ key: 'Tab', shiftKey, preventDefault() { prevented = true } }); return prevented }
  return { doc, app, opener, panel, first, last, manager, element, tab, listeners }
}

test('Tab and Shift+Tab stay inside the active modal and restore the opener', () => {
  const f = fixture(), session = f.manager.activate(f.panel)
  assert.equal(f.doc.activeElement, f.panel)
  assert.equal(f.app.getAttribute('inert'), '')
  assert.equal(f.tab(), true)
  assert.equal(f.doc.activeElement, f.first)
  assert.equal(f.tab(true), true)
  assert.equal(f.doc.activeElement, f.last)
  assert.equal(f.tab(), true)
  assert.equal(f.doc.activeElement, f.first)
  f.opener.focus()
  assert.equal(f.doc.activeElement, f.first)
  session.release()
  assert.equal(f.doc.activeElement, f.opener)
  assert.equal(f.app.getAttribute('inert'), null)
  assert.equal(f.listeners.size, 0)
})

test('only the highest layer owns keyboard focus; closing a nested layer returns inside its parent', () => {
  const f = fixture(), parent = f.manager.activate(f.panel, 1)
  f.last.focus()
  const other = f.element('nested-backdrop', f.doc.body), child = f.element('nested-panel', other)
  const nested = f.manager.activate(child, 2)
  assert.equal(parent.isTop(), false)
  assert.equal(nested.isTop(), true)
  assert.equal(f.tab(), true)
  assert.equal(f.doc.activeElement, child)
  nested.release()
  assert.equal(f.doc.activeElement, f.last)
  assert.equal(parent.isTop(), true)
  parent.release()
  assert.equal(f.doc.activeElement, f.opener)
})

test('a lower dialog mounted later cannot steal focus; out-of-order release is safe', () => {
  const f = fixture(), highest = f.manager.activate(f.panel, 40)
  const backdrop = f.element('lower', f.doc.body), panel = f.element('lower-panel', backdrop)
  const lower = f.manager.activate(panel, 1)
  assert.equal(f.doc.activeElement, f.panel)
  lower.release()
  lower.release()
  assert.equal(highest.isTop(), true)
  highest.release()
  assert.equal(f.app.getAttribute('inert'), null)
})

test('disabled fields are skipped; removed triggers are not focused and original inertness survives', () => {
  const f = fixture()
  const original = f.element('already-inert', f.doc.body)
  original.setAttribute('inert', 'original')
  const session = f.manager.activate(f.panel)
  f.first.disabled = true
  f.tab()
  assert.equal(f.doc.activeElement, f.last)
  f.opener.isConnected = false
  session.release()
  assert.notEqual(f.doc.activeElement, f.opener)
  assert.equal(original.getAttribute('inert'), 'original')
})
