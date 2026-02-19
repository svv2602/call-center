import * as tw from './tw.js';
import { t } from './i18n.js';

const PAGE_SIZE = 25;

/**
 * Render pagination controls into a container element.
 * @param {Object} opts
 * @param {string} opts.containerId - ID of the DOM container to render into
 * @param {number} opts.total - Total number of records
 * @param {number} opts.offset - Current offset
 * @param {number} [opts.pageSize=25] - Records per page
 * @param {function(number):void} opts.onPage - Callback with new offset
 */
export function renderPagination({ containerId, total, offset, pageSize = PAGE_SIZE, onPage }) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const ps = pageSize || PAGE_SIZE;
    const totalPages = Math.max(1, Math.ceil(total / ps));
    const currentPage = Math.floor(offset / ps);

    if (totalPages <= 1) {
        container.innerHTML = '';
        return;
    }

    // Build page numbers with ellipsis
    const pages = [];
    const addPage = (n) => { if (!pages.includes(n)) pages.push(n); };

    addPage(0);
    addPage(totalPages - 1);
    for (let i = Math.max(0, currentPage - 1); i <= Math.min(totalPages - 1, currentPage + 1); i++) {
        addPage(i);
    }
    pages.sort((a, b) => a - b);

    // Build HTML
    const pageInfo = t('common.pageInfo', { current: currentPage + 1, total: totalPages });
    let html = `<div class="${tw.paginationWrap}">`;

    // Prev button
    html += `<button class="${tw.pageBtn}" data-page-offset="${Math.max(0, (currentPage - 1) * ps)}" ${currentPage === 0 ? 'disabled style="opacity:0.4;cursor:default"' : ''}>«</button>`;

    let lastPage = -1;
    for (const p of pages) {
        if (lastPage >= 0 && p - lastPage > 1) {
            html += `<span class="px-2 py-1.5 text-sm text-neutral-400">…</span>`;
        }
        const isActive = p === currentPage;
        html += `<button class="${tw.pageBtn}${isActive ? ' active' : ''}" data-page-offset="${p * ps}">${p + 1}</button>`;
        lastPage = p;
    }

    // Next button
    html += `<button class="${tw.pageBtn}" data-page-offset="${Math.min((totalPages - 1) * ps, (currentPage + 1) * ps)}" ${currentPage >= totalPages - 1 ? 'disabled style="opacity:0.4;cursor:default"' : ''}>»</button>`;

    html += `<span class="px-2 py-1.5 text-xs text-neutral-500 dark:text-neutral-400 self-center">${pageInfo}</span>`;
    html += `</div>`;

    container.innerHTML = html;

    // Attach click handlers via event delegation
    container.querySelectorAll('[data-page-offset]').forEach(btn => {
        if (btn.disabled) return;
        btn.addEventListener('click', () => {
            const newOffset = parseInt(btn.dataset.pageOffset, 10);
            if (!isNaN(newOffset) && onPage) onPage(newOffset);
        });
    });
}

/**
 * Build URLSearchParams from a filter config.
 * Reads values from DOM elements by ID.
 * @param {Object} opts
 * @param {number} [opts.limit=25] - Limit
 * @param {number} [opts.offset=0] - Offset
 * @param {Object.<string, string>} [opts.filters={}] - Map of param name → DOM element ID
 * @returns {URLSearchParams}
 */
export function buildParams({ limit = PAGE_SIZE, offset = 0, filters = {} } = {}) {
    const params = new URLSearchParams();
    params.set('limit', String(limit));
    params.set('offset', String(offset));

    for (const [paramName, elementId] of Object.entries(filters)) {
        const el = document.getElementById(elementId);
        if (!el) continue;
        const val = el.value?.trim();
        if (val) params.set(paramName, val);
    }

    return params;
}
