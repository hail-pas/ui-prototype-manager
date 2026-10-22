import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const context = {};
const source = readFileSync(new URL('../app/static/page-list.js', import.meta.url), 'utf8');
const playerSource = readFileSync(new URL('../app/static/player.js', import.meta.url), 'utf8');
vm.runInNewContext(source, context);
const pageList = context.UIPMPageList;
const pages = [
  {id: 'a', name: '登录页面'},
  {id: 'b', name: '用户列表'},
  {id: 'c', name: '用户详情'},
];

test('page name search returns every fuzzy substring match and clears to all pages', () => {
  assert.deepEqual(
    Array.from(pageList.filterByName(pages, '用户'), (page) => page.id),
    ['b', 'c'],
  );
  assert.deepEqual(
    Array.from(pageList.filterByName(pages, ''), (page) => page.id),
    ['a', 'b', 'c'],
  );
});

test('page order helper moves a page before or after the drop target', () => {
  assert.deepEqual(
    Array.from(pageList.movePage(pages, 'c', 'a', false), (page) => page.id),
    ['c', 'a', 'b'],
  );
  assert.deepEqual(
    Array.from(pageList.movePage(pages, 'a', 'b', true), (page) => page.id),
    ['b', 'a', 'c'],
  );
});

test('blank-point detection excludes configured interaction regions', () => {
  const regions = [{x: 0.1, y: 0.2, width: 0.3, height: 0.4}];
  assert.equal(pageList.pointInsideRegions(regions, 0.2, 0.3), true);
  assert.equal(pageList.pointInsideRegions(regions, 0.8, 0.8), false);
  assert.match(playerSource, /const HOTSPOT_REVEAL_MS = 500;/);
});
