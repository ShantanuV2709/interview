/**
 * Main Application Logic - State, API interactions, and core interview flow.
 */

// ── HELPERS ──
const getOpenAIKey = () => '';
const getSarvamKey = () => '';

function requireKeys(...keys) {
    return true; // Managed server-side
}

// ── STATE ──
const WS_BASE_URL = (window.location.protocol === "https:" ? "wss://" : "ws://") + window.location.hostname + ":3002/brain/";

let sessionCost = { qgen: 0, tts: 0, stt: 0, score: 0 };
let generatedQuestions = [];
let storedTranscripts = [];
let currentQIdx = -1;
let callActive = false;
let ttsCharCount = 0;
let sttSecCount = 0;
let mediaRecorder = null;
let audioChunks = [];
let recordingStartTime = null;
let currentAudio = null;
let lastAssistantResponse = "";

let vadAudioContext = null;
let vadSource = null;
let vadNode = null;
let isNavigating = false;

let deepgramKey = '';
let liveTranscript = '';
let dgSocket = null;
let conversationalWS = null;
let dgAudioCtx = null;
let dgSource = null;
let dgProcessor = null;
let sttFallbackMode = false;

let brainWS = null;
let brainReconnectTimer = null;
const BRAIN_WS_URL = WS_BASE_URL + 'brain';
let persistentAudioCtx = null;

const RETELL_PER_MIN = 0.103;
const SARVAM_STT_PER_SEC = 0.0001;
const STT_CHUNK_SEC = 25;
const STT_SAMPLE_RATE = 16000;

// ── COST TRACKING ──
function trackCost(component, amount) {
    sessionCost[component] = (sessionCost[component] || 0) + amount;
    updateCostDisplay();
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

function updateCostDisplay() {
    const total = Object.values(sessionCost).reduce((a, b) => a + b, 0);
    const retell = calcRetellCost();
    const sarvamStack = calcSarvamStackCost();
    const saving = Math.max(0, retell - total);

    document.getElementById('sessionCostDisplay').textContent = '$' + total.toFixed(5);
    const pct = retell > 0 ? Math.min(100, (total / retell) * 100) : 0;
    document.getElementById('costBar').style.width = pct + '%';
    document.getElementById('costPct').textContent = Math.round(pct) + '%';

    document.getElementById('statTotalNew').textContent = '$' + total.toFixed(5);
    document.getElementById('statRetell').textContent = '$' + retell.toFixed(4);
    document.getElementById('statSaving').textContent = '$' + saving.toFixed(5);
    document.getElementById('statSarvamStack').textContent = '$' + sarvamStack.toFixed(5);

    const min = Math.floor(sttSecCount / 60);
    const sec = Math.round(sttSecCount % 60);
    document.getElementById('statSpeakingTime').textContent = min > 0 ? `${min}m ${sec}s` : `${sec}s`;

    document.getElementById('mCostQGen').textContent = '$' + (sessionCost.qgen || 0).toFixed(5);
    document.getElementById('mCostTTS').textContent = '$' + (sessionCost.tts || 0).toFixed(5);
    document.getElementById('mCostSTT').textContent = '$' + (sessionCost.stt || 0).toFixed(5);
    document.getElementById('mCostScore').textContent = '$' + (sessionCost.score || 0).toFixed(5);
    document.getElementById('mCostTotal').textContent = '$' + total.toFixed(5);
    document.getElementById('mCostRetell').textContent = '$' + retell.toFixed(4) + ' (' + Math.max(1, Math.ceil((sttSecCount + ttsCharCount / 14) / 60)) + ' min × $' + RETELL_PER_MIN + ')';
    document.getElementById('mCostSarvamStack').textContent = '$' + sarvamStack.toFixed(5);

    if (total > 0) {
        document.getElementById('savingPill').style.display = 'inline-flex';
        document.getElementById('savingAmount').textContent = '$' + saving.toFixed(5);
    }
}

function updateInterviewCostDisplay() {
    const ttsCost = ttsCharCount * 0.0000165;
    const sttCost = sttSecCount * 0.000092;
    document.getElementById('ttsChars').textContent = ttsCharCount;
    document.getElementById('ttsCost').textContent = '$' + ttsCost.toFixed(6);
    document.getElementById('sttSecs').textContent = sttSecCount.toFixed(1) + 's';
    document.getElementById('sttCost').textContent = '$' + sttCost.toFixed(6);
    document.getElementById('sarvamRunning').textContent = '$' + (ttsCost + sttCost).toFixed(6);
}

// ── SCREEN 1: QUESTION GENERATION ──
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

    const systemPrompt = `You are an expert HR interviewer specialising in tech hiring.
Your task is to generate interview questions from a job description.
Rules:
- Write ALL questions in clear, professional English
- Keep language warm and conversational
- Generate exactly ${numQ} questions
- Distribute: 50% Technical, 30% Behavioural, 20% HR/Culture
- Each question should be answerable verbally in under 90 seconds
- Avoid compound questions — one idea per question only
- Use second person "you" in a warm, professional tone
Output ONLY valid JSON, no markdown:
{"questions":[{"id":1,"text":"<question>","category":"technical|behavioural|hr","difficulty":"easy|medium|hard"}]}`;

    const userPrompt = `Role: ${title}\nExperience: ${exp}\nNumber of questions: ${numQ}\n\nJob Description:\n${jd}\n\nGenerate the questions now.`;

    try {
        const res = await fetch('/proxy/openai/v1/chat/completions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                model: 'gpt-4o',
                response_format: { type: 'json_object' },
                messages: [
                    { role: 'system', content: systemPrompt },
                    { role: 'user', content: userPrompt }
                ]
            })
        });

        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.error?.message || `HTTP ${res.status}`);
        }

        const data = await res.json();
        const usage = data.usage;
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

        const parsed = JSON.parse(data.choices[0].message.content);
        generatedQuestions = parsed.questions || [];
        renderQuestions();

        status.textContent = `✓ ${generatedQuestions.length} questions generated`;
        showToast(`✅ Generated ${generatedQuestions.length} questions — cost: $${totalCost.toFixed(6)}`, 'teal');
        brainRecord('question_gen', 'success', `Generated ${generatedQuestions.length} questions. Cost: $${totalCost.toFixed(6)}`, 'ok');

        setTimeout(() => {
            const navItem = document.querySelector('.nav-item[onclick*="interview"]');
            if (navItem) navItem.click(); else showSection('interview');
        }, 800);

    } catch (err) {
        status.textContent = 'error — check console';
        showToast(`❌ GPT-4o error: ${err.message}`, 'rose');
        console.error(err);
        brainRecord('question_gen', 'error', err.message, 'error');
    } finally {
        btn.disabled = false;
        btnTxt.textContent = '⚡ Generate Questions';
    }
}

