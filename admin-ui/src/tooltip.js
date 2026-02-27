/**
 * Contextual tooltip utility.
 *
 * Usage in HTML (static):
 *   <label>Extensions <span data-tooltip="tooltip.tenants.extensions" class="tooltip-trigger">?</span></label>
 *
 * Usage in JS (dynamic):
 *   import { renderTooltip } from './tooltip.js';
 *   label.innerHTML += renderTooltip('tooltip.tenants.extensions');
 */
import { t } from './i18n.js';

/**
 * Render a tooltip trigger (info icon) that shows a popover on hover/focus.
 * @param {string} i18nKey - Translation key for tooltip content
 * @returns {string} HTML string for the tooltip trigger
 */
export function renderTooltip(i18nKey) {
    const text = t(i18nKey);
    if (!text) return '';
    return `<span class="tooltip-trigger" tabindex="0" role="button"
        aria-label="Info" data-tooltip="${i18nKey}">?</span>`;
}

/** Initialize tooltip event delegation. */
export function initTooltips() {
    let activePopover = null;

    function show(trigger) {
        hide();
        const key = trigger.dataset.tooltip;
        const text = t(key);
        if (!text) return;

        const popover = document.createElement('div');
        popover.className = 'tooltip-popover';
        popover.setAttribute('role', 'tooltip');
        popover.textContent = text;

        document.body.appendChild(popover);

        // Position near trigger
        const rect = trigger.getBoundingClientRect();
        popover.style.top = `${rect.bottom + 6 + window.scrollY}px`;
        popover.style.left = `${Math.max(8, rect.left + rect.width / 2 - 120)}px`;

        trigger.setAttribute('aria-describedby', 'tooltip-active');
        popover.id = 'tooltip-active';
        activePopover = popover;
    }

    function hide() {
        if (activePopover) {
            activePopover.remove();
            activePopover = null;
        }
        document.querySelectorAll('[aria-describedby="tooltip-active"]').forEach(
            el => el.removeAttribute('aria-describedby')
        );
    }

    document.addEventListener('mouseenter', e => {
        const trigger = e.target.closest?.('[data-tooltip]');
        if (trigger) show(trigger);
    }, true);

    document.addEventListener('mouseleave', e => {
        if (e.target.closest?.('[data-tooltip]')) hide();
    }, true);

    document.addEventListener('focusin', e => {
        const trigger = e.target.closest?.('[data-tooltip]');
        if (trigger) show(trigger);
    });

    document.addEventListener('focusout', e => {
        if (e.target.closest?.('[data-tooltip]')) hide();
    });
}
