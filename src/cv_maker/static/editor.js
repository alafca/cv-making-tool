const yamlEl = document.getElementById('yaml');
const previewEl = document.getElementById('preview');
const cvEl = document.getElementById('cv');
const colorEl = document.getElementById('color');
const styleEl = document.getElementById('style');
const photoEl = document.getElementById('photo');
const statusEl = document.getElementById('status');
const fieldsView = document.getElementById('fieldsView');
const btnYaml = document.getElementById('btnYaml');
const btnFields = document.getElementById('btnFields');

// The textarea is the visible pane on first paint, so start in 'yaml' and
// switch to fields after the initial load. Keeping this in sync with the DOM
// matters: getPayload() overwrites the textarea from the fields when
// currentView is 'fields'.
let currentView = 'yaml';
let dirty = false;

// Styles the server offers, from /api/config. The choice lives in the CV's
// meta.style, so both the toolbar select and the Meta field show the same
// value and either one writes it into the YAML.
let styles = [{ id: 'classic', label: 'Classic', detail: '' }];
const DEFAULT_STYLE = 'classic';

function knownStyle(id) {
    return styles.some(s => s.id === id) ? id : DEFAULT_STYLE;
}

function styleOptions(selected) {
    const current = knownStyle(selected);
    return styles.map(s =>
        `<option value="${escAttr(s.id)}" title="${escAttr(s.detail || '')}"` +
        `${s.id === current ? ' selected' : ''}>${esc(s.label)}</option>`
    ).join('');
}

// Keep the toolbar select in step with whatever YAML is about to render.
function syncStyleSelect(yamlText) {
    try {
        const data = jsyaml.load(yamlText) || {};
        styleEl.value = knownStyle((data.meta || {}).style);
    } catch (e) { /* invalid YAML: leave the select alone */ }
}
let loadedCv = null;
// Modification time of the file as loaded; sent with each save so the server
// can refuse to overwrite a newer version written by another tab.
let loadedMtime = null;
let defaultCv = null;

function markDirty() { dirty = true; }

let flashTimer;
function flash(msg, err) {
    statusEl.textContent = msg;
    statusEl.classList.toggle('error', !!err);
    statusEl.classList.add('show');
    clearTimeout(flashTimer);
    if (err) {
        // An error stays until dismissed: it names what to fix, and a message
        // that fades in 2.6 seconds cannot be read, let alone acted on.
        const close = document.createElement('button');
        close.className = 'toast-close';
        close.setAttribute('aria-label', 'Dismiss message');
        close.textContent = '\u00d7';
        close.addEventListener('click', () => statusEl.classList.remove('show'));
        statusEl.appendChild(close);
        return;
    }
    // Keep the text while it fades so the box does not blank mid-transition.
    flashTimer = setTimeout(() => statusEl.classList.remove('show'), 2600);
}

function getPayload() {
    if (currentView === 'fields') {
        yamlEl.value = fieldsToYaml();
    }
    return {
        yaml: yamlEl.value,
        cv: cvEl.value || null,
        show_photo: photoEl.checked,
        primary_color: colorEl.value,
    };
}

function downloadName(ext) {
    return (cvEl.value || 'cv') + '.' + ext;
}

let debounceTimer;
function schedulePreview() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(refreshPreview, 500);
}

let previewSeq = 0;
previewEl.onload = () => { fitPreview(); attachPreviewClicks(); };