// ── SCREEN 2: INTERVIEW FLOW ──
function startInterview() {
    if (!generatedQuestions.length) {
        showToast('⚠️ Please generate questions first (Screen 1).', 'amber'); return;
    }
    callActive = true; currentQIdx = -1;
    storedTranscripts = []; ttsCharCount = 0; sttSecCount = 0;
    document.getElementById('startCallBtn').disabled = true;
    document.getElementById('nextQBtn').disabled = false;
    document.getElementById('endCallBtn').disabled = false;
    document.getElementById('transcriptStore').innerHTML = '';
    document.getElementById('transcriptCount').textContent = '0 pairs';

    // Start by getting the conversational introduction/greeting from the backend
    (async () => {
        const res = await generateConversationalNext("", "", "Begin interview with introduction and name collection.");
        if (res.action === 'end') { endInterview(); return; }
        lastAssistantResponse = res.response;
        startRecordingForCurrent();
    })();
}

async function loadQuestion(idx) {
    if (idx >= generatedQuestions.length) { endInterview(); return; }
    currentQIdx = idx;
    const q = generatedQuestions[idx];
    document.getElementById('qProgressTag').textContent = idx === -1 ? "Intro" : `Q ${idx + 1}/${generatedQuestions.length}`;
    const textToSpeak = q.dynamicText ? q.dynamicText : q.text;
    document.getElementById('currentQ').textContent = textToSpeak;
    document.getElementById('callDot').className = 'status-dot speaking';
    document.getElementById('callStatusText').textContent = `TTS: Generating audio for Q${idx + 1}...`;
    setWaveform(true);
    const spoke = await speakQuestion(textToSpeak);
    if (!spoke) await new Promise(r => setTimeout(r, 1000));
    startRecordingForCurrent();
}

