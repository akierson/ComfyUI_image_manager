// Tests for "Move to Folder in Right-Click Menu" plan.
// Run: node --test tests/test_move_to_folder_context_menu.js
const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(
  path.join(__dirname, '..', 'web', 'index.html'),
  'utf8'
);

// Extract context menu HTML block (end at next HTML comment after the menu)
const menuStart = html.indexOf('<div id="context-menu"');
const menuEnd = html.indexOf('<!-- Drawer backdrop -->', menuStart);
const menuHtml = html.slice(menuStart, menuEnd);

// Extract _ctxMove function body
const ctxMoveMatch = html.match(/function _ctxMove\(\)\s*\{[\s\S]*?\n\s*\}/);
const ctxMoveFn = ctxMoveMatch ? ctxMoveMatch[0] : '';

describe('Criterion 1 — "Move to folder" item exists in context menu', () => {
  it('context menu contains a "Move to folder" item', () => {
    assert.match(menuHtml, /Move to folder/, 'context menu must have a "Move to folder" item');
  });

  it('"Move to folder" item calls _ctxMove()', () => {
    assert.match(menuHtml, /onclick="_ctxMove\(\)"[^>]*>Move to folder/, '"Move to folder" item must call _ctxMove()');
  });
});

describe('Criterion 1 — "Move to folder" is positioned between "Fork chain" and the separator', () => {
  it('"Fork chain" appears before "Move to folder" in context menu', () => {
    const forkIdx = menuHtml.indexOf('Fork chain');
    const moveIdx = menuHtml.indexOf('Move to folder');
    assert.ok(forkIdx !== -1, '"Fork chain" not found in context menu');
    assert.ok(moveIdx !== -1, '"Move to folder" not found in context menu');
    assert.ok(forkIdx < moveIdx, '"Fork chain" must precede "Move to folder"');
  });

  it('"Move to folder" appears before the ctx-separator', () => {
    const moveIdx = menuHtml.indexOf('Move to folder');
    const sepIdx = menuHtml.indexOf('ctx-separator');
    assert.ok(sepIdx !== -1, 'ctx-separator not found in context menu');
    assert.ok(moveIdx < sepIdx, '"Move to folder" must precede the ctx-separator');
  });
});

describe('Criterion 2 — _ctxMove() implementation', () => {
  it('_ctxMove() function is defined', () => {
    assert.ok(ctxMoveFn.length > 0, '_ctxMove() function not found in HTML');
  });

  it('_ctxMove() calls _hideContextMenu()', () => {
    assert.match(ctxMoveFn, /_hideContextMenu\(\)/, '_ctxMove() must call _hideContextMenu()');
  });

  it('_ctxMove() calls openMoveDialog()', () => {
    assert.match(ctxMoveFn, /openMoveDialog\(\)/, '_ctxMove() must call openMoveDialog()');
  });

  it('_ctxMove() calls _hideContextMenu() before openMoveDialog()', () => {
    const hideIdx = ctxMoveFn.indexOf('_hideContextMenu()');
    const openIdx = ctxMoveFn.indexOf('openMoveDialog()');
    assert.ok(hideIdx < openIdx, '_hideContextMenu() must precede openMoveDialog() in _ctxMove()');
  });
});

describe('Criterion 5 — confirmMove() still uses _selectedUUIDs and move-chains (no regression)', () => {
  const confirmMoveMatch = html.match(/async function confirmMove\(\)\s*\{[\s\S]*?\n\s*\}/);
  const confirmMoveFn = confirmMoveMatch ? confirmMoveMatch[0] : '';

  it('confirmMove() is defined', () => {
    assert.ok(confirmMoveFn.length > 0, 'confirmMove() not found');
  });

  it('confirmMove() reads from _selectedUUIDs', () => {
    assert.match(confirmMoveFn, /_selectedUUIDs/, 'confirmMove() must use _selectedUUIDs');
  });

  it('confirmMove() calls /api/move-chains route', () => {
    assert.match(confirmMoveFn, /move-chains/, 'confirmMove() must use the /api/move-chains route');
  });
});