async function refreshPreview() {
    const seq = ++previewSeq;
    try {
        const payload = getPayload();
        syncStyleSelect(payload.yaml);
        const res = await fetch('/api/preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const html = await res.text();
        if (seq !== previewSeq) return; // a newer request is in flight
        previewEl.srcdoc = html;
        updatePageCount(res.headers.get('X-Page-Count'));
    } catch (e) {
        flash('Unable to render the preview: ' + e.message, true);
    }
}

const pageCountEl = document.getElementById('pageCount');
function updatePageCount(value) {
    const n = parseInt(value, 10);
    if (!n) { pageCountEl.textContent = ''; return; }
    pageCountEl.textContent = n === 1 ? '1 page' : n + ' pages';
    pageCountEl.style.color = n === 1 ? '#81c784' : '#ffb74d';
}

// --- Toggle View ---
function toggleView(mode) {
    if (mode === currentView) return;

    if (mode === 'fields') {
        try {
            const data = jsyaml.load(yamlEl.value) || {};
            yamlToFields(data);
        } catch (e) {
            flash('Invalid YAML: ' + e.message, true);
            return;
        }
        yamlEl.style.display = 'none';
        fieldsView.style.display = 'block';
        document.getElementById('linkHint').style.display = '';
        btnFields.classList.add('active');
        btnYaml.classList.remove('active');
        btnFields.setAttribute('aria-pressed', 'true');
        btnYaml.setAttribute('aria-pressed', 'false');
    } else {
        yamlEl.value = fieldsToYaml();
        yamlEl.style.display = '';
        fieldsView.style.display = 'none';
        document.getElementById('linkHint').style.display = 'none';
        btnYaml.classList.add('active');
        btnFields.classList.remove('active');
        btnYaml.setAttribute('aria-pressed', 'true');
        btnFields.setAttribute('aria-pressed', 'false');
    }
    currentView = mode;
}

// --- Fields → YAML ---
// The last full document parsed into the fields view. fieldsToYaml() only
// knows about the sections it renders, so anything else in the file (a section
// added by hand, or one this page is too old to know about) is carried over
// from here instead of being silently dropped on the next save.
let sourceDoc = {};

function fieldsToYaml() {
    const obj = Object.assign({}, sourceDoc);
    obj.name = gv('f-name');
    obj.headline = gv('f-headline');

    obj.contact = {
        email: gv('f-email'),
        phone: gv('f-phone'),
        location: gv('f-location'),
        linkedin: { text: gv('f-linkedin-text'), url: gv('f-linkedin-url') },
        github: { text: gv('f-github-text'), url: gv('f-github-url') },
    };

    obj.meta = {
        photo: gv('f-photo'),
        lang: gv('f-lang'),
        // Classic is the default, so it is left out rather than written.
        style: gv('f-style') === DEFAULT_STYLE ? '' : gv('f-style'),
    };

    obj.summary = gv('f-summary');

    // Experience
    obj.experience = [];
    fieldsView.querySelectorAll('.exp-item').forEach(el => {
        const bullets = el.querySelector('.exp-bullets').value
            .split('\n').map(s => s.trim()).filter(Boolean);
        obj.experience.push({
            title: el.querySelector('.exp-title').value,
            company: el.querySelector('.exp-company').value,
            location: el.querySelector('.exp-location').value,
            dates: el.querySelector('.exp-dates').value,
            description: el.querySelector('.exp-desc').value,
            bullets,
        });
    });

    // Education
    obj.education = [];
    fieldsView.querySelectorAll('.edu-item').forEach(el => {
        obj.education.push({
            institution: el.querySelector('.edu-inst').value,
            degree: el.querySelector('.edu-degree').value,
            dates: el.querySelector('.edu-dates').value,
        });
    });

    // Publications
    obj.publications = [];
    fieldsView.querySelectorAll('.pub-item').forEach(el => {
        obj.publications.push({
            title: el.querySelector('.pub-title').value,
            venue: el.querySelector('.pub-venue').value,
            year: el.querySelector('.pub-year').value,
            url: el.querySelector('.pub-url').value,
            note: el.querySelector('.pub-note').value,
        });
    });

    // Skills
    obj.skills = [];
    fieldsView.querySelectorAll('.skill-item').forEach(el => {
        obj.skills.push({
            category: el.querySelector('.skill-cat').value,
            entries: el.querySelector('.skill-entries').value,
        });
    });

    // Languages
    obj.languages = [];
    fieldsView.querySelectorAll('.lang-item').forEach(el => {
        obj.languages.push({
            name: el.querySelector('.lang-name').value,
            level: el.querySelector('.lang-level').value,
        });
    });

    // Certifications
    const certs = [];
    fieldsView.querySelectorAll('.cert-input').forEach(el => {
        if (el.value.trim()) certs.push(el.value.trim());
    });
    obj.certifications = certs;

    return jsyaml.dump(prune(obj) || {}, { lineWidth: -1, quotingType: '"', forceQuotes: false });
}

// Drop empty strings, arrays and objects recursively so blanks (e.g. an unused
// LinkedIn text/url pair) don't end up in the YAML and render in the CV.
function prune(v) {
    if (Array.isArray(v)) {
        const arr = v.map(prune).filter(x => x !== undefined);
        return arr.length ? arr : undefined;
    }
    if (v && typeof v === 'object') {
        const out = {};
        for (const [k, val] of Object.entries(v)) {
            const p = prune(val);
            if (p !== undefined) out[k] = p;
        }
        return Object.keys(out).length ? out : undefined;
    }
    if (typeof v === 'string' && v.trim() === '') return undefined;
    return v;
}

function gv(id) { return document.getElementById(id)?.value || ''; }

// --- YAML → Fields ---
function yamlToFields(data) {
    sourceDoc = data || {};
    const c = data.contact || {};
    const m = data.meta || {};

    let html = '';

    // Basic
    html += section('Basic', `
        ${field('Name', 'text', 'f-name', data.name || '')}
        ${field('Headline', 'text', 'f-headline', data.headline || '')}
    `);

    // Contact
    const li = (typeof c.linkedin === 'object' && c.linkedin) ? c.linkedin : { text: c.linkedin || '', url: c.linkedin ? 'https://linkedin.com/in/' + c.linkedin : '' };
    const gh = (typeof c.github === 'object' && c.github) ? c.github : { text: c.github || '', url: c.github ? 'https://github.com/' + c.github : '' };
    html += section('Contact', `
        ${field('Email', 'text', 'f-email', c.email || '')}
        ${field('Phone', 'text', 'f-phone', c.phone || '')}
        ${field('Location', 'text', 'f-location', c.location || '')}
        ${field('LinkedIn text', 'text', 'f-linkedin-text', li.text || '')}
        ${field('LinkedIn URL', 'text', 'f-linkedin-url', li.url || '')}
        ${field('GitHub text', 'text', 'f-github-text', gh.text || '')}
        ${field('GitHub URL', 'text', 'f-github-url', gh.url || '')}
    `);

    // Meta
    html += section('Meta', `
        ${field('Photo path', 'text', 'f-photo', m.photo || '')}
        <label class="field-label" for="f-lang">Language</label>
        <select id="f-lang" class="field-input">
            <option value="es" ${m.lang === 'es' ? 'selected' : ''}>ES</option>
            <option value="en" ${m.lang === 'en' ? 'selected' : ''}>EN</option>
        </select>
        <label class="field-label" for="f-style">Style</label>
        <select id="f-style" class="field-input">${styleOptions(m.style)}</select>
    `);

    // Summary
    html += section('Summary', `
        ${area('Summary', '', data.summary || '', 4, 'f-summary')}
    `);

    // Experience
    const expItems = (data.experience || []).map(expCard).join('');
    html += section('Experience', `
        <div id="exp-list">${expItems}</div>
        <button type="button" class="add-btn" onclick="addExp()">+ Add experience</button>
    `);

    // Education
    const eduItems = (data.education || []).map(eduCard).join('');
    html += section('Education', `
        <div id="edu-list">${eduItems}</div>
        <button type="button" class="add-btn" onclick="addEdu()">+ Add education</button>
    `);

    // Publications
    const pubItems = (data.publications || []).map(pubCard).join('');
    html += section('Publications', `
        <div id="pub-list">${pubItems}</div>
        <button type="button" class="add-btn" onclick="addPub()">+ Add publication</button>
    `);

    // Skills
    const skillItems = (data.skills || []).map(skillCard).join('');
    html += section('Skills', `
        <div id="skill-list">${skillItems}</div>
        <button type="button" class="add-btn" onclick="addSkill()">+ Add skill</button>
    `);

    // Languages
    const langItems = (data.languages || []).map(langCard).join('');
    html += section('Languages', `
        <div id="lang-list">${langItems}</div>
        <button type="button" class="add-btn" onclick="addLang()">+ Add language</button>
    `);

    // Certifications
    const certItems = (data.certifications || []).map(certInput).join('');
    html += section('Certifications', `
        <div id="cert-list">${certItems}</div>
        <button type="button" class="add-btn" onclick="addCert()">+ Add certification</button>
    `);

    fieldsView.innerHTML = html;
    fieldsView.querySelectorAll('input, textarea, select').forEach(el => {
        el.addEventListener('input', onFieldInput);
    });
}

function onFieldInput() {
    yamlEl.value = fieldsToYaml();
    markDirty();
    schedulePreview();
}

// --- HTML helpers ---
function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function escAttr(s) { return s.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

function section(title, inner) {
    return `<div class="section"><h2 class="section-title">${title}</h2>${inner}</div>`;
}

// Every control gets an id so its label can point at it; the repeatable cards
// have no natural id, so they draw one from a counter.
let fieldSeq = 0;
function fieldId(id) { return id || ('f-auto-' + (++fieldSeq)); }

function field(label, type, id, value, cls) {
    const fid = fieldId(id);
    return `<label class="field-label" for="${fid}">${label}</label>` +
        `<input type="${type}" id="${fid}"${cls ? ` class="${cls}"` : ''} value="${escAttr(value)}">`;
}

function area(label, cls, value, rows, id) {
    const fid = fieldId(id);
    return `<label class="field-label" for="${fid}">${label}</label>` +
        `<textarea id="${fid}"${cls ? ` class="${cls}"` : ''} rows="${rows}">${esc(value)}</textarea>`;
}

function itemActions(noun) {
    return `<div class="item-actions">
        <button type="button" class="move-btn" onclick="moveItem(this,-1)" aria-label="Move ${noun} up">&#9650;</button>
        <button type="button" class="move-btn" onclick="moveItem(this,1)" aria-label="Move ${noun} down">&#9660;</button>
        <button type="button" class="remove-btn" onclick="this.closest('.repeatable-item').remove(); onFieldInput();" aria-label="Remove ${noun}">&times;</button>
    </div>`;
}

function moveItem(btn, dir) {
    const item = btn.closest('.repeatable-item');
    const sibling = dir === -1 ? item.previousElementSibling : item.nextElementSibling;
    if (!sibling || !sibling.classList.contains('repeatable-item')) return;
    if (dir === -1) item.parentElement.insertBefore(item, sibling);
    else item.parentElement.insertBefore(sibling, item);
    onFieldInput();
}

function expCard(e) {
    e = e || {};
    const bullets = (e.bullets || []).join('\n');
    return `<div class="repeatable-item exp-item">
        ${itemActions('experience entry')}
        ${field('Title', 'text', '', e.title || '', 'exp-title')}
        ${field('Company', 'text', '', e.company || '', 'exp-company')}
        ${field('Location', 'text', '', e.location || '', 'exp-location')}
        ${field('Dates', 'text', '', e.dates || '', 'exp-dates')}
        ${area('Description', 'exp-desc', e.description || '', 2)}
        ${area('Bullets (one per line)', 'exp-bullets', bullets, 3)}
    </div>`;
}

function eduCard(e) {
    e = e || {};
    return `<div class="repeatable-item edu-item">
        ${itemActions('education entry')}
        ${field('Institution', 'text', '', e.institution || '', 'edu-inst')}
        ${field('Degree', 'text', '', e.degree || '', 'edu-degree')}
        ${field('Dates', 'text', '', e.dates || '', 'edu-dates')}
    </div>`;
}

function pubCard(e) {
    e = e || {};
    return `<div class="repeatable-item pub-item">
        ${itemActions('publication')}
        ${area('Title', 'pub-title', e.title || '', 2)}
        ${field('Venue (journal, conference, preprint server)', 'text', '', e.venue || '', 'pub-venue')}
        ${field('Year', 'text', '', e.year === undefined ? '' : String(e.year), 'pub-year')}
        ${field('URL', 'text', '', e.url || '', 'pub-url')}
        ${field('Note (for example co-author, preprint)', 'text', '', e.note || '', 'pub-note')}
    </div>`;
}

function skillCard(e) {
    e = e || {};
    return `<div class="repeatable-item skill-item">
        ${itemActions('skill group')}
        ${field('Category', 'text', '', e.category || '', 'skill-cat')}
        ${field('Entries', 'text', '', e.entries || '', 'skill-entries')}
    </div>`;
}

function langCard(e) {
    e = e || {};
    return `<div class="repeatable-item lang-item">
        ${itemActions('language')}
        ${field('Name', 'text', '', e.name || '', 'lang-name')}
        ${field('Level', 'text', '', e.level || '', 'lang-level')}
    </div>`;
}

function certInput(val) {
    return `<div class="repeatable-item" style="padding:8px 14px;">
        ${itemActions('certification')}
        <input type="text" class="cert-input" aria-label="Certification" value="${escAttr(val || '')}">
    </div>`;
}

// --- Add buttons ---
function addRepeatable(listId, cardFn) {
    const list = document.getElementById(listId);
    const tmp = document.createElement('div');
    tmp.innerHTML = cardFn({});
    const item = tmp.firstElementChild;
    list.appendChild(item);
    item.querySelectorAll('input, textarea, select').forEach(el => {
        el.addEventListener('input', onFieldInput);
    });
    item.querySelector('input, textarea')?.focus();
    onFieldInput();
}

function addExp() { addRepeatable('exp-list', expCard); }
function addEdu() { addRepeatable('edu-list', eduCard); }
function addPub() { addRepeatable('pub-list', pubCard); }
function addSkill() { addRepeatable('skill-list', skillCard); }
function addLang() { addRepeatable('lang-list', langCard); }
function addCert() { addRepeatable('cert-list', certInput); }

async function loadYaml(cv) {
    const res = await fetch('/api/yaml?cv=' + encodeURIComponent(cv));
    if (!res.ok) throw new Error(await errorDetail(res));
    const d = await res.json();
    yamlEl.value = d.yaml;
    dirty = false;
    loadedCv = cv;
    loadedMtime = d.mtime;
    if (currentView === 'fields') {
        try {
            const data = jsyaml.load(yamlEl.value) || {};
            yamlToFields(data);
        } catch (e) {
            flash('Invalid YAML: ' + e.message, true);
        }
    }
    refreshPreview();
}

// --- CV list ---
async function loadCvs(select) {
    const res = await fetch('/api/cvs');
    if (!res.ok) throw new Error(await errorDetail(res));
    const d = await res.json();
    const wanted = select || cvEl.value || d.default_cv;
    cvEl.innerHTML = d.cvs
        .map(name => `<option value="${escAttr(name)}">${esc(name)}</option>`)
        .join('');
    if ([...cvEl.options].some(o => o.value === wanted)) cvEl.value = wanted;
    // Deleting the startup CV is refused server-side; reflect that in the UI.
    document.getElementById('delCvBtn').disabled = (cvEl.value === defaultCv);
    return d;
}

// Confirm before throwing away edits, and roll the select back if declined.
function confirmDiscard(question, el, previous) {
    if (!dirty || confirm('You have unsaved changes. Discard them and ' + question)) return true;
    if (previous) el.value = previous;
    return false;
}

// Load config + CV list + YAML on start, then switch to the fields view.
(async () => {
    try {
        const cfg = await fetch('/api/config').then(r => r.json());
        defaultCv = cfg.default_cv;
        if (Array.isArray(cfg.styles) && cfg.styles.length) styles = cfg.styles;
        styleEl.innerHTML = styleOptions(DEFAULT_STYLE);
    } catch (e) { /* keep the select's default */ }
    try {
        await loadCvs(defaultCv);
        await loadYaml(cvEl.value);
        toggleView('fields');
    } catch (e) {
        flash('Unable to load the CV: ' + e.message, true);
    }
})();

yamlEl.addEventListener('input', () => { markDirty(); schedulePreview(); });
cvEl.addEventListener('change', async () => {
    if (!confirmDiscard('switch CV?', cvEl, loadedCv)) return;
    try {
        await loadYaml(cvEl.value);
        document.getElementById('delCvBtn').disabled = (cvEl.value === defaultCv);
    } catch (e) {
        flash('Unable to load the CV: ' + e.message, true);
    }
});

// --- New / Duplicate / Delete CV ---
async function createCv(copyFrom) {
    const label = copyFrom ? `Duplicate "${copyFrom}" as:` : 'Name for the new CV:';
    const suggested = copyFrom ? copyFrom + '-copy' : '';
    const name = (prompt(label + '\n(letters, digits, hyphens, underscores)', suggested) || '').trim();
    if (!name) return;
    if (!confirmDiscard('create a new CV?', cvEl, loadedCv)) return;
    try {
        const res = await fetch('/api/cvs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, copy_from: copyFrom || null }),
        });
        if (!res.ok) throw new Error(await errorDetail(res));
        const d = await res.json();
        await loadCvs(d.name);
        await loadYaml(cvEl.value);
        flash(copyFrom ? `Duplicated as "${d.name}"` : `Created "${d.name}"`);
    } catch (e) {
        flash('Unable to create the CV: ' + e.message, true);
    }
}

document.getElementById('newCvBtn').addEventListener('click', () => createCv(null));
document.getElementById('dupCvBtn').addEventListener('click', () => createCv(cvEl.value));

document.getElementById('delCvBtn').addEventListener('click', async () => {
    const name = cvEl.value;
    if (!name) return;
    if (!confirm(`Delete the CV "${name}"?\n\nA .bak copy is kept next to it.`)) return;
    try {
        const res = await fetch('/api/cvs/' + encodeURIComponent(name), { method: 'DELETE' });
        if (!res.ok) throw new Error(await errorDetail(res));
        dirty = false; // the file it came from is gone
        await loadCvs(defaultCv);
        await loadYaml(cvEl.value);
        flash(`Deleted "${name}"`);
    } catch (e) {
        flash('Unable to delete the CV: ' + e.message, true);
    }
});
colorEl.addEventListener('input', schedulePreview);

styleEl.innerHTML = styleOptions(DEFAULT_STYLE);
styleEl.addEventListener('change', () => {
    const id = knownStyle(styleEl.value);
    if (currentView === 'fields') {
        const field = document.getElementById('f-style');
        if (field) field.value = id;
        onFieldInput();
        return;
    }
    // YAML view: set meta.style in the document. Re-dumping reformats the
    // text the same way the fields view does on every edit.
    let data;
    try {
        data = jsyaml.load(yamlEl.value) || {};
    } catch (e) {
        flash('Invalid YAML: ' + e.message, true);
        syncStyleSelect(yamlEl.value);
        return;
    }
    data.meta = Object.assign({}, data.meta || {});
    if (id === DEFAULT_STYLE) delete data.meta.style; else data.meta.style = id;
    yamlEl.value = jsyaml.dump(prune(data) || {}, { lineWidth: -1, quotingType: '"', forceQuotes: false });
    markDirty();
    schedulePreview();
});
photoEl.addEventListener('change', refreshPreview);
window.addEventListener('beforeunload', (e) => {
    if (dirty) e.preventDefault();
});
function saveShortcut(e) {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault();
        document.getElementById('saveBtn').click();
    }
}
window.addEventListener('keydown', saveShortcut);