async function loadQuestionNoSpeak(idx) {
    if (idx >= generatedQuestions.length) { endInterview(); return; }
    currentQIdx = idx;
    const q = generatedQuestions[idx];
    document.getElementById('qProgressTag').textContent = `Q ${idx + 1}/${generatedQuestions.length}`;
    await startRecordingForCurrent();
}

async function startRecordingForCurrent() {
    document.getElementById('callDot').className = 'status-dot live';
    document.getElementById('callStatusText').textContent = `🎙 Recording your answer...`;
    document.getElementById('liveTranscript').innerHTML = `
        <span class="transcript-tag">Your Answer</span>
        <span class="transcript-lang">en-IN · Deepgram nova-2</span>
        <span class="text-muted">🔴 Recording... speak your answer now</span>
    `;
    await startRecording();
}

async function startRecording() {
    liveTranscript = '';
    try {
        const stream = await navigator.mediaDevices.getUserMedia({
            audio: { sampleRate: 16000, channelCount: 1, echoCancellation: true }
        });
        audioChunks = [];
        recordingStartTime = performance.now();
        const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : 'audio/webm';
        mediaRecorder = new MediaRecorder(stream, { mimeType });
        mediaRecorder.ondataavailable = e => { if (e.data.size > 0) audioChunks.push(e.data); };
        mediaRecorder.start(100);

        dgAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
        if (dgAudioCtx.state === 'suspended') await dgAudioCtx.resume();
        dgSource = dgAudioCtx.createMediaStreamSource(stream);

        let analyser = dgAudioCtx.createAnalyser();
        analyser.fftSize = 1024;
        vadNode = dgAudioCtx.createScriptProcessor(2048, 1, 1);
        dgSource.connect(analyser);
        analyser.connect(vadNode);
        vadNode.connect(dgAudioCtx.destination);
        let silenceStart = Date.now(), hasSpoken = false;

        if (!sttFallbackMode) {
            return new Promise((resolve) => {
                dgSocket = new WebSocket(WS_BASE_URL);
                dgSocket.binaryType = 'arraybuffer';
                dgSocket.onopen = () => {
                    dgSocket.send(JSON.stringify({ action: 'stt', sample_rate: dgAudioCtx.sampleRate }));
                    document.getElementById('dg-debug-status').textContent = 'CONNECTED';
                    resolve();
                };
                dgSocket.onclose = () => { sttFallbackMode = true; };
                dgProcessor = dgAudioCtx.createScriptProcessor(4096, 1, 1);
                dgSource.connect(dgProcessor);
                dgProcessor.connect(dgAudioCtx.destination);
                dgProcessor.onaudioprocess = (e) => {
                    if (!dgSocket || dgSocket.readyState !== WebSocket.OPEN) return;
                    const f32 = e.inputBuffer.getChannelData(0), i16 = new Int16Array(f32.length);
                    for (let i = 0; i < f32.length; i++) {
                        const s = Math.max(-1, Math.min(1, f32[i]));
                        i16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
                    }
                    dgSocket.send(i16.buffer);
                };
                dgSocket.onmessage = (event) => {
                    try {
                        const data = JSON.parse(event.data);
                        if (data.type === 'Results') {
                            const t = data.channel?.alternatives?.[0]?.transcript || '';
                            if (t.trim()) {
                                if (data.is_final) liveTranscript += (liveTranscript ? ' ' : '') + t.trim();
                                document.getElementById('liveTranscript').innerHTML = `
                                    <span class="transcript-tag">Your Answer</span>
                                    <span class="transcript-lang">en-IN &middot; Deepgram nova-2 &#x26A1;</span>
                                    ${liveTranscript} <span style="opacity:0.6">${!data.is_final ? t : ''}</span>`;
                            }
                        }
                    } catch(e) {}
                };
                setupVAD(analyser, silenceStart, hasSpoken);
            });
        } else {
            setupVAD(analyser, silenceStart, hasSpoken);
        }
        document.getElementById('volume-meter-container').style.display = 'flex';
    } catch (err) {
        showToast(`Mic error: ${err.message}`, 'rose');
    }
}

