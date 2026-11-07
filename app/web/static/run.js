// Обновляет страницу работающего запуска без перезагрузки: прогресс и таблица результатов.
// Без JavaScript работает ссылка «Обновить страницу». Только запросы к своему серверу.
(function () {
  'use strict';
  var root = document.getElementById('run');
  if (!root || root.dataset.status !== 'RUNNING') return;
  var runId = root.dataset.runId;
  var statusUrl = '/runs/' + encodeURIComponent(runId) + '/status';
  var pageUrl = '/runs/' + encodeURIComponent(runId);
  var INTERVAL_MS = 1500;
  var MAX_ERRORS = 5;
  var last = null;
  var errors = 0;

  function copyText(from, to) {
    if (from && to) to.textContent = from.textContent;
  }

  function refreshContent() {
    return fetch(pageUrl, { headers: { Accept: 'text/html' } })
      .then(function (response) { return response.text(); })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, 'text/html');
        copyText(doc.getElementById('progress-text'), document.getElementById('progress-text'));
        var progress = document.getElementById('progress');
        var fresh = doc.getElementById('progress');
        if (progress && fresh) {
          progress.max = fresh.max;
          progress.value = fresh.value;
        }
        var results = document.getElementById('results');
        var freshResults = doc.getElementById('results');
        if (results && freshResults) results.replaceWith(freshResults);
        else if (!results && freshResults) root.appendChild(freshResults);
      });
  }

  function tick() {
    fetch(statusUrl, { headers: { Accept: 'application/json' } })
      .then(function (response) {
        if (!response.ok) throw new Error('status ' + response.status);
        return response.json();
      })
      .then(function (state) {
        errors = 0;
        if (state.status !== 'RUNNING') {
          window.location.reload();  // итоговое состояние рисует сервер
          return null;
        }
        var key = [state.done, state.total, state.stop_requested].join(':');
        var update = key !== last ? refreshContent() : Promise.resolve();
        last = key;
        return update.then(function () { window.setTimeout(tick, INTERVAL_MS); });
      })
      .catch(function () {
        errors += 1;
        if (errors < MAX_ERRORS) window.setTimeout(tick, INTERVAL_MS * 2);
      });
  }

  window.setTimeout(tick, INTERVAL_MS);
})();
