'use strict';

// Dependency-free behavioral tests for the dashboard's browser scripts. The
// harness deliberately drains microtasks after EACH event listener, matching a
// browser dispatch rather than relying on Node's dispatchEvent timing.
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function fixture({login = false, stored = {}, storageBlocked = false} = {}) {
  const microtasks = [];
  const timers = new Map();
  let timerId = 0;
  class Element {
    constructor(tag, attrs = {}) {
      this.tagName = tag;
      this.children = [];
      this.listeners = [];
      this.attributes = {};
      this.className = '';
      this.hidden = false;
      this.open = false;
      this.disabled = false;
      this.name = '';
      this.value = '';
      this.type = tag === 'button' ? 'submit' : 'text';
      Object.assign(this, attrs);
      this.classList = {
        contains: name => this.className.split(/\s+/).includes(name),
        toggle: (name, enabled) => {
          const names = new Set(this.className.split(/\s+/).filter(Boolean));
          if (enabled) names.add(name); else names.delete(name);
          this.className = [...names].join(' ');
        },
      };
    }
    append(...children) {
      children.forEach(child => { child.parentElement = this; this.children.push(child); });
    }
    before(child) { this.insertSibling(child, 0); }
    after(child) { this.insertSibling(child, 1); }
    insertSibling(child, offset) {
      child.parentElement = this.parentElement;
      this.parentElement.children.splice(this.parentElement.children.indexOf(this) + offset, 0, child);
    }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name] ?? null; }
    removeAttribute(name) { delete this.attributes[name]; }
    addEventListener(type, handler, capture = false) { this.listeners.push({type, handler, capture}); }
    matches(selector) {
      return selector.split(',').some(part => {
        part = part.trim();
        if (part.startsWith('[')) return Object.hasOwn(this.attributes, part.slice(1, -1));
        const [tag, ...classes] = part.split('.');
        return (!tag || this.tagName === tag) && classes.every(name => this.classList.contains(name));
      });
    }
    querySelectorAll(selector) {
      return this.children.flatMap(child => [
        ...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector),
      ]);
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) || null; }
    get elements() { return this.querySelectorAll('input, select, textarea, button'); }
    set innerHTML(value) {
      assert.equal(this.tagName, 'dialog');
      this.markup = value;
      ['cancel', 'continue'].forEach(action => {
        const button = new Element('button', {type: 'button'});
        button.setAttribute('data-' + action, '');
        this.append(button);
      });
    }
    showModal() { this.open = true; }
    close() { this.open = false; dispatch(this, 'close'); }
    focus() { document.activeElement = this; }
    requestSubmit(submitter) { this.lastSubmission = dispatch(this, 'submit', {submitter}); }
  }
  const window = new Element('window');
  const document = new Element('document');
  window.append(document);
  document.body = new Element('body', {className: login ? 'login' : ''});
  document.append(document.body);
  document.createElement = tag => new Element(tag);
  document.getElementById = id => document.querySelectorAll('*').find(node => node.id === id) || null;
  // Universal traversal is intentionally separate from the small selector subset.
  const matches = Element.prototype.matches;
  Element.prototype.matches = function(selector) { return selector === '*' || matches.call(this, selector); };
  const main = new Element('main', {id: 'main-content'});
  const heading = new Element('h1', {className: 'page-heading'});
  const nav = new Element('nav', {className: 'economy-tabs'});
  main.append(heading, nav);
  document.body.append(main);
  const panels = ['rules', 'stocks'].map(name => new Element('section', {
    id: 'economy-' + name, className: 'economy-tab-panel',
  }));
  const tabs = panels.map(panel => new Element('a', {hash: '#' + panel.id}));
  nav.append(...tabs);
  main.append(...panels);
  const forms = panels.map((panel, index) => {
    const form = new Element('form', {method: 'post'});
    const editor = new Element('details', {id: 'editor-' + index, className: 'record-editor'});
    const input = new Element('input', {name: 'name', value: 'original'});
    const button = new Element('button', {name: 'action', value: 'save'});
    editor.append(input, button);
    form.append(editor);
    panel.append(form);
    return {form, editor, input, button};
  });
  const store = new Map(Object.entries(stored));
  const sessionStorage = {
    getItem(key) { if (storageBlocked) throw Error('blocked'); return store.get(key) ?? null; },
    setItem(key, value) { if (storageBlocked) throw Error('blocked'); store.set(key, String(value)); },
    removeItem(key) { if (storageBlocked) throw Error('blocked'); store.delete(key); },
  };
  const location = {hash: ''};
  const context = vm.createContext({
    document, window, sessionStorage, location,
    history: {replaceState(_state, _title, hash) { location.hash = hash; }},
    queueMicrotask: callback => microtasks.push(callback),
    setTimeout(callback, delay) { const id = ++timerId; timers.set(id, {callback, delay}); return id; },
    clearTimeout: id => timers.delete(id),
  });
  function dispatch(target, type, properties = {}) {
    const event = {target, type, defaultPrevented: false, ...properties,
      preventDefault() { this.defaultPrevented = true; }};
    const route = [];
    for (let node = target; node; node = node.parentElement) route.push(node);
    const invoke = (node, capture) => node.listeners.filter(item => item.type === type && !!item.capture === capture)
      .forEach(item => {
        item.handler(event);
        while (microtasks.length) microtasks.shift()();
      });
    [...route].reverse().forEach(node => invoke(node, true));
    route.forEach(node => invoke(node, false));
    return event;
  }
  function runTimers(maxDelay = 0) {
    for (const [id, timer] of [...timers]) {
      if (timer.delay <= maxDelay && timers.delete(id)) timer.callback();
    }
  }
  function load(name) { vm.runInContext(readFileSync(path.join(__dirname, 'static', name), 'utf8'), context); }
  load('economy-tabs.js');
  load('dashboard-forms.js');
  return {document, window, main, nav, tabs, panels, forms, store, dispatch, runTimers,
    Element, dialog: document.querySelector('dialog'),
    edit(index, value = 'changed') { forms[index].input.value = value; dispatch(forms[index].form, 'input'); },
    submit(index = 0) { return dispatch(forms[index].form, 'submit', {submitter: forms[index].button}); },
    unload() { return dispatch(window, 'beforeunload'); },
  };
}

