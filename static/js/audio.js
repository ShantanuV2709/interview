/**
 * Audio Module - Handles TTS, STT, VAD, and audio processing.
 */

function cleanupVAD() {
    if (dgProcessor) { dgProcessor.disconnect(); dgProcessor.onaudioprocess = null; dgProcessor = null; }
    if (dgSocket) { if (dgSocket.readyState === WebSocket.OPEN) dgSocket.close(); dgSocket = null; }
    if (vadNode) { vadNode.disconnect(); vadNode.onaudioprocess = null; vadNode = null; }
    if (dgSource) { dgSource.disconnect(); dgSource = null; }
    if (dgAudioCtx && dgAudioCtx.state !== "closed") { dgAudioCtx.close().catch(e => console.error(e)); dgAudioCtx = null; }
    if (vadSource) { vadSource.disconnect(); vadSource = null; }
    if (vadAudioContext && vadAudioContext.state !== "closed") { vadAudioContext.close().catch(e => console.error(e)); vadAudioContext = null; }
    brainRecord('interview', 'cleanup', 'VAD and audio resources released', 'info');
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
            if (!hasSpoken) {
                console.log('🎤 VAD: Speech detected');
                hasSpoken = true;
            }
        } else if (hasSpoken && (Date.now() - silenceStart > 3000)) { // 3s pause safe
            if (Date.now() - recordingStartTime > 3000) {
                console.log('🔇 VAD: Silence detected (3s), submitting...');
                hasSpoken = false;
                nextQ();
            } else {
                hasSpoken = false;
            }
        }
    };
}

async function speakQuestion(text) {
    const startFetch = performance.now();
    console.log(`[LATENCY] Q${currentQIdx + 1} TTS Fetch started...`);
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
            console.error('TTS raw error response:', raw);
            let msg = `TTS HTTP ${res.status}`;
            try { msg = JSON.parse(raw)?.error?.message || msg; } catch (_) { }
            throw new Error(msg);
        }

        const data = await res.json();
        const audioB64 = data.audios?.[0];
        
        if (!audioB64) throw new Error("Received empty or corrupt audio buffer");

        const fetchEnd = performance.now();
        console.log(`[LATENCY] Q${currentQIdx + 1} TTS Fetch + Buffer: ${(fetchEnd - startFetch).toFixed(1)}ms`);

        const cost = chars * 0.0000165;
        ttsCharCount += chars;
        trackCost('tts', cost);
        updateInterviewCostDisplay();

        return await new Promise((resolve) => {
            const timer = setTimeout(() => {
                console.warn('⌛ TTS playback safety timeout reached');
                resolve(false); 
            }, 15000); 
            
            currentAudio = new Audio(`data:audio/mp3;base64,${audioB64}`);
            currentAudio.onplay = () => {
                const playStart = performance.now();
                console.log(`[LATENCY] Q${currentQIdx + 1} Audio Playback Started: ${(playStart - fetchEnd).toFixed(1)}ms after fetch`);
            };
            currentAudio.onended = () => { 
                clearTimeout(timer); 
                console.log(`[LATENCY] Q${currentQIdx + 1} Audio Playback Finished. Waiting for sync buffer...`);
                setTimeout(() => {
                    console.log(`[LATENCY] Q${currentQIdx + 1} Sync Buffer Done. Starting Recording.`);
                    resolve(true); 
                }, 1000); 
            };
            currentAudio.onerror = (e) => { 
                clearTimeout(timer); 
                console.error('Audio object error:', e);
                resolve(false); 
            };
            currentAudio.play().catch(err => {
                clearTimeout(timer);
                console.warn('🔇 Audio play blocked or failed:', err);
                resolve(false); 
            });
        });

    } catch (err) {
        showToast(`TTS error: ${err.message}`, 'rose');
        console.error('TTS error full:', err);
        return false;
    }
}

