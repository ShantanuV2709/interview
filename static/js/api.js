// ─────────────────────────────────────────────────────────────────────────────
// API HELPERS
// ─────────────────────────────────────────────────────────────────────────────
function brainRecord(component, eventType, detail, severity='info') {
    if (brainWS && brainWS.readyState === WebSocket.OPEN) {
        brainWS.send(JSON.stringify({
            action: "record",
            component, type: eventType, detail, severity
        }));
    }
}

function brainConnect() {
    const WS_PROTO = window.location.protocol === "https:" ? "wss://" : "ws://";
    const BRAIN_URL = WS_PROTO + window.location.host + "/ws/v1/brain";
    
    if (brainWS && (brainWS.readyState === WebSocket.OPEN || brainWS.readyState === WebSocket.CONNECTING)) return;
    console.log('🧠 Connecting to OpenAI Brain...');
    brainWS = new WebSocket(BRAIN_URL);

    brainWS.onopen = () => {
        console.log('🧠 Brain WS connected');
        const pill = document.getElementById('brain-conn-pill');
        if (pill) {
            pill.textContent = 'Connected';
            pill.style.cssText = 'background:var(--teal-dim);color:var(--teal);';
        }
        const badge = document.getElementById('brain-status-badge');
        if (badge) {
            badge.textContent = 'ON';
            badge.style.cssText = 'background:var(--teal-dim);color:var(--teal);border-color:rgba(0,212,170,0.3);';
        }
        const lastPing = document.getElementById('brain-last-ping');
        if (lastPing) lastPing.textContent = 'Connected at ' + new Date().toLocaleTimeString();
        if (brainReconnectTimer) { clearInterval(brainReconnectTimer); brainReconnectTimer = null; }
    };

    brainWS.onclose = () => {
        console.warn('🧠 Brain WS closed — will retry...');
        const pill = document.getElementById('brain-conn-pill');
        if (pill) {
            pill.textContent = 'Disconnected';
            pill.style.cssText = 'background:var(--rose-dim);color:var(--rose);';
        }
        const badge = document.getElementById('brain-status-badge');
        if (badge) {
            badge.textContent = 'OFF';
            badge.style.cssText = 'background:var(--rose-dim);color:var(--rose);border-color:rgba(240,93,122,0.3);';
        }
        if (!brainReconnectTimer) {
            brainReconnectTimer = setInterval(() => {
                brainConnect();
            }, 5000);
        }
    };

    brainWS.onmessage = (evt) => {
        try {
            const msg = JSON.parse(evt.data);
            if (msg.type === 'snapshot') {
                if (typeof renderBrainSnapshot === 'function') renderBrainSnapshot(msg.data);
            } else if (msg.type === 'event') {
                if (typeof appendBrainEvent === 'function') appendBrainEvent(msg.data);
                if (msg.data.type === 'brain_analysis' && typeof renderBrainAnalysis === 'function') {
                    renderBrainAnalysis(msg.data.analysis);
                }
            } else if (msg.type === 'ack') {
                showToast('🧠 ' + msg.msg, 'teal');
            }
        } catch(e) { console.error('Brain WS parse error:', e); }
    };
}

function brainRefreshSnapshot() {
    if (!brainWS || brainWS.readyState !== WebSocket.OPEN) return;
    brainWS.send(JSON.stringify({ action: 'get_snapshot' }));
}

function brainForceAnalysis() {
    if (!brainWS || brainWS.readyState !== WebSocket.OPEN) {
        showToast('Brain not connected', 'amber'); return;
    }
    brainWS.send(JSON.stringify({ action: 'force_analysis' }));
    showToast('🧠 GPT-4o analysis requested...', 'teal');
}

