// ─────────────────────────────────────────────────────────────────────────────
// HELPERS
// ─────────────────────────────────────────────────────────────────────────────
function showToast(msg, type = 'teal') {
    const colors = { teal: 'var(--teal)', amber: 'var(--amber)', rose: 'var(--rose)' };
    const t = document.createElement('div');
    t.style.cssText = `position:fixed;bottom:24px;right:24px;z-index:99999;
background:var(--surface2);border:1px solid ${colors[type]};
color:${colors[type]};padding:12px 18px;border-radius:10px;
font-family:var(--mono);font-size:12px;font-weight:600;
max-width:360px;line-height:1.5;box-shadow:0 8px 24px rgba(0,0,0,0.4);
animation:fadeUp 0.3s ease;`;
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 4000);
}

function setWaveform(active) {
    document.querySelectorAll('.wave-bar').forEach((b, i) => {
        if (active) { b.classList.add('live'); b.style.animationDelay = (i * 0.06) + 's'; }
        else { b.classList.remove('live'); }
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// NAVIGATION
// ─────────────────────────────────────────────────────────────────────────────
function showSection(id) {
    document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => {
        n.classList.remove('active');
        if (n.getAttribute('onclick')?.includes(`'${id}'`)) n.classList.add('active');
    });
    const section = document.getElementById('section-' + id);
    if (section) section.classList.add('active');
    
    if (id === 'brain' && (!brainWS || brainWS.readyState > 1)) {
        brainConnect();
    }
    
    // Auto-start interview logic
    if (id === 'interview' && !callActive) {
        console.log('🚀 Auto-starting interview...');
        brainRecord('interview', 'started', 'Auto-start triggered', 'info');
        setTimeout(startInterview, 50); 
    }
}

function switchInnerTab(group, tab) {
    document.querySelectorAll(`[id^="${group}-"]`).forEach(p => p.classList.remove('active'));
    const target = document.getElementById(`${group}-${tab}`);
    if (target) target.classList.add('active');
    
    const btns = event.currentTarget.parentElement.querySelectorAll('.tab-btn');
    btns.forEach(b => b.classList.remove('active'));
    event.currentTarget.classList.add('active');
}

// ─────────────────────────────────────────────────────────────────────────────
// COST TRACKING
// ─────────────────────────────────────────────────────────────────────────────
const RETELL_PER_MIN = 0.103;
const SARVAM_STT_PER_SEC = 0.0001;

function trackCost(component, amount) {
    sessionCost[component] = (sessionCost[component] || 0) + amount;
    updateCostDisplay();
}

function updateCostDisplay() {
    const total = Object.values(sessionCost).reduce((a, b) => a + b, 0);
    const retell = calcRetellCost();
    const sarvamStack = calcSarvamStackCost();
    const saving = Math.max(0, retell - total);

    const elTotal = document.getElementById('sessionCostDisplay');
    if (elTotal) elTotal.textContent = '$' + total.toFixed(5);
    
    const pct = retell > 0 ? Math.min(100, (total / retell) * 100) : 0;
    const bar = document.getElementById('costBar');
    if (bar) bar.style.width = pct + '%';
    const pctTxt = document.getElementById('costPct');
    if (pctTxt) pctTxt.textContent = Math.round(pct) + '%';

    // Update other stat displays if they exist
    const stats = {
        'statTotalNew': '$' + total.toFixed(5),
        'statRetell': '$' + retell.toFixed(4),
        'statSaving': '$' + saving.toFixed(5),
        'statSarvamStack': '$' + sarvamStack.toFixed(5),
        'mCostQGen': '$' + (sessionCost.qgen || 0).toFixed(5),
        'mCostTTS': '$' + (sessionCost.tts || 0).toFixed(5),
        'mCostSTT': '$' + (sessionCost.stt || 0).toFixed(5),
        'mCostScore': '$' + (sessionCost.score || 0).toFixed(5),
        'mCostTotal': '$' + total.toFixed(5),
        'mCostSarvamStack': '$' + sarvamStack.toFixed(5),
    };
    
    for (const [id, val] of Object.entries(stats)) {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
    }

    const speakingTimeEl = document.getElementById('statSpeakingTime');
    if (speakingTimeEl) {
        const min = Math.floor(sttSecCount / 60);
        const sec = Math.round(sttSecCount % 60);
        speakingTimeEl.textContent = min > 0 ? `${min}m ${sec}s` : `${sec}s`;
    }

    const mCostRetell = document.getElementById('mCostRetell');
    if (mCostRetell) {
        const callMin = Math.max(1, Math.ceil((sttSecCount + ttsCharCount / 14) / 60));
        mCostRetell.textContent = `$${retell.toFixed(4)} (${callMin} min × $${RETELL_PER_MIN})`;
    }

    const savingPill = document.getElementById('savingPill');
    if (savingPill && total > 0) {
        savingPill.style.display = 'inline-flex';
        const savingAmt = document.getElementById('savingAmount');
        if (savingAmt) savingAmt.textContent = '$' + saving.toFixed(5);
    }
}

function calcRetellCost() {
    const ttsSec = ttsCharCount / 14;
    const totalSec = sttSecCount + ttsSec;
    const totalMin = totalSec / 60;
    return Math.max(1, Math.ceil(totalMin)) * RETELL_PER_MIN;
}

function calcSarvamStackCost() {
    const openAICost = (sessionCost.qgen || 0) + (sessionCost.score || 0);
    const ttsCost = ttsCharCount * 0.0000165;
    const sarvamSTTCost = sttSecCount * SARVAM_STT_PER_SEC;
    return openAICost + ttsCost + sarvamSTTCost;
}

function buildScaleTable() {
    const tbody = document.getElementById('scaleTable');
    if (!tbody) return;
    const vols = [100, 250, 500, 1000, 2500, 5000, 10000];
    const totalSec = sttSecCount + ttsCharCount / 14;
    const callMin = totalSec > 0 ? Math.max(1, Math.ceil(totalSec / 60)) : 0;
    const retellPer = callMin > 0 ? callMin * RETELL_PER_MIN : 0.19;
    const currentTotal = Object.values(sessionCost).reduce((a, b) => a + b, 0);
    const dgStackPer = currentTotal || 0.041;
    const sarvamStackPer = calcSarvamStackCost() || 0.045;

    tbody.innerHTML = vols.map((v, i) => {
        const r = (v * retellPer).toFixed(2);
        const dg = (v * dgStackPer).toFixed(2);
        const sv = (v * sarvamStackPer).toFixed(2);
        const s = (v * Math.max(0, retellPer - dgStackPer)).toFixed(2);
        const a = (v * Math.max(0, retellPer - dgStackPer) * 12).toFixed(0);
        const bg = i % 2 === 0 ? 'var(--surface2)' : 'var(--surface)';
        return `<tr style="background:${bg};">
            <td style="padding:8px 12px;border:1px solid var(--border);font-weight:700;">${v.toLocaleString()}</td>
            <td style="padding:8px 12px;border:1px solid var(--border);text-align:right;color:var(--rose);">$${r}</td>
            <td style="padding:8px 12px;border:1px solid var(--border);text-align:right;color:var(--muted);">$${dg}</td>
            <td style="padding:8px 12px;border:1px solid var(--border);text-align:right;color:var(--blue);">$${sv}</td>
            <td style="padding:8px 12px;border:1px solid var(--border);text-align:right;color:var(--amber);font-weight:700;">$${s}</td>
            <td style="padding:8px 12px;border:1px solid var(--border);text-align:right;color:var(--muted);">$${parseInt(a).toLocaleString()}</td>
        </tr>`;
    }).join('');
}
