/**
 * Custom confirm dialog — replaces browser `confirm()`.
 * Returns a Promise<boolean> (true = confirmed, false = cancelled).
 *
 * Usage:
 *   import { confirmAction } from './confirm.js';
 *   if (await confirmAction(t('knowledge.deleteConfirm', {title}))) { ... }
 */
import { t } from './i18n.js';
import { trapFocus } from './focus-trap.js';

let _activeResolve = null;
let _releaseTrap = null;

function _getOrCreate() {
    let overlay = document.getElementById('confirmDialog');
    if (overlay) return overlay;

    overlay = document.createElement('div');
    overlay.id = 'confirmDialog';
    overlay.className = 'modal-overlay';
    overlay.setAttribute('role', 'alertdialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.innerHTML = `
        <div class="modal-box max-w-md">
            <p id="confirmMessage" class="text-sm text-neutral-700 dark:text-neutral-300 mb-6"></p>
            <div class="flex justify-end gap-3">
                <button id="confirmCancelBtn"
                    class="px-4 py-2 rounded-lg text-sm font-medium bg-neutral-200 dark:bg-neutral-700 text-neutral-700 dark:text-neutral-300 hover:bg-neutral-300 dark:hover:bg-neutral-600 cursor-pointer">
                </button>
                <button id="confirmOkBtn"
                    class="px-4 py-2 rounded-lg text-sm font-medium text-white bg-red-600 hover:bg-red-700 cursor-pointer">
                </button>
            </div>
        </div>`;
    document.body.appendChild(overlay);

    document.getElementById('confirmCancelBtn').addEventListener('click', () => _close(false));
    document.getElementById('confirmOkBtn').addEventListener('click', () => _close(true));
    overlay.addEventListener('click', (e) => { if (e.target === overlay) _close(false); });
    overlay.addEventListener('keydown', (e) => { if (e.key === 'Escape') _close(false); });

    return overlay;
}

function _close(result) {
    const overlay = document.getElementById('confirmDialog');
    if (overlay) overlay.classList.remove('show');
    if (_releaseTrap) { _releaseTrap(); _releaseTrap = null; }
    if (_activeResolve) { _activeResolve(result); _activeResolve = null; }
}

/**
 * Show a styled confirm dialog.
 * @param {string} message - question to display
 * @param {object} [opts]
 * @param {string} [opts.confirmText] - confirm button text (default: common.delete)
 * @param {string} [opts.cancelText] - cancel button text (default: common.cancel)
 * @param {boolean} [opts.danger=true] - if true, confirm button is red
 * @returns {Promise<boolean>}
 */
export function confirmAction(message, opts = {}) {
    const overlay = _getOrCreate();
    document.getElementById('confirmMessage').textContent = message;

    const okBtn = document.getElementById('confirmOkBtn');
    const cancelBtn = document.getElementById('confirmCancelBtn');
    okBtn.textContent = opts.confirmText || t('common.delete');
    cancelBtn.textContent = opts.cancelText || t('common.cancel');

    if (opts.danger === false) {
        okBtn.className = okBtn.className.replace('bg-red-600 hover:bg-red-700', 'bg-blue-600 hover:bg-blue-700');
    } else {
        okBtn.className = okBtn.className.replace('bg-blue-600 hover:bg-blue-700', 'bg-red-600 hover:bg-red-700');
    }

    overlay.classList.add('show');

    // Focus the cancel button (safety-first)
    _releaseTrap = trapFocus(overlay);
    requestAnimationFrame(() => cancelBtn.focus());

    return new Promise(resolve => { _activeResolve = resolve; });
}
