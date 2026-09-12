(() => {
  const nav = document.getElementById('dashboard-nav');
  const toggle = document.getElementById('mobile-menu');
  const search = document.getElementById('page-search');
  search?.addEventListener('input', () => {
    let matches = 0;
    for (const group of nav.querySelectorAll('.nav-group')) {
      let visible = 0;
      for (const link of group.querySelectorAll('a')) {
        link.hidden = !link.textContent.toLowerCase().includes(search.value.trim().toLowerCase());
        if (!link.hidden) visible++;
      }
      group.hidden = visible === 0;
      matches += visible;
    }
    document.getElementById('page-search-empty').hidden = matches > 0;
  });
  const index = document.querySelector('.page-index');
  document.querySelectorAll('main .panel > h2').forEach((heading, n) => {
    heading.id ||= `panel-section-${n}`;
    if (index) {
      const link = document.createElement('a');
      link.href = `#${heading.id}`; link.textContent = heading.textContent;
      index.append(link);
    }
  });
  for (const table of document.querySelectorAll('main table')) {
    const wrapper = document.createElement('div'); wrapper.className = 'table-scroll';
    wrapper.tabIndex = 0; wrapper.setAttribute('role','region');
    wrapper.setAttribute('aria-label','Scrollable data table');
    table.before(wrapper); wrapper.append(table);
  }
  for (const link of nav?.querySelectorAll('a[href]') || []) {
    if (new URL(link.href).pathname === location.pathname) {
      link.classList.add('active'); link.setAttribute('aria-current','page');
    }
  }
  if (toggle && nav) toggle.setAttribute('aria-expanded', String(getComputedStyle(nav).display !== 'none' && !document.body.classList.contains('nav-collapsed')));
  const metadata = JSON.parse(document.getElementById('setting-metadata')?.textContent || '{}');
  const forms = [...document.querySelectorAll('main form[method="post"]')];
  const dirtyForms = new Set();
  for (const form of forms) {
    // Move labels within their original form only. Actions, CSRF and all other
    // fields remain untouched; no configuration is kept in browser storage.
    const groups = new Map();
    for (const field of form.querySelectorAll('input:not([type="hidden"]),select,textarea')) {
      let key = field.name;
      if (location.pathname === '/research' && form.querySelector('[name="action"]')?.value === 'settings')
        key = {enabled:'tier8_enabled',queue_limit:'tier8_queue_limit'}[key] || key;
      const spec = metadata[key], label = field.closest('label');
      if (!spec || !label || form.hasAttribute('data-settings-grouped')) continue;
      if (field.type === 'number') { field.min = spec.minimum; field.max = spec.maximum; field.step = '1'; field.required = true; }
      const hint = document.createElement('small'); hint.className = 'setting-hint';
      hint.textContent = `${spec.unit} · ${spec.range}. ${spec.timing}`;
      label.append(hint);
      if (!groups.has(spec.group)) {
        const section = document.createElement('fieldset'); section.className = 'setting-group';
        const legend = document.createElement('legend'); legend.textContent = spec.group;
        const grid = document.createElement('div'); grid.className = 'fields-grid';
        section.append(legend, grid); groups.set(spec.group, {section,grid});
      }
      groups.get(spec.group).grid.append(label);
    }
    if (groups.size) {
      const fragment = document.createDocumentFragment();
      for (const {section} of groups.values()) fragment.append(section);
      form.prepend(fragment);
      form.querySelectorAll('.fields-grid').forEach(grid => { if (!grid.children.length) grid.remove(); });
    }
    if (!form.querySelector('input:not([type="hidden"]),select,textarea')) continue;
    const state = document.createElement('div'); state.className = 'form-state'; state.setAttribute('role','status');
    state.textContent = 'No unsaved changes'; form.prepend(state);
    const baseline = () => JSON.stringify([...new FormData(form)]);
    const original = baseline();
    const update = () => {
      const dirty = baseline() !== original;
      dirty ? dirtyForms.add(form) : dirtyForms.delete(form);
      state.classList.toggle('dirty',dirty); state.textContent = dirty ? 'Unsaved changes' : 'No unsaved changes';
    };
    form.addEventListener('input',update); form.addEventListener('change',update);
    form.addEventListener('submit', event => {
      if (event.defaultPrevented) return;
      dirtyForms.delete(form); state.textContent = 'Saving…';
    });
  }
  window.addEventListener('beforeunload', event => {
    if (dirtyForms.size) { event.preventDefault(); event.returnValue = ''; }
  });
})();
