/**
 * UI Manager - Handles navigation, rendering, and toast notifications.
 */

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

function showSection(id) {
    document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => {
        n.classList.remove('active');
        if (n.getAttribute('onclick')?.includes(`'${id}'`)) n.classList.add('active');
    });
    document.getElementById('section-' + id).classList.add('active');
    
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
    document.getElementById(`${group}-${tab}`).classList.add('active');
    const btns = event.currentTarget.parentElement.querySelectorAll('.tab-btn');
    btns.forEach(b => b.classList.remove('active'));
    event.currentTarget.classList.add('active');
}

function renderQuestions() {
    const list = document.getElementById('qGenList');
    document.getElementById('qGenCount').textContent = generatedQuestions.length + ' questions';
    if (!generatedQuestions.length) {
        list.innerHTML = '<div class="text-muted" style="text-align:center;padding:40px 0;">No questions yet</div>';
        return;
    }
    const catColor = { technical: 'tag-blue', behavioural: 'tag-amber', hr: 'tag-teal' };
    list.innerHTML = generatedQuestions.map((q, i) => `
<div class="q-item">
<div class="q-num">${i + 1}</div>
<div class="q-text">${q.text}</div>
<span class="tag ${catColor[q.category] || 'tag-blue'}">${q.category}</span>
</div>
`).join('');
}

function renderScoreReport(result, model, cost, usage) {
    const overall = result.overall_score || 0;
    const rec = result.recommendation || 'hold';
    const recClass = { shortlist: 'tag-teal', hold: 'tag-amber', reject: 'tag-rose' };
    const pct = Math.round(overall * 10);

    document.getElementById('scoreReport').innerHTML = `
<div class="card-title">Evaluation Report
<span class="tag ${recClass[rec]}">${rec.toUpperCase()}</span>
</div>

<div style="text-align:center; padding:10px 0 16px;">
<div class="overall-ring" style="--pct:${pct};">
<span class="overall-ring-val">${overall.toFixed(1)}</span>
</div>
<div style="font-size:10px; color:var(--muted); letter-spacing:1px; text-transform:uppercase;">Overall Score / 10</div>
<div style="margin-top:6px; font-size:11px; color:var(--muted);">
Model: <span style="color:var(--blue)">${model}</span> &nbsp;·&nbsp;
Tokens: <span style="color:var(--muted)">${usage.prompt_tokens} in / ${usage.completion_tokens} out</span> &nbsp;·&nbsp;
Cost: <span style="color:var(--teal)">$${cost.toFixed(6)}</span>
</div>
</div>

${result.summary ? `<div style="font-size:12px;color:var(--muted);line-height:1.7;margin-bottom:14px;padding:10px 14px;background:var(--surface2);border-radius:8px;border:1px solid var(--border);">${result.summary}</div>` : ''}

${(result.scores || []).map((s, i) => `
<div class="score-card">
<div style="display:flex; justify-content:space-between; align-items:flex-start; gap:8px;">
<div style="font-size:11px; color:var(--muted); flex:1; line-height:1.4;">
    ${generatedQuestions[i]?.text || storedTranscripts[i]?.question || 'Q' + (i + 1)}
</div>
<span class="score-num">${(s.weighted_score || s.score || 0).toFixed(1)}</span>
</div>
<div class="score-bar-row">
<div class="score-bar-bg">
    <div class="score-bar-fill" style="width:${(s.weighted_score || s.score || 0) * 10}%"></div>
</div>
</div>
<div style="display:flex; gap:8px; margin-top:6px; flex-wrap:wrap;">
<span class="tag tag-blue">Relevance: ${s.relevance}/10</span>
<span class="tag tag-amber">Depth: ${s.depth}/10</span>
<span class="tag tag-teal">Comm: ${s.communication}/10</span>
<span class="tag tag-rose">Impression: ${s.impression}/10</span>
</div>
${s.feedback ? `<div style="font-size:10px;color:var(--muted);margin-top:6px;line-height:1.5;">${s.feedback}</div>` : ''}
</div>
`).join('')}

${result.strengths?.length ? `
<div style="margin-top:12px;">
<div style="font-size:10px;color:var(--teal);letter-spacing:1px;text-transform:uppercase;margin-bottom:6px;">Strengths</div>
${result.strengths.map(s => `<div style="font-size:11px;color:var(--muted);padding:3px 0;">✅ ${s}</div>`).join('')}
</div>` : ''}

${result.areas_to_probe?.length ? `
<div style="margin-top:10px;">
<div style="font-size:10px;color:var(--amber);letter-spacing:1px;text-transform:uppercase;margin-bottom:6px;">Areas to Probe</div>
${result.areas_to_probe.map(a => `<div style="font-size:11px;color:var(--muted);padding:3px 0;">🔍 ${a}</div>`).join('')}
</div>` : ''}
`;
}

function setWaveform(active) {
    document.querySelectorAll('.wave-bar').forEach((b, i) => {
        if (active) { b.classList.add('live'); b.style.animationDelay = (i * 0.06) + 's'; }
        else { b.classList.remove('live'); }
    });
}
