// Structural tests for CSS variable refactor (light-theme plan).
// Run: node --test tests/test_light_theme.js
const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(
  path.join(__dirname, '..', 'web', 'index.html'),
  'utf8'
);

// ─── Phase 1: CSS variable palette ───────────────────────────────────────────

describe('Phase 1 — :root CSS variable palette', () => {
  const vars = [
    '--im-bg', '--im-bg-card', '--im-bg-elevated', '--im-bg-subtle', '--im-bg-input',
    '--im-text', '--im-text-muted', '--im-text-dim', '--im-text-faint',
    '--im-border', '--im-border-sub', '--im-border-dim', '--im-border-faint',
    '--im-btn-bg', '--im-btn-text',
    '--im-input-bg', '--im-input-text', '--im-input-border',
  ];

  for (const v of vars) {
    it(`:root defines ${v}`, () => {
      assert.match(html, new RegExp(`:root\\s*\\{[^}]*${v.replace('-', '\\-')}\\s*:`));
    });
  }

  it('body.light-theme overrides --im-bg to light value', () => {
    assert.match(html, /body\.light-theme\s*\{[^}]*--im-bg\s*:/);
  });

  it('body.light-theme overrides --im-btn-bg', () => {
    assert.match(html, /body\.light-theme\s*\{[^}]*--im-btn-bg\s*:/);
  });
});

// ─── Phase 2: toolbar CSS classes ────────────────────────────────────────────

describe('Phase 2 — toolbar CSS classes', () => {
  it('.toolbar-btn class defined using --im-btn-bg', () => {
    assert.match(html, /\.toolbar-btn\s*\{[^}]*var\(--im-btn-bg\)/);
  });

  it('.toolbar-btn class defined using --im-btn-text', () => {
    assert.match(html, /\.toolbar-btn\s*\{[^}]*var\(--im-btn-text\)/);
  });

  it('.toolbar-select class defined using --im-bg-input', () => {
    assert.match(html, /\.toolbar-select\s*\{[^}]*var\(--im-bg-input\)/);
  });

  it('.toolbar-input class defined using --im-bg-input', () => {
    assert.match(html, /\.toolbar-input\s*\{[^}]*var\(--im-bg-input\)/);
  });
});

// ─── Phase 3: toolbar elements stripped of inline color styles ────────────────

describe('Phase 3 — toolbar inline styles replaced with classes', () => {
  const btnIds = ['flat-btn', 'filmstrip-btn', 'cluster-btn', 'order-btn', 'orphan-btn', 'link-btn'];

  for (const id of btnIds) {
    it(`#${id} has class="toolbar-btn"`, () => {
      assert.match(html, new RegExp(`id="${id}"[^>]*class="toolbar-btn"|class="toolbar-btn"[^>]*id="${id}"`));
    });

    it(`#${id} has no inline background: #444`, () => {
      // Button element lines should not have both the id and a hardcoded background color
      const btnRegex = new RegExp(`id="${id}"[^\\n]*style="[^"]*background\\s*:`);
      assert.doesNotMatch(html, btnRegex);
    });
  }

  it('Rebuild Index button has class="toolbar-btn"', () => {
    assert.match(html, /rebuildIndex\(\)[^>]*class="toolbar-btn"|class="toolbar-btn"[^>]*rebuildIndex\(\)/);
  });

  it('#sort-select has class="toolbar-select"', () => {
    assert.match(html, /id="sort-select"[^>]*class="toolbar-select"|class="toolbar-select"[^>]*id="sort-select"/);
  });

  it('#sort-select retains display:none inline style', () => {
    assert.match(html, /id="sort-select"[^>]*style="display:none"/);
  });

  it('#search-input has class="toolbar-input"', () => {
    assert.match(html, /id="search-input"[^>]*class="toolbar-input"|class="toolbar-input"[^>]*id="search-input"/);
  });

  it('#search-input has no inline background style', () => {
    assert.doesNotMatch(html, /id="search-input"[^>]*style="[^"]*background\s*:/);
  });
});

// ─── Phase 4: existing CSS rules converted to variables ───────────────────────

describe('Phase 4 — CSS rules use --im-* variables', () => {
  it('body rule uses --im-bg for background', () => {
    assert.match(html, /body\s*\{[^}]*background\s*:\s*var\(--im-bg\)/);
  });

  it('body rule uses --im-text for color', () => {
    assert.match(html, /body\s*\{[^}]*color\s*:\s*var\(--im-text\)/);
  });

  it('.card uses --im-bg-card for background', () => {
    assert.match(html, /\.card\s*\{[^}]*background\s*:\s*var\(--im-bg-card\)/);
  });

  it('#lb-panel uses --im-bg-card for background', () => {
    assert.match(html, /#lb-panel\s*\{[^}]*background\s*:\s*var\(--im-bg-card\)/);
  });

  it('#sidebar uses --im-bg-elevated for background', () => {
    assert.match(html, /#sidebar\s*\{[^}]*background\s*:\s*var\(--im-bg-elevated\)/);
  });

  it('#context-menu uses --im-bg-elevated for background', () => {
    assert.match(html, /#context-menu\s*\{[^}]*background\s*:\s*var\(--im-bg-elevated\)/);
  });

  it('#toast uses --im-bg-card for background', () => {
    assert.match(html, /#toast\s*\{[^}]*background\s*:\s*var\(--im-bg-card\)/);
  });
});

// ─── Phase 5: old per-element body.light-theme overrides removed ──────────────

describe('Phase 5 — per-element body.light-theme overrides removed', () => {
  it('no body.light-theme .card rule exists', () => {
    assert.doesNotMatch(html, /body\.light-theme\s+\.card\s*\{/);
  });

  it('no body.light-theme #lb-panel rule exists', () => {
    assert.doesNotMatch(html, /body\.light-theme\s+#lb-panel\s*\{/);
  });

  it('no body.light-theme #sidebar rule exists', () => {
    assert.doesNotMatch(html, /body\.light-theme\s+#sidebar\s*\{/);
  });

  it('no body.light-theme .ctx-item rule exists', () => {
    assert.doesNotMatch(html, /body\.light-theme\s+\.ctx-item\s*\{/);
  });
});