// ─────────────────────────────────────────────────────────────────────────────
// CONVERSATIONAL AI (Unified WebSocket)
// ─────────────────────────────────────────────────────────────────────────────
let persistentAudioCtx = null;
async function generateConversationalNext(prevQ, userAns, nextQObjOrInstruction) {
    document.getElementById('callStatusText').textContent = 'Thinking...';
    document.getElementById('currentQ').innerHTML = ''; 
    document.getElementById('callDot').className = 'status-dot speaking';
    
    if (!persistentAudioCtx) {
        persistentAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
    const audioCtx = persistentAudioCtx;
    if (audioCtx.state === 'suspended') await audioCtx.resume();

    return new Promise((resolve, reject) => {
        let ws;
        let fullResponse = "";
        let isPlaying = false;
        let isDone = false;
        let nextPlayTime = 0;
        let sourceNodes = []; 
        let pendingChunks = 0;
        
        async function playAudioChunk(arrayBuffer) {
            pendingChunks++;
            try {
                if (arrayBuffer.byteLength === 0) { pendingChunks--; return; }
                const audioBuffer = await audioCtx.decodeAudioData(arrayBuffer.slice(0)); 
                const source = audioCtx.createBufferSource();
                source.buffer = audioBuffer;
                source.connect(audioCtx.destination);
                
                const currentTime = audioCtx.currentTime;
                if (nextPlayTime < currentTime) nextPlayTime = currentTime;
                
                source.start(nextPlayTime);
                if (!isPlaying) {
                    setWaveform(true);
                    isPlaying = true;
                }
                sourceNodes.push(source);
                
                source.onended = () => {
                    pendingChunks--;
                    if (isDone && pendingChunks === 0) {
                        isPlaying = false;
                        setWaveform(false);
                        
                        let action = fullResponse.includes("[[END_INTERVIEW]]") ? "end" : 
                                     (fullResponse.includes("[[PREVIOUS]]") ? "previous" :
                                     (fullResponse.includes("[[REPEAT]]") || (fullResponse.toLowerCase().includes("repeat") && fullResponse.length < 200) ? "repeat" : "next"));
                        
                        setTimeout(() => {
                            ws.onmessage = null; 
                            resolve({ action, response: fullResponse.replace("[[END_INTERVIEW]]", "").replace("[[REPEAT]]", "").replace("[[PREVIOUS]]", "").trim() });
                        }, 1000); 
                    }
                };
                
                nextPlayTime += audioBuffer.duration;
            } catch (e) {
                 pendingChunks--;
                 console.error("Audio Decode Error:", e);
                 if (isDone && pendingChunks === 0) {
                     let action = fullResponse.includes("[[END_INTERVIEW]]") ? "end" : 
                                 (fullResponse.includes("[[PREVIOUS]]") ? "previous" :
                                 (fullResponse.includes("[[REPEAT]]") || (fullResponse.toLowerCase().includes("repeat") && fullResponse.length < 200) ? "repeat" : "next"));
                     resolve({ action, response: fullResponse.replace("[[END_INTERVIEW]]", "").replace("[[REPEAT]]", "").replace("[[PREVIOUS]]", "").trim() });
                 }
            }
        }
        
        const onMessage = (msg) => {
            if (msg.data instanceof ArrayBuffer) {
                playAudioChunk(msg.data);
                return;
            }
        
            const data = JSON.parse(msg.data);
            if (data.type === "token") {
                fullResponse += data.text;
                const displayText = fullResponse.replace("[[END_INTERVIEW]]", "").replace("[[REPEAT]]", "").replace("[[PREVIOUS]]", "").replace(/\\n/g, '<br/>');
                document.getElementById('currentQ').innerHTML = displayText;
            } else if (data.type === "usage") {
                if (data.openai) {
                    trackCost('qgen', (data.openai.input * 0.0000025) + (data.openai.output * 0.00001));
                }
                if (data.tts_chars) {
                    ttsCharCount += data.tts_chars;
                    trackCost('tts', data.tts_chars * 0.000030);
                }
            } else if (data.type === "done") {
                isDone = true;
                if (pendingChunks === 0) {
                    isPlaying = false;
                    setWaveform(false);
                    let action = fullResponse.includes("[[END_INTERVIEW]]") ? "end" : 
                                 (fullResponse.includes("[[PREVIOUS]]") ? "previous" :
                                 (fullResponse.includes("[[REPEAT]]") || (fullResponse.toLowerCase().includes("repeat") && fullResponse.length < 200) ? "repeat" : "next"));
                    
                    setTimeout(() => {
                        ws.onmessage = null; 
                        resolve({ action, response: fullResponse.replace("[[END_INTERVIEW]]", "").replace("[[REPEAT]]", "").replace("[[PREVIOUS]]", "").trim() });
                    }, 1000); 
                }
            } else if (data.type === "error") {
                console.error("WebSocket Stream Error:", data.msg);
                showToast(`❌ logic server error: ${data.msg}`, 'rose');
                ws.onmessage = null;
                resolve({ response: "I'm sorry, I encountered an error. Could you repeat that?", action: "repeat" });
            }
        };

        const sendAsk = () => {
            const askStart = performance.now();
            console.log(`[LATENCY] Thinking request sent at ${askStart.toFixed(1)}ms`);
            ws.send(JSON.stringify({
                action: "ask",
                prev: prevQ,
                transcript: userAns,
                nextQ: nextQObjOrInstruction
            }));
        };

        const WS_PROTO = window.location.protocol === "https:" ? "wss://" : "ws://";
        const STREAM_URL = WS_PROTO + window.location.host + "/ws/v1/interview-stream";

        if (conversationalWS && conversationalWS.readyState === WebSocket.OPEN) {
            ws = conversationalWS;
            ws.onmessage = onMessage;
            sendAsk();
        } else {
            console.log('🔌 Opening new logic server connection...');
            ws = new WebSocket(STREAM_URL);
            ws.binaryType = "arraybuffer";
            conversationalWS = ws;
            ws.onopen = sendAsk;
            ws.onmessage = onMessage;
            
            setTimeout(() => {
                if (ws.readyState === WebSocket.CONNECTING) {
                    console.warn('⌛ Logic server connection slow/stuck...');
                    showToast('Logic server connection slow...', 'amber');
                }
            }, 1000);
        }
        
        ws.onerror = (err) => {
            console.error("WebSocket connection error:", err);
            showToast('Logic server connection failed. Retrying interview turn...', 'rose');
            ws.onmessage = null;
            resolve({ response: "Connection issue. Let me try again.", action: "repeat" });
        };
    });
}
