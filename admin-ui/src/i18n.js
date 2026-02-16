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

export function t(key, params) {
    let str = LANGS[currentLang]?.[key] ?? LANGS['ru']?.[key] ?? key;
    if (params) {
        for (const [k, v] of Object.entries(params))
            str = str.replace(`{${k}}`, v);
    }
    return str;
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
}

export function getLocale() {
    return currentLang === 'ru' ? 'ru-RU' : 'en-US';
}

export function getLang() {
    return currentLang;
}
