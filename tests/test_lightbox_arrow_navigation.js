// Tests for "Lightbox Arrow Key Navigation" plan.
// Run: node --test tests/test_lightbox_arrow_navigation.js
const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(
  path.join(__dirname, '..', 'web', 'index.html'),
  'utf8'
);

// ── Helpers ───────────────────────────────────────────────────────────────────

function bodyOf(fnName) {
  const start = html.indexOf(`function ${fnName}`);
  if (start === -1) return '';
  // Find the next top-level function declaration after this one
  const nextFn = html.indexOf('\n    function ', start + 1);
  const nextAsync = html.indexOf('\n    async function ', start + 1);
  const candidates = [nextFn, nextAsync].filter(n => n > start);
  const end = candidates.length ? Math.min(...candidates) : start + 5000;
  return html.slice(start, end);
}

const openLightboxBody = bodyOf('openLightbox');
const closeLightboxBody = bodyOf('closeLightbox');
const lbNavigateBody = bodyOf('lbNavigate');
const keydownBlock = (() => {
  const start = html.indexOf("document.addEventListener('keydown'");
  return start === -1 ? '' : html.slice(start, start + 800);
})();

// ── 1. Snapshot nav list on open ──────────────────────────────────────────────

describe('Lightbox arrow navigation — snapshot on open', () => {
  it('openLightbox snapshots [data-uuid] cards into _lbNavList', () => {
    assert.ok(
      openLightboxBody.includes('_lbNavList'),
      'openLightbox must populate _lbNavList'
    );
    assert.ok(
      openLightboxBody.includes('[data-uuid]'),
      'openLightbox must query [data-uuid] elements for _lbNavList'
    );
  });

  it('openLightbox stores the opened card index in _lbNavIdx', () => {
    assert.ok(
      openLightboxBody.includes('_lbNavIdx'),
      'openLightbox must set _lbNavIdx'
    );
  });

  it('_lbNavList and _lbNavIdx are declared as module-level let variables', () => {
    assert.match(html, /let\s+_lbNavList/, '_lbNavList must be declared with let');
    assert.match(html, /let\s+_lbNavIdx/, '_lbNavIdx must be declared with let');
  });
});

// ── 2. ArrowRight — increment and navigate ────────────────────────────────────

describe('Lightbox arrow navigation — ArrowRight', () => {
  it('keydown handler handles ArrowRight key', () => {
    assert.ok(
      keydownBlock.includes("'ArrowRight'"),
      "keydown handler must check for 'ArrowRight'"
    );
  });

  it('ArrowRight clamps at the last index (no wrap-around)', () => {
    // Clamping logic lives in lbNavigate(), which ArrowRight delegates to
    assert.ok(
      lbNavigateBody.includes('_lbNavList.length'),
      'lbNavigate must reference _lbNavList.length for clamping'
    );
  });

  it('ArrowRight re-invokes openLightbox via _cardData', () => {
    assert.ok(
      lbNavigateBody.includes('openLightbox'),
      'lbNavigate must call openLightbox'
    );
    assert.ok(
      lbNavigateBody.includes('_cardData'),
      'lbNavigate must look up _cardData for the new uuid'
    );
  });
});

// ── 3. ArrowLeft — decrement and navigate ─────────────────────────────────────

describe('Lightbox arrow navigation — ArrowLeft', () => {
  it('keydown handler handles ArrowLeft key', () => {
    assert.ok(
      keydownBlock.includes("'ArrowLeft'"),
      "keydown handler must check for 'ArrowLeft'"
    );
  });

  it('ArrowLeft clamps at index 0 (no wrap-around)', () => {
    // Clamping logic lives in lbNavigate(), which ArrowLeft delegates to
    assert.ok(
      lbNavigateBody.match(/Math\.max\s*\(\s*0/) || lbNavigateBody.includes('_lbNavIdx > 0'),
      'lbNavigate must clamp at 0 (Math.max(0,...) or guard check)'
    );
  });
});

// ── 4. Arrow keys guarded by lightbox open state ──────────────────────────────

describe('Lightbox arrow navigation — open guard', () => {
  it('ArrowLeft/Right only fire when lightbox has class "open"', () => {
    // The keydown block must check lightbox open before handling arrow keys
    assert.ok(
      keydownBlock.includes("lightbox") && keydownBlock.includes("'open'"),
      'keydown handler must guard arrow keys with a check for lightbox open state'
    );
  });
});

// ── 5. Chevron buttons in HTML ────────────────────────────────────────────────

describe('Lightbox arrow navigation — chevron buttons', () => {
  it('HTML contains a left chevron button #lb-nav-prev', () => {
    assert.ok(
      html.includes('lb-nav-prev'),
      'lightbox HTML must contain an element with id lb-nav-prev'
    );
  });

  it('HTML contains a right chevron button #lb-nav-next', () => {
    assert.ok(
      html.includes('lb-nav-next'),
      'lightbox HTML must contain an element with id lb-nav-next'
    );
  });

  it('chevron buttons are inside #lightbox', () => {
    const lbStart = html.indexOf('<div id="lightbox"');
    const lbEnd = html.indexOf('<!-- Tree panel -->', lbStart);
    const lbHtml = lbEnd > lbStart ? html.slice(lbStart, lbEnd) : '';
    assert.ok(lbHtml.includes('lb-nav-prev'), '#lb-nav-prev must be inside #lightbox');
    assert.ok(lbHtml.includes('lb-nav-next'), '#lb-nav-next must be inside #lightbox');
  });
});

// ── 6. Button disabled at boundaries ─────────────────────────────────────────

describe('Lightbox arrow navigation — boundary disabled state', () => {
  it('openLightbox sets prev button disabled when at index 0', () => {
    assert.ok(
      openLightboxBody.includes('lb-nav-prev'),
      'openLightbox must update lb-nav-prev disabled state'
    );
    assert.ok(
      openLightboxBody.includes('_lbNavIdx === 0') ||
      openLightboxBody.match(/disabled\s*=\s*_lbNavIdx\s*===\s*0/),
      'openLightbox must disable lb-nav-prev when _lbNavIdx === 0'
    );
  });

  it('openLightbox sets next button disabled when at last index', () => {
    assert.ok(
      openLightboxBody.includes('lb-nav-next'),
      'openLightbox must update lb-nav-next disabled state'
    );
    assert.ok(
      openLightboxBody.includes('_lbNavList.length - 1'),
      'openLightbox must disable lb-nav-next when at last index'
    );
  });
});

// ── 7. Re-opening resets nav list ─────────────────────────────────────────────

describe('Lightbox arrow navigation — reset on re-open', () => {
  it('openLightbox always re-queries cards (not cached from prior open)', () => {
    // Each call to openLightbox must assign _lbNavList from a fresh querySelectorAll
    const assignCount = (openLightboxBody.match(/_lbNavList\s*=/g) || []).length;
    assert.ok(assignCount >= 1, 'openLightbox must assign _lbNavList on every call');
  });

  it('openLightbox always assigns _lbNavIdx based on the current open uuid', () => {
    const assignCount = (openLightboxBody.match(/_lbNavIdx\s*=/g) || []).length;
    assert.ok(assignCount >= 1, 'openLightbox must assign _lbNavIdx on every call');
  });
});
