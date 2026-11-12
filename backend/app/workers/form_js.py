"""JavaScript snippets evaluated inside target pages by the form engine.

Every snippet is read-only except for `data-raven-*` marker attributes, which let Python
re-locate the exact elements the analysis looked at.
"""

# Shared helpers injected into snippets: deep queries that pierce open shadow roots.
_DEEP = r"""
  const __roots = () => {
    const out = [document];
    for (let i = 0; i < out.length && out.length < 400; i++) {
      out[i].querySelectorAll('*').forEach(el => { if (el.shadowRoot) out.push(el.shadowRoot); });
    }
    return out;
  };
  const deepAll = (root, sel) => {
    const res = Array.from(root.querySelectorAll(sel));
    root.querySelectorAll('*').forEach(el => { if (el.shadowRoot) res.push(...deepAll(el.shadowRoot, sel)); });
    return res;
  };
  const deepOne = (sel) => { for (const r of __roots()) { const e = r.querySelector(sel); if (e) return e; } return null; };
"""

# Returns [{href, text}] for anchors on the page.
COLLECT_LINKS_JS = r"""
() => Array.from(document.querySelectorAll('a[href]')).slice(0, 2000).map(a => ({
  href: a.href,
  text: (a.innerText || a.getAttribute('aria-label') || a.title || '').trim().slice(0, 120),
}))
"""

# Returns a list of captcha kinds found in this document (empty list = none).
DETECT_CAPTCHA_JS = r"""
() => {
  const found = new Set();
  const q = (sel) => document.querySelector(sel) !== null;
  if (q('.g-recaptcha, iframe[src*="recaptcha"], script[src*="recaptcha/api.js"], script[src*="recaptcha/enterprise.js"], script[src*="gstatic.com/recaptcha"]') || typeof window.grecaptcha !== 'undefined') found.add('recaptcha');
  if (q('.h-captcha, iframe[src*="hcaptcha"], script[src*="hcaptcha.com"]') || typeof window.hcaptcha !== 'undefined') found.add('hcaptcha');
  if (q('.cf-turnstile, iframe[src*="challenges.cloudflare.com"], script[src*="challenges.cloudflare.com/turnstile"]') || typeof window.turnstile !== 'undefined') found.add('turnstile');
  if (q('.frc-captcha, script[src*="friendlycaptcha"]')) found.add('friendlycaptcha');
  if (q('iframe[title*="captcha" i], img[src*="captcha" i], input[name*="captcha" i]')) found.add('captcha');
  const title = (document.title || '').toLowerCase();
  if (title.includes('just a moment') || title.includes('attention required') || q('#challenge-form, #cf-challenge-running')) found.add('cloudflare_challenge');
  return Array.from(found);
}
"""