async function nextQ() {
    if (isNavigating || !callActive) return;
    isNavigating = true;
    console.log(`--- nextQ triggered --- (currentQIdx: ${currentQIdx})`);
    try {
        cleanupVAD();
        if (mediaRecorder && mediaRecorder.state !== 'inactive') {
            await new Promise(resolve => {
                mediaRecorder.onstop = resolve;
                mediaRecorder.stop();
                mediaRecorder.stream.getTracks().forEach(t => t.stop());
            });
            const durationSec = (performance.now() - recordingStartTime) / 1000;
            console.log(`[LATENCY] recorder stop took: ${(performance.now() - recordingStartTime).toFixed(2)}ms`);
            if (dgSocket && dgSocket.readyState === WebSocket.OPEN) {
                dgSocket.send(JSON.stringify({ type: 'CloseStream' }));
                await new Promise(r => setTimeout(r, 100));
            }
            let transcript = liveTranscript;
            if (!transcript) transcript = await transcribeAnswer(durationSec);
            console.log(`Transcript choice: "${transcript}"`);
            const answeredQ = currentQIdx === -1 ? "Introduction" : (generatedQuestions[currentQIdx].dynamicText || generatedQuestions[currentQIdx].text);
            storeTranscript(currentQIdx, answeredQ, transcript);
            
            if (currentQIdx === -1) {
                const res = await generateConversationalNext(lastAssistantResponse, transcript, generatedQuestions[0].text, storedTranscripts);
                if (res.action === 'end') { endInterview(); return; }
                generatedQuestions[0].dynamicText = res.response;
                lastAssistantResponse = res.response;
            } else if (currentQIdx + 1 < generatedQuestions.length) {
                const res = await generateConversationalNext(lastAssistantResponse, transcript, generatedQuestions[currentQIdx+1].text, storedTranscripts);
                if (res.action === 'previous' && currentQIdx > 0) { currentQIdx--; loadQuestion(currentQIdx); return; }
                if (res.action === 'repeat') { 
                    generatedQuestions[currentQIdx].dynamicText = res.response; 
                    lastAssistantResponse = res.response;
                    loadQuestion(currentQIdx); return; 
                }
                if (res.action === 'end') { endInterview(); return; }
                generatedQuestions[currentQIdx+1].dynamicText = res.response;
                lastAssistantResponse = res.response;
            } else {
                await generateConversationalNext(lastAssistantResponse, transcript, "Wrap up", storedTranscripts);
                endInterview(); return;
            }
        }
        await loadQuestionNoSpeak(currentQIdx + 1);
    } catch (err) { console.error(err); } finally { isNavigating = false; }
}

function storeTranscript(qIdx, question, answer) {
    const finalAnswer = answer ? answer.trim() : "[No speech detected]";
    
    // Store in data structure for scoring
    if (!storedTranscripts[qIdx]) storedTranscripts[qIdx] = [];
    storedTranscripts[qIdx].push({ question, answer: finalAnswer });

    // Update UI
    const store = document.getElementById('transcriptStore');
    let existingItem = document.querySelector(`.q-item[data-qidx="${qIdx}"]`);
    
    if (existingItem) {
        const answerSpan = existingItem.querySelector('.a-text-content');
        if (answerSpan && !answerSpan.textContent.includes(finalAnswer)) {
            const retryDiv = document.createElement('div');
            retryDiv.className = 'retry-text';
            retryDiv.style = 'color: var(--accent); opacity: 0.6; font-size: 0.8em; margin-top: 5px; border-top: 1px solid rgba(255,255,255,0.1); padding-top: 5px;';
            retryDiv.textContent = `Retried: ${finalAnswer}`;
            answerSpan.parentNode.appendChild(retryDiv);
        }
    } else {
        const item = document.createElement('div');
        item.className = 'q-item';
        item.setAttribute('data-qidx', qIdx);
        item.style = 'background: rgba(255,255,255,0.03); border-radius: 6px; padding: 12px; margin-bottom: 15px; border-left: 3px solid var(--accent);';
        item.innerHTML = `
            <div style="display: flex; gap: 15px;">
                <div class="q-num" style="background: var(--accent); color: #000; font-weight: 800; font-size: 0.8em; padding: 2px 8px; border-radius: 4px; height: fit-content;">${qIdx === -1 ? "Intro" : "Q" + (qIdx + 1)}</div>
                <div style="flex: 1;">
                    <div style="color: var(--accent); font-weight: 500; margin-bottom: 8px; font-size: 0.95em; line-height: 1.4;">${question}</div>
                    <div class="a-text-content" style="color: #fff; opacity: 0.8; font-size: 0.9em; padding: 10px; background: rgba(0,0,0,0.2); border-left: 2px solid #ff4b82; line-height: 1.5;">${finalAnswer}</div>
                </div>
            </div>
        `;
        store.appendChild(item);
    }
    
    store.scrollTop = store.scrollHeight;
    let total = 0; for (let k in storedTranscripts) total += storedTranscripts[k].length;
    document.getElementById('transcriptCount').textContent = `${total} segments`;
}

