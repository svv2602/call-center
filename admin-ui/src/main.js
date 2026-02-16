// Styles
import './styles/main.css';

// Core modules
import { getToken } from './api.js';
import { login, logout, checkTokenExpiry, applyRoleVisibility } from './auth.js';
import { showPage } from './router.js';
import { connectWebSocket, setWsEventHandler } from './websocket.js';
import { closeModal } from './utils.js';
import { initTheme, toggleTheme } from './theme.js';

// Page modules — each registers its page loader via init()
import { init as initDashboard } from './pages/dashboard.js';
import { init as initCalls, getCallsOffset } from './pages/calls.js';
import { init as initPrompts } from './pages/prompts.js';
import { init as initKnowledge } from './pages/knowledge.js';
import { init as initOperators } from './pages/operators.js';
import { init as initSettings } from './pages/settings.js';
import { init as initUsers } from './pages/users.js';
import { init as initAudit } from './pages/audit.js';

// Initialize theme
initTheme();

// Initialize all page loaders
initDashboard();
initCalls();
initPrompts();
initKnowledge();
initOperators();
initSettings();
initUsers();
initAudit();

// WebSocket event handler — dispatches real-time updates to active page
setWsEventHandler((msg) => {
    const activePage = localStorage.getItem('admin_active_page') || 'dashboard';

    if (msg.type === 'call:started' || msg.type === 'call:ended' || msg.type === 'call:transferred') {
        if (activePage === 'dashboard') window._pages.dashboard.loadDashboard();
        if (activePage === 'calls') window._pages.calls.loadCalls(getCallsOffset());
    }
    if (msg.type === 'operator:status_changed') {
        if (activePage === 'operators') {
            window._pages.operators.loadOperators();
            window._pages.operators.loadQueueStatus();
        }
    }
    if (msg.type === 'dashboard:metrics_updated') {
        if (activePage === 'dashboard') window._pages.dashboard.loadDashboard();
    }
});

// Periodic token expiry check
setInterval(checkTokenExpiry, 60000);

// App initialization — restore session if token exists
if (getToken()) {
    checkTokenExpiry();
    if (getToken()) {
        document.getElementById('loginContainer').style.display = 'none';
        document.getElementById('app').style.display = 'block';
        applyRoleVisibility();
        connectWebSocket();
        const savedPage = localStorage.getItem('admin_active_page') || 'dashboard';
        showPage(savedPage);
    }
}

// Global event listeners
document.getElementById('loginPassword').addEventListener('keypress', e => {
    if (e.key === 'Enter') login();
});

document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
        document.querySelectorAll('.modal-overlay.show').forEach(m => m.classList.remove('show'));
    }
});

// Mobile sidebar toggle
function toggleSidebar() {
    document.getElementById('sidebar').classList.toggle('open');
}

document.querySelectorAll('.sidebar a').forEach(a => {
    a.addEventListener('click', () => {
        if (window.innerWidth <= 768) {
            document.getElementById('sidebar').classList.remove('open');
        }
    });
});

// Expose globals for onclick handlers in HTML
window._app = { login, logout, showPage, closeModal, toggleSidebar, toggleTheme };