function pcmToWavBlob(int16, sampleRate) {
    const wavBuf = new ArrayBuffer(44 + int16.byteLength);
    const view = new DataView(wavBuf);
    const ws = (off, str) => { for (let i = 0; i < str.length; i++) view.setUint8(off + i, str.charCodeAt(i)); };
    ws(0, 'RIFF');
    view.setUint32(4, 36 + int16.byteLength, true);
    ws(8, 'WAVE');
    ws(12, 'fmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);  // PCM
    view.setUint16(22, 1, true);  // mono
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    ws(36, 'data');
    view.setUint32(40, int16.byteLength, true);
    new Int16Array(wavBuf, 44).set(int16);
    return new Blob([wavBuf], { type: 'audio/wav' });
}

async function sttRequest(wavBlob, chunkIdx, total) {
    if (total > 1) {
        document.getElementById('callStatusText').textContent = `Transcribing chunk ${chunkIdx + 1}/${total}...`;
    }
    const res = await fetch('/proxy/deepgram/v1/listen?model=nova-2&language=en-IN&smart_format=true&punctuate=true', {
        method: 'POST',
        headers: { 'Content-Type': 'audio/wav' },
        body: wavBlob
    });
    if (!res.ok) {
        const raw = await res.text();
        let msg = `STT HTTP ${res.status}`;
        try { msg = JSON.parse(raw)?.err_msg || msg; } catch (_) { }
        throw new Error(msg);
    }
    const data = await res.json();
    return data?.results?.channels?.[0]?.alternatives?.[0]?.transcript || '';
}

async function transcribeAnswer(durationSec) {
    if (!audioChunks.length) return '';

    let transcript = '';
    const answeredQ = generatedQuestions[currentQIdx].dynamicText || generatedQuestions[currentQIdx].text;

    try {
        document.getElementById('callStatusText').textContent = 'Converting audio to WAV...';
        const rawBlob = new Blob(audioChunks, { type: audioChunks[0]?.type || 'audio/webm' });
        const arrayBuf = await rawBlob.arrayBuffer();
        const audioCtx = new AudioContext({ sampleRate: STT_SAMPLE_RATE });
        const decoded = await audioCtx.decodeAudioData(arrayBuf);
        audioCtx.close();

        const numSamples = decoded.length;
        const pcm = new Float32Array(numSamples);
        for (let c = 0; c < decoded.numberOfChannels; c++) {
            const ch = decoded.getChannelData(c);
            for (let i = 0; i < numSamples; i++) pcm[i] += ch[i];
        }
        if (decoded.numberOfChannels > 1) {
            for (let i = 0; i < numSamples; i++) pcm[i] /= decoded.numberOfChannels;
        }

        const int16 = new Int16Array(numSamples);
        for (let i = 0; i < numSamples; i++) {
            const s = Math.max(-1, Math.min(1, pcm[i]));
            int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }

        const chunkSamples = STT_CHUNK_SEC * STT_SAMPLE_RATE;
        const numChunks = Math.ceil(numSamples / chunkSamples);
        const transcripts = [];

        for (let i = 0; i < numChunks; i++) {
            const start = i * chunkSamples;
            const end = Math.min(start + chunkSamples, numSamples);
            const slice = int16.slice(start, end);
            const wavBlob = pcmToWavBlob(slice, STT_SAMPLE_RATE);
            const text = await sttRequest(wavBlob, i, numChunks);
            transcripts.push(text);
        }

        transcript = transcripts.join(' ').trim() || '[No transcript returned]';
        const cost = durationSec * 0.0000716;
        sttSecCount += durationSec;
        trackCost('stt', cost);
        updateInterviewCostDisplay();

        document.getElementById('liveTranscript').innerHTML = `
<span class="transcript-tag">Your Answer</span>
<span class="transcript-lang">en-IN · Deepgram nova-2</span>
${transcript}
`;
        storeTranscript(currentQIdx, answeredQ, transcript);
        document.getElementById('callStatusText').textContent =
            `Q${currentQIdx + 1} transcribed (${durationSec.toFixed(1)}s · ${numChunks} chunk${numChunks > 1 ? 's' : ''} · $${cost.toFixed(5)})`;
    } catch (err) {
        showToast(`STT error: ${err.message}`, 'rose');
        console.error('STT error:', err);
        storeTranscript(currentQIdx, answeredQ, '[Transcription failed]');
        transcript = '[Transcription failed]';
    }
    setWaveform(false);
    return transcript;
}

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
                if (!isPlaying) { setWaveform(true); isPlaying = true; }
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
                                 (fullResponse.toLowerCase().includes("repeat") && fullResponse.length < 200) ? "repeat" : "next");
                     resolve({ action, response: fullResponse.replace("[[END_INTERVIEW]]", "").replace("[[REPEAT]]", "").replace("[[PREVIOUS]]", "").trim() });
                 }
            }
        }
        
        const onMessage = (msg) => {
            if (msg.data instanceof ArrayBuffer) { playAudioChunk(msg.data); return; }
            const data = JSON.parse(msg.data);
            if (data.type === "token") {
                fullResponse += data.text;
                const displayText = fullResponse.replace("[[END_INTERVIEW]]", "").replace("[[REPEAT]]", "").replace("[[PREVIOUS]]", "").replace(/\\n/g, '<br/>');
                document.getElementById('currentQ').innerHTML = displayText;
            } else if (data.type === "usage") {
                if (data.openai) trackCost('qgen', (data.openai.input * 0.0000025) + (data.openai.output * 0.00001));
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
            ws.send(JSON.stringify({
                action: "ask",
                prev: prevQ,
                transcript: userAns,
                nextQ: nextQObjOrInstruction
            }));
        };

        if (conversationalWS && conversationalWS.readyState === WebSocket.OPEN) {
            ws = conversationalWS;
            ws.onmessage = onMessage;
            sendAsk();
        } else {
            ws = new WebSocket(WS_BASE_URL);
            ws.binaryType = "arraybuffer";
            conversationalWS = ws;
            ws.onopen = sendAsk;
            ws.onmessage = onMessage;
        }
        ws.onerror = (err) => {
            console.error("WebSocket connection error:", err);
            showToast('Logic server connection failed. Retrying interview turn...', 'rose');
            ws.onmessage = null;
            resolve({ response: "Connection issue. Let me try again.", action: "repeat" });
        };
    });
}
