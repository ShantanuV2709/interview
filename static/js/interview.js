// ─────────────────────────────────────────────────────────────────────────────
// INTERVIEW FLOW
// ─────────────────────────────────────────────────────────────────────────────
async function demoGenerateQuestions() {
    const jd = document.getElementById('jdText').value.trim();
    const title = document.getElementById('jdTitle').value.trim() || 'Software Engineer';
    const exp = document.getElementById('jdExp').value;
    const numQ = parseInt(document.getElementById('numQ').value) || 6;

    const btn = document.getElementById('genQBtn');
    const btnTxt = document.getElementById('genQBtnText');
    const status = document.getElementById('genQStatus');

    btn.disabled = true;
    btnTxt.innerHTML = '<span class="spin"></span> Calling GPT-4o...';
    status.textContent = 'sending request...';
    brainRecord('question_gen', 'start', `Title: ${title}, NumQ: ${numQ}`, 'info');

    try {
        const res = await fetch('/api/v1/generate-questions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ jd, title, experience: exp, num_questions: numQ })
        });

        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || `HTTP ${res.status}`);
        }

        const data = await res.json();
        const usage = data.usage || { prompt_tokens: 0, completion_tokens: 0 };

        const inputCost = usage.prompt_tokens * 0.0000025;
        const outputCost = usage.completion_tokens * 0.00001;
        const totalCost = inputCost + outputCost;
        trackCost('qgen', totalCost);

        document.getElementById('costInputTok').textContent = usage.prompt_tokens;
        document.getElementById('costOutputTok').textContent = usage.completion_tokens;
        document.getElementById('costInput').textContent = '$' + inputCost.toFixed(6);
        document.getElementById('costOutput').textContent = '$' + outputCost.toFixed(6);
        document.getElementById('costQGenTotal').textContent = '$' + totalCost.toFixed(6);
        document.getElementById('genCostCard').style.display = 'block';

        generatedQuestions = data.questions || [];
        renderQuestions();

        status.textContent = `✓ ${generatedQuestions.length} questions generated`;
        showToast(`✅ Generated ${generatedQuestions.length} questions`, 'teal');
        brainRecord('question_gen', 'success', `Generated ${generatedQuestions.length} questions`, 'ok');

        setTimeout(() => showSection('interview'), 800);
    } catch (err) {
        status.textContent = 'error — check console';
        showToast(`❌ error: ${err.message}`, 'rose');
        console.error(err);
    } finally {
        btn.disabled = false;
        btnTxt.textContent = '⚡ Generate Questions';
    }
}

