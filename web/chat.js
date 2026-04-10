const messagesEl = document.getElementById('messages');
const inputEl = document.getElementById('query-input');
const sendBtn = document.getElementById('send-btn');
const statusEl = document.getElementById('status');

let sessionId = localStorage.getItem('smartcat_session') || null;
let isStreaming = false;

// Restore chat history from localStorage
function restoreHistory() {
    const saved = localStorage.getItem('smartcat_messages');
    if (!saved) return;
    try {
        const msgs = JSON.parse(saved);
        const welcome = document.getElementById('welcome');
        if (msgs.length > 0 && welcome) welcome.style.display = 'none';
        msgs.forEach(m => {
            const div = document.createElement('div');
            div.className = `message ${m.role}`;
            if (m.role === 'assistant' && typeof marked !== 'undefined') {
                const answerDiv = document.createElement('div');
                answerDiv.className = 'answer-text';
                answerDiv.innerHTML = marked.parse(m.text);
                div.appendChild(answerDiv);
            } else {
                div.textContent = m.text;
            }
            messagesEl.appendChild(div);
        });
        messagesEl.scrollTop = messagesEl.scrollHeight;
    } catch (e) {}
}

function saveHistory(role, text) {
    try {
        const saved = JSON.parse(localStorage.getItem('smartcat_messages') || '[]');
        saved.push({ role, text });
        // Keep last 50 messages
        while (saved.length > 50) saved.shift();
        localStorage.setItem('smartcat_messages', JSON.stringify(saved));
    } catch (e) {}
}

function saveSession(sid) {
    sessionId = sid;
    localStorage.setItem('smartcat_session', sid);
}

restoreHistory();

const SAMPLE_QUESTIONS = [
    "Когда Enron подал заявление о банкротстве по Chapter 11?",
    "Кто отправил письмо о бонусах перед банкротством и какая сумма упоминалась?",
    "Кто такой Jeff Dasovich и какова была его роль?",
    "Когда PG&E подала заявление о банкротстве?",
    "Кто были самые частые отправители писем в Enron?",
    "Кто такая Sara Shackleton и в каком отделе она работала?",
    "За что отвечала Tana Jones судя по её переписке?",
    "Кто были ключевые люди в обсуждении Калифорнийского энергетического кризиса?",
    "Что произошло в Enron в октябре 2001 года?",
    "Найди письма об уничтожении документов Arthur Andersen",
    "Когда Ken Lay отправил последнее корпоративное письмо?",
    "Какие основные юридические вопросы обсуждались в переписке Enron?",
    "Найди обсуждения контрактов ISDA и торговых соглашений",
    "Какие стратегии торговли природным газом обсуждались?",
    "Какие предупреждающие знаки существовали перед крахом Enron?",
    "Какая связь между Калифорнийским энергетическим кризисом и торговлей Enron?",
    "Были ли письма, указывающие на сокрытие информации сотрудниками?",
    "Кто были ключевые лица, принимающие решения в последние месяцы?",
];

// Set random sample question
const sampleQ = SAMPLE_QUESTIONS[Math.floor(Math.random() * SAMPLE_QUESTIONS.length)];
const sampleTextEl = document.getElementById('sample-text');
if (sampleTextEl) sampleTextEl.textContent = sampleQ;

let lastQuery = '';

function useSample() {
    const el = document.querySelector('.welcome-sample-text');
    inputEl.value = el ? el.textContent : sampleQ;
    sendMessage();
}

function retryLast() {
    if (lastQuery && !isStreaming) {
        // Remove the failed message pair
        const msgs = messagesEl.querySelectorAll('.message');
        if (msgs.length >= 2) {
            msgs[msgs.length - 1].remove(); // assistant (error)
            msgs[msgs.length - 2].remove(); // user
        }
        inputEl.value = lastQuery;
        sendMessage();
    }
}

function hideWelcome() {
    const welcome = document.getElementById('welcome');
    if (welcome) welcome.style.display = 'none';
}

