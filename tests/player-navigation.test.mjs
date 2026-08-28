import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return {promise, reject, resolve};
}

function navigationRuntime(overrides = {}) {
  const browserWindow = {};
  const context = {
    AbortController,
    Promise,
    window: browserWindow,
  };
  const source = readFileSync(new URL('../app/static/player-navigation.js', import.meta.url), 'utf8');
  vm.runInNewContext(source, context);
  const commits = [];
  const discards = [];
  const errors = [];
  const coordinator = browserWindow.UIPMPlayerNavigation.create({
    commit: (prepared, transition) => commits.push({prepared, transition}),
    discard: (prepared) => discards.push(prepared),
    initialPageId: 'a',
    isValidPage: (pageId) => ['a', 'b', 'c'].includes(pageId),
    onError: (error) => errors.push(error),
    prepare: async (pageId) => ({pageId}),
    ...overrides,
  });
  return {commits, coordinator, discards, errors};
}

test('keeps the committed page and history unchanged until the target is ready', async () => {
  const target = deferred();
  const {commits, coordinator} = navigationRuntime({
    prepare: () => target.promise,
  });

  const navigation = coordinator.navigate('b');

  assert.equal(coordinator.getState().currentPageId, 'a');
  assert.deepEqual(Array.from(coordinator.getState().historyStack), ['a']);
  assert.equal(coordinator.getState().pendingPageId, 'b');
  assert.equal(commits.length, 0);

  target.resolve({pageId: 'b'});
  assert.equal(await navigation, true);
  assert.equal(coordinator.getState().currentPageId, 'b');
  assert.deepEqual(Array.from(coordinator.getState().historyStack), ['a', 'b']);
  assert.equal(coordinator.getState().pendingPageId, null);
  assert.equal(commits.length, 1);
});

test('initial replacement creates history only after the first page is ready', async () => {
  const target = deferred();
  const {coordinator} = navigationRuntime({
    initialPageId: null,
    prepare: () => target.promise,
  });

  const initial = coordinator.replace('b');
  assert.equal(coordinator.getState().currentPageId, null);
  assert.deepEqual(Array.from(coordinator.getState().historyStack), []);

  target.resolve({pageId: 'b'});
  assert.equal(await initial, true);
  assert.equal(coordinator.getState().currentPageId, 'b');
  assert.deepEqual(Array.from(coordinator.getState().historyStack), ['b']);
});

test('leaves the current page, URL state, and history untouched when preparation fails', async () => {
  const failure = new Error('target failed');
  const {commits, coordinator, errors} = navigationRuntime({
    prepare: async () => { throw failure; },
  });

  assert.equal(await coordinator.navigate('b'), false);
  assert.equal(coordinator.getState().currentPageId, 'a');
  assert.deepEqual(Array.from(coordinator.getState().historyStack), ['a']);
  assert.equal(commits.length, 0);
  assert.deepEqual(errors, [failure]);
});

test('only commits the latest target during rapid consecutive navigation', async () => {
  const targets = {b: deferred(), c: deferred()};
  const {commits, coordinator, discards} = navigationRuntime({
    prepare: (pageId) => targets[pageId].promise,
  });

  const first = coordinator.navigate('b');
  const second = coordinator.navigate('c');
  targets.c.resolve({pageId: 'c'});

  assert.equal(await second, true);
  assert.equal(coordinator.getState().currentPageId, 'c');
  assert.deepEqual(Array.from(coordinator.getState().historyStack), ['a', 'c']);

  targets.b.resolve({pageId: 'b'});
  assert.equal(await first, false);
  assert.deepEqual(commits.map((item) => item.prepared.pageId), ['c']);
  assert.deepEqual(discards.map((item) => item.pageId), ['b']);
});

test('pops history only after the previous page has been prepared and committed', async () => {
  const backTarget = deferred();
  const {coordinator} = navigationRuntime({
    prepare: (pageId) => pageId === 'a' ? backTarget.promise : Promise.resolve({pageId}),
  });
  await coordinator.navigate('b');

  const back = coordinator.goBack();
  assert.equal(coordinator.getState().currentPageId, 'b');
  assert.deepEqual(Array.from(coordinator.getState().historyStack), ['a', 'b']);
  assert.equal(coordinator.getState().pendingPageId, 'a');

  backTarget.resolve({pageId: 'a'});
  assert.equal(await back, true);
  assert.equal(coordinator.getState().currentPageId, 'a');
  assert.deepEqual(Array.from(coordinator.getState().historyStack), ['a']);
});

test('back cancels an unfinished forward navigation before changing history', async () => {
  const target = deferred();
  const {coordinator} = navigationRuntime({prepare: () => target.promise});

  const forward = coordinator.navigate('b');
  assert.equal(await coordinator.goBack(), false);
  assert.equal(coordinator.getState().currentPageId, 'a');
  assert.deepEqual(Array.from(coordinator.getState().historyStack), ['a']);
  assert.equal(coordinator.getState().pendingPageId, null);

  target.resolve({pageId: 'b'});
  assert.equal(await forward, false);
});
