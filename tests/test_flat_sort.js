// Tests for _sortImages pure function.
// Run: node --test tests/test_flat_sort.js
const { describe, it } = require('node:test');
const assert = require('node:assert/strict');

// Inline the pure sort function (matches what will be in index.html).
function _sortImages(imgs, sort = 'date-desc') {
  const copy = [...imgs];
  switch (sort) {
    case 'date-asc':  copy.sort((a, b) => a.created_at.localeCompare(b.created_at)); break;
    case 'name-asc':  copy.sort((a, b) => a.filename.localeCompare(b.filename)); break;
    case 'name-desc': copy.sort((a, b) => b.filename.localeCompare(a.filename)); break;
    case 'gen-asc':   copy.sort((a, b) => a.generation - b.generation); break;
    case 'gen-desc':  copy.sort((a, b) => b.generation - a.generation); break;
    default:          copy.sort((a, b) => b.created_at.localeCompare(a.created_at)); break;
  }
  return copy;
}

const imgs = [
  { uuid: 'a', filename: 'cat.png',    created_at: '2026-06-01T10:00:00', generation: 0 },
  { uuid: 'b', filename: 'apple.png',  created_at: '2026-06-03T08:00:00', generation: 2 },
  { uuid: 'c', filename: 'zebra.png',  created_at: '2026-06-02T15:00:00', generation: 1 },
];

describe('_sortImages', () => {
  it('does not mutate the original array', () => {
    const original = [...imgs];
    _sortImages(imgs, 'name-asc');
    assert.deepEqual(imgs, original);
  });

  it('date-desc: newest first', () => {
    const result = _sortImages(imgs, 'date-desc');
    assert.equal(result[0].uuid, 'b');
    assert.equal(result[1].uuid, 'c');
    assert.equal(result[2].uuid, 'a');
  });

  it('date-asc: oldest first', () => {
    const result = _sortImages(imgs, 'date-asc');
    assert.equal(result[0].uuid, 'a');
    assert.equal(result[1].uuid, 'c');
    assert.equal(result[2].uuid, 'b');
  });

  it('name-asc: alphabetical by filename', () => {
    const result = _sortImages(imgs, 'name-asc');
    assert.deepEqual(result.map(i => i.filename), ['apple.png', 'cat.png', 'zebra.png']);
  });

  it('name-desc: reverse alphabetical', () => {
    const result = _sortImages(imgs, 'name-desc');
    assert.deepEqual(result.map(i => i.filename), ['zebra.png', 'cat.png', 'apple.png']);
  });

  it('gen-asc: generation 0 first', () => {
    const result = _sortImages(imgs, 'gen-asc');
    assert.deepEqual(result.map(i => i.generation), [0, 1, 2]);
  });

  it('gen-desc: deepest generation first', () => {
    const result = _sortImages(imgs, 'gen-desc');
    assert.deepEqual(result.map(i => i.generation), [2, 1, 0]);
  });

  it('defaults to date-desc when sort is unknown', () => {
    const result = _sortImages(imgs, 'bogus');
    assert.equal(result[0].uuid, 'b');
  });
});
