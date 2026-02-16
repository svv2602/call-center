import { t } from './i18n.js';

const STORAGE_KEY = 'admin_theme';

const SUN_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>';
const MOON_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';

export function initTheme() {
    updateToggleUI();
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
        if (!localStorage.getItem(STORAGE_KEY)) {
            document.documentElement.classList.toggle('dark', e.matches);
            updateToggleUI();
        }
    });
}

export function toggleTheme() {
    const isDark = document.documentElement.classList.toggle('dark');
    localStorage.setItem(STORAGE_KEY, isDark ? 'dark' : 'light');
    updateToggleUI();
}

function updateToggleUI() {
    const isDark = document.documentElement.classList.contains('dark');
    const icon = document.getElementById('themeIcon');
    const label = document.getElementById('themeLabel');
    if (icon) icon.innerHTML = isDark ? SUN_ICON : MOON_ICON;
    if (label) label.textContent = isDark ? t('theme.light') : t('theme.dark');
}

export function refreshThemeLabel() {
    updateToggleUI();
}