// Tab key inserts spaces in textarea
yamlEl.addEventListener('keydown', (e) => {
    if (e.key === 'Tab') {
        e.preventDefault();
        const start = yamlEl.selectionStart;
        const end = yamlEl.selectionEnd;
        yamlEl.value = yamlEl.value.substring(0, start) + '  ' + yamlEl.value.substring(end);
        yamlEl.selectionStart = yamlEl.selectionEnd = start + 2;
        markDirty();
        schedulePreview();
    }
});

async function errorDetail(res) {
    try {
        const d = await res.json();
        return d.detail || ('HTTP ' + res.status);
    } catch (e) {
        return 'HTTP ' + res.status;
    }
}

async function downloadFrom(endpoint, filename) {
    const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(getPayload()),
    });
    if (!res.ok) throw new Error(await errorDetail(res));
    const blob = await res.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
}

async function postSave(baseMtime) {
    return fetch('/api/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            yaml: getPayload().yaml,
            cv: cvEl.value || null,
            base_mtime: cvEl.value === loadedCv ? baseMtime : null,
        }),
    });
}

document.getElementById('saveBtn').addEventListener('click', async () => {
    try {
        let res = await postSave(loadedMtime);
        if (res.status === 409) {
            const overwrite = confirm(
                'This CV changed on disk after you opened it, probably in another tab.\n\n' +
                'OK overwrites the newer version with what is in this tab (a backup is kept).\n' +
                'Cancel leaves the file alone so you can reload it.'
            );
            if (!overwrite) {
                flash('Not saved: the file on disk is newer. Reload the CV to see it.', true);
                return;
            }
            res = await postSave(null);
        }
        if (!res.ok) throw new Error(await errorDetail(res));
        const saved = await res.json();
        loadedMtime = saved.mtime;
        loadedCv = cvEl.value;
        dirty = false;
        // Saving a CV that did not exist yet adds a file, so refresh the list.
        loadCvs(cvEl.value).catch(() => {});
        flash('Saved');
    } catch (e) {
        flash('Unable to save: ' + e.message, true);
    }
});

