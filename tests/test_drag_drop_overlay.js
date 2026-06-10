// Tests for Context-Aware Drag-Drop Import Overlay.
// Run: node --test tests/test_drag_drop_overlay.js
const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(
  path.join(__dirname, '..', 'web', 'index.html'),
  'utf8'
);

// ─── Phase 1: Remove static drop zone ────────────────────────────────────────

describe('Phase 1 — static drop zone removed', () => {
  it('no #drop-zone element in HTML', () => {
    assert.doesNotMatch(html, /id="drop-zone"/);
  });

  it('no getElementById("drop-zone") reference in JS', () => {
    assert.doesNotMatch(html, /getElementById\(['"]drop-zone['"]\)/);
  });
});

// ─── Phase 2: Overlay HTML structure ─────────────────────────────────────────

describe('Phase 2 — overlay HTML and CSS', () => {
  it('#drop-overlay element exists', () => {
    assert.match(html, /id="drop-overlay"/);
  });

  it('#drop-overlay-inner element exists', () => {
    assert.match(html, /id="drop-overlay-inner"/);
  });

  it('#drop-overlay-text element exists', () => {
    assert.match(html, /id="drop-overlay-text"/);
  });

  it('#drop-overlay is positioned before #page-body', () => {
    const overlayIdx = html.indexOf('id="drop-overlay"');
    const pageBodyIdx = html.indexOf('id="page-body"');
    assert.ok(overlayIdx < pageBodyIdx, 'overlay should appear before page-body in HTML');
  });

  it('#drop-overlay defaults to display: none', () => {
    assert.match(html, /#drop-overlay\s*\{[^}]*display\s*:\s*none/);
  });

  it('#drop-overlay.active shows as display: flex', () => {
    assert.match(html, /#drop-overlay\.active\s*\{[^}]*display\s*:\s*flex/);
  });

  it('#drop-overlay has position: fixed', () => {
    assert.match(html, /#drop-overlay\s*\{[^}]*position\s*:\s*fixed/);
  });

  it('#drop-overlay has inset: 0', () => {
    assert.match(html, /#drop-overlay\s*\{[^}]*inset\s*:\s*0/);
  });

  it('#drop-overlay-inner has pointer-events: none', () => {
    assert.match(html, /#drop-overlay-inner\s*\{[^}]*pointer-events\s*:\s*none/);
  });

  it('body.light-theme #drop-overlay-inner rule exists', () => {
    assert.match(html, /body\.light-theme\s+#drop-overlay-inner\s*\{/);
  });
});

// ─── Phase 3: dragEnterCount counter logic ────────────────────────────────────
// Inlined pure version of the counter transitions from index.html.

function isFileDrag(types) {
  return Array.isArray(types) && types.includes('Files');
}

function applyDragEnter(count, types) {
  if (!isFileDrag(types)) return count;
  return count + 1;
}

function applyDragLeave(count, types) {
  if (!isFileDrag(types)) return count;
  const next = count - 1;
  return next <= 0 ? 0 : next;
}

function applyDrop(count, types) {
  if (!isFileDrag(types)) return count;
  return 0;
}

describe('Phase 3 — dragEnterCount counter logic', () => {
  it('dragenter with Files increments counter', () => {
    assert.equal(applyDragEnter(0, ['Files']), 1);
    assert.equal(applyDragEnter(2, ['Files']), 3);
  });

  it('dragleave with Files decrements counter', () => {
    assert.equal(applyDragLeave(3, ['Files']), 2);
    assert.equal(applyDragLeave(1, ['Files']), 0);
  });

  it('dragleave never goes below 0', () => {
    assert.equal(applyDragLeave(0, ['Files']), 0);
    assert.equal(applyDragLeave(-1, ['Files']), 0);
  });

  it('drop resets counter to 0', () => {
    assert.equal(applyDrop(5, ['Files']), 0);
    assert.equal(applyDrop(1, ['Files']), 0);
  });

  it('dragenter without Files type is ignored', () => {
    assert.equal(applyDragEnter(0, ['text/plain']), 0);
    assert.equal(applyDragEnter(0, []), 0);
    assert.equal(applyDragEnter(2, null), 2);
  });

  it('dragleave without Files type is ignored', () => {
    assert.equal(applyDragLeave(3, ['text/uri-list']), 3);
    assert.equal(applyDragLeave(1, null), 1);
  });

  it('drop without Files type is ignored', () => {
    assert.equal(applyDrop(2, ['text/plain']), 2);
  });
});

// ─── Phase 3: JS structure in index.html ─────────────────────────────────────

describe('Phase 3 — document-level drag listeners wired in HTML', () => {
  it('document.addEventListener("dragenter") exists', () => {
    assert.match(html, /document\.addEventListener\(['"]dragenter['"]/);
  });

  it('document.addEventListener("dragleave") exists', () => {
    assert.match(html, /document\.addEventListener\(['"]dragleave['"]/);
  });

  it('document.addEventListener("dragover") exists', () => {
    assert.match(html, /document\.addEventListener\(['"]dragover['"]/);
  });

  it('document.addEventListener("drop") exists', () => {
    assert.match(html, /document\.addEventListener\(['"]drop['"]/);
  });

  it('_dragEnterCount variable declared', () => {
    assert.match(html, /let\s+_dragEnterCount\s*=/);
  });

  it('Files type filter guards dragenter', () => {
    assert.match(html, /types\.includes\(['"]Files['"]\)/);
  });
});
