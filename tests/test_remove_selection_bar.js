// Structural tests for Remove Selection Bar plan.
// Run: node --test tests/test_remove_selection_bar.js
const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(
  path.join(__dirname, '..', 'web', 'index.html'),
  'utf8'
);

describe('Phase 1 — removed HTML elements', () => {
  it('selection-count is not in the HTML', () => {
    assert.doesNotMatch(html, /id="selection-count"/);
  });

  it('selection-link-btn is not in the HTML', () => {
    assert.doesNotMatch(html, /id="selection-link-btn"/);
  });

  it('selection-fork-btn is not in the HTML', () => {
    assert.doesNotMatch(html, /id="selection-fork-btn"/);
  });

  it('selection-delete-btn is not in the HTML', () => {
    assert.doesNotMatch(html, /id="selection-delete-btn"/);
  });
});

describe('Phase 2 — pick-parent elements remain', () => {
  it('pick-parent-message is still in the HTML', () => {
    assert.match(html, /id="pick-parent-message"/);
  });

  it('pick-parent-cancel-btn is still in the HTML', () => {
    assert.match(html, /id="pick-parent-cancel-btn"/);
  });

  it('pick-parent-confirm is still in the HTML', () => {
    assert.match(html, /id="pick-parent-confirm"/);
  });
});

describe('Phase 3 — _updateSelectionBar no longer references removed elements', () => {
  // Extract just the _updateSelectionBar function body for scoped assertions
  const fnMatch = html.match(/function _updateSelectionBar\(\)\s*\{[\s\S]*?\n\s*\}/);
  const fn = fnMatch ? fnMatch[0] : '';

  it('_updateSelectionBar does not reference selection-count', () => {
    assert.doesNotMatch(fn, /selection-count/);
  });

  it('_updateSelectionBar does not reference selection-link-btn', () => {
    assert.doesNotMatch(fn, /selection-link-btn/);
  });

  it('_updateSelectionBar does not reference selection-fork-btn', () => {
    assert.doesNotMatch(fn, /selection-fork-btn/);
  });

  it('_updateSelectionBar does not reference selection-delete-btn', () => {
    assert.doesNotMatch(fn, /selection-delete-btn/);
  });

  it('_updateSelectionBar still opens bar when _pickingParent', () => {
    assert.match(fn, /_pickingParent/);
    assert.match(fn, /classList\.add\('open'\)/);
  });

  it('_updateSelectionBar does not open bar outside pick-parent phase', () => {
    // The else branch must NOT contain classList.toggle('open', n > 0) anymore
    assert.doesNotMatch(fn, /classList\.toggle\('open'/);
  });
});

describe('Phase 4 — CSS rules for removed elements are gone', () => {
  it('no CSS rule for #selection-count', () => {
    assert.doesNotMatch(html, /#selection-count\s*\{/);
  });

  it('no CSS rule for #selection-link-btn', () => {
    assert.doesNotMatch(html, /#selection-link-btn/);
  });

  it('no CSS rule for #selection-fork-btn', () => {
    assert.doesNotMatch(html, /#selection-fork-btn/);
  });

  it('no CSS rule for #selection-delete-btn', () => {
    assert.doesNotMatch(html, /#selection-delete-btn/);
  });
});

describe('Phase 5 — multi-select and context-menu delete still work', () => {
  it('_rangeSelect function is defined', () => {
    assert.match(html, /function _rangeSelect\(/);
  });

  it('shift-key handler calls _rangeSelect', () => {
    // The shiftKey block and _rangeSelect call are in the same card-click handler
    assert.match(html, /if\s*\(e\.shiftKey\)/);
    assert.match(html, /_rangeSelect\(uuid\)/);
  });

  it('contextmenu handler adds card to _selectedUUIDs when not already selected', () => {
    // The contextmenu handler must check !_selectedUUIDs.has(uuid) and then add
    assert.match(html, /!_selectedUUIDs\.has\(uuid\)[\s\S]{0,100}_selectedUUIDs\.add\(uuid\)/);
  });

  it('context menu has Delete item calling _deleteSelected', () => {
    assert.match(html, /onclick="_deleteSelected\(\)"[^>]*>Delete/);
  });

  it('_deleteSelected iterates _selectedUUIDs', () => {
    const fnMatch = html.match(/async function _deleteSelected\(\)\s*\{[\s\S]*?\n\s*\}/);
    const fn = fnMatch ? fnMatch[0] : '';
    assert.match(fn, /_selectedUUIDs/);
  });
});

describe('Phase 6 — no JS errors from missing elements after bar cleanup', () => {
  // _updateSelectionBar must only call getElementById with IDs that still exist in the HTML
  const fnMatch = html.match(/function _updateSelectionBar\(\)\s*\{[\s\S]*?\n\s*\}/);
  const fn = fnMatch ? fnMatch[0] : '';

  // Collect all getElementById calls within the function
  const idRefs = [...fn.matchAll(/getElementById\('([^']+)'\)/g)].map(m => m[1]);

  it('_updateSelectionBar references at least one element', () => {
    assert.ok(idRefs.length > 0, 'expected getElementById calls in _updateSelectionBar');
  });

  it('every element referenced by _updateSelectionBar exists in the HTML', () => {
    for (const id of idRefs) {
      assert.match(html, new RegExp(`id="${id}"`), `#${id} referenced in _updateSelectionBar but not found in HTML`);
    }
  });
});