# Analyses every form (and form-like container) in the document.
# Marks elements with data-raven-form / data-raven-idx / data-raven-submit and returns descriptors.
EXTRACT_FORMS_JS = r"""
(frameKey) => {
""" + _DEEP + r"""
  const EXCLUDE_RE = /(search|newsletter|subscribe|sign[-_ ]?up|log[-_ ]?in|sign[-_ ]?in|register|cart|checkout|password|coupon|comment-form|commentform)/i;
  const SUBMIT_TEXT_RE = /(send|submit|contact|get in touch|request|enquire|inquire|let'?s talk|talk to us|go)/i;
  const NEXT_RE = /^(next( step)?|continue|proceed|step\s*\d+|go to step\s*\d+)\b|^(next|continue)\s*[›>→»]?$/i;
  const HONEYPOT_RE = /(honeypot|hp[-_]field|leave (this )?(field )?(blank|empty)|do not fill|bot[-_]?field|ak_hp)/i;

  const styleHidden = (el) => {
    let n = el;
    while (n && n.nodeType === 1) {
      const cs = getComputedStyle(n);
      if (cs.display === 'none' || cs.visibility === 'hidden' || cs.visibility === 'collapse') return true;
      if (parseFloat(cs.opacity) === 0) return true;
      n = n.parentElement;
    }
    return false;
  };
  const boxVisible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    if (r.right < -100 || r.bottom < -100 - window.scrollY) return false;
    if (r.left < -1000 || r.top < -5000) return false;
    return true;
  };
  const labelText = (el) => {
    const parts = [];
    if (el.labels && el.labels.length) parts.push(Array.from(el.labels).map(l => l.innerText).join(' '));
    const lb = el.getAttribute('aria-labelledby');
    if (lb) lb.split(/\s+/).forEach(id => { const rn = el.getRootNode(); const t = (rn.getElementById ? rn.getElementById(id) : null) || document.getElementById(id); if (t) parts.push(t.innerText); });
    if (!parts.length) {
      const wrap = el.closest('label');
      if (wrap) parts.push(wrap.innerText);
    }
    if (!parts.length) {
      // Nearest preceding text in the same field wrapper (common in page builders).
      let p = el.parentElement;
      for (let i = 0; i < 3 && p && !parts.length; i++, p = p.parentElement) {
        const lab = p.querySelector('label, .label, legend, .gfield_label, .elementor-field-label');
        if (lab && !lab.contains(el)) parts.push(lab.innerText);
      }
    }
    return parts.join(' ').replace(/\s+/g, ' ').trim().slice(0, 200);
  };
  const isHoneypot = (el, label) => {
    const blob = [el.name, el.id, el.className, label, el.getAttribute('aria-label')].join(' ');
    return HONEYPOT_RE.test(blob) || el.getAttribute('tabindex') === '-1' || el.getAttribute('aria-hidden') === 'true';
  };
  const fieldVisible = (el) => {
    if (el.type === 'hidden') return false;
    if (el.disabled || el.readOnly) return false;
    if (el.type === 'checkbox' || el.type === 'radio') {
      // Custom-styled checkboxes often hide the input itself; accept a visible label instead.
      if (!styleHidden(el) && boxVisible(el)) return true;
      const lab = (el.labels && el.labels[0]) || el.closest('label');
      return !!lab && !styleHidden(lab) && boxVisible(lab);
    }
    return !styleHidden(el) && boxVisible(el);
  };

  // Candidate containers: real forms plus formless wrappers around a visible textarea.
  const containers = [];
  __roots().forEach(root => root.querySelectorAll('form').forEach(f => containers.push(f)));
  const up = (n) => n.parentElement || ((n.getRootNode && n.getRootNode().host) || null);
  deepAll(document, 'textarea').forEach(ta => {
    if (ta.closest('form')) return;
    let p = up(ta);
    for (let i = 0; i < 8 && p; i++, p = up(p)) {
      const inputs = deepAll(p, 'input:not([type=hidden]), select, textarea');
      const btn = deepAll(p, 'button, input[type=submit], [role=button]')[0];
      if (inputs.length >= 2 && btn) { if (!containers.includes(p)) containers.push(p); break; }
    }
  });

  const results = [];
  containers.forEach((form, fi) => {
    const fkey = `${frameKey}-${fi}`;
    const meta = [form.id, form.className, form.getAttribute('name'), form.getAttribute('action'), form.getAttribute('role')].join(' ');
    const hasPassword = deepAll(form, 'input[type=password]').length > 0;
    const formVisible = !styleHidden(form) && boxVisible(form);
    // Re-scans (multi-step forms, revealed fields) must not leave stale markers behind.
    deepAll(form, '[data-raven-idx], [data-raven-submit], [data-raven-next]').forEach(el => {
      el.removeAttribute('data-raven-idx'); el.removeAttribute('data-raven-submit'); el.removeAttribute('data-raven-next');
    });
    const fields = [];
    let i = 0;
    deepAll(form, 'input, textarea, select').forEach(el => {
      const type = (el.type || el.tagName).toLowerCase();
      if (['submit', 'button', 'reset', 'image', 'file', 'hidden'].includes(type)) return;
      const label = labelText(el);
      if (!fieldVisible(el)) return;
      if (isHoneypot(el, label)) return;
      const idx = `${fkey}-${i++}`;
      el.setAttribute('data-raven-idx', idx);
      fields.push({
        idx,
        tag: el.tagName.toLowerCase(),
        type,
        name: el.name || '',
        id: el.id || '',
        placeholder: el.getAttribute('placeholder') || '',
        label,
        aria: el.getAttribute('aria-label') || '',
        autocomplete: el.getAttribute('autocomplete') || '',
        required: el.required || el.getAttribute('aria-required') === 'true' || /\*/.test(label),
        maxlength: el.maxLength > 0 ? el.maxLength : null,
        value: type === 'checkbox' || type === 'radio' ? (el.value || '') : '',
        options: el.tagName === 'SELECT'
          ? Array.from(el.options).slice(0, 60).map(o => ({ value: o.value, text: (o.text || '').trim().slice(0, 80), disabled: o.disabled }))
          : [],
      });
    });

    // Submit control.
    let submitIdx = null;
    const buttons = deepAll(form, 'button, input[type=submit], input[type=image], [role=button], a.button, a.btn');
    const ranked = buttons
      .filter(b => !styleHidden(b) && boxVisible(b))
      .map(b => {
        const txt = (b.innerText || b.value || b.getAttribute('aria-label') || '').trim();
        let s = 0;
        if ((b.getAttribute('type') || '').toLowerCase() === 'submit') s += 3;
        if (b.tagName === 'BUTTON' && !b.getAttribute('type')) s += 2;
        if (SUBMIT_TEXT_RE.test(txt)) s += 2;
        if (/(cancel|reset|clear|close|back|previous)/i.test(txt)) s -= 5;
        if (NEXT_RE.test(txt)) s -= 10;  // a multi-step 'Next' is never the final submit
        return { b, s, txt };
      })
      .filter(x => x.s > 0)
      .sort((a, b) => b.s - a.s);
    if (ranked.length) {
      submitIdx = `${fkey}-submit`;
      ranked[0].b.setAttribute('data-raven-submit', submitIdx);
    }

    // Multi-step forms: a visible Next/Continue button means more fields follow.
    let nextIdx = null, nextText = null, nextIsSubmit = false;
    const nextBtn = buttons.find(b => !styleHidden(b) && boxVisible(b) &&
      NEXT_RE.test((b.innerText || b.value || b.getAttribute('aria-label') || '').trim()));
    if (nextBtn) {
      nextIdx = `${fkey}-next`;
      nextBtn.setAttribute('data-raven-next', nextIdx);
      nextText = (nextBtn.innerText || nextBtn.value || '').trim().slice(0, 40);
      const t = (nextBtn.getAttribute('type') || (nextBtn.tagName === 'BUTTON' ? 'submit' : '')).toLowerCase();
      nextIsSubmit = (t === 'submit' || nextBtn.tagName === 'INPUT' && t === 'submit');
    }
    const hasAnyTextarea = deepAll(form, 'textarea').length > 0;

    form.setAttribute('data-raven-form', fkey);
    const visibleText = (form.innerText || '').replace(/\s+/g, ' ').trim();
    results.push({
      key: fkey,
      is_form_tag: form.tagName === 'FORM',
      meta: meta.slice(0, 300),
      excluded: EXCLUDE_RE.test(meta) || hasPassword || (form.getAttribute('role') || '') === 'search',
      visible: formVisible,
      text: visibleText.slice(0, 1500),
      fields,
      submit_idx: submitIdx,
      submit_text: ranked.length ? ranked[0].txt.slice(0, 60) : null,
      next_idx: nextIdx,
      next_text: nextText,
      next_is_submit: nextIsSubmit,
      has_any_textarea: hasAnyTextarea,
    });
  });
  return results;
}
"""

