import { api } from '../api.js';
import { showToast } from '../notifications.js';
import { escapeHtml } from '../utils.js';
import { registerPageLoader } from '../router.js';
import { t } from '../i18n.js';
import * as tw from '../tw.js';

async function loadTelegramConfig() {
    const container = document.getElementById('telegramConfigContainer');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;
    try {
        const data = await api('/admin/notifications/telegram');
        let tokenStatus;
        if (data.token_set) {
            tokenStatus = `<span class="${tw.badgeGreen}">${t('settings.telegramTokenSet')}</span>`;
            if (data.token_hint) tokenStatus += ` <span class="text-xs text-neutral-400 font-mono">...${escapeHtml(data.token_hint)}</span>`;
        } else {
            tokenStatus = `<span class="${tw.badgeYellow}">${t('settings.telegramTokenMissing')}</span>`;
        }
        const sourceLabel = data.source === 'redis' ? 'Redis' : data.source === 'env' ? 'ENV' : '—';

        let html = `<div class="space-y-3">
            <div class="flex items-center gap-2 text-sm">
                <span class="text-neutral-500 dark:text-neutral-400">${t('settings.telegramStatus')}:</span>
                ${tokenStatus}
                <span class="${tw.badge}">${sourceLabel}</span>
            </div>
            <div>
                <label class="block text-xs font-medium text-neutral-600 dark:text-neutral-400 mb-1">${t('settings.telegramBotToken')}</label>
                <input id="tgBotToken" type="password" placeholder="${t('settings.telegramBotTokenPlaceholder')}" class="w-full text-sm font-mono bg-white dark:bg-neutral-800 text-neutral-800 dark:text-neutral-200 border border-neutral-300 dark:border-neutral-600 rounded px-2 py-1.5 focus:outline-none focus:border-blue-500">
            </div>
            <div>
                <label class="block text-xs font-medium text-neutral-600 dark:text-neutral-400 mb-1">${t('settings.telegramChatId')}</label>
                <input id="tgChatId" type="text" value="${escapeHtml(data.chat_id || '')}" placeholder="${t('settings.telegramChatIdPlaceholder')}" class="w-full text-sm font-mono bg-white dark:bg-neutral-800 text-neutral-800 dark:text-neutral-200 border border-neutral-300 dark:border-neutral-600 rounded px-2 py-1.5 focus:outline-none focus:border-blue-500">
            </div>
            <div class="flex gap-2">
                <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.notifications.saveTelegramConfig()">${t('common.save')}</button>
                <button class="${tw.btnSm} border border-neutral-300 dark:border-neutral-600 text-neutral-700 dark:text-neutral-300 hover:bg-neutral-100 dark:hover:bg-neutral-800" onclick="window._pages.notifications.testTelegram()">${t('settings.telegramTestBtn')}</button>
            </div>
        </div>`;
        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('settings.telegramLoadFailed', {error: escapeHtml(e.message)})}</div>`;
    }
}

async function saveTelegramConfig() {
    const botToken = document.getElementById('tgBotToken')?.value?.trim() || '';
    const chatId = document.getElementById('tgChatId')?.value?.trim() || '';

    if (!botToken && !chatId) {
        showToast(t('settings.telegramFillFields'), 'error');
        return;
    }

    const body = {};
    if (botToken) body.bot_token = botToken;
    if (chatId) body.chat_id = chatId;

    try {
        await api('/admin/notifications/telegram', {
            method: 'PATCH',
            body: JSON.stringify(body),
        });
        showToast(t('settings.telegramSaved'), 'success');
        loadTelegramConfig();
    } catch (e) {
        showToast(t('settings.telegramSaveFailed', {error: e.message}), 'error');
    }
}

async function testTelegram() {
    showToast(t('settings.telegramTesting'), 'info');
    try {
        const result = await api('/admin/notifications/telegram/test', { method: 'POST' });
        if (result.success) {
            showToast(t('settings.telegramTestSuccess', {latency: result.latency_ms}), 'success');
        } else {
            showToast(t('settings.telegramTestFailed', {error: result.error || 'Unknown'}), 'error');
        }
    } catch (e) {
        showToast(t('settings.telegramTestFailed', {error: e.message}), 'error');
    }
}

export function init() {
    registerPageLoader('notifications', loadTelegramConfig);
}

window._pages = window._pages || {};
window._pages.notifications = { loadTelegramConfig, saveTelegramConfig, testTelegram };
