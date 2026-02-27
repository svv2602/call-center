/**
 * Focus trap utility for modals and drawers.
 * Keeps Tab/Shift+Tab cycling within a container.
 */

const FOCUSABLE = [
    'a[href]',
    'button:not(:disabled)',
    'input:not(:disabled)',
    'select:not(:disabled)',
    'textarea:not(:disabled)',
    '[tabindex]:not([tabindex="-1"])',
].join(', ');

/**
 * Trap focus within a container element.
 * @param {HTMLElement} container - The element to trap focus within
 * @returns {Function} cleanup function — call to release trap and restore focus
 */
export function trapFocus(container) {
    const previouslyFocused = document.activeElement;

    function getFocusable() {
        return [...container.querySelectorAll(FOCUSABLE)].filter(
            el => el.offsetParent !== null // visible only
        );
    }

    function handleKeydown(e) {
        if (e.key !== 'Tab') return;

        const focusable = getFocusable();
        if (focusable.length === 0) return;

        const first = focusable[0];
        const last = focusable[focusable.length - 1];

        if (e.shiftKey) {
            if (document.activeElement === first) {
                e.preventDefault();
                last.focus();
            }
        } else {
            if (document.activeElement === last) {
                e.preventDefault();
                first.focus();
            }
        }
    }

    container.addEventListener('keydown', handleKeydown);

    // Focus first focusable element (prefer input, fallback to close button)
    requestAnimationFrame(() => {
        const focusable = getFocusable();
        const firstInput = focusable.find(el => el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT');
        (firstInput || focusable[0])?.focus();
    });

    // Return cleanup function
    return function release() {
        container.removeEventListener('keydown', handleKeydown);
        if (previouslyFocused && previouslyFocused.focus) {
            previouslyFocused.focus();
        }
    };
}