document.getElementById('pdfBtn').addEventListener('click', async () => {
    flash('Generating PDF...');
    try {
        await downloadFrom('/api/pdf', downloadName('pdf'));
        flash('PDF downloaded');
    } catch (e) {
        flash('Unable to generate the PDF: ' + e.message, true);
    }
});

document.getElementById('htmlBtn').addEventListener('click', async () => {
    try {
        await downloadFrom('/api/export-html', downloadName('html'));
        flash('HTML exported');
    } catch (e) {
        flash('Unable to export: ' + e.message, true);
    }
});
// --- Copy the CV as Markdown (for pasting into a chat with an AI) ---
async function copyText(text) {
    // navigator.clipboard needs a secure context; 127.0.0.1 counts, but fall
    // back to a hidden textarea so the button still works if it does not.
    if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return;
    }
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try {
        if (!document.execCommand('copy')) throw new Error('clipboard unavailable');
    } finally {
        document.body.removeChild(ta);
    }
}

document.getElementById('mdBtn').addEventListener('click', async () => {
    try {
        const res = await fetch('/api/markdown', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(getPayload()),
        });
        if (!res.ok) throw new Error(await errorDetail(res));
        const md = await res.text();
        await copyText(md);
        flash('Markdown copied to the clipboard');
    } catch (e) {
        flash('Unable to copy: ' + e.message, true);
    }
});

