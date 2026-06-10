// Tests for "Copy Button for Prompts in Lightbox" plan.
// Run: node --test tests/test_lightbox_prompt_copy.js
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
  const nextFn = html.indexOf('\n    function ', start + 1);
  const nextAsync = html.indexOf('\n    async function ', start + 1);
  const candidates = [nextFn, nextAsync].filter(n => n > start);
  const end = candidates.length ? Math.min(...candidates) : start + 5000;
  return html.slice(start, end);
}

const renderBody = bodyOf('renderMetadataPanel');

// ── 1. lb-prompt-header structure ────────────────────────────────────────────

describe('Lightbox prompt copy — lb-prompt-header structure', () => {
  it('renderMetadataPanel uses lb-prompt-header div for positive prompt block', () => {
    assert.ok(
      renderBody.includes('lb-prompt-header'),
      'renderMetadataPanel must use lb-prompt-header for prompt blocks'
    );
  });

  it('positive prompt block has a copy button with data-copy attribute', () => {
    const posIdx = renderBody.indexOf('positive_prompt');
    assert.ok(posIdx !== -1, 'renderMetadataPanel must handle positive_prompt');
    const posBlock = renderBody.slice(posIdx, posIdx + 500);
    assert.ok(
      posBlock.includes('data-copy'),
      'positive prompt block must include a data-copy button'
    );
  });

  it('negative prompt block has a copy button with data-copy attribute', () => {
    const negIdx = renderBody.indexOf('negative_prompt');
    assert.ok(negIdx !== -1, 'renderMetadataPanel must handle negative_prompt');
    const negBlock = renderBody.slice(negIdx, negIdx + 500);
    assert.ok(
      negBlock.includes('data-copy'),
      'negative prompt block must include a data-copy button'
    );
  });
});

// ── 2. LoRA block has no copy button ─────────────────────────────────────────

describe('Lightbox prompt copy — LoRA block excluded', () => {
  it('LoRA block does not include a data-copy button', () => {
    const loraIdx = renderBody.indexOf('lb-loras');
    assert.ok(loraIdx !== -1, 'renderMetadataPanel must have a loras block');
    // Find the lora block template: from `if (meta.loras` to the closing backtick/paren
    const loraStart = renderBody.indexOf('meta.loras');
    const loraBlock = renderBody.slice(loraStart, loraStart + 400);
    assert.ok(
      !loraBlock.includes('data-copy'),
      'LoRA block must NOT include a data-copy button'
    );
  });
});

// ── 3. Click handler calls clipboard.writeText and showToast ─────────────────

describe('Lightbox prompt copy — click handler', () => {
  it('a click handler calls navigator.clipboard.writeText', () => {
    assert.ok(
      html.includes('navigator.clipboard.writeText'),
      'page must call navigator.clipboard.writeText for copy action'
    );
  });

  it("click handler calls showToast('Copied!', false) after writing", () => {
    // Find the copy handler: the clipboard.writeText call should be followed by showToast
    const cpIdx = html.indexOf('navigator.clipboard.writeText');
    assert.ok(cpIdx !== -1, 'clipboard.writeText must be present');
    const block = html.slice(cpIdx, cpIdx + 300);
    assert.ok(
      block.includes("showToast('Copied!'") || block.includes('showToast("Copied!"'),
      "copy handler must call showToast('Copied!', false)"
    );
  });

  it('click handler is wired to lb-copy-btn or data-copy elements', () => {
    const cpIdx = html.indexOf('navigator.clipboard.writeText');
    // Walk back up to 600 chars to find the event listener setup
    const context = html.slice(Math.max(0, cpIdx - 600), cpIdx + 300);
    assert.ok(
      context.includes('data-copy') || context.includes('lb-copy-btn'),
      'click handler must reference data-copy or lb-copy-btn to identify the button'
    );
  });
});

// ── 4. Escaping — data-copy uses escHtml ─────────────────────────────────────

describe('Lightbox prompt copy — escaping', () => {
  it('data-copy attribute value is passed through escHtml', () => {
    // The template literal for the copy button must use escHtml on the prompt text
    const posIdx = renderBody.indexOf('positive_prompt');
    const posBlock = renderBody.slice(posIdx, posIdx + 500);
    assert.ok(
      posBlock.includes('escHtml'),
      'data-copy value for positive prompt must be escaped with escHtml'
    );
  });
});

// ── 5. CSS rule for lb-prompt-header ─────────────────────────────────────────

describe('Lightbox prompt copy — CSS', () => {
  it('.lb-prompt-header CSS rule exists', () => {
    assert.ok(
      html.includes('.lb-prompt-header'),
      'CSS must define .lb-prompt-header'
    );
  });

  it('.lb-prompt-header uses display:flex', () => {
    const cssIdx = html.indexOf('.lb-prompt-header');
    assert.ok(cssIdx !== -1);
    const cssBlock = html.slice(cssIdx, cssIdx + 200);
    assert.ok(
      cssBlock.includes('flex'),
      '.lb-prompt-header must use display:flex'
    );
  });

  it('.lb-prompt-header uses justify-content:space-between', () => {
    const cssIdx = html.indexOf('.lb-prompt-header');
    const cssBlock = html.slice(cssIdx, cssIdx + 200);
    assert.ok(
      cssBlock.includes('space-between'),
      '.lb-prompt-header must use justify-content:space-between'
    );
  });
});
