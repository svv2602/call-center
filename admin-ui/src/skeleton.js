/**
 * Skeleton loader components for perceived-faster loading.
 */

const _PULSE = 'animate-pulse bg-neutral-200 dark:bg-neutral-700 rounded';

/**
 * Render a table skeleton (rows of rectangles).
 * @param {number} rows
 * @param {number} cols
 * @returns {string} HTML string
 */
export function skeletonTable(rows = 5, cols = 4) {
    const headerCells = Array.from({ length: cols }, () =>
        `<th class="px-4 py-3"><div class="${_PULSE} h-4 w-20"></div></th>`
    ).join('');

    const bodyRows = Array.from({ length: rows }, () => {
        const cells = Array.from({ length: cols }, () =>
            `<td class="px-4 py-3"><div class="${_PULSE} h-4 w-full"></div></td>`
        ).join('');
        return `<tr>${cells}</tr>`;
    }).join('');

    return `<table class="w-full"><thead><tr>${headerCells}</tr></thead><tbody>${bodyRows}</tbody></table>`;
}

/**
 * Render card skeletons (stat cards like dashboard).
 * @param {number} count
 * @returns {string} HTML string
 */
export function skeletonCards(count = 4) {
    return Array.from({ length: count }, () =>
        `<div class="rounded-xl border border-neutral-200 dark:border-neutral-700 p-6 text-center">
            <div class="${_PULSE} h-8 w-16 mx-auto mb-2"></div>
            <div class="${_PULSE} h-4 w-24 mx-auto"></div>
        </div>`
    ).join('');
}

/**
 * Render text line skeletons.
 * @param {number} lines
 * @returns {string} HTML string
 */
export function skeletonText(lines = 3) {
    return Array.from({ length: lines }, (_, i) => {
        const w = i === lines - 1 ? 'w-2/3' : 'w-full';
        return `<div class="${_PULSE} h-4 ${w} mb-2"></div>`;
    }).join('');
}
