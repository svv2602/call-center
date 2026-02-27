import ru from './translations/ru.js';
import en from './translations/en.js';

const STORAGE_KEY = 'admin_lang';
const LANGS = { ru, en };
let currentLang = 'ru';

export function initLang() {
    currentLang = localStorage.getItem(STORAGE_KEY) || 'ru';
    document.documentElement.lang = currentLang;
    const langLabel = document.getElementById('langLabel');
    if (langLabel) langLabel.textContent = currentLang === 'ru' ? 'EN' : 'RU';
}

const _isDev = typeof window !== 'undefined' && window.location?.hostname === 'localhost';

export function t(key, params) {
    let str = LANGS[currentLang]?.[key] ?? LANGS['ru']?.[key];
    if (str === undefined) {
        if (_isDev) console.warn(`[i18n] missing key: ${key}`);
        str = _isDev ? `[${key}]` : '';
    }
    if (params) {
        for (const [k, v] of Object.entries(params))
            str = str.replace(`{${k}}`, v);
    }
    return str;
}

/**
 * Pluralization helper.
 * Russian: 3 forms (one, few, many). English: 2 forms (one, other).
 *
 * Usage:
 *   plural(1, 'звонок', 'звонка', 'звонков') → 'звонок'
 *   plural(3, 'звонок', 'звонка', 'звонков') → 'звонка'
 *   plural(5, 'звонок', 'звонка', 'звонков') → 'звонков'
 *   plural(1, 'call', 'calls') → 'call'  (English, 2 args)
 *
 * @param {number} n
 * @param {string} one   - form for 1 (Russian: 1 звонок)
 * @param {string} few   - form for 2-4 (Russian: 2 звонка), or "other" for English
 * @param {string} [many] - form for 5+ (Russian: 5 звонков); if omitted → uses `few`
 * @returns {string}
 */
export function plural(n, one, few, many) {
    if (!many) many = few; // English: only 2 forms
    const abs = Math.abs(n);
    const mod10 = abs % 10;
    const mod100 = abs % 100;
    if (currentLang === 'en') {
        return abs === 1 ? one : few;
    }
    // Russian pluralization rules
    if (mod10 === 1 && mod100 !== 11) return one;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
    return many;
}

export function toggleLang() {
    currentLang = currentLang === 'ru' ? 'en' : 'ru';
    localStorage.setItem(STORAGE_KEY, currentLang);
    document.documentElement.lang = currentLang;
    translateStaticDOM();
    const langLabel = document.getElementById('langLabel');
    if (langLabel) langLabel.textContent = currentLang === 'ru' ? 'EN' : 'RU';
    window.dispatchEvent(new CustomEvent('langchange'));
}

export function translateStaticDOM() {
    document.querySelectorAll('[data-i18n]').forEach(el => {
        el.textContent = t(el.dataset.i18n);
    });
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
        el.placeholder = t(el.dataset.i18nPlaceholder);
    });
    document.querySelectorAll('[data-i18n-aria]').forEach(el => {
        el.setAttribute('aria-label', t(el.dataset.i18nAria));
    });
    document.querySelectorAll('[data-i18n-title]').forEach(el => {
        el.title = t(el.dataset.i18nTitle);
    });
}

export function getLocale() {
    return currentLang === 'ru' ? 'ru-RU' : 'en-US';
}

export function getLang() {
    return currentLang;
}
