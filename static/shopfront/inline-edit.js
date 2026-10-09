/* Storefront inline editor — owner / manager / DGC / platform admin only.
 *
 * Injected by InlineEditMiddleware (never served to shoppers). Config is read
 * from this script tag's data-* attributes. In edit mode the templates carry
 * data-ed="kind:pk:field" markers (data-ed-t = text|multiline|email|rich|image,
 * data-ed-link on an <a> for its URL); this turns them into click-to-edit
 * controls that POST to /_edit/* and keep an undo/redo history server-side.
 * All editor chrome lives in a shadow root so no skin CSS can touch it.
 */
(function () {
  'use strict';
  var script = document.currentScript || document.querySelector('script[src*="inline-edit.js"]');
  if (!script) return;
  var cfg = script.dataset;
  var active = cfg.active === '1';

  var host = document.createElement('div');
  host.id = 'sd-ed-host';
  var root = host.attachShadow({ mode: 'open' });
  root.innerHTML =
    '<style>' +
    ':host{all:initial}' +
    '*{box-sizing:border-box;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}' +
    '.layer{position:fixed;inset:0;pointer-events:none;z-index:2147483000}' +
    '.layer>*{pointer-events:auto}' +
    'button,a.btn,.pill{font-size:13px;line-height:1;border:0;cursor:pointer;color:#fff;text-decoration:none}' +
    '.bar{position:fixed;left:50%;bottom:16px;transform:translateX(-50%);display:flex;align-items:center;gap:6px;' +
    'background:#111827;color:#fff;padding:8px 10px;border-radius:14px;box-shadow:0 8px 30px rgba(0,0,0,.35);max-width:96vw;flex-wrap:wrap;justify-content:center}' +
    '.bar .tag{font-size:12px;font-weight:600;padding:0 8px;color:#c7d2fe;white-space:nowrap}' +
    '.bar button,.bar a.btn{background:#1f2937;padding:8px 11px;border-radius:9px;display:inline-flex;align-items:center;gap:5px}' +
    '.bar button:hover:not(:disabled),.bar a.btn:hover{background:#374151}' +
    '.bar button:disabled{opacity:.4;cursor:default}' +
    '.bar .done{background:#4f46e5}.bar .done:hover{background:#6366f1!important}' +
    '.bar label{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:#d1d5db}' +
    '.bar input[type=color]{width:26px;height:26px;padding:0;border:0;border-radius:6px;background:none;cursor:pointer}' +
    '.pill{position:fixed;left:16px;bottom:16px;background:#4f46e5;padding:11px 15px;border-radius:999px;font-weight:600;' +
    'box-shadow:0 6px 22px rgba(79,70,229,.45);display:inline-flex;gap:6px;align-items:center}' +
    '.pill:hover{background:#6366f1}' +
    '.toast{position:fixed;left:50%;bottom:78px;transform:translateX(-50%);background:#111827;color:#fff;padding:10px 14px;' +
    'border-radius:10px;font-size:13px;display:none;align-items:center;gap:12px;box-shadow:0 6px 24px rgba(0,0,0,.3);max-width:92vw}' +
    '.toast.err{background:#b91c1c}.toast button{background:none;color:#a5b4fc;font-weight:600;padding:0;text-decoration:underline}' +
    '.badge{position:fixed;display:none;align-items:center;gap:5px;background:#4f46e5;color:#fff;padding:7px 10px;border-radius:8px;' +
    'font-size:12px;font-weight:600;box-shadow:0 4px 14px rgba(0,0,0,.3)}.badge:hover{background:#6366f1}' +
    '.pop{position:fixed;display:none;background:#fff;color:#111827;border-radius:12px;padding:10px;box-shadow:0 10px 36px rgba(0,0,0,.3);' +
    'gap:6px;align-items:center;max-width:94vw}' +
    '.pop.money{flex-wrap:wrap}.pop label{font-size:12px;display:flex;flex-direction:column;gap:3px;color:#374151}' +
    '.pop.money input{width:112px}' +
    '.sec{position:fixed;display:none;gap:2px;align-items:center;background:#111827;color:#fff;border-radius:8px;padding:3px;box-shadow:0 4px 14px rgba(0,0,0,.3);font-size:11px}' +
    '.sec span{padding:0 6px;color:#c7d2fe;font-weight:600;white-space:nowrap}.sec button{background:#374151;padding:6px 8px;border-radius:6px}.sec button:hover:not(:disabled){background:#4f46e5}.sec button:disabled{opacity:.3;cursor:default}' +
    '.pop input{font-size:14px;padding:8px 10px;border:1px solid #d1d5db;border-radius:8px;width:min(320px,70vw)}' +
    '.pop button{background:#4f46e5;padding:8px 12px;border-radius:8px}' +
    '.pop button.ghost{background:#e5e7eb;color:#111827}' +
    '.rt{position:fixed;display:none;background:#111827;border-radius:10px;padding:4px;gap:2px;box-shadow:0 6px 22px rgba(0,0,0,.35)}' +
    '.rt button{background:none;padding:7px 10px;border-radius:7px;font-weight:700}.rt button:hover{background:#374151}' +
    '</style><div class="layer" id="layer"></div>';
  var layer = root.getElementById('layer');
  function mk(html) { var d = document.createElement('div'); d.innerHTML = html; return d.firstChild; }
  function mount() { document.body.appendChild(host); }
  if (document.body) mount(); else document.addEventListener('DOMContentLoaded', mount);

  // ---- inactive: just the "Edit this store" pill ------------------------
  if (!active) {
    layer.appendChild(mk('<a class="pill" href="' + cfg.on + '">✎ Edit this store</a>'));
    return;
  }

  // ---- state / helpers --------------------------------------------------
  document.addEventListener('DOMContentLoaded', function () { document.body.classList.add('sd-ed'); });
  if (document.body) document.body.classList.add('sd-ed');
  var undoStack = [], redoStack = [], current = null, toastTimer = null;
  var HKEY = 'sd_ed_hist', TKEY = 'sd_ed_toast';
  try {
    var h = JSON.parse(sessionStorage.getItem(HKEY) || '{}');
    undoStack = h.u || []; redoStack = h.r || [];
  } catch (e) {}
  function persist() { try { sessionStorage.setItem(HKEY, JSON.stringify({ u: undoStack, r: redoStack })); } catch (e) {} }
  // Some edits (prices, section order) change layout — reload, keep the undo history.
  function reloadWith(msg) {
    persist();
    try { if (msg) sessionStorage.setItem(TKEY, msg); } catch (e) {}
    location.reload();
  }

  function qsa(sel) { return Array.prototype.slice.call(document.querySelectorAll(sel)); }
  function post(url, body, isForm) {
    var headers = { 'X-CSRFToken': cfg.csrf };
    if (!isForm) headers['Content-Type'] = 'application/json';
    return fetch(url, {
      method: 'POST', credentials: 'same-origin', keepalive: !isForm, headers: headers,
      body: isForm ? body : JSON.stringify(body)
    }).then(function (r) {
      return r.json().catch(function () { return { ok: false, error: 'Server error' }; })
        .then(function (j) { if (!r.ok) j.ok = false; return j; });
    }).catch(function () { return { ok: false, error: 'No connection — not saved.' }; });
  }

  // ---- bottom bar -------------------------------------------------------
  var bar = mk(
    '<div class="bar"><span class="tag" title="Click text or images to edit. Ctrl/Cmd-click a link to follow it.">✎ Editing</span>' +
    '<button id="undo" disabled>↶ Undo</button><button id="redo" disabled>↷ Redo</button>' +
    '<label>Accent <input type="color" id="accent"></label>' +
    '<a class="btn" href="' + cfg.admin + '" target="_blank" rel="noopener">Admin ↗</a>' +
    '<a class="btn done" href="' + cfg.off + '">Done</a></div>');
  var toast = mk('<div class="toast" id="toast"><span id="tmsg"></span><button id="tact" style="display:none"></button></div>');
  layer.appendChild(bar); layer.appendChild(toast);
  if (document.querySelector('[data-ed-popup]')) {
    var pb = document.createElement('button'); pb.id = 'popbtn'; pb.textContent = '▣ Popup';
    pb.title = 'Show the pop-up banner so you can edit it';
    pb.onclick = function () {
      var el = document.querySelector('[data-ed-popup]');
      if (window.Alpine && Alpine.$data) Alpine.$data(el).open = true;
    };
    bar.insertBefore(pb, bar.querySelector('a.btn'));
  }
  var btnUndo = bar.querySelector('#undo'), btnRedo = bar.querySelector('#redo');
  var accent = bar.querySelector('#accent');

  function syncButtons() { btnUndo.disabled = !undoStack.length; btnRedo.disabled = !redoStack.length; persist(); }
  syncButtons();
  try {
    var pending = sessionStorage.getItem(TKEY);
    if (pending) {
      sessionStorage.removeItem(TKEY);
      if (undoStack.length) say(pending, { action: 'Undo', run: undo }); else say(pending);
    }
  } catch (e) {}
  function say(msg, opts) {
    opts = opts || {};
    var t = toast, m = t.querySelector('#tmsg'), a = t.querySelector('#tact');
    m.textContent = msg; t.className = 'toast' + (opts.error ? ' err' : ''); t.style.display = 'flex';
    if (opts.action) { a.style.display = ''; a.textContent = opts.action; a.onclick = function () { t.style.display = 'none'; opts.run(); }; }
    else { a.style.display = 'none'; a.onclick = null; }
    clearTimeout(toastTimer);
    if (!opts.sticky) toastTimer = setTimeout(function () { t.style.display = 'none'; }, opts.error ? 6000 : 7000);
  }

  // ---- applying a server result to every matching element ---------------
  function setImg(el, url) {
    if (el.tagName !== 'IMG') return;
    el.removeAttribute('srcset');
    var pic = el.parentElement;
    if (pic && pic.tagName === 'PICTURE') qsa('picture > source').forEach(function (s) { if (s.parentElement === pic) s.remove(); });
    el.src = url;
  }
  function rgb(hex) { var n = parseInt(hex.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
  function applyAccent(hex) {
    var c = rgb(hex); document.documentElement.style.setProperty('--accent', c.join(' ')); accent.value = hex;
  }
  function apply(res) {
    if (res.type === 'decimal' || res.type === 'sections') { reloadWith(res.type === 'decimal' ? 'Price updated' : 'Section order updated'); return; }
    if (res.kind === 'theme') { if (res.value) applyAccent(res.value); return; }
    var key = res.kind + ':' + res.pk + ':' + res.field;
    qsa('[data-ed="' + key + '"]').forEach(function (el) {
      if (res.type === 'image') setImg(el, res.value);
      else if (res.type === 'rich') el.innerHTML = res.value;
      else el.textContent = res.value;
    });
    qsa('[data-ed-link="' + key + '"]').forEach(function (el) { if (res.value) el.setAttribute('href', res.value); });
  }
  function committed(res, label) {
    if (!res.ok) { say(res.error || 'Could not save.', { error: true }); return false; }
    if (res.log) { undoStack.push(res.log); redoStack.length = 0; syncButtons(); }
    apply(res);
    if (res.log) say(label || 'Saved', { action: 'Undo', run: undo });
    return true;
  }
  function history(from, to, url, msg) {
    var id = from.pop(); if (!id) return;
    syncButtons();
    post(url, { id: id }).then(function (res) {
      if (!res.ok) { say(res.error || 'Nothing to change.', { error: true }); return; }
      to.push(id); syncButtons(); apply(res); say(msg);
    });
  }
  function undo() { history(undoStack, redoStack, cfg.undo, 'Undone'); }
  function redo() { history(redoStack, undoStack, cfg.redo, 'Redone'); }
  btnUndo.onclick = undo; btnRedo.onclick = redo;

  // ---- accent colour ----------------------------------------------------
  (function () {
    var v = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim().split(/\s+/).map(Number);
    if (v.length === 3 && v.every(function (n) { return n >= 0 && n <= 255; })) {
      accent.value = '#' + v.map(function (n) { return ('0' + n.toString(16)).slice(-2); }).join('');
    }
    accent.addEventListener('input', function () { applyAccent(accent.value); });
    accent.addEventListener('change', function () {
      post(cfg.save, { kind: 'theme', pk: 0, field: 'primary_color', value: accent.value })
        .then(function (r) { committed(r, 'Accent colour saved'); });
    });
  })();

  // ---- text / rich editing ---------------------------------------------
  var rt = mk('<div class="rt" id="rt"><button data-c="bold">B</button><button data-c="italic" style="font-style:italic">I</button>' +
    '<button data-c="h2">H2</button><button data-c="ul">•</button><button data-c="link">🔗</button></div>');
  layer.appendChild(rt);
  var savedRange = null;
  function place(node, el, above) {
    var r = el.getBoundingClientRect();
    node.style.display = 'flex';
    var top = above ? r.top - node.offsetHeight - 8 : r.bottom + 8;
    node.style.top = Math.max(8, Math.min(innerHeight - node.offsetHeight - 8, top)) + 'px';
    node.style.left = Math.max(8, Math.min(innerWidth - node.offsetWidth - 8, r.left)) + 'px';
  }

  function startEdit(el) {
    if (current && current.el === el) return;
    if (current) current.el.blur();
    var type = el.dataset.edT;
    var st = { el: el, type: type, cancelled: false, orig: type === 'rich' ? el.innerHTML : el.textContent };
    current = st;
    el.contentEditable = 'true';
    el.focus();
    var sel = getSelection(), range = document.createRange();
    range.selectNodeContents(el); range.collapse(false); sel.removeAllRanges(); sel.addRange(range);
    if (type === 'rich') place(rt, el, true);

    function onKey(e) {
      if (e.key === 'Escape') { st.cancelled = true; el.blur(); }
      else if (e.key === 'Enter' && type !== 'multiline' && type !== 'rich') { e.preventDefault(); el.blur(); }
    }
    function onPaste(e) {
      e.preventDefault();
      var t = (e.clipboardData || window.clipboardData).getData('text/plain');
      if (type !== 'multiline' && type !== 'rich') t = t.replace(/\s*\n+\s*/g, ' ');
      document.execCommand('insertText', false, t);
    }
    function onBlur() {
      el.removeEventListener('keydown', onKey); el.removeEventListener('paste', onPaste); el.removeEventListener('blur', onBlur);
      el.removeAttribute('contenteditable'); rt.style.display = 'none'; popLink.style.display = 'none';
      if (current === st) current = null;
      if (st.cancelled) { if (type === 'rich') el.innerHTML = st.orig; else el.textContent = st.orig; return; }
      var val = type === 'rich' ? el.innerHTML : (type === 'multiline' ? el.innerText : el.textContent);
      if (type !== 'rich') val = val.replace(/ /g, ' ');
      if (val.trim() === st.orig.trim()) { if (type !== 'rich') el.textContent = st.orig; return; }
      var parts = el.dataset.ed.split(':');
      post(cfg.save, { kind: parts[0], pk: +parts[1], field: parts[2], value: val }).then(function (res) {
        if (!committed(res)) { if (type === 'rich') el.innerHTML = st.orig; else el.textContent = st.orig; }
      });
    }
    el.addEventListener('keydown', onKey); el.addEventListener('paste', onPaste); el.addEventListener('blur', onBlur);
  }

  // rich-text toolbar: keep the selection (mousedown must not steal focus)
  rt.addEventListener('mousedown', function (e) { e.preventDefault(); });
  rt.addEventListener('click', function (e) {
    var b = e.target.closest('button'); if (!b || !current) return;
    var c = b.dataset.c;
    if (c === 'bold' || c === 'italic') document.execCommand(c);
    else if (c === 'h2') document.execCommand('formatBlock', false, 'h2');
    else if (c === 'ul') document.execCommand('insertUnorderedList');
    else if (c === 'link') {
      var s = getSelection(); savedRange = s.rangeCount ? s.getRangeAt(0).cloneRange() : null;
      openLink('', function (url) {
        if (savedRange) { var s2 = getSelection(); s2.removeAllRanges(); s2.addRange(savedRange); }
        if (url) document.execCommand('createLink', false, url); else document.execCommand('unlink');
      }, current.el);
    }
  });

  // ---- link popover (CTA button URL, rich-text links) -------------------
  var popLink = mk('<div class="pop" id="poplink"><input id="lurl" placeholder="https://… or /shop/" autocomplete="off">' +
    '<button id="lok">Save</button><button class="ghost" id="lno">Cancel</button></div>');
  layer.appendChild(popLink);
  var lurl = popLink.querySelector('#lurl'), linkDone = null;
  function openLink(value, done, near) {
    linkDone = done; lurl.value = value || ''; place(popLink, near, false); lurl.focus();
  }
  function closeLink() { popLink.style.display = 'none'; linkDone = null; }
  popLink.querySelector('#lno').onclick = closeLink;
  popLink.querySelector('#lok').onclick = function () { var d = linkDone, v = lurl.value.trim(); closeLink(); if (d) d(v); };
  lurl.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); popLink.querySelector('#lok').click(); }
    if (e.key === 'Escape') closeLink();
  });
  popLink.addEventListener('mousedown', function (e) { if (e.target !== lurl) e.preventDefault(); });

  // ---- images -----------------------------------------------------------
  var picker = document.createElement('input');
  picker.type = 'file'; picker.accept = 'image/jpeg,image/png,image/webp,image/gif'; picker.style.display = 'none';
  layer.appendChild(picker);
  var pickTarget = null;
  function pickImage(el) { pickTarget = el; picker.value = ''; picker.click(); }
  picker.addEventListener('change', function () {
    var f = picker.files && picker.files[0], el = pickTarget; if (!f || !el) return;
    var parts = el.dataset.ed.split(':'), fd = new FormData();
    fd.append('kind', parts[0]); fd.append('pk', parts[1]); fd.append('field', parts[2]); fd.append('file', f);
    say('Uploading…', { sticky: true });
    post(cfg.image, fd, true).then(function (res) { committed(res, 'Image updated'); });
  });

  var badges = [];
  function buildBadges() {
    qsa('[data-ed][data-ed-t="image"]').forEach(function (el) {
      if (badges.some(function (b) { return b.el === el; })) return;
      var node = mk('<button class="badge" type="button">📷 Change image</button>');
      node.onclick = function () { pickImage(el); };
      layer.appendChild(node); badges.push({ el: el, node: node });
    });
  }
  function layout() {
    buildBadges();
    layoutSections();
    layoutCardChips();
    badges.forEach(function (b) {
      if (!document.contains(b.el)) { b.node.style.display = 'none'; return; }
      var r = b.el.getBoundingClientRect();
      var show = r.width > 24 && r.height > 12 && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth;
      b.node.style.display = show ? 'inline-flex' : 'none';
      if (!show) return;
      b.node.textContent = r.width >= 220 ? '📷 Change image' : '📷';
      b.node.style.top = Math.max(8, r.top + 8) + 'px';
      b.node.style.left = Math.max(8, Math.min(innerWidth - b.node.offsetWidth - 8, r.right - b.node.offsetWidth - 8)) + 'px';
    });
  }
  var raf = 0;
  function schedule() { if (!raf) raf = requestAnimationFrame(function () { raf = 0; layout(); }); }
  addEventListener('scroll', schedule, { passive: true }); addEventListener('resize', schedule);
  setInterval(schedule, 700); schedule();

  // ---- product cards / category tiles: open the admin form, which returns here after Save ----
  var cardChips = [];
  var FORMS = [
    { attr: 'data-ed-product', key: 'edProduct', tpl: function () { return cfg.product; }, label: 'Edit product' },
    { attr: 'data-ed-category', key: 'edCategory', tpl: function () { return cfg.category; }, label: 'Edit category' }
  ];
  function adminFormUrl(tpl, pk) {
    // Admin on another origin (platform host): send the full storefront URL back.
    var crossOrigin = /^https?:/.test(tpl) && tpl.indexOf(location.origin) !== 0;
    var back = crossOrigin ? location.href : location.pathname + location.search;
    return tpl.replace('/0/', '/' + pk + '/') + '?next=' + encodeURIComponent(back);
  }
  function buildCardChips() {
    FORMS.forEach(function (f) {
      if (!f.tpl()) return;
      qsa('[' + f.attr + ']').forEach(function (el) {
        if (cardChips.some(function (c) { return c.el === el; })) return;
        var node = mk('<a class="badge" style="text-decoration:none"></a>');
        node.href = adminFormUrl(f.tpl(), el.dataset[f.key]);
        node.dataset.full = '✎ ' + f.label;
        layer.appendChild(node); cardChips.push({ el: el, node: node });
      });
    });
  }
  function layoutCardChips() {
    buildCardChips();
    cardChips.forEach(function (c) {
      var r = c.el.getBoundingClientRect();
      var show = document.contains(c.el) && r.height > 50 && r.width > 50 && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth;
      c.node.style.display = show ? 'inline-flex' : 'none';
      if (!show) return;
      c.node.textContent = r.width >= 150 ? c.node.dataset.full : '✎';
      c.node.style.top = Math.max(8, r.top + 8) + 'px';
      c.node.style.left = Math.max(8, Math.min(innerWidth - c.node.offsetWidth - 8, r.left + (r.width - c.node.offsetWidth) / 2)) + 'px';
    });
  }

  // ---- price popover (product cards) -------------------------------------
  var popMoney = mk('<div class="pop money" id="popmoney"><label>Price<input id="mprice" type="number" min="0" step="0.01"></label>' +
    '<label>Sale price<input id="msale" type="number" min="0" step="0.01" placeholder="none"></label>' +
    '<button id="mok">Save</button><button class="ghost" id="mno">Cancel</button></div>');
  layer.appendChild(popMoney);
  var mprice = popMoney.querySelector('#mprice'), msale = popMoney.querySelector('#msale'), moneyEl = null;
  function openMoney(el) {
    moneyEl = el; mprice.value = el.dataset.price || ''; msale.value = el.dataset.sale || '';
    place(popMoney, el, false); mprice.focus();
  }
  function closeMoney() { popMoney.style.display = 'none'; moneyEl = null; }
  popMoney.querySelector('#mno').onclick = closeMoney;
  popMoney.querySelector('#mok').onclick = function () {
    var el = moneyEl; if (!el) return;
    var pk = +el.dataset.edMoney, jobs = [];
    var np = mprice.value.trim(), ns = msale.value.trim();
    var op = parseFloat(el.dataset.price || ''), os = el.dataset.sale === '' ? null : parseFloat(el.dataset.sale);
    if (np !== '' && parseFloat(np) !== op) jobs.push({ field: 'price', value: np });
    if ((ns === '' ? null : parseFloat(ns)) !== os) jobs.push({ field: 'sale_price', value: ns });
    closeMoney();
    if (!jobs.length) return;
    (function next(i) {
      if (i >= jobs.length) { reloadWith('Price saved'); return; }
      post(cfg.save, { kind: 'product', pk: pk, field: jobs[i].field, value: jobs[i].value }).then(function (r) {
        if (!r.ok) { say(r.error || 'Could not save.', { error: true }); if (i > 0) reloadWith(); return; }
        if (r.log) { undoStack.push(r.log); redoStack.length = 0; }
        next(i + 1);
      });
    })(0);
  };
  popMoney.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); popMoney.querySelector('#mok').click(); }
    if (e.key === 'Escape') closeMoney();
  });

  // ---- homepage section order -------------------------------------------
  var secNodes = [];
  function visibleSection(el) { var r = el.getBoundingClientRect(); return r.height > 40 && r.width > 40; }
  function moveSection(el, dir) {
    var all = qsa('[data-ed-section]'), vis = all.filter(visibleSection);
    var j = vis.indexOf(el) + dir; if (j < 0 || j >= vis.length) return;
    var keys = all.map(function (k) { return k.dataset.edSection; });
    var a = keys.indexOf(el.dataset.edSection), b = keys.indexOf(vis[j].dataset.edSection), t = keys[a];
    keys[a] = keys[b]; keys[b] = t;
    post(cfg.save, { kind: 'theme', pk: 0, field: 'homepage_sections', value: keys }).then(function (r) {
      if (!r.ok) { say(r.error || 'Could not move.', { error: true }); return; }
      if (r.log) { undoStack.push(r.log); redoStack.length = 0; }
      reloadWith('Section moved');
    });
  }
  function buildSections() {
    qsa('[data-ed-section]').forEach(function (el) {
      if (secNodes.some(function (n) { return n.el === el; })) return;
      var node = mk('<div class="sec"><span></span><button data-d="-1" title="Move up">▲</button><button data-d="1" title="Move down">▼</button></div>');
      node.querySelector('span').textContent = el.dataset.edLabel || el.dataset.edSection;
      node.addEventListener('click', function (e) {
        var b = e.target.closest('button'); if (b && !b.disabled) moveSection(el, +b.dataset.d);
      });
      layer.appendChild(node); secNodes.push({ el: el, node: node });
    });
  }
  function layoutSections() {
    buildSections();
    var vis = secNodes.filter(function (n) { return document.contains(n.el) && visibleSection(n.el); });
    secNodes.forEach(function (n) {
      var r = n.el.getBoundingClientRect();
      var show = vis.indexOf(n) !== -1 && r.bottom > 60 && r.top < innerHeight;
      n.node.style.display = show ? 'flex' : 'none';
      if (!show) return;
      n.node.style.left = '8px';
      n.node.style.top = Math.max(64, Math.min(r.top + 8, innerHeight - 90)) + 'px';
      var btns = n.node.querySelectorAll('button'), i = vis.indexOf(n);
      btns[0].disabled = i === 0; btns[1].disabled = i === vis.length - 1;
    });
  }

  // ---- click routing ----------------------------------------------------
  document.addEventListener('click', function (e) {
    if (e.target === host || host.contains(e.target)) return;
    if (e.metaKey || e.ctrlKey) return;   // Ctrl/Cmd-click follows the link as normal
    var t = e.target.closest && e.target.closest('[data-ed]');
    if (t) {
      e.preventDefault(); e.stopPropagation();
      if (t.dataset.edT === 'image') pickImage(t); else startEdit(t);
      return;
    }
    for (var fi = 0; fi < FORMS.length; fi++) {   // product card / category tile -> its admin form
      var f = FORMS[fi], hit = f.tpl() && e.target.closest && e.target.closest('[' + f.attr + ']');
      if (hit) {
        e.preventDefault(); e.stopPropagation();
        location.href = adminFormUrl(f.tpl(), hit.dataset[f.key]);
        return;
      }
    }
    var m = e.target.closest && e.target.closest('[data-ed-money]');
    if (m) { e.preventDefault(); e.stopPropagation(); openMoney(m); return; }
    var a = e.target.closest && e.target.closest('a[data-ed-link]');
    if (a) {          // the button's padding: edit where it points
      e.preventDefault(); e.stopPropagation();
      var key = a.dataset.edLink.split(':');
      openLink(a.getAttribute('href') || '', function (url) {
        post(cfg.save, { kind: key[0], pk: +key[1], field: key[2], value: url })
          .then(function (r) { committed(r, 'Link saved'); });
      }, a);
    }
  }, true);

  document.addEventListener('keydown', function (e) {
    var ae = document.activeElement;
    if ((e.metaKey || e.ctrlKey) && !e.altKey && !(ae && ae.isContentEditable) && !/^(INPUT|TEXTAREA)$/.test((ae || {}).tagName || '')) {
      if (e.key === 'z' || e.key === 'Z') { e.preventDefault(); if (e.shiftKey) redo(); else undo(); }
      else if (e.key === 'y') { e.preventDefault(); redo(); }
    }
  });
})();
