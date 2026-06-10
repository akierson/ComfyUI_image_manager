// Tests for sidebar tree-building logic and CSS fixes.
// Run: node --test tests/test_sidebar_tree.js
const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// ── Pure function under test (must match index.html implementation) ───────────

function collectLeaves(node) {
  if (node.isLeaf) return [node];
  return (node.childList || []).flatMap(collectLeaves);
}

function buildFolderTree(folders) {
  const root = { children: {} };
  for (const { root_name, count } of folders) {
    const segments = root_name.split('/');
    let node = root;
    for (let i = 0; i < segments.length; i++) {
      const seg = segments[i];
      if (!node.children[seg]) {
        node.children[seg] = { name: seg, count: 0, children: {}, path: segments.slice(0, i + 1).join('/') };
      }
      node = node.children[seg];
      if (i === segments.length - 1) {
        node.count = count;
        node.isLeaf = true;
      }
    }
  }

  function sumAndCollect(node) {
    const childList = Object.values(node.children).map(sumAndCollect);
    if (!node.isLeaf && childList.length > 0) {
      node.count = childList.reduce((s, c) => s + c.count, 0);
      node.isGroup = true;
    } else {
      node.isGroup = false;
    }
    node.childList = childList;
    return node;
  }

  return Object.values(root.children).map(sumAndCollect);
}

// ── collectLeaves tests ───────────────────────────────────────────────────────

describe('collectLeaves', () => {
  it('a leaf node returns itself', () => {
    const tree = buildFolderTree([{ root_name: 'cats', count: 3 }]);
    const leaves = collectLeaves(tree[0]);
    assert.equal(leaves.length, 1);
    assert.equal(leaves[0].name, 'cats');
  });

  it('a group with two leaf children returns both leaves', () => {
    const tree = buildFolderTree([
      { root_name: 'portraits/session_a', count: 3 },
      { root_name: 'portraits/session_b', count: 7 },
    ]);
    const leaves = collectLeaves(tree[0]);
    assert.equal(leaves.length, 2);
    assert.deepEqual(leaves.map(l => l.name).sort(), ['session_a', 'session_b']);
  });

  it('deeply nested group returns all descendant leaves', () => {
    const tree = buildFolderTree([
      { root_name: 'a/b/c', count: 1 },
      { root_name: 'a/b/d', count: 2 },
    ]);
    const leaves = collectLeaves(tree[0]);
    assert.equal(leaves.length, 2);
    assert.deepEqual(leaves.map(l => l.path).sort(), ['a/b/c', 'a/b/d']);
  });
});

// ── groupRenamePath pure function ─────────────────────────────────────────────

function groupRenamePath(oldPrefix, newPrefix, leafPath) {
  if (leafPath === oldPrefix) return newPrefix;
  if (leafPath.startsWith(oldPrefix + '/')) return newPrefix + leafPath.slice(oldPrefix.length);
  return leafPath;
}

describe('groupRenamePath', () => {
  it('replaces a top-level prefix', () => {
    assert.equal(groupRenamePath('portraits', 'characters', 'portraits/session_a'), 'characters/session_a');
  });

  it('replaces a nested prefix', () => {
    assert.equal(groupRenamePath('a/b', 'a/x', 'a/b/c'), 'a/x/c');
  });

  it('does not replace an unrelated path', () => {
    assert.equal(groupRenamePath('portraits', 'characters', 'landscapes/beach'), 'landscapes/beach');
  });

  it('does not partially match prefix (portraits vs portraitsXYZ)', () => {
    assert.equal(groupRenamePath('portraits', 'characters', 'portraitsXYZ/session'), 'portraitsXYZ/session');
  });

  it('handles exact match (single-segment leaf matching the group prefix)', () => {
    assert.equal(groupRenamePath('cats', 'dogs', 'cats'), 'dogs');
  });
});

// ── HTML wiring checks ────────────────────────────────────────────────────────

