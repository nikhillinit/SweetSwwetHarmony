/**
 * Field Manual view — iframe wrapper with sidebar TOC and scroll bridge.
 * Route: #/manual and #/manual/pXX
 */
import { getHashParams } from '../router.js';

// Tool registry (mirrors shared/tools.ts — kept inline to avoid TS import)
const TOOLS = [
  { num: 1,  slug: "sale-to-be-made",   title: "Sale to Be Made",         pageId: "p03", category: "prepare",  level: "L0" },
  { num: 2,  slug: "call-router",        title: "Call Router",             pageId: "p05", category: "prepare",  level: "L0" },
  { num: 3,  slug: "walk-and-talk",      title: "Walk & Talk",             pageId: "p06", category: "activate", level: "L0" },
  { num: 4,  slug: "network-discipline", title: "Network Discipline",      pageId: "p07", category: "prepare",  level: "L1" },
  { num: 5,  slug: "bullseye",           title: "Bullseye & First 5",      pageId: "p08", category: "prepare",  level: "L0" },
  { num: 6,  slug: "competitive-map",    title: "Competitive Map",         pageId: "p09", category: "prepare",  level: "L1" },
  { num: 7,  slug: "personas",           title: "Personas",                pageId: "p10", category: "qualify",  level: "L1" },
  { num: 8,  slug: "prospecting",        title: "Prospecting",             pageId: "p11", category: "prepare",  level: "L0" },
  { num: 9,  slug: "written-outreach",   title: "Written Outreach",        pageId: "p12", category: "prepare",  level: "L1" },
  { num: 10, slug: "story-vault",        title: "Story Vault",             pageId: "p16", category: "activate", level: "L0" },
  { num: 11, slug: "ask-and-listen",     title: "Ask & Listen",            pageId: "p18", category: "qualify",  level: "L1" },
  { num: 12, slug: "objections",         title: "Skepticism & Objections", pageId: "p19", category: "qualify",  level: "L2" },
  { num: 13, slug: "visual-impact",      title: "Visual Impact",           pageId: "p20", category: "activate", level: "L0" },
  { num: 14, slug: "progress-report",    title: "Progress Report",         pageId: "p21", category: "report",   level: "L1" },
  { num: 15, slug: "weekly-cadence",     title: "Weekly Cadence",          pageId: "p22", category: "follow-up",level: "L0" },
];

const EXTRA_PAGES = [
  { pageId: "p02", title: "15 Tools Overview",         category: "overview" },
  { pageId: "p04", title: "Vitamins & Painkillers",    category: "prepare" },
  { pageId: "p13", title: "Ten Rep Rules",             category: "overview" },
  { pageId: "p14", title: "Field Entry",               category: "activate" },
  { pageId: "p15", title: "Trial Discipline",          category: "activate" },
  { pageId: "p17", title: "Story Library",             category: "activate" },
  { pageId: "p23", title: "Manager Coaching",          category: "report" },
  { pageId: "p24", title: "Visual Frameworks",         category: "overview" },
  { pageId: "p25", title: "Clean Activation Threshold",category: "activate" },
  { pageId: "p26", title: "6 Operating Cards",         category: "overview" },
  { pageId: "p27", title: "Objection Matrix",          category: "qualify" },
  { pageId: "p28", title: "Source · Claim · Evidence", category: "prepare" },
  { pageId: "p29", title: "Healthcare · Label First",  category: "qualify" },
  { pageId: "p30", title: "Quick Reference & Close",   category: "overview" },
];

const CATEGORY_COLORS = {
  overview:    { bg: "rgba(155, 162, 170, 0.15)", accent: "#9BA2AA" },
  prepare:     { bg: "rgba(76, 154, 255, 0.12)",  accent: "#4C9AFF" },
  qualify:     { bg: "rgba(185, 163, 251, 0.12)", accent: "#B9A3FB" },
  activate:    { bg: "rgba(45, 178, 153, 0.12)",  accent: "#2DB299" },
  "follow-up": { bg: "rgba(247, 184, 77, 0.12)",  accent: "#F7B84D" },
  report:      { bg: "rgba(52, 211, 153, 0.12)",  accent: "#34D399" },
};

const LEVEL_LABELS = { L0: "Core", L1: "Advanced", L2: "Expert" };

