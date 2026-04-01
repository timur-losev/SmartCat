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
    inputEl.value = sampleQ;
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

function createStepBlock(stepsDiv, step) {
    const block = document.createElement('div');
    block.className = 'step-block';
    block.id = `step-${step}`;

    const header = document.createElement('div');
    header.className = 'step-header';
    header.textContent = `Шаг ${step}`;
    header.onclick = () => {
        header.classList.toggle('expanded');
        thinking.classList.toggle('expanded');
    };

    const thinking = document.createElement('div');
    thinking.className = 'step-thinking expanded';
    thinking.id = `thinking-${step}`;

    block.appendChild(header);
    block.appendChild(thinking);
    stepsDiv.appendChild(block);

    return { block, header, thinking };
}

async function sendMessage() {
    const query = inputEl.value.trim();
    if (!query || isStreaming) return;

    isStreaming = true;
    sendBtn.disabled = true;
    inputEl.value = '';
    lastQuery = query;

    hideWelcome();
    addMessage('user', query);
    saveHistory('user', query);

    // Use async polling on mobile (SSE breaks when screen off)
    const isMobile = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
    if (isMobile) {
        await sendMessageAsync(query);
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

    let fullText = '';
    let answerText = '';
    let answerMode = false;

    function renderAnswer() {
        if (typeof marked !== 'undefined') {
            answerDiv.innerHTML = marked.parse(answerText);
        } else {
            answerDiv.textContent = answerText;
        }
    }
    let currentStep = null;
    let currentThinking = null;

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
                            const s = createStepBlock(stepsDiv, event.step);
                            currentStep = s;
                            currentThinking = s.thinking;
                            setStatus(`Шаг ${event.step}/${event.max_steps}`);
                            break;
                        }

                        case 'token': {
                            let text = event.text || '';
                            text = text.replace(/<\/?think>/g, '');
                            fullText += text;

                            if (text.includes('Answer:')) {
                                answerMode = true;
                                if (currentThinking) {
                                    currentThinking.classList.remove('expanded');
                                    if (currentStep) currentStep.header.classList.remove('expanded');
                                }
                                const after = text.split('Answer:')[1] || '';
                                answerText = after;
                                renderAnswer();
                            } else if (answerMode) {
                                answerText += text;
                                renderAnswer();
                            } else if (currentThinking) {
                                // Append to thinking block
                                currentThinking.textContent += text;
                                // Auto-scroll thinking
                                currentThinking.scrollTop = currentThinking.scrollHeight;
                            }
                            break;
                        }

                        case 'tool_call': {
                            if (currentStep) {
                                const tc = document.createElement('div');
                                tc.className = 'step-tool';
                                tc.textContent = TOOL_NAMES[event.tool] || event.tool;
                                currentStep.block.appendChild(tc);
                            }
                            setStatus(`${TOOL_NAMES[event.tool] || event.tool}...`);
                            // Collapse thinking for this step
                            if (currentThinking) {
                                currentThinking.classList.remove('expanded');
                                if (currentStep) currentStep.header.classList.remove('expanded');
                            }
                            break;
                        }

                        case 'tool_result':
                            setStatus('Анализирую...');
                            break;

                        case 'done':
                            setStatus(`Готово (${event.steps_used} шагов)`);
                            if (!answerMode && !answerText) {
                                let cleaned = fullText
                                    .replace(/```tool[\s\S]*?```/g, '')
                                    .replace(/<think>[\s\S]*?<\/think>/g, '')
                                    .replace(/<think>[\s\S]*/g, '')
                                    .replace(/^Thinking:.*$/gm, '')
                                    .replace(/^Tool result[\s\S]*?(?=\n\n|$)/gm, '')
                                    .trim();
                                answerText = cleaned || 'Ответ не сгенерирован.';
                                renderAnswer();
                            }
                            saveHistory('assistant', answerText);
                            break;

                        case 'error':
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

async function sendMessageAsync(query) {
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
    let renderedSteps = 0;

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

        while (attempts < maxAttempts) {
            await new Promise(r => setTimeout(r, 2000));
            attempts++;

            try {
                const pollRes = await fetch(`/api/chat/result/${task_id}`);
                const result = await pollRes.json();

                // Render and update steps
                if (result.steps) {
                    for (let i = 0; i < result.steps.length; i++) {
                        const s = result.steps[i];
                        let existingThinking = document.getElementById(`async-thinking-${i}`);

                        if (!existingThinking) {
                            // New step — create block
                            const stepBlock = createStepBlock(stepsDiv, s.step);
                            stepBlock.thinking.id = `async-thinking-${i}`;
                            stepBlock.block.id = `async-block-${i}`;
                            // Collapse previous steps
                            if (i > 0) {
                                const prevThink = document.getElementById(`async-thinking-${i-1}`);
                                if (prevThink) prevThink.classList.remove('expanded');
                                const prevBlock = document.getElementById(`async-block-${i-1}`);
                                if (prevBlock) {
                                    const prevHeader = prevBlock.querySelector('.step-header');
                                    if (prevHeader) prevHeader.classList.remove('expanded');
                                }
                            }
                            existingThinking = stepBlock.thinking;
                        }

                        // Update thinking text (strip think tags, fix spaces)
                        let thinkText = (s.thinking || '')
                            .replace(/<\/?think>/g, '')
                            .replace(/```tool[\s\S]*?```/g, '[tool call]');
                        existingThinking.textContent = thinkText;
                        existingThinking.scrollTop = existingThinking.scrollHeight;

                        // Update tools
                        const block = document.getElementById(`async-block-${i}`);
                        if (block) {
                            block.querySelectorAll('.step-tool').forEach(el => el.remove());
                            s.tools.forEach(t => {
                                const tc = document.createElement('div');
                                tc.className = 'step-tool';
                                tc.textContent = TOOL_NAMES[t] || t;
                                block.appendChild(tc);
                            });
                        }
                    }
                    renderedSteps = result.steps.length;
                    messagesEl.scrollTop = messagesEl.scrollHeight;
                }

                setStatus(`Шаг ${result.steps_count || 1}...`);

                if (result.status === 'done') {
                    let answer = result.answer || 'Ответ не получен.';
                    answer = answer.replace(/<\/?think>/g, '');
                    if (typeof marked !== 'undefined') {
                        answerDiv.innerHTML = marked.parse(answer);
                    } else {
                        answerDiv.textContent = answer;
                    }
                    saveHistory('assistant', answer);
                    setStatus(`Готово (${result.steps_count} шагов)`);
                    break;
                } else if (result.status === 'error') {
                    answerDiv.textContent = result.answer || 'Ошибка';
                    setStatus('Ошибка');
                    break;
                }
            } catch (pollErr) {
                // Network blip during poll — keep trying
                setStatus(`Переподключаюсь... (${attempts * 5}с)`);
            }
        }

        if (attempts >= maxAttempts) {
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
