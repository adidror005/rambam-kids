/* Static, dependency-free reader. Works both from local http.server and GitHub Pages /docs. */
'use strict';

const q = (selector) => document.querySelector(selector);
const state = {
  manifest: null,
  book: null,
  chapter: null,
  chapterData: null,
  mode: 'kids_hebrew',
  niqqud: true,
  fontSize: 21,
  search: '',
};
const LABELS = {
  original_hebrew: 'Original Hebrew',
  kids_hebrew: 'עברית קלה · Kids Hebrew',
  kids_english: 'Kids English',
};

function withoutMarks(s) {
  // Remove Hebrew diacritics, but not Hebrew punctuation (maqaf/sof pasuq/etc.).
  return String(s || '').normalize('NFD').replace(/[\u0591-\u05BD\u05BF\u05C1\u05C2\u05C4\u05C5\u05C7]/g, '');
}
function currentChapterEntry() {
  return state.book?.chapters.find((c) => c.number === state.chapter);
}
function currentChapterIndex() {
  return state.book?.chapters.findIndex((c) => c.number === state.chapter) ?? -1;
}
function sourceUrl(ref) {
  // Keep chapter and segment numbers separate from the book title.
  const matched = ref.match(/^(.+?)\s+(\d+)(?::(\d+))?$/);
  const normalized = matched
    ? `${matched[1].replaceAll(' ', '_')}.${matched[2]}${matched[3] ? '.' + matched[3] : ''}`
    : ref.replaceAll(' ', '_').replaceAll(':', '.');
  return 'https://www.sefaria.org/' + encodeURIComponent(normalized);
}
function updateUrl(anchor = '') {
  const u = new URL(location.href);
  if (state.book) u.searchParams.set('book', state.book.id);
  if (state.chapter) u.searchParams.set('chapter', String(state.chapter));
  u.searchParams.set('mode', state.mode);
  u.hash = anchor;
  history.replaceState(null, '', u);
}
function option(parent, value, label) {
  const el = document.createElement('option');
  el.value = value;
  el.textContent = label;
  parent.appendChild(el);
}
function setupSelectors() {
  const books = state.manifest.books;
  const bookSelect = q('#book-select');
  bookSelect.replaceChildren();
  for (const book of books) option(bookSelect, book.id, `${book.title}${book.he_title ? ' · ' + book.he_title : ''}`);
  bookSelect.value = state.book.id;
  bookSelect.onchange = () => {
    state.book = books.find((book) => book.id === bookSelect.value);
    state.chapter = state.book.chapters[0]?.number ?? null;
    fillChapterSelect();
    loadChapter();
  };
  fillChapterSelect();
}
function fillChapterSelect() {
  const chapters = state.book?.chapters || [];
  const select = q('#chapter-select');
  select.replaceChildren();
  for (const ch of chapters) option(select, String(ch.number), `Chapter ${ch.number} · פרק ${ch.number}`);
  select.value = String(state.chapter);
  select.onchange = () => {
    state.chapter = Number(select.value);
    loadChapter();
  };
}
function setupControls() {
  document.querySelectorAll('[data-mode]').forEach((button) => {
    button.onclick = () => {
      state.mode = button.dataset.mode;
      updateModeButtons();
      render();
      updateUrl();
    };
  });
  q('#niqqud').onchange = (e) => { state.niqqud = e.target.checked; render(); };
  q('#smaller').onclick = () => { state.fontSize = Math.max(16, state.fontSize - 2); render(); };
  q('#larger').onclick = () => { state.fontSize = Math.min(31, state.fontSize + 2); render(); };
  q('#text-search').oninput = (e) => { state.search = e.target.value.trim(); renderSegments(); };
  q('#previous-chapter').onclick = () => moveChapter(-1);
  q('#next-chapter').onclick = () => moveChapter(1);
  updateModeButtons();
}
function updateModeButtons() {
  document.querySelectorAll('[data-mode]').forEach((b) => {
    const selected = b.dataset.mode === state.mode;
    b.classList.toggle('active', selected);
    b.setAttribute('aria-pressed', String(selected));
  });
}
function moveChapter(direction) {
  const idx = currentChapterIndex() + direction;
  const next = state.book.chapters[idx];
  if (!next) return;
  state.chapter = next.number;
  q('#chapter-select').value = String(state.chapter);
  loadChapter();
  window.scrollTo({ top: q('.reader-shell').offsetTop - 15, behavior: 'smooth' });
}
async function loadChapter() {
  const entry = currentChapterEntry();
  if (!entry) return;
  state.chapterData = null;
  q('#reader-status').textContent = 'Loading chapter…';
  q('#segments').replaceChildren();
  try {
    // A chapter is a standalone JSON file. No other chapters are fetched.
    const response = await fetch(entry.path, { cache: 'no-cache' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const doc = await response.json();
    if (doc.ref !== entry.ref) throw new Error('Chapter ref does not match the manifest');
    state.chapterData = doc;
    q('#reader-status').textContent = '';
    render();
    updateUrl();
    const anchor = location.hash.slice(1);
    if (/^halacha-\d+$/.test(anchor)) {
      requestAnimationFrame(() => document.getElementById(anchor)?.scrollIntoView());
    }
  } catch (err) {
    q('#reader-status').textContent = `This chapter could not be loaded (${err.message}). Check that its JSON file exists.`;
  }
}
function pill(field, numerator, denominator) {
  const el = document.createElement('span');
  el.className = 'status-tag';
  const strong = document.createElement('b');
  strong.textContent = `${numerator}/${denominator}`;
  el.append(strong, ` ${LABELS[field]}`);
  return el;
}
function render() {
  if (!state.chapterData) return;
  const data = state.chapterData;
  const entry = currentChapterEntry();
  q('#reader-title').textContent = state.book.title;
  q('#reader-subtitle').textContent = `${state.book.he_title || 'Mishneh Torah'} · Chapter ${state.chapter} · ${data.version_title || 'Original text edition unspecified'}`;
  const original = entry.counts.original_hebrew || 0;
  q('#chapter-progress').textContent = `${original} original laws loaded · chapter ${state.chapter}`;
  const available = q('#version-progress');
  available.replaceChildren(
    pill('original_hebrew', original, entry.segments),
    pill('kids_hebrew', entry.counts.kids_hebrew || 0, entry.segments),
    pill('kids_english', entry.counts.kids_english || 0, entry.segments),
  );
  document.documentElement.style.setProperty('--reader-font', `${state.fontSize}px`);
  const idx = currentChapterIndex();
  q('#previous-chapter').disabled = idx <= 0;
  q('#next-chapter').disabled = idx >= state.book.chapters.length - 1;
  q('#sefaria-chapter').href = sourceUrl(data.ref);
  renderSegments();
}
function makePassage(field, text) {
  const p = document.createElement('p');
  p.className = `passage ${field === 'kids_english' ? 'english-text' : 'hebrew-text'}`;
  const isHebrew = field !== 'kids_english';
  p.lang = isHebrew ? 'he' : 'en';
  p.dir = isHebrew ? 'rtl' : 'ltr';
  p.textContent = (isHebrew && !state.niqqud) ? withoutMarks(text) : text;
  return p;
}
function missingContent(field, seg) {
  const box = document.createElement('div');
  box.className = 'missing';
  const title = document.createElement('b');
  title.textContent = `${LABELS[field]} · Coming soon`;
  const hint = document.createElement('span');
  hint.textContent = 'This version has not been added yet. You can still read the original in another tab.';
  box.append(title, hint);
  if (field !== 'original_hebrew' && !seg.original_hebrew) {
    hint.textContent = 'Both the original text and this reading version have not been added yet.';
  }
  return box;
}
function pane(field, seg) {
  const box = document.createElement('div');
  box.className = 'passage-pane';
  const label = document.createElement('span');
  label.className = 'passage-label';
  label.textContent = LABELS[field];
  box.appendChild(label);
  box.appendChild(seg[field] ? makePassage(field, seg[field]) : missingContent(field, seg));
  return box;
}
function addSegment(number, seg) {
  const article = document.createElement('article');
  article.id = `halacha-${number}`;
  article.className = 'segment';
  const header = document.createElement('div');
  header.className = 'segment-top';
  const title = document.createElement('h3');
  title.textContent = `Halacha ${number} · הלכה ${number}`;
  const source = document.createElement('a');
  source.href = sourceUrl(seg.ref || `${state.chapterData.ref}:${number}`);
  source.target = '_blank';
  source.rel = 'noopener noreferrer';
  source.textContent = 'Read on Sefaria ↗';
  header.append(title, source);
  article.appendChild(header);
  if (state.mode === 'compare') {
    const grid = document.createElement('div');
    grid.className = 'comparison';
    ['original_hebrew', 'kids_hebrew', 'kids_english'].forEach((field) => grid.appendChild(pane(field, seg)));
    article.appendChild(grid);
  } else {
    const field = state.mode;
    article.appendChild(seg[field] ? makePassage(field, seg[field]) : missingContent(field, seg));
    if (!seg[field] && field !== 'original_hebrew' && seg.original_hebrew) {
      const details = document.createElement('details');
      details.style.marginTop = '10px';
      const summary = document.createElement('summary');
      summary.textContent = 'Show original Hebrew while this version is missing';
      details.append(summary, makePassage('original_hebrew', seg.original_hebrew));
      article.appendChild(details);
    }
  }
  if (seg.review_status === 'draft') {
    const note = document.createElement('div');
    note.className = 'segment-note';
    note.textContent = 'AI draft — not yet reviewed';
    article.appendChild(note);
  }
  return article;
}
function renderSegments() {
  if (!state.chapterData) return;
  const target = q('#segments');
  const items = Object.entries(state.chapterData.segments || {}).sort((a, b) => Number(a[0]) - Number(b[0]));
  const needle = withoutMarks(state.search).toLowerCase();
  let shown = 0;
  const fragment = document.createDocumentFragment();
  for (const [n, seg] of items) {
    if (needle && !Object.values(seg).some((value) => typeof value === 'string' && withoutMarks(value).toLowerCase().includes(needle))) continue;
    fragment.appendChild(addSegment(n, seg));
    shown++;
  }
  target.replaceChildren(fragment);
  q('#reader-status').textContent = shown ? '' : 'No matching halachot in this chapter.';
}
async function main() {
  setupControls();
  try {
    const result = await fetch('data/manifest.json', { cache: 'no-cache' });
    if (!result.ok) throw new Error(`HTTP ${result.status}`);
    state.manifest = await result.json();
    if (!state.manifest.books.length) throw new Error('The library manifest is empty');
    const params = new URLSearchParams(location.search);
    state.book = state.manifest.books.find((b) => b.id === params.get('book')) || state.manifest.books[0];
    const requestedChapter = Number(params.get('chapter'));
    state.chapter = state.book.chapters.find((c) => c.number === requestedChapter)?.number ?? state.book.chapters[0]?.number;
    if (['kids_hebrew', 'kids_english', 'original_hebrew', 'compare'].includes(params.get('mode'))) {
      state.mode = params.get('mode');
      updateModeButtons();
    }
    setupSelectors();
    await loadChapter();
  } catch (err) {
    q('#reader-status').textContent = `Library could not be loaded: ${err.message}. Serve the docs folder over HTTP (do not double-click index.html).`;
    q('#chapter-progress').textContent = 'No chapters yet';
  }
}
main();