# Snapshot used before/after submit to judge what happened.
PAGE_STATE_JS = r"""
(formKey) => {
""" + _DEEP + r"""
  const form = formKey ? deepOne(`[data-raven-form="${formKey}"]`) : null;
  let formVisible = false;
  if (form) {
    const r = form.getBoundingClientRect();
    const cs = getComputedStyle(form);
    formVisible = r.width > 2 && r.height > 2 && cs.display !== 'none' && cs.visibility !== 'hidden';
  }
  const invalid = form ? deepAll(form, '[aria-invalid="true"], .error input, input.error, .wpcf7-not-valid, .has-error input, .is-invalid').length : 0;
  return {
    url: location.href,
    // Include text rendered inside open shadow roots (web-component forms confirm there).
    text: [document.body ? document.body.innerText : '']
      // innerText of a display:none element returns its raw text, so only count rendered children.
      .concat(__roots().slice(1).flatMap(r => Array.from(r.children)
        .filter(c => typeof c.checkVisibility !== 'function' || c.checkVisibility())
        .map(c => c.innerText || '')))
      .join(' ').replace(/\s+/g, ' ').slice(0, 20000),
    form_present: !!form,
    form_visible: formVisible,
    invalid_count: invalid,
  };
}
"""

# HTML5 constraint validation for the chosen form; returns identifiers of invalid controls
# (including ones the engine deliberately does not fill, such as required file uploads).
CHECK_VALIDITY_JS = r"""
(arg) => {
""" + _DEEP + r"""
  // arg: formKey string, or {key, visibleOnly} to validate just the current step of a multi-step form.
  const formKey = typeof arg === 'string' ? arg : arg.key;
  const visibleOnly = typeof arg === 'object' && arg !== null && !!arg.visibleOnly;
  const form = deepOne(`[data-raven-form="${formKey}"]`);
  if (!form) return [];
  return deepAll(form, 'input, select, textarea')
    .filter(el => el.type !== 'hidden' && typeof el.checkValidity === 'function' && !el.checkValidity())
    .filter(el => !visibleOnly || typeof el.checkVisibility !== 'function' || el.checkVisibility())
    .map(el => el.getAttribute('data-raven-idx') || el.name || el.id || el.type);
}
"""