const pendingKey = 'xbot-economy-submitted-editor';

test('dirty changes and reverting are tracked across hidden panels', () => {
  const ui = fixture();
  ui.edit(1);
  assert.equal(ui.panels[1].hidden, true);
  assert.equal(ui.main.querySelector('.unsaved-banner').hidden, false);
  assert.equal(ui.forms[1].form.querySelector('.form-save-state').textContent, 'Unsaved changes');
  assert.equal(ui.unload().defaultPrevented, true);
  ui.edit(1, 'original');
  assert.equal(ui.main.querySelector('.unsaved-banner').hidden, true);
  assert.equal(ui.unload().defaultPrevented, false);
});

test('accepted submit keeps submitter values, blocks duplicates, and restores its editor', () => {
  const ui = fixture();
  ui.edit(0);
  const first = ui.submit();
  assert.equal(first.defaultPrevented, false);
  assert.equal(first.submitter, ui.forms[0].button);
  assert.equal(first.submitter.disabled, false);
  assert.equal(first.submitter.name, 'action');
  assert.equal(first.submitter.value, 'save');
  assert.equal(ui.forms[0].form.getAttribute('aria-busy'), 'true');
  assert.equal(ui.forms[0].form.querySelector('.form-save-state').textContent, 'Submitting…');
  assert.equal(ui.submit().defaultPrevented, true);
  assert.equal(ui.unload().defaultPrevented, false);
  assert.equal(ui.store.get(pendingKey), 'editor-0');
});

test('form-associated controls outside the form still update dirty state', () => {
  const ui = fixture();
  const {form, editor, input, button} = ui.forms[0];
  editor.children = editor.children.filter(child => child !== input);
  ui.main.append(input);
  Object.defineProperty(form, 'elements', {get: () => [input, button]});
  input.value = 'external edit';
  ui.dispatch(input, 'input');
  assert.equal(ui.main.querySelector('.unsaved-banner').hidden, false);
  assert.equal(ui.unload().defaultPrevented, true);
  input.value = 'original';
  ui.dispatch(input, 'change');
  assert.equal(ui.main.querySelector('.unsaved-banner').hidden, true);
});

test('later window cancellation protects edits even before cleanup timers', () => {
  const ui = fixture();
  ui.edit(0);
  ui.window.addEventListener('submit', event => event.preventDefault());
  assert.equal(ui.submit().defaultPrevented, true);
  assert.equal(ui.unload().defaultPrevented, true);
  assert.equal(ui.store.has(pendingKey), false);
  ui.runTimers();
  assert.equal(ui.forms[0].form.getAttribute('aria-busy'), null);
  assert.equal(ui.forms[0].form.querySelector('.form-save-state').textContent, 'Unsaved changes');
});

test('a canceled attempt cannot lock the next synchronous submission', () => {
  const ui = fixture();
  let cancel = true;
  ui.window.addEventListener('submit', event => { if (cancel) event.preventDefault(); });
  assert.equal(ui.submit().defaultPrevented, true);
  cancel = false;
  assert.equal(ui.submit().defaultPrevented, false);
  ui.runTimers();
  assert.equal(ui.forms[0].form.getAttribute('aria-busy'), 'true');
});