async function endInterview() {
    cleanupVAD();
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
        mediaRecorder.stop(); mediaRecorder.stream.getTracks().forEach(t => t.stop());
    }
    if (currentAudio) { currentAudio.pause(); currentAudio = null; }
    if (conversationalWS) conversationalWS.close();
    callActive = false;
    document.getElementById('startCallBtn').textContent = 'Interview Finished';
    document.getElementById('callDot').className = 'status-dot';
    document.getElementById('callStatusText').textContent = 'Interview complete';
    setWaveform(false);
    showToast('Interview ended.', 'teal');
    setTimeout(() => showSection('scoring'), 1000);
}

// ── SCREEN 3: SCORING ──
async function runScoring() {
    const validPairs = storedTranscripts.filter(Boolean);
    if (!validPairs.length) { showToast('⚠️ No transcripts found.', 'amber'); return; }
    const btn = document.getElementById('scoreBtn'), btnTxt = document.getElementById('scoreBtnText');
    btn.disabled = true; btnTxt.innerHTML = '<span class="spin"></span> Evaluating...';
    const model = document.getElementById('scoringModel').value;
    const transcriptText = storedTranscripts.filter(Boolean).map((p, i) => `Q${i+1}: ${p[0].question}\nA${i+1}: ${p[0].answer}`).join('\n---\n');

    try {
        const res = await fetch('/proxy/openai/v1/chat/completions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                model, response_format: { type: 'json_object' },
                messages: [{ role: 'system', content: 'Score the interview. Output JSON.' }, { role: 'user', content: transcriptText }]
            })
        });
        const data = await res.json();
        const result = JSON.parse(data.choices[0].message.content);
        renderScoreReport(result, model, 0, data.usage);
    } catch (err) { showToast(err.message, 'rose'); } finally { btn.disabled = false; btnTxt.textContent = '📊 Run Evaluation'; }
}

// ── UTILS & BRAIN ──
function buildScaleTable() {
    const vols = [100, 250, 500, 1000, 5000];
    const tbody = document.getElementById('scaleTable');
    tbody.innerHTML = vols.map(v => `<tr><td>${v}</td><td>$${(v*0.1).toFixed(2)}</td></tr>`).join('');
}

function brainConnect() {
    if (brainWS) return;
    brainWS = new WebSocket(BRAIN_WS_URL);
    brainWS.onopen = () => { document.getElementById('brain-status-badge').textContent = 'ON'; };
    brainWS.onmessage = (evt) => { const msg = JSON.parse(evt.data); if (msg.type === 'snapshot') renderBrainSnapshot(msg.data); };
}

function brainRecord(component, type, detail, severity='info') {
    if (brainWS && brainWS.readyState === 1) brainWS.send(JSON.stringify({ action: "record", component, type, detail, severity }));
}

function renderBrainSnapshot(data) {
    document.getElementById('brain-uptime').textContent = data.uptime_sec + 's';
    const log = document.getElementById('brain-event-log');
    (data.recent_events || []).forEach(e => {
        const line = document.createElement('div');
        line.textContent = `[${e.component}] ${e.type}: ${e.detail}`;
        log.appendChild(line);
    });
}

function copyPrompt(id) {
    const el = document.getElementById(id);
    if (el) navigator.clipboard.writeText(el.innerText).then(() => showToast('Copied'));
}

// ── INIT ──
window.addEventListener('load', () => {
    buildScaleTable();
    updateCostDisplay();
    fetch('/deepgram-key').then(r => r.json()).then(d => { deepgramKey = d.key; });
    setTimeout(brainConnect, 1000);
});
