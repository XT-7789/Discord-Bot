(() => {
  const nav = document.querySelector('.economy-tabs');
  if (!nav) return;
  const tabs = [...nav.querySelectorAll('a')];
  const panels = tabs.map(tab => document.getElementById(tab.hash.slice(1)));
  const storageKey = 'xbot-economy-view';
  const pendingKey = 'xbot-economy-submitted-editor';
  let submitAttempts = [];
  const read = key => { try { return sessionStorage.getItem(key); } catch { return null; } };
  const write = (key, value) => { try { sessionStorage.setItem(key, value); } catch {} };
  const panelFor = id => {
    const target = document.getElementById(id);
    return target?.closest('.economy-tab-panel');
  };
  const activate = (panel, updateHash = true) => {
    if (!panels.includes(panel)) panel = panels[0];
    panels.forEach((item, index) => {
      const selected = item === panel;
      item.hidden = !selected;
      tabs[index].setAttribute('aria-selected', String(selected));
      tabs[index].tabIndex = selected ? 0 : -1;
    });
    write(storageKey, panel.id);
    if (updateHash) history.replaceState(null, '', '#' + panel.id);
  };
  nav.setAttribute('role', 'tablist');
  tabs.forEach((tab, index) => {
    tab.id = panels[index].id + '-tab';
    tab.setAttribute('role', 'tab');
    tab.setAttribute('aria-controls', panels[index].id);
    panels[index].setAttribute('role', 'tabpanel');
    panels[index].setAttribute('aria-labelledby', tab.id);
    panels[index].tabIndex = 0;
    tab.addEventListener('click', event => {
      event.preventDefault();
      activate(panels[index]);
    });
    tab.addEventListener('keydown', event => {
      let target;
      if (event.key === 'ArrowRight') target = (index + 1) % tabs.length;
      if (event.key === 'ArrowLeft') target = (index - 1 + tabs.length) % tabs.length;
      if (event.key === 'Home') target = 0;
      if (event.key === 'End') target = tabs.length - 1;
      if (target === undefined) return;
      event.preventDefault();
      activate(panels[target]);
      tabs[target].focus();
    });
  });
  const submitted = read(pendingKey);
  try { sessionStorage.removeItem(pendingKey); } catch {}
  const initial = panelFor(submitted) || panelFor(location.hash.slice(1)) || panelFor(read(storageKey));
  activate(initial, false);
  const editor = document.getElementById(submitted || location.hash.slice(1));
  if (editor?.matches('details')) editor.open = true;
  window.addEventListener('hashchange', () => {
    const target = document.getElementById(location.hash.slice(1));
    const panel = target?.closest('.economy-tab-panel');
    if (panel) {
      activate(panel, false);
      if (target.matches('details')) target.open = true;
    }
  });
  panels.forEach(panel => panel.querySelectorAll('form').forEach(form => {
    form.addEventListener('submit', event => {
      // Keep the original events: even a microtask can run before a later
      // listener cancels them. A canceled duplicate must not replace the first
      // accepted attempt while that request is navigating.
      submitAttempts = submitAttempts.filter(attempt => !attempt.event.defaultPrevented);
      submitAttempts.push({event, form, panel});
    });
    // Native validation must be able to focus a field in a collapsed editor.
    form.addEventListener('invalid', event => {
      activate(panel);
      let node = event.target.parentElement;
      while (node && node !== panel) {
        if (node.matches('details')) node.open = true;
        node = node.parentElement;
      }
    }, true);
  }));
  window.addEventListener('beforeunload', () => {
    const attempt = [...submitAttempts].reverse().find(item => !item.event.defaultPrevented);
    if (!attempt) return;
    const editor = attempt.form.querySelector('details.record-editor') || attempt.form.closest('details');
    write(pendingKey, editor?.id || attempt.panel.id);
    write(storageKey, attempt.panel.id);
  });
  window.addEventListener('pageshow', () => { submitAttempts = []; });
})();
