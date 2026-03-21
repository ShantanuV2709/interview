// ─────────────────────────────────────────────────────────────────────────────
// BRAIN DASHBOARD RENDERING
// ─────────────────────────────────────────────────────────────────────────────
const SEVERITY_STYLES = {
    ok:       { color: 'var(--teal)',  icon: '✅' },
    info:     { color: 'var(--muted)', icon: '📌' },
    warn:     { color: 'var(--amber)', icon: '⚠️' },
    error:    { color: 'var(--rose)',  icon: '❌' },
    healthy:  { color: 'var(--teal)',  icon: '✅' },
};

function renderBrainSnapshot(data) {
    const uptimeEl = document.getElementById('brain-uptime');
    if (uptimeEl) uptimeEl.textContent = fmtUptime(data.uptime_sec || 0);
    
    const eventsEl = document.getElementById('brain-total-events');
    if (eventsEl) eventsEl.textContent = data.total_events || 0;

    // Component health
    const comp = data.component_status || {};
    const grid = document.getElementById('brain-components');
    if (grid) {
        grid.innerHTML = Object.entries(comp).map(([k, v]) => {
            const style = SEVERITY_STYLES[v.status] || SEVERITY_STYLES.info;
            return `<div class="cost-row">
                <span style="font-size:11px;">${k.replace(/_/g,' ')}</span>
                <span style="font-size:10px;color:${style.color};">${style.icon} ${v.status}</span>
            </div>`;
        }).join('');
    }

    // Recent events
    const log = document.getElementById('brain-event-log');
    if (log) {
        log.innerHTML = '';
        (data.recent_events || []).slice().reverse().forEach(e => appendBrainEvent(e, true));
    }
}

function appendBrainEvent(e, prepend = false) {
    const log = document.getElementById('brain-event-log');
    if (!log) return;
    const style = SEVERITY_STYLES[e.severity] || SEVERITY_STYLES.info;
    const line = document.createElement('div');
    line.style.cssText = `color:${style.color};padding:2px 0;font-size:10px;border-bottom:1px solid var(--border);`;
    line.textContent = `${style.icon} ${e.ts_str || ''} [${e.component||'sys'}] ${e.detail || ''}`;
    if (prepend) log.insertBefore(line, log.firstChild);
    else {
        log.appendChild(line);
        log.scrollTop = log.scrollHeight;
    }
}

function fmtUptime(sec) {
    if (sec < 60) return sec + 's';
    const m = Math.floor(sec / 60), s = sec % 60;
    return `${m}m ${s}s`;
}