function resetChat() {
    // Clear localStorage
    localStorage.removeItem('smartcat_session');
    localStorage.removeItem('smartcat_messages');
    sessionId = null;
    _msgCounter = 0;

    // Clear messages, restore welcome
    messagesEl.innerHTML = '';
    const welcome = document.createElement('div');
    welcome.id = 'welcome';
    welcome.innerHTML = `
        <div class="welcome-emoji">&#128049;</div>
        <h2 class="welcome-title">SmartCat</h2>
        <p class="welcome-desc">
            AI-ассистент для поиска и анализа email-переписки.<br>
            245K писем Enron, гибридный поиск, 31K QA-пар.
        </p>
        <div class="welcome-sample" onclick="useSample()">
            <div class="welcome-sample-label">Попробуй спросить:</div>
            <div class="welcome-sample-text">${SAMPLE_QUESTIONS[Math.floor(Math.random() * SAMPLE_QUESTIONS.length)]}</div>
        </div>`;
    messagesEl.appendChild(welcome);

    // Update sample question for useSample()
    const newQ = SAMPLE_QUESTIONS[Math.floor(Math.random() * SAMPLE_QUESTIONS.length)];
    welcome.querySelector('.welcome-sample-text').textContent = newQ;

    setStatus('Готов');
    inputEl.value = '';
    inputEl.focus();
}

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

function setStatus(text) {
    statusEl.textContent = text;
}

const contextBar = document.getElementById('context-bar');
const contextFill = document.getElementById('context-fill');
const contextLabel = document.getElementById('context-label');

function updateContext(usage, tokens) {
    contextBar.style.display = '';
    const pct = parseInt(usage) || 0;
    contextFill.style.width = pct + '%';
    contextFill.className = pct >= 90 ? 'critical' : pct >= 70 ? 'warning' : '';
    contextLabel.textContent = `ctx ${pct}%`;
}

function hideContext() {
    contextBar.style.display = 'none';
}

let _msgCounter = 0;

async function sendMessage() {
    const query = inputEl.value.trim();
    if (!query || isStreaming) return;

    isStreaming = true;
    sendBtn.disabled = true;
    inputEl.value = '';
    lastQuery = query;

    _msgCounter++;
    const currentMsgId = `msg-${_msgCounter}`;

    hideWelcome();
    addMessage('user', query);
    saveHistory('user', query);

    // Use async polling on mobile (SSE breaks when screen off)
    const isMobile = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
    if (isMobile) {
        await sendMessageAsync(query, currentMsgId);
        return;
    }

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

    // Thinking indicator
    const thinkingDots = document.createElement('div');
    thinkingDots.className = 'thinking-dots';
    thinkingDots.innerHTML = '<span></span><span></span><span></span>';
    wrapper.appendChild(thinkingDots);

    let answerText = '';

    function renderAnswer() {
        if (typeof marked !== 'undefined') {
            answerDiv.innerHTML = marked.parse(answerText);
        } else {
            answerDiv.textContent = answerText;
        }
    }

    try {
        const body = { message: query };
        if (sessionId) body.session_id = sessionId;

        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });

        const sid = response.headers.get('X-Session-Id');
        if (sid) saveSession(sid);

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
                        case 'step_start': {
                            setStatus(`Шаг ${event.step}/${event.max_steps}`);
                            break;
                        }

                        case 'context_warning':
                        case 'context_update':
                            updateContext(event.usage, event.approx_tokens);
                            break;

                        case 'answer': {
                            thinkingDots.remove();
                            answerText = event.text || '';
                            renderAnswer();
                            break;
                        }

                        case 'tool_call': {
                            const tc = document.createElement('div');
                            tc.className = 'step-tool';
                            tc.textContent = TOOL_NAMES[event.tool] || event.tool;
                            stepsDiv.appendChild(tc);
                            setStatus(`${TOOL_NAMES[event.tool] || event.tool}...`);
                            break;
                        }

                        case 'tool_result':
                            setStatus('Анализирую...');
                            break;

                        case 'done':
                            if (thinkingDots.parentNode) thinkingDots.remove();
                            setStatus(`Готово (${event.steps_used} шагов)`);
                            if (!answerText) {
                                answerText = 'Ответ не сгенерирован.';
                                renderAnswer();
                            }
                            saveHistory('assistant', answerText);
                            break;

                        case 'error':
                            thinkingDots.remove();
                            answerText = event.message || 'Ошибка';
                            renderAnswer();
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
        // Show retry button on connection error
        answerDiv.innerHTML = `
            <div style="color:#f59e0b">Соединение прервано</div>
            <button onclick="retryLast()" style="margin-top:8px;padding:6px 16px;border-radius:8px;border:1px solid #53d8fb;background:transparent;color:#53d8fb;cursor:pointer;font-size:13px">
                Повторить запрос
            </button>`;
        setStatus('Отключено');
    }

    isStreaming = false;
    sendBtn.disabled = false;
    inputEl.focus();
}