export async function mount(container, params) {
  container.innerHTML = '';

  // --- Header ---
  const header = document.createElement('div');
  header.className = 'view-header';
  header.style.cssText = 'flex-wrap: wrap; gap: var(--space-3);';

  const titleWrap = document.createElement('div');
  const title = document.createElement('h2');
  title.className = 'view-title';
  title.textContent = 'Field Manual';
  const subtitle = document.createElement('p');
  subtitle.style.cssText = 'color: var(--color-text-muted); font-size: var(--text-sm); margin-top: 2px;';
  subtitle.textContent = 'Restless v4.2 · 15 Tools · 30 Pages';
  titleWrap.appendChild(title);
  titleWrap.appendChild(subtitle);
  header.appendChild(titleWrap);

  // Search box
  const searchWrap = document.createElement('div');
  searchWrap.style.cssText = 'position: relative; flex: 0 0 220px;';
  const searchIcon = document.createElement('span');
  searchIcon.innerHTML = `<svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2" style="position:absolute;left:10px;top:50%;transform:translateY(-50%);color:var(--color-text-muted)"><circle cx="9" cy="9" r="6"/><path d="M15 15l-3.5-3.5"/></svg>`;
  const searchInput = document.createElement('input');
  searchInput.type = 'text';
  searchInput.placeholder = 'Search tools…';
  searchInput.style.cssText = `
    width: 100%; background: var(--color-surface); border: 1px solid var(--color-border);
    border-radius: var(--radius-md); padding: 7px 12px 7px 32px;
    color: var(--color-text-primary); font-family: var(--font-body); font-size: var(--text-sm);
    outline: none; transition: border-color var(--duration-fast);
  `;
  searchInput.addEventListener('focus', () => { searchInput.style.borderColor = 'var(--color-primary)'; });
  searchInput.addEventListener('blur',  () => { searchInput.style.borderColor = 'var(--color-border)'; });
  searchWrap.appendChild(searchIcon);
  searchWrap.appendChild(searchInput);
  header.appendChild(searchWrap);
  container.appendChild(header);

  // --- Layout: sidebar + iframe ---
  const layout = document.createElement('div');
  layout.style.cssText = `
    display: grid;
    grid-template-columns: 240px 1fr;
    gap: var(--space-4);
    height: calc(100vh - 160px);
    min-height: 500px;
  `;

  // --- TOC Sidebar ---
  const toc = document.createElement('div');
  toc.style.cssText = `
    background: var(--color-surface);
    border: 1px solid rgba(73, 80, 87, 0.5);
    border-radius: var(--radius-lg);
    overflow-y: auto;
    padding: var(--space-3) 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  `;

  // TOC section label
  const makeSectionLabel = (text) => {
    const lbl = document.createElement('div');
    lbl.style.cssText = `
      font-family: var(--font-mono); font-size: 9px; letter-spacing: .22em;
      color: var(--color-text-muted); text-transform: uppercase;
      padding: var(--space-3) var(--space-4) var(--space-1);
      margin-top: var(--space-2);
    `;
    lbl.textContent = text;
    return lbl;
  };

  // TOC entry
  const makeTocEntry = (pageId, label, category, meta) => {
    const colors = CATEGORY_COLORS[category] || CATEGORY_COLORS.overview;
    const btn = document.createElement('button');
    btn.dataset.pageId = pageId;
    btn.style.cssText = `
      display: flex; align-items: center; gap: var(--space-2);
      width: 100%; text-align: left; padding: 6px var(--space-4);
      background: transparent; border: none; cursor: pointer;
      color: var(--color-text-secondary); font-family: var(--font-body);
      font-size: var(--text-sm); line-height: 1.3;
      transition: background var(--duration-fast), color var(--duration-fast);
      border-radius: 0;
    `;
    btn.addEventListener('mouseenter', () => {
      btn.style.background = colors.bg;
      btn.style.color = 'var(--color-text-primary)';
    });
    btn.addEventListener('mouseleave', () => {
      if (btn.dataset.active !== 'true') {
        btn.style.background = 'transparent';
        btn.style.color = 'var(--color-text-secondary)';
      }
    });

    const dot = document.createElement('span');
    dot.style.cssText = `
      width: 6px; height: 6px; border-radius: 50%;
      background: ${colors.accent}; flex-shrink: 0;
    `;
    btn.appendChild(dot);

    const textWrap = document.createElement('span');
    textWrap.style.cssText = 'flex: 1; min-width: 0;';
    const labelEl = document.createElement('span');
    labelEl.style.cssText = 'display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;';
    labelEl.textContent = label;
    textWrap.appendChild(labelEl);
    if (meta) {
      const metaEl = document.createElement('span');
      metaEl.style.cssText = `display: block; font-size: 10px; color: var(--color-text-muted); font-family: var(--font-mono); letter-spacing: .08em;`;
      metaEl.textContent = meta;
      textWrap.appendChild(metaEl);
    }
    btn.appendChild(textWrap);

    btn.addEventListener('click', () => scrollManualTo(pageId, btn));
    return btn;
  };

  // Build TOC
  toc.appendChild(makeSectionLabel('Overview'));
  toc.appendChild(makeTocEntry('p02', '15 Tools Overview', 'overview', null));

  toc.appendChild(makeSectionLabel('15 Tools'));
  TOOLS.forEach(t => {
    const entry = makeTocEntry(t.pageId, `${t.num}. ${t.title}`, t.category, `${LEVEL_LABELS[t.level]} · ${t.category}`);
    toc.appendChild(entry);
  });

  toc.appendChild(makeSectionLabel('Reference'));
  EXTRA_PAGES.filter(p => !['p02'].includes(p.pageId) && !TOOLS.find(t => t.pageId === p.pageId)).forEach(p => {
    toc.appendChild(makeTocEntry(p.pageId, p.title, p.category, null));
  });

  // --- iframe ---
  const iframeWrap = document.createElement('div');
  iframeWrap.style.cssText = `
    background: var(--color-surface);
    border: 1px solid rgba(73, 80, 87, 0.5);
    border-radius: var(--radius-lg);
    overflow: hidden;
    position: relative;
  `;

  const loadingOverlay = document.createElement('div');
  loadingOverlay.style.cssText = `
    position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
    background: var(--color-surface); z-index: 2; flex-direction: column; gap: var(--space-4);
  `;
  const spinner = document.createElement('div');
  spinner.className = 'spinner';
  spinner.style.cssText = `
    width: 32px; height: 32px; border: 3px solid rgba(45,178,153,0.2);
    border-top-color: var(--color-primary); border-radius: 50%;
    animation: spin 0.8s linear infinite;
  `;
  const spinStyle = document.createElement('style');
  spinStyle.textContent = '@keyframes spin { to { transform: rotate(360deg); } }';
  document.head.appendChild(spinStyle);
  const loadingText = document.createElement('p');
  loadingText.style.cssText = 'color: var(--color-text-muted); font-size: var(--text-sm);';
  loadingText.textContent = 'Loading Field Manual…';
  loadingOverlay.appendChild(spinner);
  loadingOverlay.appendChild(loadingText);
  iframeWrap.appendChild(loadingOverlay);

  const iframe = document.createElement('iframe');
  iframe.src = 'manual/v4_2.html';
  iframe.title = 'Restless Field Manual v4.2';
  iframe.setAttribute('sandbox', 'allow-same-origin allow-scripts');
  iframe.style.cssText = 'width: 100%; height: 100%; border: none; display: block;';
  iframe.setAttribute('aria-label', 'Restless Field Manual v4.2');
  iframe.addEventListener('load', () => {
    loadingOverlay.style.opacity = '0';
    loadingOverlay.style.transition = 'opacity 0.3s';
    setTimeout(() => { loadingOverlay.style.display = 'none'; }, 300);

    // If a pageId was requested, scroll to it
    const targetPage = params?.pageId || window.location.hash.split('/manual/')[1];
    if (targetPage) {
      scrollManualTo(targetPage, null);
    }
  });
  iframeWrap.appendChild(iframe);

  // Scroll bridge function
  function scrollManualTo(pageId, activeBtn) {
    // Update active state in TOC
    toc.querySelectorAll('button[data-page-id]').forEach(b => {
      b.dataset.active = 'false';
      b.style.background = 'transparent';
      b.style.color = 'var(--color-text-secondary)';
    });
    if (activeBtn) {
      activeBtn.dataset.active = 'true';
      const colors = CATEGORY_COLORS[activeBtn.dataset.category] || CATEGORY_COLORS.overview;
      activeBtn.style.background = colors.bg;
      activeBtn.style.color = 'var(--color-text-primary)';
    } else {
      const btn = toc.querySelector(`button[data-page-id="${pageId}"]`);
      if (btn) {
        btn.dataset.active = 'true';
        const cat = btn.dataset.category || 'overview';
        const colors = CATEGORY_COLORS[cat] || CATEGORY_COLORS.overview;
        btn.style.background = colors.bg;
        btn.style.color = 'var(--color-text-primary)';
        btn.scrollIntoView({ block: 'nearest' });
      }
    }
    // Post message to iframe
    if (iframe.contentWindow) {
      iframe.contentWindow.postMessage({ type: 'scrollToPage', pageId }, window.location.origin);
    }
  }

  // Search filter
  searchInput.addEventListener('input', () => {
    const q = searchInput.value.toLowerCase();
    toc.querySelectorAll('button[data-page-id]').forEach(btn => {
      const text = btn.textContent.toLowerCase();
      btn.style.display = (!q || text.includes(q)) ? 'flex' : 'none';
    });
  });

  layout.appendChild(toc);
  layout.appendChild(iframeWrap);
  container.appendChild(layout);

  // Responsive: stack on narrow viewports
  const mq = window.matchMedia('(max-width: 768px)');
  const applyMq = (e) => {
    if (e.matches) {
      layout.style.gridTemplateColumns = '1fr';
      layout.style.height = 'auto';
      toc.style.maxHeight = '200px';
    } else {
      layout.style.gridTemplateColumns = '240px 1fr';
      layout.style.height = 'calc(100vh - 160px)';
      toc.style.maxHeight = '';
    }
  };
  applyMq(mq);
  mq.addEventListener('change', applyMq);

  // Cleanup
  return () => {
    mq.removeEventListener('change', applyMq);
    spinStyle.remove();
  };
}