// --- ATS check ---
const atsPanel = document.getElementById('atsPanel');
const atsBody = document.getElementById('atsBody');
const atsScoreEl = document.getElementById('atsScore');

function scoreColour(score) {
    if (score >= 85) return '#81c784';
    if (score >= 60) return '#ffd54f';
    return '#ff8a80';
}

const atsScoresEl = document.getElementById('atsScores');

function scoreChip(label, score, title) {
    const shown = score === null || score === undefined
        ? '<span class="chip-score na">no score</span>'
        : `<span class="chip-score" style="color:${scoreColour(score)}">${score}/100</span>`;
    return `<div class="ats-chip" title="${escAttr(title || '')}">` +
        `<span class="chip-label">${esc(label)}</span>${shown}</div>`;
}

function breakdownTable(breakdown) {
    const rows = Object.entries(breakdown || {}).map(([k, v]) => {
        const val = (v && typeof v === 'object' && !Array.isArray(v))
            ? Object.entries(v).map(([a, b]) => `${a} ${b}`).join(', ')
            : Array.isArray(v) ? v.join(', ') : String(v);
        return `<tr><td>${esc(k.replace(/_/g, ' '))}</td><td>${esc(val)}</td></tr>`;
    });
    return rows.length ? `<table>${rows.join('')}</table>` : '';
}

