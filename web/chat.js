const messagesEl = document.getElementById('messages');
const inputEl = document.getElementById('query-input');
const sendBtn = document.getElementById('send-btn');
const statusEl = document.getElementById('status');

let sessionId = null;
let isStreaming = false;

function addMessage(role, content) {
    const div = document.createElement('div');
    div.className = `message ${role}`;
    div.textContent = content;
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return div;
}

function appendToLast(text) {
    const last = messagesEl.querySelector('.message:last-child');
    if (last && last.classList.contains('assistant')) {
        last.textContent += text;
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }
}

function addStepIndicator(step) {
    const last = messagesEl.querySelector('.message.assistant:last-child');
    if (last) {
        const ind = document.createElement('div');
        ind.className = 'step-indicator';
        ind.textContent = `Step ${step}`;
        last.appendChild(ind);
    }
}

function addToolCall(tool, args) {
    const last = messagesEl.querySelector('.message.assistant:last-child');
    if (last) {
        const tc = document.createElement('div');
        tc.className = 'tool-call';
        tc.textContent = `${tool}(${JSON.stringify(args).substring(0, 80)})`;
        last.appendChild(tc);
    }
}

function addToolResult(preview) {
    const last = messagesEl.querySelector('.message.assistant:last-child');
    if (last) {
        const tr = document.createElement('div');
        tr.className = 'tool-result';
        tr.textContent = preview.substring(0, 200);
        last.appendChild(tr);
    }
}

function setStatus(text) {
    statusEl.textContent = text;
}

async function sendMessage() {
    const query = inputEl.value.trim();
    if (!query || isStreaming) return;

    isStreaming = true;
    sendBtn.disabled = true;
    inputEl.value = '';

    addMessage('user', query);
    const assistantDiv = addMessage('assistant', '');
    setStatus('Thinking...');

    try {
        const body = { message: query };
        if (sessionId) body.session_id = sessionId;

        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });

        // Capture session ID
        const sid = response.headers.get('X-Session-Id');
        if (sid) sessionId = sid;

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let answerMode = false;

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                const data = line.slice(6).trim();
                if (data === '[DONE]') continue;

                try {
                    const event = JSON.parse(data);
                    switch (event.event) {
                        case 'step_start':
                            if (event.step > 1) {
                                addStepIndicator(event.step);
                            }
                            setStatus(`Step ${event.step}/${event.max_steps}`);
                            break;
                        case 'token':
                            const text = event.text || '';
                            if (text.includes('Answer:')) {
                                answerMode = true;
                                const after = text.split('Answer:')[1] || '';
                                assistantDiv.textContent = after;
                            } else if (answerMode) {
                                assistantDiv.textContent += text;
                            }
                            // Show thinking in status
                            if (!answerMode && text.length < 80) {
                                setStatus(text.replace(/\n/g, ' ').substring(0, 60));
                            }
                            break;
                        case 'tool_call':
                            addToolCall(event.tool, event.args || {});
                            setStatus(`Calling ${event.tool}...`);
                            break;
                        case 'tool_result':
                            addToolResult(event.preview || '');
                            break;
                        case 'done':
                            setStatus(`Done (${event.steps_used} steps)`);
                            // If no answer was detected, show everything
                            if (!answerMode && assistantDiv.textContent === '') {
                                // Fallback: show full response
                            }
                            break;
                        case 'error':
                            assistantDiv.textContent = event.message || 'Error';
                            setStatus('Error');
                            break;
                    }
                } catch (e) {
                    // Skip unparseable lines
                }
            }
            messagesEl.scrollTop = messagesEl.scrollHeight;
        }
    } catch (err) {
        assistantDiv.textContent = `Connection error: ${err.message}`;
        setStatus('Disconnected');
    }

    isStreaming = false;
    sendBtn.disabled = false;
    inputEl.focus();
}

// Event listeners
sendBtn.addEventListener('click', sendMessage);
inputEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

// Health check
fetch('/api/health')
    .then(r => r.json())
    .then(d => setStatus('Ready'))
    .catch(() => setStatus('Server unavailable'));
