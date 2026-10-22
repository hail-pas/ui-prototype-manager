import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

class FakeClassList {
  constructor() {
    this.values = new Set();
  }

  add(...values) {
    values.forEach((value) => this.values.add(value));
  }

  remove(...values) {
    values.forEach((value) => this.values.delete(value));
  }
}

class FakeElement {
  constructor(tagName = 'div') {
    this.tagName = tagName.toUpperCase();
    this.style = {};
    this.classList = new FakeClassList();
    this.children = [];
    this.clientWidth = 0;
    this.clientHeight = 0;
    this.contentWindow = this.tagName === 'IFRAME' ? {} : null;
  }

  appendChild(child) {
    this.children.push(child);
    child.parentElement = this;
    return child;
  }

  replaceChildren(...children) {
    this.children = [];
    children.forEach((child) => this.appendChild(child));
  }

  setAttribute(name, value) {
    this[name] = String(value);
  }
}

function frameFitRuntime() {
  const listeners = new Map();
  class FakeResizeObserver {
    observe() {}
    disconnect() {}
  }
  const browserWindow = {
    ResizeObserver: FakeResizeObserver,
    addEventListener(type, listener) {
      listeners.set(type, listener);
    },
    removeEventListener(type, listener) {
      if (listeners.get(type) === listener) listeners.delete(type);
    },
  };
  const context = {
    window: browserWindow,
    document: {createElement: (tagName) => new FakeElement(tagName)},
    ResizeObserver: FakeResizeObserver,
    getComputedStyle: () => ({paddingLeft: '0', paddingRight: '0', paddingTop: '0', paddingBottom: '0'}),
  };
  const source = readFileSync(new URL('../app/static/frame-fit.js', import.meta.url), 'utf8');
  vm.runInNewContext(source, context);
  return {frameFit: browserWindow.UIPMFrameFit, listeners};
}

test('fixed player keeps overlay layers in the authored viewport while fullscreen height changes', () => {
  const {frameFit} = frameFitRuntime();
  const host = new FakeElement();
  host.clientWidth = 1440;
  host.clientHeight = 800;
  const controller = frameFit.create({
    host,
    pageId: 'fixed-page',
    variant: 'player',
    renderMode: 'fixed',
    viewportWidth: 1920,
    viewportHeight: 1080,
  });
  const overlayLayer = new FakeElement();

  controller.attachViewportLayer(overlayLayer);

  assert.equal(overlayLayer.parentElement, controller.viewport);
  assert.equal(overlayLayer.style.width, '1920px');
  assert.equal(overlayLayer.style.height, '1080px');
  assert.equal(overlayLayer.style.transform, 'scale(0.75)');
  assert.equal(controller.iframe.style.height, `${800 / 0.75}px`);

  host.clientHeight = 900;
  controller.relayout();

  assert.equal(controller.viewport.style.height, '900px');
  assert.equal(controller.iframe.style.height, '1200px');
  assert.equal(overlayLayer.style.height, '1080px');
  assert.equal(overlayLayer.style.transform, 'scale(0.75)');
});

test('responsive player overlay layers continue to fill the live viewport', () => {
  const {frameFit} = frameFitRuntime();
  const host = new FakeElement();
  host.clientWidth = 1280;
  host.clientHeight = 720;
  const controller = frameFit.create({
    host,
    pageId: 'responsive-page',
    variant: 'player',
    renderMode: 'responsive',
  });
  const overlayLayer = new FakeElement();

  controller.attachViewportLayer(overlayLayer);

  assert.equal(overlayLayer.style.inset, '0');
  assert.equal(overlayLayer.style.width, '');
  assert.equal(overlayLayer.style.height, '');
  assert.equal(overlayLayer.style.transform, '');
});

test('auto mode updates an attached overlay layer when a fixed canvas is detected', () => {
  const {frameFit, listeners} = frameFitRuntime();
  const host = new FakeElement();
  host.clientWidth = 1440;
  host.clientHeight = 800;
  const controller = frameFit.create({
    host,
    pageId: 'auto-page',
    variant: 'player',
    renderMode: 'auto',
    viewportWidth: 1920,
    viewportHeight: 1080,
  });
  const overlayLayer = new FakeElement();
  controller.attachViewportLayer(overlayLayer);

  assert.equal(overlayLayer.style.inset, '0');
  listeners.get('message')({
    source: controller.iframe.contentWindow,
    data: {
      type: 'uipm-render-size',
      pageId: 'auto-page',
      viewportWidth: 1440,
      contentWidth: 1920,
      contentHeight: 1080,
    },
  });

  assert.equal(overlayLayer.style.width, '1920px');
  assert.equal(overlayLayer.style.height, '1080px');
  assert.equal(overlayLayer.style.transform, 'scale(0.75)');
});
