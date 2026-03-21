// ─────────────────────────────────────────────────────────────────────────────
// AUDIO & VAD
// ─────────────────────────────────────────────────────────────────────────────
async function speakQuestion(text) {
    const startFetch = performance.now();
    console.log(`[LATENCY] TTS Fetch started...`);
    try {
        const chars = text.length;
        const res = await fetch('/proxy/sarvam/text-to-speech', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                inputs: [text],
                target_language_code: 'en-IN',
                speaker: 'anushka',
                model: 'bulbul:v2',
                audio_format: 'mp3',
                pace: 0.95,
                pitch: 0,
                loudness: 1.4,
                enable_preprocessing: true
            })
        });

        if (!res.ok) {
            const raw = await res.text();
            let msg = `TTS HTTP ${res.status}`;
            try { msg = JSON.parse(raw)?.error?.message || msg; } catch (_) { }
            throw new Error(msg);
        }

        const data = await res.json();
        const audioB64 = data.audios?.[0];
        if (!audioB64) throw new Error("Received empty audio buffer");

        const fetchEnd = performance.now();
        console.log(`[LATENCY] TTS Fetch + Buffer: ${(fetchEnd - startFetch).toFixed(1)}ms`);

        const cost = chars * 0.0000165;
        ttsCharCount += chars;
        trackCost('tts', cost);
        if (typeof updateInterviewCostDisplay === 'function') updateInterviewCostDisplay();

        return await new Promise((resolve) => {
            const timer = setTimeout(() => {
                console.warn('⌛ TTS playback safety timeout reached');
                resolve(false); 
            }, 10000); 
            
            currentAudio = new Audio(`data:audio/mp3;base64,${audioB64}`);
            currentAudio.onplay = () => {
                console.log(`[LATENCY] Audio Playback Started: ${(performance.now() - fetchEnd).toFixed(1)}ms after fetch`);
            };
            currentAudio.onended = () => { 
                clearTimeout(timer); 
                console.log(`[LATENCY] Audio Playback Finished. Waiting for sync...`);
                setTimeout(() => resolve(true), 800); 
            };
            currentAudio.onerror = (e) => { 
                clearTimeout(timer); 
                console.error('Audio object error:', e);
                resolve(false); 
            };
            currentAudio.play().catch(err => {
                clearTimeout(timer);
                console.warn('🔇 Audio play blocked:', err);
                resolve(false); 
            });
        });

    } catch (err) {
        showToast(`TTS error: ${err.message}`, 'rose');
        console.error('TTS error:', err);
        return false;
    }
}

async function startRecording() {
    console.log('🚀 startRecording() called');
    liveTranscript = '';
    try {
        const stream = await navigator.mediaDevices.getUserMedia({
            audio: { sampleRate: 16000, channelCount: 1, echoCancellation: true }
        });
        audioChunks = [];
        recordingStartTime = Date.now();

        const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
            ? 'audio/webm;codecs=opus' : 'audio/webm';
        mediaRecorder = new MediaRecorder(stream, { mimeType });
        mediaRecorder.ondataavailable = e => { if (e.data.size > 0) audioChunks.push(e.data); };
        mediaRecorder.start(100);

        dgAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
        if (dgAudioCtx.state === 'suspended') await dgAudioCtx.resume();
        dgSource = dgAudioCtx.createMediaStreamSource(stream);

        // VAD setup
        let analyser = dgAudioCtx.createAnalyser();
        analyser.smoothingTimeConstant = 0.8;
        analyser.fftSize = 512;
        vadNode = dgAudioCtx.createScriptProcessor(2048, 1, 1);
        dgSource.connect(analyser);
        analyser.connect(vadNode);
        vadNode.connect(dgAudioCtx.destination);
        
        let silenceStart = Date.now();
        let hasSpoken = false;

        // STT Logic
        if (!sttFallbackMode) {
            setupDeepgramWS(analyser, silenceStart, hasSpoken);
        } else {
            console.log('Using STT Fallback Mode (Batch HTTP)');
            document.getElementById('callStatusText').textContent = '🎙 Recording (Batch Fallback)...';
            setupVAD(analyser, silenceStart, hasSpoken);
        }

        const volContainer = document.getElementById('volume-meter-container');
        if (volContainer) volContainer.style.display = 'flex';
    } catch (err) {
        showToast(`Microphone access denied: ${err.message}`, 'rose');
        document.getElementById('callStatusText').textContent = 'Microphone access denied';
    }
}

