'use strict';
/* 语言与语音: which language the wake word, recognition, voice and answers are in, which engine speaks, why Jarvis is
   silent when it is, and the switch between Chinese and English (the service downloads missing models with their checksums
   verified, then restarts itself; this window reconnects on its own). */
const vErr = document.createElement('div');
vErr.id = 'voiceErr'; vErr.hidden = true; vErr.title = '点击打开「语言与语音」';
document.body.appendChild(vErr);
const vBox = document.createElement('div');
vBox.id = 'voiceBox'; vBox.hidden = true;
vBox.innerHTML = `<div class="vb-top"><span class="pip"></span>语言与语音<button type="button" class="vb-x" aria-label="关闭">×</button></div>
<dl class="vb-rows" id="vbRows"></dl>
<div class="vb-row"><span>语言</span><span id="vbLangBtns"></span></div>
<div class="vb-row"><span>中文播报引擎</span><select id="vbTts" aria-label="中文播报引擎"><option value="auto">自动（优先本机模型）</option><option value="local">本机 Kokoro 模型</option><option value="system">macOS 系统声音</option></select><button type="button" id="vbTest">试听一句</button></div>
<div class="vb-style" id="vbStyle" hidden>
<div class="vb-row"><span>中文男声</span><select id="vbSpk" aria-label="中文男声"></select></div>
<div class="vb-row"><span>机械效果</span><select id="vbFx" aria-label="机械效果"><option value="scifi">轻回声（默认）</option><option value="robot">机械（金属感）</option><option value="off">关闭</option></select><input type="range" id="vbAmt" min="0" max="100" step="5" aria-label="效果强度"><b id="vbAmtV"></b></div>
<div class="vb-row"><span></span><button type="button" id="vbPrev">试听（不保存）</button><button type="button" id="vbUse">使用这个声音</button><button type="button" id="vbReset">恢复默认</button></div>
<div class="vb-note">声音是模型自带的 45 个中文男声之一（名字和编号来自模型官方的说话人表，基频是我们实测的中位数，只供参考）；效果是单独的开关，随时可以关掉或恢复默认，不会改变语速。</div>
</div>
<div class="vb-msg" id="vbMsg"></div>
<div class="vb-note">识别、唤醒和语音合成都在本机运行；只有识别出的文字（和工具结果）会发给你在「接入大脑」里选的大模型。</div>`;
document.body.appendChild(vBox);
const vq = s => vBox.querySelector(s);
let VOICE = null;
const LANG_NAME = { zh: '中文', en: 'English' };
function voiceOpen(open) { vBox.hidden = !open; if (open) liveSend({ type: 'voice_info' }); }
$('#btnLang').addEventListener('click', e => { e.stopPropagation(); $('#menuPop').hidden = true; voiceOpen(vBox.hidden); });
vErr.addEventListener('click', () => voiceOpen(true));
vq('.vb-x').addEventListener('click', () => voiceOpen(false));
addEventListener('keydown', e => { if (e.key === 'Escape' && !vBox.hidden) voiceOpen(false); });
vq('#vbTest').addEventListener('click', () => { if (!liveSend({ type: 'voice_test' })) vq('#vbMsg').textContent = '没有连上语音服务。'; });
vq('#vbTts').addEventListener('change', e => { liveSend({ type: 'voice_set', tts: e.target.value }); vq('#vbMsg').textContent = '正在切换播报引擎…'; });
vq('#vbLangBtns').addEventListener('click', e => {
  const b = e.target.closest('button[data-l]'); if (!b) return;
  if (!liveSend({ type: 'voice_set', lang: b.dataset.l })) { vq('#vbMsg').textContent = '没有连上语音服务。'; return; }
  vq('#vbMsg').textContent = '正在检查模型…'; vq('#vbLangBtns').querySelectorAll('button').forEach(x => { x.disabled = true; });
});
function voiceInfo(m) {
  VOICE = m; WAKE = m.wake_word || WAKE;
  if (typeof state !== 'undefined' && state === 'idle') stateText.textContent = listening ? `待命中 · 说 “${WAKE}”` : '已暂停聆听 · 可以打字';
  input.placeholder = `说 “${WAKE}”，或在这里打字`;
  const t = m.tts || {};
  const rows = [['语音语言', `${LANG_NAME[m.lang]}（回答：${LANG_NAME[m.reply_lang]}）`], ['唤醒词', `${m.wake_word} · ${m.wake}`], ['语音识别', m.stt + (m.stt_last ? `；最近一次用：${m.stt_last}` : '')],
    ['语音播报', t.error ? `不可用：${t.error}` : `${t.label}（${t.lang}）`], ['大模型', m.brain]];
  if (m.fallback) rows.unshift(['注意', `想用${LANG_NAME[m.fallback.wanted]}语音，但模型不完整（${m.fallback.missing.join('、')}），现在临时用${LANG_NAME[m.fallback.using]}。`]);
  vq('#vbRows').innerHTML = rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${escapeHtml(String(v))}</dd></div>`).join('');
  vq('#vbLangBtns').innerHTML = ['zh', 'en'].map(l => `<button type="button" data-l="${l}"${l === m.lang && !m.fallback ? ' disabled' : ''}>${l === 'zh' ? '中文语音' : 'English voice'}${l === m.lang && !m.fallback ? '（当前）' : m.other_ready || l === m.lang ? '' : '（需下载）'}</button>`).join('');
  vq('#vbTts').value = m.tts_mode || 'auto'; vq('#vbTts').disabled = m.lang !== 'zh';
  voiceStyle(m);
  if (!vq('#vbMsg').textContent.startsWith('正在')) vq('#vbMsg').textContent = '';
}
const fxLabel = { scifi: '轻回声', robot: '机械', off: '无' };
const vSend = (type, extra = {}) => liveSend({ type, speaker: +vq('#vbSpk').value, fx: vq('#vbFx').value, amount: +vq('#vbAmt').value, ...extra });
vq('#vbAmt').addEventListener('input', () => { vq('#vbAmtV').textContent = vq('#vbAmt').value + '%'; });
vq('#vbFx').addEventListener('change', () => { vq('#vbAmt').disabled = vq('#vbFx').value === 'off'; });
vq('#vbPrev').addEventListener('click', () => { if (vSend('voice_preview')) vq('#vbMsg').textContent = '正在试听…（没有保存）'; });
vq('#vbUse').addEventListener('click', () => { if (vSend('voice_style')) vq('#vbMsg').textContent = '正在保存并试听…'; });
vq('#vbReset').addEventListener('click', () => { if (liveSend({ type: 'voice_style_reset' })) vq('#vbMsg').textContent = '正在恢复默认…'; });
function voiceStyle(m) {
  const st = m.style, box = vq('#vbStyle');
  box.hidden = !st; if (!st) return;
  vq('#vbSpk').innerHTML = st.speakers.map(x => `<option value="${x.sid}">${x.name} · sid ${x.sid}${x.f0 ? ` · 约 ${x.f0} Hz` : ''}${x.sid === st.default.speaker ? ' · 默认' : ''}</option>`).join('');
  vq('#vbSpk').value = st.speaker; vq('#vbFx').value = st.fx;
  vq('#vbAmt').value = Math.round(st.amount * 100); vq('#vbAmtV').textContent = vq('#vbAmt').value + '%'; vq('#vbAmt').disabled = st.fx === 'off';
}
function voiceStyleMsg(m) { vBox.hidden = false; vq('#vbMsg').textContent = m.text; vq('#vbMsg').classList.remove('bad'); }
const STAGE = { audio: '麦克风', wake: '唤醒', stt: '语音识别', brain: '大脑', tts: '语音播报' };
function voiceError(m) {
  vErr.hidden = false; vErr.textContent = `${STAGE[m.stage] || '语音'}：${m.text}`;
  clearTimeout(vErr._t); vErr._t = setTimeout(() => { vErr.hidden = true; }, 45000);
}
function voiceSwitch(m) {
  vBox.hidden = false;
  vq('#vbMsg').textContent = (m.stage === 'error' ? '没有切换：' : m.stage === 'restart' ? '' : '正在切换：') + m.text;
  vq('#vbMsg').classList.toggle('bad', m.stage === 'error');
  if (m.stage === 'error') vq('#vbLangBtns').querySelectorAll('button').forEach(x => { x.disabled = false; });
}
