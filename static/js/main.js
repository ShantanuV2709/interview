// ─────────────────────────────────────────────────────────────────────────────
// STATE
// ─────────────────────────────────────────────────────────────────────────────
let WS_BASE_URL;
if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
    WS_BASE_URL = "ws://" + window.location.hostname + ":3000/ws/v1/interview-stream"; // Updated to unified WS
} else {
    WS_BASE_URL = (window.location.protocol === "https:" ? "wss://" : "ws://") + window.location.host + "/ws/v1/interview-stream";
}

let sessionCost = { qgen: 0, tts: 0, stt: 0, score: 0 };
let generatedQuestions = [];
let storedTranscripts = [];
let currentQIdx = -1;
let callActive = false;
let currentQHistory = [];
let ttsCharCount = 0;
let sttSecCount = 0;
let mediaRecorder = null;
let audioChunks = [];
let recordingStartTime = null;
let currentAudio = null;

// VAD (Voice Activity Detection) state variables
let vadAudioContext = null;
let vadSource = null;
let vadNode = null;
let isNavigating = false;

// Deepgram real-time streaming STT state
let deepgramKey = '';
let liveTranscript = '';
let dgSocket = null;
let conversationalWS = null; // Reused for thinking turns
let dgAudioCtx = null;
let dgSource = null;
let dgProcessor = null;
let sttFallbackMode = false; // Set to true if Deepgram WS fails

// Brain telemetry WebSocket
let brainWS = null;
let brainReconnectTimer = null;
const BRAIN_WS_URL = "ws://" + window.location.hostname + ":3000/ws/v1/brain"; // Updated to unified port

// ─────────────────────────────────────────────────────────────────────────────
// INITIALIZATION
// ─────────────────────────────────────────────────────────────────────────────
window.addEventListener('load', () => {
    console.log('✅ Page fully loaded');
    buildScaleTable();
    updateCostDisplay();
    
    // Fetch Deepgram key
    fetch('/deepgram-key')
        .then(r => r.json())
        .then(d => { 
            deepgramKey = d.key; 
            console.log('Deepgram key loaded ⚡'); 
            const dgKeyEl = document.getElementById('dg-debug-key');
            if (dgKeyEl) {
                dgKeyEl.textContent = 'LOADED';
                dgKeyEl.style.color = 'var(--teal)';
            }
        })
        .catch(e => console.warn('Deepgram key fetch failed:', e));

    // Connect to Brain
    setTimeout(() => brainConnect(), 1000);
});
