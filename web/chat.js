const messagesEl = document.getElementById('messages');
const inputEl = document.getElementById('query-input');
const sendBtn = document.getElementById('send-btn');
const statusEl = document.getElementById('status');

let sessionId = null;
let isStreaming = false;

const TOOL_NAMES = {
    'search_emails': 'Поиск писем',
    'search_by_participant': 'Поиск по участнику',
    'search_by_date_range': 'Поиск по дате',
    'search_entities': 'Поиск сущностей',
    'get_email': 'Загрузка письма',
    'get_thread': 'Загрузка цепочки',
    'get_email_stats': 'Статистика',
    'get_top_senders': 'Топ отправителей',
};

function addMessage(role, content) {
    const div = document.createElement('div');
    div.className = `message ${role}`;
    if (content) div.textContent = content;
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return div;
}

function addStepIndicator(msgDiv, step) {
    const ind = document.createElement('div');
    ind.className = 'step-indicator';
    ind.textContent = `Шаг ${step}`;
    msgDiv.appendChild(ind);
}

function addToolCall(msgDiv, tool) {
    const tc = document.createElement('div');
    tc.className = 'tool-call';
    tc.textContent = TOOL_NAMES[tool] || tool;
    msgDiv.appendChild(tc);
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

    // Create steps container (collapsible) and answer container
    const wrapper = document.createElement('div');
    wrapper.className = 'message assistant';
    messagesEl.appendChild(wrapper);

    const stepsDiv = document.createElement('div');
    stepsDiv.className = 'steps-container';
    wrapper.appendChild(stepsDiv);

    const answerDiv = document.createElement('div');
    answerDiv.className = 'answer-text';
    wrapper.appendChild(answerDiv);

    setStatus('Думаю...');

    let fullText = '';
    let answerMode = false;

    try {
        const body = { message: query };
        if (sessionId) body.session_id = sessionId;

        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });

        const sid = response.headers.get('X-Session-Id');
        if (sid) sessionId = sid;

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                const data = line.slice(6).trim();
                if (data === '[DONE]') break;

                try {
                    const event = JSON.parse(data);
                    switch (event.event) {
                        case 'step_start':
                            addStepIndicator(stepsDiv, event.step);
                            setStatus(`Шаг ${event.step}/${event.max_steps}`);
                            break;

                        case 'token':
                            const text = event.text || '';
                            fullText += text;

                            if (text.includes('Answer:')) {
                                answerMode = true;
                                const after = text.split('Answer:')[1] || '';
                                answerDiv.textContent = after;
                            } else if (answerMode) {
                                answerDiv.textContent += text;
                            }

                            if (!answerMode && text.length < 80) {
                                const statusText = text.replace(/\n/g, ' ').trim();
                                if (statusText) setStatus(statusText.substring(0, 60));
                            }
                            break;

                        case 'tool_call':
                            addToolCall(stepsDiv, event.tool);
                            setStatus(`${TOOL_NAMES[event.tool] || event.tool}...`);
                            break;

                        case 'tool_result':
                            // Don't show raw results — just update status
                            setStatus('Анализирую результаты...');
                            break;

                        case 'done':
                            setStatus(`Готово (${event.steps_used} шагов)`);
                            if (!answerMode && answerDiv.textContent === '') {
                                // Fallback: extract answer from full text
                                // Remove tool call blocks and thinking prefixes
                                let cleaned = fullText
                                    .replace(/```tool[\s\S]*?```/g, '')
                                    .replace(/^Thinking:.*$/gm, '')
                                    .replace(/^Tool result[\s\S]*?(?=\n\n|\Z)/gm, '')
                                    .trim();
                                answerDiv.textContent = cleaned || 'Ответ не сгенерирован.';
                            }
                            // Hide steps if empty
                            if (!stepsDiv.children.length) {
                                stepsDiv.style.display = 'none';
                            }
                            break;

                        case 'error':
                            answerDiv.textContent = event.message || 'Ошибка';
                            setStatus('Ошибка');
                            break;
                    }
                } catch (e) {
                    // Skip unparseable
                }
            }
            messagesEl.scrollTop = messagesEl.scrollHeight;
        }
    } catch (err) {
        answerDiv.textContent = `Ошибка подключения: ${err.message}`;
        setStatus('Отключено');
    }

    isStreaming = false;
    sendBtn.disabled = false;
    inputEl.focus();
}

sendBtn.addEventListener('click', sendMessage);
inputEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

fetch('/api/health')
    .then(r => r.json())
    .then(() => setStatus('Готов'))
    .catch(() => setStatus('Сервер недоступен'));