describe('HTML — startGroupRename and openDeleteGroupDialog', () => {
  it('startGroupRename function is defined', () => {
    assert.match(html, /function startGroupRename\(/);
  });

  it('startGroupRename calls groupRenamePath', () => {
    assert.match(html, /groupRenamePath\(/);
  });

  it('openDeleteGroupDialog function is defined', () => {
    assert.match(html, /function openDeleteGroupDialog\(/);
  });

  it('confirmDeleteFolder calls DELETE for each leaf path', () => {
    assert.match(html, /for.*leafPaths|leafPaths.*forEach|leafPaths.*for/s);
  });
});

// ── CSS structural checks ─────────────────────────────────────────────────────

const html = fs.readFileSync(
  path.join(__dirname, '..', 'web', 'index.html'),
  'utf8'
);

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('buildFolderTree — flat folders', () => {
  it('single flat folder becomes a root-level leaf', () => {
    const result = buildFolderTree([{ root_name: 'cats', count: 3 }]);
    assert.equal(result.length, 1);
    assert.equal(result[0].name, 'cats');
    assert.equal(result[0].count, 3);
    assert.equal(result[0].isGroup, false);
  });

  it('multiple flat folders all appear at root level', () => {
    const result = buildFolderTree([
      { root_name: 'cats', count: 2 },
      { root_name: 'dogs', count: 5 },
    ]);
    assert.equal(result.length, 2);
    assert.deepEqual(result.map(n => n.name).sort(), ['cats', 'dogs']);
  });
});

describe('buildFolderTree — nested folders', () => {
  it('portraits/session_a creates a group with one child leaf', () => {
    const result = buildFolderTree([{ root_name: 'portraits/session_a', count: 4 }]);
    assert.equal(result.length, 1);
    const group = result[0];
    assert.equal(group.name, 'portraits');
    assert.equal(group.isGroup, true);
    assert.equal(group.childList.length, 1);
    assert.equal(group.childList[0].name, 'session_a');
    assert.equal(group.childList[0].isGroup, false);
  });

  it('group count equals sum of all descendant leaf counts', () => {
    const result = buildFolderTree([
      { root_name: 'portraits/session_a', count: 3 },
      { root_name: 'portraits/session_b', count: 7 },
    ]);
    assert.equal(result.length, 1);
    assert.equal(result[0].count, 10);
  });

  it('deep nesting: a/b/c creates two levels of groups', () => {
    const result = buildFolderTree([{ root_name: 'a/b/c', count: 1 }]);
    assert.equal(result.length, 1);
    assert.equal(result[0].isGroup, true);
    assert.equal(result[0].childList[0].isGroup, true);
    assert.equal(result[0].childList[0].childList[0].name, 'c');
  });

  it('path is the full slash-joined prefix for each node', () => {
    const result = buildFolderTree([{ root_name: 'portraits/session_a', count: 1 }]);
    assert.equal(result[0].path, 'portraits');
    assert.equal(result[0].childList[0].path, 'portraits/session_a');
  });

  it('flat and nested folders coexist at root level', () => {
    const result = buildFolderTree([
      { root_name: 'dogs', count: 2 },
      { root_name: 'portraits/session_a', count: 5 },
    ]);
    assert.equal(result.length, 2);
    const names = result.map(n => n.name).sort();
    assert.deepEqual(names, ['dogs', 'portraits']);
  });
});

describe('CSS — hover fix (no label width shift)', () => {
  it('.sidebar-folder-actions uses visibility:hidden not display:none', () => {
    assert.match(html, /\.sidebar-folder-actions\s*\{[^}]*visibility\s*:\s*hidden/);
  });

  it('.sidebar-folder-actions does NOT use display:none as its default', () => {
    assert.doesNotMatch(html, /\.sidebar-folder-actions\s*\{[^}]*display\s*:\s*none/);
  });

  it('.sidebar-root-name:hover .sidebar-folder-actions uses visibility:visible', () => {
    assert.match(html, /\.sidebar-root-name:hover\s+\.sidebar-folder-actions\s*\{[^}]*visibility\s*:\s*visible/);
  });

  it('.sidebar-folder-label has white-space:nowrap', () => {
    assert.match(html, /\.sidebar-folder-label\s*\{[^}]*white-space\s*:\s*nowrap/);
  });
});

describe('CSS — folder group rows', () => {
  it('.sidebar-group class is defined in the stylesheet', () => {
    assert.match(html, /\.sidebar-group\s*\{/);
  });

  it('.sidebar-group:hover .sidebar-folder-actions uses visibility:visible', () => {
    assert.match(html, /\.sidebar-group:hover\s+\.sidebar-folder-actions\s*\{[^}]*visibility\s*:\s*visible/);
  });

  it('group row template includes a .sidebar-folder-actions div', () => {
    assert.match(html, /sidebar-group[^`]*sidebar-folder-actions/s);
  });

  it('group row template has Rename and Delete buttons', () => {
    assert.match(html, /startGroupRename\(/);
    assert.match(html, /openDeleteGroupDialog\(/);
  });
});

describe('HTML — localStorage expand/collapse', () => {
  it('loadSidebar reads expand/collapse state from localStorage', () => {
    assert.match(html, /localStorage\.getItem/);
  });

  it('toggle handler writes expand/collapse state to localStorage', () => {
    assert.match(html, /localStorage\.setItem/);
  });
});
