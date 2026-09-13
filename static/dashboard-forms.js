(() => {
  if (document.body.classList.contains('login')) return;
  const records = [];
  let leaving = false;
  let approvedForm = null;
  let pendingSubmit = null;
  const editable = form => [...form.elements].filter(field =>
    field.name && !field.disabled && field.matches('input, select, textarea') &&
    !['hidden', 'submit', 'button', 'reset'].includes(field.type));
  // Keep snapshots in memory only, including changes inside hidden tabs.
  const snapshot = form => JSON.stringify(editable(form).map(field => [field.name,
    field.type === 'checkbox' || field.type === 'radio' ? field.checked :
    field.multiple ? [...field.selectedOptions].map(option => option.value) : field.value]));
  const banner = document.createElement('div');
  banner.className = 'unsaved-banner';
  banner.setAttribute('role', 'status');
  banner.hidden = true;
  const main = document.getElementById('main-content');
  if (!main) return;
  // Let native validation reveal and focus required fields in any disclosure.
  main.addEventListener('invalid', event => {
    let parent = event.target.parentElement;
    while (parent && parent !== main) {
      if (parent.matches('details')) parent.open = true;
      parent = parent.parentElement;
    }
  }, true);
  main.querySelector('.page-heading')?.after(banner);
  const dialog = document.createElement('dialog');
  dialog.className = 'save-confirm-dialog';
  dialog.setAttribute('aria-labelledby', 'save-confirm-title');
  dialog.setAttribute('aria-describedby', 'save-confirm-description');
  dialog.innerHTML = '<h2 id="save-confirm-title">Other edits are not saved</h2>' +
    '<p id="save-confirm-description">Submitting this form reloads the page. Changes in other forms will be discarded. Cancel to review them, or submit only this form.</p>' +
    '<div class="save-confirm-actions"><button type="button" class="btn secondary" data-cancel autofocus>Cancel</button>' +
    '<button type="button" class="btn danger" data-continue>Submit and discard other edits</button></div>';
  document.body.append(dialog);
  dialog.querySelector('[data-cancel]').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => { pendingSubmit = null; });
  dialog.querySelector('[data-continue]').addEventListener('click', () => {
    const pending = pendingSubmit;
    dialog.close();
    if (!pending) return;
    approvedForm = pending.form;
    try {
      pending.form.requestSubmit(pending.submitter || undefined);
    } finally {
      approvedForm = null;
    }
  });
  const refresh = () => {
    records.forEach(record => {
      record.dirty = snapshot(record.form) !== record.initial;
      record.form.classList.toggle('form-dirty', record.dirty);
      record.status.hidden = !record.dirty;
      record.status.textContent = record.dirty ? 'Unsaved changes' : '';
    });
    const count = records.filter(record => record.dirty).length;
    banner.hidden = !count;
    banner.textContent = `${count} form${count === 1 ? ' has' : 's have'} unsaved changes. Save one form at a time; submitting reloads this page.`;
  };
  main.querySelectorAll('form').forEach(form => {
    if (form.method.toLowerCase() !== 'post' || !editable(form).length) return;
    const button = [...form.elements].find(field => field.type === 'submit');
    if (!button) return;
    const status = document.createElement('span');
    status.className = 'form-save-state';
    status.setAttribute('role', 'status');
    status.hidden = true;
    button.before(status);
    records.push({form, status, initial: snapshot(form), dirty: false});
    form.addEventListener('input', refresh);
    form.addEventListener('change', refresh);
    form.addEventListener('reset', () => setTimeout(refresh, 0));
  });
  // Run after form-level validation and confirmation handlers.
  document.addEventListener('submit', event => {
    if (event.defaultPrevented) return;
    refresh();
    const others = records.filter(record => record.dirty && record.form !== event.target);
    if (others.length && approvedForm !== event.target) {
      event.preventDefault();
      pendingSubmit = {form: event.target, submitter: event.submitter};
      dialog.showModal();
      return;
    }
    leaving = true;
    const record = records.find(record => record.form === event.target);
    if (record) {
      record.status.hidden = false;
      record.status.textContent = 'Submitting…';
      // Do not disable the submitter: its name/value may select the server action.
      setTimeout(() => { leaving = false; refresh(); }, 15000);
    }
  });
  window.addEventListener('beforeunload', event => {
    if (leaving) return;
    if (records.some(record => snapshot(record.form) !== record.initial)) {
      event.preventDefault();
      event.returnValue = '';
    }
  });
  window.addEventListener('pageshow', () => { leaving = false; refresh(); });
})();
