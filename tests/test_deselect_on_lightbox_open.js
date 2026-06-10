// Tests for "Deselect Images When Opening Lightbox" plan.
// Run: node --test tests/test_deselect_on_lightbox_open.js
const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(
  path.join(__dirname, '..', 'web', 'index.html'),
  'utf8'
);

// Extract character positions inside openLightbox for ordering assertions.
const fnStart = html.indexOf('async function openLightbox');
// Find next top-level async function after openLightbox to bound the search.
const fnEnd = html.indexOf('\n    async function ', fnStart + 1);
const fnBody = fnEnd > fnStart ? html.slice(fnStart, fnEnd) : html.slice(fnStart, fnStart + 2000);

describe('Deselect on lightbox open', () => {
  it('openLightbox calls clearSelection()', () => {
    assert.ok(
      fnBody.includes('clearSelection()'),
      'openLightbox should call clearSelection()'
    );
  });

  it('clearSelection() is called before the lightbox open class is added', () => {
    const clearIdx = fnBody.indexOf('clearSelection()');
    const openClassIdx = fnBody.indexOf("lightbox').classList.add('open')");
    assert.ok(clearIdx !== -1, 'clearSelection() not found in openLightbox');
    assert.ok(openClassIdx !== -1, "classList.add('open') not found in openLightbox");
    assert.ok(
      clearIdx < openClassIdx,
      `clearSelection() (pos ${clearIdx}) must precede classList.add('open') (pos ${openClassIdx})`
    );
  });

  it('openLightbox cancels pick-parent phase when _pickingParent is true', () => {
    assert.match(
      fnBody,
      /if\s*\(_pickingParent\)\s*cancelPickParentPhase\(\)/,
      'openLightbox should guard cancelPickParentPhase() with if (_pickingParent)'
    );
  });

  it('cancelPickParentPhase() is called before clearSelection() in openLightbox', () => {
    const cancelIdx = fnBody.indexOf('cancelPickParentPhase()');
    const clearIdx = fnBody.indexOf('clearSelection()');
    assert.ok(cancelIdx !== -1, 'cancelPickParentPhase() not found in openLightbox');
    assert.ok(clearIdx !== -1, 'clearSelection() not found in openLightbox');
    assert.ok(
      cancelIdx < clearIdx,
      `cancelPickParentPhase() (pos ${cancelIdx}) must precede clearSelection() (pos ${clearIdx})`
    );
  });
});