function renderQuestions() {
    const list = document.getElementById('qGenList');
    if (!list) return;
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

function startInterview() {
    if (!generatedQuestions.length) {
        showToast('⚠️ Please generate questions first (Screen 1).', 'amber'); return;
    }
    
    // Inject Phase 0 (Name Collection) if missing
    if (!generatedQuestions[0].isPhase0) {
        generatedQuestions.unshift({
            isPhase0: true,
            text: "Hey, welcome to Ideal IT Techno! I'm Emma, and I'll be your interviewer today. Before we jump in — what's your name?",
            dynamicText: "Hey, welcome to Ideal IT Techno! I'm Emma, and I'll be your interviewer today. Before we jump in — what's your name?",
            category: "hr"
        });
    }

    callActive = true; currentQIdx = 0;
    storedTranscripts = []; ttsCharCount = 0; sttSecCount = 0;

    document.getElementById('startCallBtn').disabled = true;
    document.getElementById('nextQBtn').disabled = false;
    document.getElementById('endCallBtn').disabled = false;
    document.getElementById('transcriptStore').innerHTML = '';
    document.getElementById('transcriptCount').textContent = '0 pairs';

    loadQuestion(0);
}

async function loadQuestion(idx) {
    if (idx >= generatedQuestions.length) { endInterview(); return; }
    currentQIdx = idx;
    currentQHistory = [];
    const q = generatedQuestions[idx];

    const progressTag = document.getElementById('qProgressTag');
    if (progressTag) progressTag.textContent = `Q ${idx + 1}/${generatedQuestions.length}`;
    
    const textToSpeak = q.dynamicText ? q.dynamicText : q.text;
    document.getElementById('currentQ').textContent = textToSpeak;
    document.getElementById('callDot').className = 'status-dot speaking';
    document.getElementById('callStatusText').textContent = `TTS: Speaking Q${idx + 1}...`;
    setWaveform(true);

    const spoke = await speakQuestion(textToSpeak);
    startRecordingForCurrent();
}

async function startRecordingForCurrent() {
    document.getElementById('callDot').className = 'status-dot live';
    document.getElementById('callStatusText').textContent = `🎙 Recording your answer...`;
    const liveEl = document.getElementById('liveTranscript');
    if (liveEl) {
        liveEl.innerHTML = `
            <span class="transcript-tag">Your Answer</span>
            <span class="transcript-lang">en-IN · Deepgram nova-2</span>
            <span class="text-muted">🔴 Recording... speak your answer now</span>
        `;
    }
    await startRecording();
}

async function nextQ() {
    if (isNavigating || !callActive) return;
    isNavigating = true;
    showToast('⏳ Processing answer...', 'teal');
    try {
        const volContainer = document.getElementById('volume-meter-container');
        if (volContainer) volContainer.style.display = 'none';
        
        cleanupVAD();
        
        if (mediaRecorder && mediaRecorder.state !== 'inactive') {
            await new Promise(resolve => {
                mediaRecorder.onstop = resolve;
                mediaRecorder.stop();
                mediaRecorder.stream.getTracks().forEach(t => t.stop());
            });

            const durationSec = (Date.now() - recordingStartTime) / 1000;
            if (dgSocket && dgSocket.readyState === WebSocket.OPEN) {
                dgSocket.send(JSON.stringify({ type: 'CloseStream' }));
            }

            let transcript = liveTranscript;
            if (!transcript || transcript === '') {
                transcript = await transcribeAnswerBatch(durationSec); 
            }

            const cost = durationSec * 0.0000716; 
            sttSecCount += durationSec;
            trackCost('stt', cost);
            updateInterviewCostDisplay();

            const answeredQ = generatedQuestions[currentQIdx].dynamicText || generatedQuestions[currentQIdx].text;
            storeTranscript(currentQIdx, answeredQ, transcript);
            
            if (currentQIdx + 1 < generatedQuestions.length) {
                let prevContext = generatedQuestions[currentQIdx].text;
                if (currentQHistory.length > 0) {
                    prevContext = `Original Question: ${generatedQuestions[currentQIdx].text}\nPast Interaction:\n` + currentQHistory.join("\n");
                }
                
                const result = await generateConversationalNext(prevContext, transcript, generatedQuestions[currentQIdx+1].text);
                currentQHistory.push(`Candidate: ${transcript}`);
                currentQHistory.push(`Interviewer: ${result.response}`);
                
                if (result.action === 'previous') {
                    if (currentQIdx > 0) currentQIdx--;
                    loadQuestion(currentQIdx); return;
                }
                if (result.action === 'repeat') {
                    generatedQuestions[currentQIdx].dynamicText = result.response;
                    loadQuestionNoSpeakOnlyRecord(currentQIdx); return;
                }
                if (result.action === 'end') { endInterview(); return; }
                
                generatedQuestions[currentQIdx+1].dynamicText = result.response;
            } else {
                endInterview(); return;
            }
        }
        await new Promise(r => setTimeout(r, 500));
        await loadQuestionNoSpeakOnlyRecord(currentQIdx + 1);
    } catch (err) {
        console.error('Error in nextQ:', err);
    } finally {
        isNavigating = false;
    }
}

async function loadQuestionNoSpeakOnlyRecord(idx) {
    if (idx >= generatedQuestions.length) { endInterview(); return; }
    currentQIdx = idx;
    const q = generatedQuestions[idx];
    document.getElementById('qProgressTag').textContent = `Q ${idx + 1}/${generatedQuestions.length}`;
    await startRecordingForCurrent();
}

async function transcribeAnswerBatch(durationSec) {
    if (!audioChunks.length) return '[No speech detected]';
    try {
        const rawBlob = new Blob(audioChunks, { type: audioChunks[0]?.type || 'audio/webm' });
        const res = await fetch('/api/v1/stt', {
            method: 'POST',
            body: rawBlob
        });
        const data = await res.json();
        return data.transcript || '[No transcript returned]';
    } catch (e) {
        console.error('Batch STT Error:', e);
        return '[Transcription failed]';
    }
}

function storeTranscript(qIdx, question, answer) {
    if (!storedTranscripts[qIdx]) storedTranscripts[qIdx] = [];
    storedTranscripts[qIdx].push({ question, answer });
    
    const store = document.getElementById('transcriptStore');
    if (!store) return;
    const div = document.createElement('div');
    div.style.marginBottom = '12px';
    div.innerHTML = `<div class="q-item"><b>Q:</b> ${question}</div><div class="q-item"><b>A:</b> ${answer}</div>`;
    store.appendChild(div);
    store.scrollTop = store.scrollHeight;
    
    let totalPairs = storedTranscripts.flat().length;
    document.getElementById('transcriptCount').textContent = totalPairs + ' pairs';
}

function updateInterviewCostDisplay() {
    const ttsCost = ttsCharCount * 0.0000165;
    const sttCost = sttSecCount * 0.000092;
    const elChars = document.getElementById('ttsChars');
    if (elChars) elChars.textContent = ttsCharCount;
    const elTTS = document.getElementById('ttsCost');
    if (elTTS) elTTS.textContent = '$' + ttsCost.toFixed(6);
    const elSTT = document.getElementById('sttCost');
    if (elSTT) elSTT.textContent = '$' + sttCost.toFixed(6);
}

function endInterview() {
    cleanupVAD();
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
        mediaRecorder.stop();
        mediaRecorder.stream.getTracks().forEach(t => t.stop());
    }
    if (currentAudio) { currentAudio.pause(); currentAudio = null; }
    callActive = false;
    document.getElementById('startCallBtn').textContent = 'Interview Finished';
    setWaveform(false);
    showSection('scoring');
}
