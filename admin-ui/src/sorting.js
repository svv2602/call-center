// Shared table sorting utility.
// Adds click-to-sort on <th data-sortable> columns.
// Sorts visible rows in-place (DOM-based, no data reload).

const ARROW_UP = '\u2009\u25B2';   // thin space + ▲
const ARROW_DOWN = '\u2009\u25BC'; // thin space + ▼

/**
 * Make a <table> sortable by clicking column headers.
 * Only <th> elements with the `data-sortable` attribute become clickable.
 * Handles paired rows (data row + detail/expand row) by keeping them together.
 * Skips tables with rowspan grouping to avoid breaking layouts.
 *
 * @param {HTMLTableElement|string} tableOrId — table element or its id
 */
export function makeSortable(tableOrId) {
    const table = typeof tableOrId === 'string'
        ? document.getElementById(tableOrId)
        : tableOrId;
    if (!table) return;

    const thead = table.querySelector('thead');
    const tbody = table.querySelector('tbody');
    if (!thead || !tbody) return;

    const ths = Array.from(thead.querySelectorAll('th'));

    ths.forEach((th, colIndex) => {
        if (!th.hasAttribute('data-sortable')) return;

        // Guard against duplicate listeners when makeSortable is called
        // repeatedly on the same static <thead> (e.g. calls pagination, operators auto-refresh)
        if (th.dataset.sortInit) return;
        th.dataset.sortInit = '1';

        th.style.cursor = 'pointer';
        th.style.userSelect = 'none';

        th.addEventListener('click', () => {
            sortByColumn(tbody, ths, th, colIndex);
        });
    });
}

function sortByColumn(tbody, ths, activeTh, colIndex) {
    // Determine direction
    const prev = activeTh.dataset.sortDir;
    const dir = prev === 'asc' ? 'desc' : 'asc';

    // Reset all headers
    ths.forEach(h => {
        delete h.dataset.sortDir;
        const arrow = h.querySelector('.sort-arrow');
        if (arrow) arrow.textContent = '';
    });

    activeTh.dataset.sortDir = dir;

    // Ensure arrow span exists
    let arrow = activeTh.querySelector('.sort-arrow');
    if (!arrow) {
        arrow = document.createElement('span');
        arrow.className = 'sort-arrow';
        arrow.style.opacity = '0.6';
        arrow.style.fontSize = '0.65em';
        activeTh.appendChild(arrow);
    }
    arrow.textContent = dir === 'asc' ? ARROW_UP : ARROW_DOWN;

    const allRows = Array.from(tbody.querySelectorAll('tr'));

    // Skip tables with rowspan grouping
    if (allRows.some(r => Array.from(r.cells).some(c => c.rowSpan > 1))) return;

    // Group rows: data rows have enough cells for colIndex, detail rows don't
    // (detail rows typically have a single td with colspan)
    const thCount = ths.length;
    const groups = [];
    for (const row of allRows) {
        const isDetailRow = row.cells.length < thCount;
        if (isDetailRow && groups.length > 0) {
            groups[groups.length - 1].detail.push(row);
        } else {
            groups.push({ data: row, detail: [] });
        }
    }

    groups.sort((a, b) => {
        const cellA = a.data.cells[colIndex];
        const cellB = b.data.cells[colIndex];
        if (!cellA || !cellB) return 0;

        // Use data-sort-value if present (e.g. raw ISO dates), otherwise textContent
        const textA = (cellA.dataset.sortValue ?? cellA.textContent ?? '').trim();
        const textB = (cellB.dataset.sortValue ?? cellB.textContent ?? '').trim();

        // Try numeric comparison.
        // Number() requires the FULL string to be numeric (unlike parseFloat which
        // stops at the first non-numeric char — e.g. parseFloat("2026-02-18") = 2026,
        // which breaks ISO date sorting). Strip currency/unit chars first.
        const strippedA = textA.replace(/[^0-9.\-]/g, '');
        const strippedB = textB.replace(/[^0-9.\-]/g, '');
        const numA = Number(strippedA);
        const numB = Number(strippedB);

        if (strippedA !== '' && strippedB !== '' && !isNaN(numA) && !isNaN(numB)) {
            return dir === 'asc' ? numA - numB : numB - numA;
        }

        // Fallback to string comparison (works for ISO dates, text, etc.)
        const cmp = textA.localeCompare(textB, undefined, { numeric: true, sensitivity: 'base' });
        return dir === 'asc' ? cmp : -cmp;
    });

    // Re-append in sorted order (moves DOM nodes)
    const fragment = document.createDocumentFragment();
    for (const group of groups) {
        fragment.appendChild(group.data);
        for (const detailRow of group.detail) {
            fragment.appendChild(detailRow);
        }
    }
    tbody.appendChild(fragment);
}