function renderAtsReport(report) {
    atsScoreEl.textContent = report.score + '/100';
    atsScoreEl.style.color = scoreColour(report.score);

    const external = report.external || [];
    atsScoresEl.innerHTML = scoreChip('Built-in', report.score, "This app's own checks") +
        external.map(e => scoreChip(e.label, e.score, e.note)).join('');

    const findings = report.findings.length ? report.findings.map(f => `
        <div class="ats-finding sev-${escAttr(f.severity)}">
            <span class="tag">${esc(f.severity)} &middot; ${esc(f.check)}</span>
            <div class="msg">${esc(f.message)}</div>
            ${f.detail ? `<div class="det">${esc(f.detail)}</div>` : ''}
        </div>`).join('') : '<p>No findings.</p>';

    const others = external.map(e => `
        <div class="ats-external" data-tool="${escAttr(e.id)}">
            <h4>${esc(e.label)}: ${e.score === null ? 'no score' : e.score + '/100'}</h4>
            ${e.note ? `<div class="note">${esc(e.note)}</div>` : ''}
            ${breakdownTable(e.breakdown)}
            ${e.findings && e.findings.length ? `<ul>${e.findings.map(f => `<li>${esc(f)}</li>`).join('')}</ul>` : ''}
        </div>`).join('');

    atsBody.innerHTML = findings + others;
}

