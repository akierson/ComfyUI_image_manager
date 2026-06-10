// Structural tests for Search Clear Button plan.
// Run: node --test tests/test_search_clear.js
const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(
  path.join(__dirname, '..', 'web', 'index.html'),
  'utf8'
);

describe('Phase 3 — clear button click handler', () => {
  it('click handler sets input.value to empty string', () => {
    assert.match(html, /search-clear-btn[\s\S]{0,500}input\.value\s*=\s*['"]{2}/);
  });

  it('click handler calls exitSearchMode()', () => {
    assert.match(html, /search-clear-btn[\s\S]{0,500}exitSearchMode\(\)/);
  });

  it('click handler calls input.focus()', () => {
    assert.match(html, /search-clear-btn[\s\S]{0,500}input\.focus\(\)/);
  });
});

describe('Phase 2 — show/hide on input event', () => {
  it('input listener updates search-clear-btn display based on value', () => {
    assert.match(html, /search-clear-btn[\s\S]{0,200}style\.display\s*=\s*this\.value/);
  });
});

describe('Phase 1 — wrapper and button structure', () => {
  it('search-clear-btn element exists', () => {
    assert.match(html, /id="search-clear-btn"/);
  });

  it('button starts hidden (display:none)', () => {
    assert.match(html, /id="search-clear-btn"[^>]*display:none/);
  });

  it('button is position:absolute (inside the input, no width impact)', () => {
    assert.match(html, /id="search-clear-btn"[^>]*position:absolute/);
  });
});