# Best-effort cookie banner dismissal. Prefers "reject"/"necessary only" over "accept".
DISMISS_COOKIES_JS = r"""
() => {
""" + _DEEP + r"""
  const REJECT = /^(reject( all)?( cookies)?|decline( all)?|deny( all)?|(use )?(only )?(strictly )?necessary( cookies)?( only)?|refuse( all)?)$/i;
  const ACCEPT = /^(accept( all)?( cookies)?|allow( all)?( cookies)?|i agree|agree|got it|ok(ay)?|i understand|continue)$/i;
  const SCOPE = '#onetrust-banner-sdk, #onetrust-consent-sdk, #CybotCookiebotDialog, .cc-window, #cookie-law-info-bar, .cky-consent-container, [id*="cookie" i], [class*="cookie" i], [id*="consent" i], [class*="consent" i], [aria-label*="cookie" i]';
  const visible = (b) => { const r = b.getBoundingClientRect(); const cs = getComputedStyle(b); return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const txt = (b) => (b.innerText || b.value || b.getAttribute('aria-label') || '').trim();
  const buttons = [];
  deepAll(document, SCOPE).forEach(sc => deepAll(sc, 'button, a[role=button], [role=button], input[type=button]').forEach(b => buttons.push(b)));
  const pick = buttons.find(b => visible(b) && REJECT.test(txt(b))) || buttons.find(b => visible(b) && ACCEPT.test(txt(b)));
  if (pick) { pick.click(); return txt(pick); }
  return null;
}
"""

# Finds a button on a contact page that reveals a hidden form (modal / accordion) and marks it.
FIND_FORM_OPENER_JS = r"""
() => {
""" + _DEEP + r"""
  const RE = /^(send (us )?(a |your )?(message|inquiry|enquiry)|contact (us|sales|our team)|get in touch|request (a )?(call|callback|consultation|information|info)|message us|submit (an )?(inquiry|enquiry|request)|let'?s talk|talk to (us|sales|an expert)|write to us)$/i;
  const norm = (b) => (b.innerText || b.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ');
  const cands = deepAll(document, 'button, [role=button], a[href^="#"], a:not([href]), summary, [data-toggle], [data-bs-toggle], [aria-controls]')
    .filter(b => { const r = b.getBoundingClientRect(); return r.width > 0 && r.height > 0 && !b.closest('nav, header, footer'); })
    .filter(b => RE.test(norm(b)));
  if (!cands.length) return null;
  cands[0].setAttribute('data-raven-opener', '1');
  return norm(cands[0]).slice(0, 60);
}
"""
