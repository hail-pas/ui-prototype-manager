(function (global) {
  'use strict';

  function normalize(value) {
    return String(value || '').trim().toLocaleLowerCase();
  }

  function filterByName(pages, query) {
    const needle = normalize(query);
    if (!needle) return Array.from(pages || []);
    return Array.from(pages || []).filter((page) => normalize(page.name).includes(needle));
  }

  function movePage(pages, sourceId, targetId, placeAfter) {
    const items = Array.from(pages || []);
    const sourceIndex = items.findIndex((page) => page.id === sourceId);
    const targetIndex = items.findIndex((page) => page.id === targetId);
    if (sourceIndex < 0 || targetIndex < 0 || sourceIndex === targetIndex) return items;
    const [source] = items.splice(sourceIndex, 1);
    const adjustedTarget = items.findIndex((page) => page.id === targetId);
    items.splice(adjustedTarget + (placeAfter ? 1 : 0), 0, source);
    return items;
  }

  function pointInsideRegions(regions, x, y) {
    return Array.from(regions || []).some((region) => (
      x >= region.x && x <= region.x + region.width
      && y >= region.y && y <= region.y + region.height
    ));
  }

  global.UIPMPageList = {filterByName, movePage, normalize, pointInsideRegions};
})(typeof window === 'undefined' ? globalThis : window);