function positionAtsPanel() {
    const bar = document.querySelector('.toolbar').getBoundingClientRect();
    atsPanel.style.top = bar.bottom + 'px';
}
window.addEventListener('resize', () => {
    if (atsPanel.classList.contains('open')) positionAtsPanel();
});

function closeAtsPanel() {
    atsPanel.classList.remove('open');
    document.getElementById('atsBtn').focus();
}

async function runAtsCheck() {
    positionAtsPanel();
    const wasOpen = atsPanel.classList.contains('open');
    atsPanel.classList.add('open');
    // Focus lands on the panel's heading, so a keyboard user arrives inside it.
    if (!wasOpen) document.getElementById('atsTitle').focus();
    atsBody.innerHTML = '<p>Rendering the PDF and reading it back, then asking the other parsers...</p>';
    atsScoreEl.textContent = '';
    atsScoresEl.innerHTML = '';
    try {
        const payload = getPayload();
        const res = await fetch('/api/ats', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                yaml: payload.yaml,
                show_photo: payload.show_photo,
                job_description: document.getElementById('atsJd').value,
            }),
        });
        if (!res.ok) throw new Error(await errorDetail(res));
        renderAtsReport(await res.json());
    } catch (e) {
        atsBody.innerHTML = '';
        flash('ATS check failed: ' + e.message, true);
    }
}

document.getElementById('atsBtn').addEventListener('click', runAtsCheck);
document.getElementById('atsClose').addEventListener('click', closeAtsPanel);
window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && atsPanel.classList.contains('open')) closeAtsPanel();
});
// Re-run against the current text whenever a job description is pasted in.
document.getElementById('atsJd').addEventListener('change', () => {
    if (atsPanel.classList.contains('open')) runAtsCheck();
});