async function sendMessageAsync(query, msgId) {
    const wrapper = document.createElement('div');
    wrapper.className = 'message assistant';
    messagesEl.appendChild(wrapper);

    const stepsDiv = document.createElement('div');
    stepsDiv.className = 'steps-container';
    wrapper.appendChild(stepsDiv);

    const answerDiv = document.createElement('div');
    answerDiv.className = 'answer-text';
    wrapper.appendChild(answerDiv);

    setStatus('Обрабатываю запрос...');

    const thinkingDots = document.createElement('div');
    thinkingDots.className = 'thinking-dots';
    thinkingDots.innerHTML = '<span></span><span></span><span></span>';
    wrapper.appendChild(thinkingDots);

    try {
        const body = { message: query };
        if (sessionId) body.session_id = sessionId;

        // Submit task
        const startRes = await fetch('/api/chat/async', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const { task_id, session_id: sid } = await startRes.json();
        if (sid) saveSession(sid);

        // Poll for result
        let attempts = 0;
        const maxAttempts = 300; // 10 min max (2s intervals)
        let renderedTools = new Set();

        while (attempts < maxAttempts) {
            await new Promise(r => setTimeout(r, 2000));
            attempts++;

            try {
                const pollRes = await fetch(`/api/chat/result/${task_id}`);
                const result = await pollRes.json();

                // Show tool badges
                if (result.steps) {
                    for (const s of result.steps) {
                        if (s.tools) {
                            for (const t of s.tools) {
                                const key = `${s.step}-${t}`;
                                if (!renderedTools.has(key)) {
                                    renderedTools.add(key);
                                    const tc = document.createElement('div');
                                    tc.className = 'step-tool';
                                    tc.textContent = TOOL_NAMES[t] || t;
                                    stepsDiv.appendChild(tc);
                                }
                            }
                        }
                    }
                    messagesEl.scrollTop = messagesEl.scrollHeight;
                }

                // Update context bar
                if (result.context_usage) {
                    updateContext(result.context_usage);
                }

                setStatus(`Шаг ${result.steps_count || 1}...`);

                if (result.status === 'done') {
                    if (thinkingDots.parentNode) thinkingDots.remove();
                    let answer = result.answer || 'Ответ не получен.';
                    if (typeof marked !== 'undefined') {
                        answerDiv.innerHTML = marked.parse(answer);
                    } else {
                        answerDiv.textContent = answer;
                    }
                    saveHistory('assistant', answer);
                    setStatus(`Готово (${result.steps_count} шагов)`);
                    break;
                } else if (result.status === 'error') {
                    if (thinkingDots.parentNode) thinkingDots.remove();
                    answerDiv.textContent = result.answer || 'Ошибка';
                    setStatus('Ошибка');
                    break;
                }
            } catch (pollErr) {
                setStatus(`Переподключаюсь... (${attempts * 2}с)`);
            }
        }

        if (attempts >= maxAttempts) {
            if (thinkingDots.parentNode) thinkingDots.remove();
            answerDiv.textContent = 'Превышено время ожидания.';
            setStatus('Таймаут');
        }
    } catch (err) {
        answerDiv.innerHTML = `
            <div style="color:#f59e0b">Ошибка отправки</div>
            <button onclick="retryLast()" style="margin-top:8px;padding:6px 16px;border-radius:8px;border:1px solid #53d8fb;background:transparent;color:#53d8fb;cursor:pointer;font-size:13px">
                Повторить запрос
            </button>`;
        setStatus('Отключено');
    }

    isStreaming = false;
    sendBtn.disabled = false;
    inputEl.focus();
    messagesEl.scrollTop = messagesEl.scrollHeight;
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
