(() => {
  if (document.body.classList.contains('login')) return;
  const records = [];
  let approvedForm = null;
  let pendingSubmit = null;
  let submission = null;
  let submissionTimer = null;
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
  dialog.querySelector('[data-cancel]').addEventListener('click', () => {
    pendingSubmit = null;
    dialog.close();
  });
  dialog.addEventListener('cancel', () => { pendingSubmit = null; });
  dialog.querySelector('[data-continue]').addEventListener('click', () => {
    const pending = pendingSubmit;
    pendingSubmit = null;
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
      const submitting = submission?.form === record.form;
      record.status.hidden = !record.dirty && !submitting;
      record.status.textContent = submitting ? 'Submitting…' : record.dirty ? 'Unsaved changes' : '';
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
    form.addEventListener('reset', () => setTimeout(refresh, 0));
  });
  // Controls linked with form="..." need not be descendants of their form.
  main.addEventListener('input', refresh);
  main.addEventListener('change', refresh);
  const resetSubmission = () => {
    clearTimeout(submissionTimer);
    submission?.form.removeAttribute('aria-busy');
    submission = null;
    refresh();
  };
  // Run after form-level validation and confirmation handlers.
  document.addEventListener('submit', event => {
    if (event.defaultPrevented) return;
    // A later listener may have canceled the previous event before its cleanup
    // timer ran. Such an attempt must not block a new submission.
    if (submission?.event.defaultPrevented) resetSubmission();
    // Preserve the submitter's name/value while blocking rapid duplicate POSTs.
    if (submission) {
      event.preventDefault();
      return;
    }
    refresh();
    const others = records.filter(record => record.dirty && record.form !== event.target);
    if (others.length && approvedForm !== event.target) {
      event.preventDefault();
      pendingSubmit = {form: event.target, submitter: event.submitter};
      if (!dialog.open) dialog.showModal();
      return;
    }
    submission = {form: event.target, event};
    submission.form.setAttribute('aria-busy', 'true');
    refresh();
    // Microtasks can run between browser event listeners. A task checks final
    // cancellation for UI cleanup; beforeunload checks the event directly so it
    // stays correct even if navigation happens before this timer runs.
    setTimeout(() => {
      if (submission?.event !== event) return;
      if (event.defaultPrevented) resetSubmission();
    }, 0);
    // A failed request must not leave the page permanently locked. This also
    // covers action-only forms that have no editable fields to track.
    submissionTimer = setTimeout(resetSubmission, 15000);
  });
  window.addEventListener('beforeunload', event => {
    if (submission && !submission.event.defaultPrevented) return;
    if (records.some(record => snapshot(record.form) !== record.initial)) {
      event.preventDefault();
      event.returnValue = '';
    }
  });
  window.addEventListener('pageshow', resetSubmission);
})();