// --- Draggable splitter ---
(function() {
    const splitter = document.getElementById('splitter');
    const editorPane = document.querySelector('.editor-pane');
    const previewPane = document.getElementById('previewPane');
    const main = document.querySelector('.main');

    function setSplit(pct) {
        pct = Math.min(Math.max(pct, 10), 90);
        editorPane.style.flex = 'none';
        previewPane.style.flex = 'none';
        editorPane.style.width = pct + '%';
        previewPane.style.width = (100 - pct) + '%';
        splitter.setAttribute('aria-valuenow', String(Math.round(pct)));
        fitPreview();
    }

    function currentSplit() {
        return editorPane.getBoundingClientRect().width / main.getBoundingClientRect().width * 100;
    }

    // Keyboard path: arrow keys move the divider by 5% of the window.
    splitter.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowLeft') setSplit(currentSplit() - 5);
        else if (e.key === 'ArrowRight') setSplit(currentSplit() + 5);
        else if (e.key === 'Home') setSplit(10);
        else if (e.key === 'End') setSplit(90);
        else return;
        e.preventDefault();
    });

    splitter.addEventListener('pointerdown', (e) => {
        e.preventDefault();
        splitter.setPointerCapture(e.pointerId);
        splitter.classList.add('dragging');

        const onMove = (e) => {
            const rect = main.getBoundingClientRect();
            setSplit((e.clientX - rect.left) / rect.width * 100);
        };

        const onUp = (e) => {
            splitter.classList.remove('dragging');
            splitter.removeEventListener('pointermove', onMove);
            splitter.removeEventListener('pointerup', onUp);
        };

        splitter.addEventListener('pointermove', onMove);
        splitter.addEventListener('pointerup', onUp);
    });
})();

// --- Scale-to-fit A4 preview ---
function fitPreview() {
    const pane = document.getElementById('previewPane');
    const iframe = document.getElementById('preview');
    const pad = 40; // 20px padding each side
    const availW = pane.clientWidth - pad;
    const ifrW = iframe.offsetWidth;
    if (!ifrW) return;

    // Auto-size iframe height to its content
    try {
        const doc = iframe.contentDocument;
        if (doc && doc.body) {
            iframe.style.height = doc.documentElement.scrollHeight + 'px';
        }
    } catch (e) {}

    const scale = Math.min(availW / ifrW, 1);
    iframe.style.transform = 'scale(' + scale + ')';
    // Adjust the visible height so the pane scrollbar reflects scaled size
    const actualH = iframe.offsetHeight;
    iframe.style.marginBottom = (actualH * (scale - 1)) + 'px';
}
window.addEventListener('resize', fitPreview);

// --- Click-to-edit: clicking preview elements jumps to editor field ---
const REPEATABLE_MAP = {
    experience: { selector: '.exp-item', fields: { title: '.exp-title', company: '.exp-company', location: '.exp-location', dates: '.exp-dates', description: '.exp-desc', bullets: '.exp-bullets' } },
    education: { selector: '.edu-item', fields: { institution: '.edu-inst', degree: '.edu-degree', dates: '.edu-dates' } },
    publications: { selector: '.pub-item', fields: { title: '.pub-title', venue: '.pub-venue', year: '.pub-year', url: '.pub-url', note: '.pub-note' } },
    skills: { selector: '.skill-item', fields: { category: '.skill-cat', entries: '.skill-entries' } },
    languages: { selector: '.lang-item', fields: { name: '.lang-name', level: '.lang-level' } },
};
const SIMPLE_MAP = {
    name: 'f-name', headline: 'f-headline',
    email: 'f-email', phone: 'f-phone', location: 'f-location',
    linkedin: 'f-linkedin-text', github: 'f-github-text',
    summary: 'f-summary',
};

function resolveFieldByPath(path) {
    if (SIMPLE_MAP[path]) return document.getElementById(SIMPLE_MAP[path]);

    if (path.startsWith('certifications.')) {
        const idx = parseInt(path.split('.')[1], 10);
        return fieldsView.querySelectorAll('.cert-input')[idx] || null;
    }

    const parts = path.split('.');
    if (parts.length >= 3) {
        const [kind, idxStr, field] = parts;
        const cfg = REPEATABLE_MAP[kind];
        if (!cfg) return null;
        const item = fieldsView.querySelectorAll(cfg.selector)[parseInt(idxStr, 10)];
        if (!item) return null;
        return item.querySelector(cfg.fields[field]) || item.querySelector('input, textarea');
    }
    return null;
}

function focusEditorField(path) {
    if (currentView !== 'fields') toggleView('fields');
    const target = resolveFieldByPath(path);
    if (!target) return;
    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    target.focus();
    if (target.select) target.select();
    target.classList.remove('field-flash');
    void target.offsetWidth; // restart animation
    target.classList.add('field-flash');
}

function attachPreviewClicks() {
    const doc = previewEl.contentDocument;
    if (!doc) return;
    doc.addEventListener('keydown', saveShortcut); // Ctrl+S works with preview focused too

    doc.querySelectorAll('[data-edit]').forEach(el => {
        el.addEventListener('click', (e) => {
            // Allow real link clicks (anchor tags) to still navigate
            if (e.target.closest('a')) return;
            e.preventDefault();
            focusEditorField(el.getAttribute('data-edit'));
        });
    });
}
