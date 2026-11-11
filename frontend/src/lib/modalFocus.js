const SELECTOR = 'button, [href], input, select, textarea, summary, [tabindex], [contenteditable="true"]'

export function focusableElements(panel) {
  return [...panel.querySelectorAll(SELECTOR)].filter(element =>
    element.tabIndex >= 0 && !element.matches(':disabled') && !element.closest('[inert], [hidden]')
      && element.getClientRects().length > 0 && getComputedStyle(element).visibility !== 'hidden')
}

// One coordinator owns background inertness and keyboard focus for all layers.
// A lower layer may unmount first; never return focus to a removed dialog.
export function createModalFocusManager(doc) {
  const entries = []
  const inerted = new Map()
  let observer = null
  let redirecting = false
  const top = () => entries.reduce((best, entry) => !best || entry.layer >= best.layer ? entry : best, null)
  const restoreInert = element => {
    if (!inerted.has(element)) return
    const previous = inerted.get(element)
    if (previous === null) element.removeAttribute('inert')
    else element.setAttribute('inert', previous)
    inerted.delete(element)
  }
  function sync() {
    const current = top()
    const descriptions = new Set(current ? [...current.panel.querySelectorAll('[aria-describedby]')]
      .flatMap(element => (element.getAttribute('aria-describedby') || '').split(/\s+/)) : [])
    for (const element of [...doc.body.children]) {
      const outside = current && !element.contains(current.panel)
        && !['SCRIPT', 'STYLE', 'LINK'].includes(element.tagName)
        && !(element.getAttribute('role') === 'tooltip' && descriptions.has(element.id))
      if (outside) {
        if (!inerted.has(element)) inerted.set(element, element.getAttribute('inert'))
        element.setAttribute('inert', '')
      } else restoreInert(element)
    }
    for (const element of [...inerted.keys()]) if (!element.isConnected) restoreInert(element)
  }
  function focusInside(entry, preferred) {
    if (!entry || redirecting) return
    redirecting = true
    try {
      const target = preferred?.isConnected && entry.panel.contains(preferred)
        && !preferred.closest('[inert], [hidden]') ? preferred : entry.panel
      target.focus({ preventScroll: true })
    } finally { redirecting = false }
  }
  function onFocus(event) {
    const current = top()
    if (current && !current.panel.contains(event.target)) focusInside(current, current.lastFocus)
    else if (current) current.lastFocus = event.target
  }
  function onKey(event) {
    const current = top()
    if (!current || event.key !== 'Tab' || event.defaultPrevented) return
    const items = focusableElements(current.panel)
    const index = items.indexOf(doc.activeElement)
    if (!items.length || index < 0 || (!event.shiftKey && index === items.length - 1) || (event.shiftKey && index === 0)) {
      event.preventDefault()
      const target = event.shiftKey ? items.at(-1) : items[0]
      ;(target || current.panel).focus({ preventScroll: true })
    }
  }
  return {
    activate(panel, layer = 0, opener = doc.activeElement) {
      const entry = { panel, layer: Number(layer) || 0, opener, lastFocus: panel }
      entries.push(entry)
      if (entries.length === 1) {
        doc.addEventListener('keydown', onKey)
        doc.addEventListener('focusin', onFocus)
        if (typeof MutationObserver !== 'undefined') {
          observer = new MutationObserver(sync)
          observer.observe(doc.body, { childList: true, subtree: true })
        }
      }
      sync()
      if (top() === entry) focusInside(entry)
      let released = false
      return {
        isTop: () => top() === entry,
        release() {
          if (released) return
          released = true
          const wasTop = top() === entry
          entries.splice(entries.indexOf(entry), 1)
          sync()
          const current = top()
          if (!entries.length) {
            observer?.disconnect()
            observer = null
            doc.removeEventListener('keydown', onKey)
            doc.removeEventListener('focusin', onFocus)
            for (const element of [...inerted.keys()]) restoreInert(element)
          }
          if (!wasTop) return
          if (current) focusInside(current, entry.opener)
          else if (entry.opener?.isConnected && !entry.opener.closest('[inert], [hidden]') && !entry.opener.matches(':disabled')) {
            entry.opener.focus({ preventScroll: true })
          }
        }
      }
    }
  }
}

let shared
export function activateModalFocus(panel, layer, opener) {
  if (!shared) shared = createModalFocusManager(document)
  return shared.activate(panel, layer, opener)
}