function setupDeepgramWS(analyser, silenceStart, hasSpoken) {
    const WS_PROTO = window.location.protocol === "https:" ? "wss://" : "ws://";
    const STT_URL = WS_PROTO + window.location.host + "/ws/v1/interview-stream";

    dgSocket = new WebSocket(STT_URL);
    dgSocket.binaryType = 'arraybuffer';

    const dgTimeout = setTimeout(() => {
        if (dgSocket.readyState !== WebSocket.OPEN) {
            console.warn('⌛ STT Proxy connection timed out — enabling fallback');
            sttFallbackMode = true;
            resolveSTT(); 
        }
    }, 5000);

    dgSocket.onopen = () => {
        clearTimeout(dgTimeout);
        console.log('✅ Connected to STT Proxy');
        dgSocket.send(JSON.stringify({ action: 'stt', sample_rate: dgAudioCtx.sampleRate }));
        document.getElementById('callStatusText').textContent = '🎙 Recording with Deepgram...';
        const stBad = document.getElementById('dg-debug-status');
        if (stBad) {
            stBad.textContent = 'CONNECTED';
            stBad.style.color = 'var(--teal)';
        }
    };

    dgSocket.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.type === 'Results') {
                const t = data.channel?.alternatives?.[0]?.transcript || '';
                if (t.trim()) {
                    if (data.is_final) {
                        liveTranscript += (liveTranscript ? ' ' : '') + t.trim();
                        const lowerFull = liveTranscript.toLowerCase();
                        if (lowerFull.includes("next question") || lowerFull.includes("move on")) {
                            showToast('🗣 Command detected: Moving to next...', 'teal');
                            if (typeof nextQ === 'function') nextQ();
                            return; 
                        }
                    }
                    const liveEl = document.getElementById('liveTranscript');
                    if (liveEl) {
                        liveEl.innerHTML = `
                            <span class="transcript-tag">Your Answer</span>
                            <span class="transcript-lang">en-IN &middot; Deepgram nova-2 &#x26A1;</span>
                            ${liveTranscript} <span style="opacity:0.6">${!data.is_final ? t : ''}</span>`;
                    }
                }
            }
        } catch(e) {}
    };

    dgProcessor = dgAudioCtx.createScriptProcessor(4096, 1, 1);
    dgSource.connect(dgProcessor);
    dgProcessor.connect(dgAudioCtx.destination);
    dgProcessor.onaudioprocess = (e) => {
        if (!dgSocket || dgSocket.readyState !== WebSocket.OPEN) return;
        const f32 = e.inputBuffer.getChannelData(0);
        const i16 = new Int16Array(f32.length);
        for (let i = 0; i < f32.length; i++) {
            const s = Math.max(-1, Math.min(1, f32[i]));
            i16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        dgSocket.send(i16.buffer);
    };

    setupVAD(analyser, silenceStart, hasSpoken);
}

function setupVAD(analyser, silenceStart, hasSpoken) {
    vadNode.onaudioprocess = function() {
        let array = new Uint8Array(analyser.frequencyBinCount);
        analyser.getByteFrequencyData(array);
        let sum = 0;
        for(let i = 0; i < array.length; i++) sum += array[i];
        const avg = sum / array.length;
        
        const volBar = document.getElementById('volume-bar');
        if (volBar) {
            const pct = Math.min(100, (avg / 60) * 100);
            volBar.style.width = pct + '%';
            volBar.style.background = avg > 20 ? 'var(--teal)' : 'var(--muted)';
        }

        if (avg > 15) {
            silenceStart = Date.now();
            if (!hasSpoken) { hasSpoken = true; }
        } else if (hasSpoken && (Date.now() - silenceStart > 2500)) { 
            if (Date.now() - recordingStartTime > 2500) {
                console.log('🔇 VAD: Silence detected (2.5s), submitting...');
                hasSpoken = false;
                if (typeof nextQ === 'function') nextQ();
            } else {
                hasSpoken = false;
            }
        }
    };
}

function cleanupVAD() {
    if (dgProcessor) { dgProcessor.disconnect(); dgProcessor.onaudioprocess = null; dgProcessor = null; }
    if (dgSocket) { if (dgSocket.readyState === WebSocket.OPEN) dgSocket.close(); dgSocket = null; }
    if (vadNode) { vadNode.disconnect(); vadNode.onaudioprocess = null; vadNode = null; }
    if (dgSource) { dgSource.disconnect(); dgSource = null; }
    if (dgAudioCtx && dgAudioCtx.state !== 'closed') { dgAudioCtx.close().catch(() => {}); dgAudioCtx = null; }
    brainRecord('audio', 'cleanup', 'Audio resources released', 'info');
}
