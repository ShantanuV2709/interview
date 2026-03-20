# Artificial Intelligence Interview Agent - API Integration & Microservice Report

This document outlines the refactored system architecture of the standalone AI Interviewer microservice, the step-by-step instructions for integrating it into the `hr-solution` frontend, the potential risks to mitigate, and a comprehensive summary of the system improvements implemented today.

## Integration Blueprint

**The Pre-Interview Stage**
Before the candidate even joins the screen, your `hr-solution` backend or database will trigger a request to `POST /api/v1/generate-questions` with the Job Description. You will save those 6 JSON questions into your database for that specific interview session.

**The Live Interview Loop**
Once the user clicks "Start Interview" in your front-end (React/Next.js), you bypass Retell entirely using this flow:
* **The Microphone:** Let the frontend record the candidate's voice natively (using `MediaRecorder`).
* **Transcription:** When the candidate stops talking, the frontend shoots that audio completely independently to `POST /api/v1/stt` to fetch the transcribed text string.
* **The Brain (WebSocket):** The frontend opens a persistent connection to `ws://[YOUR_SERVER_IP]:3000/ws/v1/interview-stream`. As soon as the frontend gets the STT string, it passes a JSON payload into the socket containing the text and the current question.
* **The Response:** The WebSocket instantly spits back a stream of tokens (for typing out the UI transcript) and tightly packed MP3 Audio arrays (for the browser to play out loud seamlessly). 

**The Verdict**
When "Emma" signals `[[END_INTERVIEW]]`, your frontend drops the WebSocket connection, compiles the 6 questions and 6 transcribed answers into a single array, and posts it to `POST /api/v1/score` to generate the final candidate report card!

---


