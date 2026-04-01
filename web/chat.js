const messagesEl = document.getElementById('messages');
const inputEl = document.getElementById('query-input');
const sendBtn = document.getElementById('send-btn');
const statusEl = document.getElementById('status');

let sessionId = null;
let isStreaming = false;

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

function useSample() {
    inputEl.value = sampleQ;
    inputEl.focus();
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

    hideWelcome();
    addMessage('user', query);

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
                                // Collapse current thinking
                                if (currentThinking) {
                                    currentThinking.classList.remove('expanded');
                                    if (currentStep) currentStep.header.classList.remove('expanded');
                                }
                                const after = text.split('Answer:')[1] || '';
                                answerDiv.textContent = after;
                            } else if (answerMode) {
                                answerDiv.textContent += text;
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
                            if (!answerMode && answerDiv.textContent === '') {
                                let cleaned = fullText
                                    .replace(/```tool[\s\S]*?```/g, '')
                                    .replace(/<think>[\s\S]*?<\/think>/g, '')
                                    .replace(/<think>[\s\S]*/g, '')
                                    .replace(/^Thinking:.*$/gm, '')
                                    .replace(/^Tool result[\s\S]*?(?=\n\n|$)/gm, '')
                                    .trim();
                                answerDiv.textContent = cleaned || 'Ответ не сгенерирован.';
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