test('form-level cancellation does not set busy state or restoration storage', () => {
  const ui = fixture();
  ui.edit(0);
  ui.forms[0].form.addEventListener('submit', event => event.preventDefault());
  assert.equal(ui.submit().defaultPrevented, true);
  assert.equal(ui.forms[0].form.getAttribute('aria-busy'), null);
  assert.equal(ui.unload().defaultPrevented, true);
  assert.equal(ui.store.has(pendingKey), false);
});

test('other edits require confirmation; cancel preserves dirty protection', () => {
  const ui = fixture();
  ui.edit(1);
  assert.equal(ui.submit().defaultPrevented, true);
  assert.equal(ui.dialog.open, true);
  ui.dispatch(ui.dialog.querySelector('[data-cancel]'), 'click');
  assert.equal(ui.dialog.open, false);
  assert.equal(ui.unload().defaultPrevented, true);
  assert.equal(ui.store.has(pendingKey), false);
  ui.dispatch(ui.dialog.querySelector('[data-continue]'), 'click');
  assert.equal(ui.forms[0].form.lastSubmission, undefined);
});

test('continuing confirmation resubmits with original submitter and accepted editor', () => {
  const ui = fixture();
  ui.edit(1);
  ui.submit();
  ui.dispatch(ui.dialog.querySelector('[data-continue]'), 'click');
  assert.equal(ui.forms[0].form.lastSubmission.defaultPrevented, false);
  assert.equal(ui.forms[0].form.lastSubmission.submitter, ui.forms[0].button);
  assert.equal(ui.dialog.open, false);
  assert.equal(ui.unload().defaultPrevented, false);
  assert.equal(ui.store.get(pendingKey), 'editor-0');
});

test('Escape cancellation clears pending confirmation', () => {
  const ui = fixture();
  ui.edit(1);
  ui.submit();
  ui.dispatch(ui.dialog, 'cancel');
  ui.dialog.close();
  ui.dispatch(ui.dialog.querySelector('[data-continue]'), 'click');
  assert.equal(ui.forms[0].form.lastSubmission, undefined);
  assert.equal(ui.unload().defaultPrevented, true);
});

test('failed navigation timeout and bfcache pageshow release submission lock', () => {
  const ui = fixture();
  ui.edit(0);
  ui.submit();
  ui.runTimers(15000);
  assert.equal(ui.forms[0].form.getAttribute('aria-busy'), null);
  assert.equal(ui.unload().defaultPrevented, true);
  assert.equal(ui.submit().defaultPrevented, false);
  ui.dispatch(ui.window, 'pageshow');
  assert.equal(ui.forms[0].form.getAttribute('aria-busy'), null);
  assert.equal(ui.unload().defaultPrevented, true);
  assert.equal(ui.submit().defaultPrevented, false);
});

test('action-only POST forms also recover from duplicate-submit lock', () => {
  const ui = fixture();
  const form = new ui.Element('form', {method: 'post'});
  const button = new ui.Element('button', {name: 'action', value: 'run'});
  form.append(button);
  ui.main.append(form);
  assert.equal(ui.dispatch(form, 'submit', {submitter: button}).defaultPrevented, false);
  assert.equal(ui.dispatch(form, 'submit', {submitter: button}).defaultPrevented, true);
  ui.runTimers(15000);
  assert.equal(form.getAttribute('aria-busy'), null);
  assert.equal(ui.dispatch(form, 'submit', {submitter: button}).defaultPrevented, false);
});

test('invalid hidden fields reveal their panel and enclosing disclosure', () => {
  const ui = fixture();
  ui.dispatch(ui.forms[1].input, 'invalid');
  assert.equal(ui.forms[1].editor.open, true);
  assert.equal(ui.panels[1].hidden, false);
  assert.equal(ui.panels[0].hidden, true);
});

test('stored accepted editor restores once; keyboard tabs remain usable', () => {
  const ui = fixture({stored: {[pendingKey]: 'editor-1'}});
  assert.equal(ui.forms[1].editor.open, true);
  assert.equal(ui.panels[1].hidden, false);
  assert.equal(ui.store.has(pendingKey), false);
  const event = ui.dispatch(ui.tabs[1], 'keydown', {key: 'Home'});
  assert.equal(event.defaultPrevented, true);
  assert.equal(ui.panels[0].hidden, false);
  assert.equal(ui.document.activeElement, ui.tabs[0]);
});

test('blocked browser storage does not break tabs or submission', () => {
  const ui = fixture({storageBlocked: true});
  ui.dispatch(ui.tabs[1], 'click');
  assert.equal(ui.panels[1].hidden, false);
  assert.equal(ui.submit(1).defaultPrevented, false);
  assert.equal(ui.unload().defaultPrevented, false);
});

test('login page does not acquire dashboard dirty-state UI', () => {
  assert.equal(fixture({login: true}).dialog, null);
});
