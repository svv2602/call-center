import { t, getLang } from './i18n.js';
import { HELP_PAGES } from './help-content.js';
import { trapFocus } from './focus-trap.js';

let overlayEl = null;
let contentEl = null;
let searchEl = null;
let currentPageId = null;
let releaseTrap = null;
let _searchTimer = null;

export function initHelp() {
    overlayEl = document.createElement('div');
    overlayEl.className = 'help-overlay';
    overlayEl.innerHTML = `
        <div class="help-drawer" role="complementary" aria-label="Help">
            <div class="flex items-center justify-between px-5 py-4 border-b border-neutral-200 dark:border-neutral-700">
                <h2 class="text-base font-semibold text-neutral-900 dark:text-neutral-50 help-drawer-title"></h2>
                <button onclick="window._app.closeHelp()" class="text-neutral-400 hover:text-neutral-600 dark:hover:text-neutral-200" aria-label="Close">
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
                </button>
            </div>
            <div class="px-5 pt-3 pb-2">
                <input type="text" id="helpSearch" class="w-full px-3 py-1.5 text-sm rounded-lg border border-neutral-300 dark:border-neutral-600 bg-white dark:bg-neutral-800 text-neutral-800 dark:text-neutral-200 placeholder-neutral-400" data-i18n-placeholder="help.searchPlaceholder" placeholder="Search help...">
            </div>
            <div class="help-drawer-content flex-1 overflow-y-auto px-5 py-4 text-sm text-neutral-700 dark:text-neutral-300 space-y-4"></div>
        </div>`;
    overlayEl.addEventListener('click', (e) => {
        if (e.target === overlayEl) closeHelp();
    });
    document.body.appendChild(overlayEl);
    contentEl = overlayEl.querySelector('.help-drawer-content');
    searchEl = overlayEl.querySelector('#helpSearch');
    searchEl.addEventListener('input', () => {
        clearTimeout(_searchTimer);
        _searchTimer = setTimeout(() => {
            if (currentPageId) renderContent(currentPageId, searchEl.value.trim());
        }, 300);
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && overlayEl.classList.contains('open')) {
            closeHelp();
        }
    });

    window.addEventListener('langchange', () => {
        if (currentPageId && overlayEl.classList.contains('open')) {
            renderContent(currentPageId);
        }
    });
}

export function openHelp(pageId) {
    if (!HELP_PAGES[pageId]) return;
    currentPageId = pageId;
    renderContent(pageId);
    overlayEl.classList.add('open');
    // Trap focus inside the drawer
    const drawer = overlayEl.querySelector('.help-drawer');
    releaseTrap = trapFocus(drawer);
}

export function closeHelp() {
    overlayEl.classList.remove('open');
    currentPageId = null;
    if (releaseTrap) {
        releaseTrap();
        releaseTrap = null;
    }
}

function _highlight(text, query) {
    if (!query) return text;
    const esc = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return text.replace(new RegExp(`(${esc})`, 'gi'), '<mark>$1</mark>');
}

function renderContent(pageId, query = '') {
    const page = HELP_PAGES[pageId];
    overlayEl.querySelector('.help-drawer-title').textContent = t(page.titleKey);
    const q = query.toLowerCase();

    let html = `<div class="text-neutral-600 dark:text-neutral-400">${_highlight(t(page.overviewKey), query)}</div>`;

    for (const section of page.sections) {
        const title = t(section.titleKey);
        const content = t(section.contentKey);
        // If searching, skip non-matching sections
        if (q && !title.toLowerCase().includes(q) && !content.toLowerCase().includes(q)) continue;

        const open = q ? ' open' : '';
        html += `
        <details class="help-section border border-neutral-200 dark:border-neutral-700 rounded-lg"${open}>
            <summary class="flex items-center gap-2 px-4 py-3 text-sm font-medium text-neutral-800 dark:text-neutral-200 hover:bg-neutral-50 dark:hover:bg-neutral-800 rounded-lg">
                <svg class="help-chevron w-4 h-4 text-neutral-400 transition-transform duration-200 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/></svg>
                ${_highlight(title, query)}
            </summary>
            <div class="px-4 pb-3 text-sm text-neutral-600 dark:text-neutral-400 [&_ul]:list-disc [&_ul]:pl-5 [&_ul]:mt-1 [&_ul]:space-y-1 [&_code]:bg-neutral-100 [&_code]:dark:bg-neutral-800 [&_code]:px-1 [&_code]:rounded [&_code]:text-xs">${_highlight(content, query)}</div>
        </details>`;
    }

    if (page.tipsKey) {
        const tips = t(page.tipsKey);
        if (!q || tips.toLowerCase().includes(q)) {
            html += `
            <div class="bg-blue-50 dark:bg-blue-950 border border-blue-200 dark:border-blue-800 rounded-lg p-4">
                <div class="text-sm font-medium text-blue-800 dark:text-blue-300 mb-2">${t('help.tipsTitle')}</div>
                <div class="text-sm text-blue-700 dark:text-blue-400 [&_ul]:list-disc [&_ul]:pl-5 [&_ul]:space-y-1">${_highlight(tips, query)}</div>
            </div>`;
        }
    }

    contentEl.innerHTML = html;
}
