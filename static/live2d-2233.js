'use strict';

(function () {
  const models = {
    22: '/static/vendor/live2d-2233-model/22-default.json',
    33: '/static/vendor/live2d-2233-model/33-default.json'
  };
  let lastState = '';

  function $(id) { return document.getElementById(id); }

  function showTip(person, message, duration) {
    const tip = $('live2dTip' + person);
    if (!tip) return;
    tip.textContent = message;
    tip.classList.add('show');
    clearTimeout(showTip.timers[person]);
    showTip.timers[person] = setTimeout(() => tip.classList.remove('show'), duration || 3600);
  }
  showTip.timers = {};

  function showBoth(leftMessage, rightMessage, duration) {
    showTip('22', leftMessage, duration);
    showTip('33', rightMessage || leftMessage, duration);
  }

  function loadModel(person) {
    if (typeof window.loadlive2d !== 'function') {
      showBoth('Live2D runtime 加载中', 'Live2D runtime 加载中');
      return;
    }
    window.loadlive2d('live2dCanvas' + person, models[person]);
  }

  function setState(state, line, pct) {
    ['22', '33'].forEach(person => {
      const panel = $('live2dPanel' + person);
      if (!panel) return;
      panel.classList.remove('live2d-empty', 'live2d-running', 'live2d-done', 'live2d-error');
      panel.classList.add('live2d-' + state);
    });
    if (state === lastState) return;
    lastState = state;
    if (state === 'running') {
      const progress = Math.max(0, pct || 0) + '%';
      showBoth('我来盯下载进度 ' + progress, '翻译和上传我会看着', 4200);
    } else if (state === 'done') {
      showBoth('完成啦', '任务队列已同步', 4200);
    } else if (state === 'error') {
      showBoth('有任务出错了', '日志里有线索', 5200);
    } else if (line) {
      showBoth('等你投喂链接', '队列空闲中', 3200);
    }
  }

  window.y2bLive2D = { loadModel, setState, showTip: showBoth };

  document.addEventListener('DOMContentLoaded', () => {
    ['22', '33'].forEach(person => {
      loadModel(person);
      const canvas = $('live2dCanvas' + person);
      if (canvas) {
        canvas.addEventListener('click', () => showTip(person, '我在看任务队列呢'));
      }
    });
    setTimeout(() => showBoth('已就位', '已就位', 3800), 600);
  });
})();
