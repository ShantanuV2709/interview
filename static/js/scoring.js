// ─────────────────────────────────────────────────────────────────────────────
// SCORING & EVALUATION
// ─────────────────────────────────────────────────────────────────────────────
async function runScoring() {
    const validPairs = storedTranscripts.filter(Boolean);
    if (!validPairs.length) {
        showToast('⚠️ No transcripts found. Complete the interview first.', 'amber'); return;
    }

    const btn = document.getElementById('scoreBtn');
    const btnTxt = document.getElementById('scoreBtnText');
    btn.disabled = true;
    btnTxt.innerHTML = '<span class="spin"></span> Evaluating...';
    
    const model = document.getElementById('scoringModel').value;
    brainRecord('scoring', 'start', `Model: ${model}, Pairs: ${validPairs.length}`, 'info');

    const transcriptText = storedTranscripts
        .filter(Boolean)
        .map((attempts, i) => {
            const finalAttempt = attempts[attempts.length - 1];
            return `Q${i + 1}: ${finalAttempt.question}\nA${i + 1}: ${finalAttempt.answer}`;
        })
        .join('\n---\n');

    try {
        const res = await fetch('/api/v1/score', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ transcript: transcriptText, model: model })
        });

        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || `HTTP ${res.status}`);
        }

        const data = await res.json();
        const usage = data.usage || { prompt_tokens: 0, completion_tokens: 0 };
        const totalCost = (usage.prompt_tokens * 0.00000015) + (usage.completion_tokens * 0.0000006); // Mini rates
        
        trackCost('score', totalCost);
        renderScoreReport(data.report, model, totalCost, usage);
        showToast(`✅ Scoring complete`, 'teal');
        brainRecord('scoring', 'success', `Scored. Total: ${data.report.overall_score}`, 'ok');
    } catch (err) {
        showToast(`❌ Scoring error: ${err.message}`, 'rose');
        console.error(err);
    } finally {
        btn.disabled = false;
        btnTxt.textContent = '📊 Run Evaluation';
    }
}

function renderScoreReport(result, model, cost, usage) {
    const overall = result.overall_score || 0;
    const rec = result.recommendation || 'hold';
    const recClass = { shortlist: 'tag-teal', hold: 'tag-amber', reject: 'tag-rose' };
    const pct = Math.round(overall * 10);

    const reportEl = document.getElementById('scoreReport');
    if (!reportEl) return;
    
    reportEl.innerHTML = `
        <div class="card-title">Evaluation Report <span class="tag ${recClass[rec]}">${rec.toUpperCase()}</span></div>
        <div style="text-align:center; padding:10px 0 16px;">
            <div class="overall-ring" style="--pct:${pct};"><span class="overall-ring-val">${overall.toFixed(1)}</span></div>
            <div style="font-size:10px; color:var(--muted);">Overall Score / 10</div>
        </div>
        ${result.summary ? `<div class="summary-box">${result.summary}</div>` : ''}
        ${(result.scores || []).map((s, i) => `
            <div class="score-card">
                <div style="display:flex; justify-content:space-between;">
                    <div class="q-text-small">${s.question_text || 'Q'+(i+1)}</div>
                    <span class="score-num">${(s.weighted_score || 0).toFixed(1)}</span>
                </div>
                <div class="score-bar-bg"><div class="score-bar-fill" style="width:${(s.weighted_score || 0) * 10}%"></div></div>
                <div class="tag-row">
                    <span class="tag tag-blue">Rel: ${s.relevance}</span>
                    <span class="tag tag-amber">Dep: ${s.depth}</span>
                    <span class="tag tag-teal">Comm: ${s.communication}</span>
                </div>
            </div>
        `).join('')}
    `;
}
