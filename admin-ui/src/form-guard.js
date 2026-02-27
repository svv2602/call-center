/**
 * Unsaved changes guard.
 *
 * Tracks "dirty" modals/forms — warns when user navigates away or closes
 * the browser tab with unsaved edits.
 *
 * Usage:
 *   import { watchForm, markClean, hasDirtyForms } from './form-guard.js';
 *   // Start tracking inputs inside a modal after it opens:
 *   watchForm('tenantModal');
 *   // After successful save:
 *   markClean('tenantModal');
 */
import { t } from './i18n.js';

/** Set of modal IDs with unsaved changes */
const _dirty = new Set();

/** Cleanup functions keyed by modal id */
const _cleanups = new Map();

/**
 * Start watching input/change events on fields inside a modal.
 * Any user edit marks the form as dirty.
 * @param {string} modalId
 */
export function watchForm(modalId) {
    // Prevent double-watch
    if (_cleanups.has(modalId)) return;
    const el = document.getElementById(modalId);
    if (!el) return;

    function onInput() { _dirty.add(modalId); }

    el.addEventListener('input', onInput);
    el.addEventListener('change', onInput);

    _cleanups.set(modalId, () => {
        el.removeEventListener('input', onInput);
        el.removeEventListener('change', onInput);
    });
}

/**
 * Mark a form as clean (no unsaved changes).
 * Call after a successful save or when closing a modal intentionally.
 * @param {string} modalId
 */
export function markClean(modalId) {
    _dirty.delete(modalId);
    const cleanup = _cleanups.get(modalId);
    if (cleanup) {
        cleanup();
        _cleanups.delete(modalId);
    }
}

/**
 * Check if a specific form or any form has unsaved changes.
 * @param {string} [modalId] - specific modal; omit to check all
 * @returns {boolean}
 */
export function hasDirtyForms(modalId) {
    if (modalId) return _dirty.has(modalId);
    return _dirty.size > 0;
}

/**
 * If any modal has unsaved changes, show a confirm dialog.
 * Returns true if safe to proceed (no dirty forms or user confirmed).
 * @returns {boolean}
 */
export function confirmIfDirty() {
    if (_dirty.size === 0) return true;
    return window.confirm(t('common.unsavedChanges'));
}

// Warn on tab/window close
window.addEventListener('beforeunload', (e) => {
    if (_dirty.size > 0) {
        e.preventDefault();
        // Modern browsers ignore custom text but require returnValue to be set
        e.returnValue = '';
    }
});
