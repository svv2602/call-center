import { getToken } from './api.js';

let ws = null;
let wsReconnectDelay = 1000;
const WS_MAX_RECONNECT_DELAY = 30000;
let eventHandler = null;

export function setWsEventHandler(handler) {
    eventHandler = handler;
}

export function connectWebSocket() {
    const token = getToken();
    if (!token) return;
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${proto}//${location.host}/ws?token=${token}`;

    try { ws = new WebSocket(wsUrl); } catch { return; }

    ws.onopen = () => {
        wsReconnectDelay = 1000;
        updateWsStatus('connected');
    };

    ws.onclose = () => {
        updateWsStatus('disconnected');
        scheduleReconnect();
    };

    ws.onerror = () => {
        updateWsStatus('disconnected');
    };

    ws.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            if (msg.type === 'ping') {
                if (ws && ws.readyState === WebSocket.OPEN) ws.send('pong');
                return;
            }
            if (msg.type === 'pong') return;
            if (eventHandler) eventHandler(msg);
        } catch { /* ignore parse errors */ }
    };
}

export function disconnectWebSocket() {
    if (ws) { ws.close(); ws = null; }
}

function scheduleReconnect() {
    const token = getToken();
    if (!token) return;
    updateWsStatus('reconnecting');
    setTimeout(() => {
        if (getToken()) connectWebSocket();
    }, wsReconnectDelay);
    wsReconnectDelay = Math.min(wsReconnectDelay * 2, WS_MAX_RECONNECT_DELAY);
}

function updateWsStatus(state) {
    const dot = document.getElementById('wsDot');
    const label = document.getElementById('wsLabel');
    if (!dot || !label) return;
    const colors = { connected: 'bg-green-500', reconnecting: 'bg-yellow-500', disconnected: 'bg-red-500' };
    dot.className = `w-2 h-2 rounded-full ${colors[state] || 'bg-red-500'}`;
    const labels = { connected: 'Live', reconnecting: 'Reconnecting...', disconnected: 'Offline' };
    label.textContent = labels[state] || 'Offline';
}

// Keep-alive pings
setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send('ping');
}, 25000);
