// 中文自然语言 → 提示词 助手（本地扩展，2026-10-07）
// 在页面右下角挂一个小面板：写中文描述 → 点按钮 → 用本机 Ollama 转成 danbooru 提示词并填进 Prompt 框。

let promptHelperPanel = null;

// 客户端兜底清理：模型偶尔会吐 "bad tag / none / n/a" 这类占位词，服务端词表校验之外再扫一遍
const PROMPT_HELPER_JUNK = ['bad tag', 'bad_tag', 'none', 'n/a', 'null', 'unknown', 'todo'];

function promptHelperClean(text) {
    if (!text) {
        return text;
    }
    let parts = text.split(',').map(s => s.trim()).filter(s => s.length > 0);
    parts = parts.filter(p => !PROMPT_HELPER_JUNK.includes(p.toLowerCase()));
    return parts.join(', ');
}

function promptHelperGetPromptInput() {
    return document.getElementById('input_prompt') || document.getElementById('alt_prompt_textbox');
}

function promptHelperSetMode(mode) {
    let sel = document.getElementById('prompt_helper_mode');
    if (sel) {
        sel.value = mode;
    }
}

function promptHelperSetPrompt(text) {
    let input = promptHelperGetPromptInput();
    if (!input) {
        alert('没找到提示词输入框（input_prompt）');
        return;
    }
    input.value = text;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    input.focus();
    input.scrollTop = input.scrollHeight;
}

function promptHelperAppend(text) {
    let input = promptHelperGetPromptInput();
    if (!input) {
        return;
    }
    let cur = (input.value || '').trim();
    promptHelperSetPrompt(cur ? `${cur}, ${text}` : text);
}

async function promptHelperRun(append) {
    let box = document.getElementById('prompt_helper_input');
    let out = document.getElementById('prompt_helper_output');
    let btn = document.getElementById('prompt_helper_run');
    let mode = document.getElementById('prompt_helper_mode')?.value || 'prompt';
    let text = (box?.value || '').trim();
    if (!text) {
        box?.focus();
        return;
    }
    btn.disabled = true;
    btn.textContent = '生成中…';
    out.textContent = '';
    genericRequest('PromptHelper', { text: text, mode: mode }, (data) => {
        if (data.error) {
            out.textContent = data.error;
        } else {
            data.result = promptHelperClean(data.result);
            out.textContent = data.result;
            if (data.invented) {
                out.textContent += `\n\n【词表外（已保留，可自行替换）】${data.invented}`;
            }
            if (append) {
                promptHelperAppend(data.result);
            } else {
                promptHelperSetPrompt(data.result);
            }
        }
        btn.disabled = false;
        btn.textContent = '生成提示词';
    });
}

function promptHelperInstall() {
    if (promptHelperPanel || document.getElementById('prompt_helper_panel')) {
        return;
    }
    let panel = document.createElement('div');
    panel.id = 'prompt_helper_panel';
    panel.innerHTML = `
        <div id="prompt_helper_header">
            <span>提示词助手（中文 → 标签）</span>
            <span id="prompt_helper_toggle" title="收起/展开">－</span>
        </div>
        <div id="prompt_helper_body">
            <textarea id="prompt_helper_input" placeholder="用中文描述你想要的画面，例如：初音未来站在贴满照片的墙前，微笑看镜头，冷色调"></textarea>
            <div id="prompt_helper_row">
                <select id="prompt_helper_mode">
                    <option value="prompt">正向提示词</option>
                    <option value="outfit">只翻服装（换装用）</option>
                    <option value="negative">负向提示词</option>
                </select>
                <button id="prompt_helper_run" class="basic-button">生成提示词</button>
                <button id="prompt_helper_append" class="basic-button">追加</button>
            </div>
            <div id="prompt_helper_output"></div>
        </div>`;
    document.body.appendChild(panel);
    promptHelperPanel = panel;
    document.getElementById('prompt_helper_run').addEventListener('click', () => promptHelperRun(false));
    document.getElementById('prompt_helper_append').addEventListener('click', () => promptHelperRun(true));
    document.getElementById('prompt_helper_toggle').addEventListener('click', () => {
        let body = document.getElementById('prompt_helper_body');
        let open = body.style.display != 'none';
        body.style.display = open ? 'none' : 'block';
        document.getElementById('prompt_helper_toggle').textContent = open ? '＋' : '－';
    });
    document.getElementById('prompt_helper_input').addEventListener('keydown', (e) => {
        if (e.ctrlKey && e.key == 'Enter') {
            promptHelperRun(false);
        }
    });
}

if (document.readyState == 'loading') {
    document.addEventListener('DOMContentLoaded', promptHelperInstall);
} else {
    promptHelperInstall();
}
