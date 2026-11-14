'use strict';
/* ---------------- HUD data ---------------- */
/* the helpers Jarvis hands work to (jarvis/agents.py sends their live state as {type:'agents'}) */
const AGENTS = [
  { id: 'brain', name: 'BRAIN', zh: '大脑', desc: '等待语音服务…', icon: '<path d="M12 5.5l5.6 3.25v6.5L12 18.5l-5.6-3.25v-6.5z" fill="currentColor"></path><path d="M12 9.6l2.1 1.2v2.4L12 14.4l-2.1-1.2v-2.4z" fill="#03080d"></path>', get c() { return `rgb(${SKC.pri})`; } },
  { id: 'jobs', name: 'JOBS', zh: '后台任务', desc: '等待语音服务…', icon: '<path d="M12 5.5l6.5 12H5.5z" fill="currentColor"></path>', get c() { return `rgb(${SKC.acc})`; } },
  { id: 'learn', name: 'LEARNING', zh: '学习', desc: '等待语音服务…', icon: '<rect x="6.5" y="6.5" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.6"></rect><rect x="9" y="9" width="2.6" height="2.6" fill="currentColor"></rect><rect x="12.4" y="9" width="2.6" height="2.6" fill="currentColor"></rect><rect x="9" y="12.4" width="2.6" height="2.6" fill="currentColor"></rect><rect x="12.4" y="12.4" width="2.6" height="2.6" fill="currentColor"></rect>', get c() { return `rgb(${SKC.soft})`; } },
  { id: 'review', name: 'REVIEW', zh: '复盘', desc: '等待语音服务…', icon: '<path d="M12 5l6 7-6 7-6-7z" fill="currentColor"></path>', get c() { return `rgb(${SKC.ink})`; } }
];
const agentBox = $('#agents');
AGENTS.forEach(a => {
  const b = document.createElement('button'); b.type = 'button'; b.className = 'agent'; b.id = 'ag-' + a.id;
  b.style.setProperty('--c', a.c);
  b.innerHTML = `<span class="ring" style="color:${a.c}"><svg viewBox="0 0 24 24">${a.icon}</svg></span><span><span class="name">${a.name}<small>${a.zh}</small></span><span class="desc">${a.desc}</span></span><canvas width="144" height="52"></canvas><span class="dot"></span><span class="bar"><i></i></span>`;
  b.addEventListener('click', () => agentClick(a.id));
  agentBox.appendChild(b);
  a.el = b; a.cv = b.querySelector('canvas'); a.ph = Math.random() * 10; a.desc0 = a.desc;
});
function agentsUpdate(m) {
  (m.items || []).forEach(it => {
    const a = AGENTS.find(x => x.id === it.id); if (!a) return;
    const el = a.el;
    el.classList.toggle('run', it.state === 'run'); el.classList.toggle('warn', it.state === 'warn');
    el.classList.toggle('off', it.state === 'off'); el.classList.toggle('live', it.state === 'run' || it.state === 'warn');
    el.querySelector('.desc').textContent = a.desc0 = it.desc || '';
    el.title = it.desc || '';
    el.querySelector('.bar i').style.width = it.state === 'run' ? Math.round((it.progress || 0.5) * 100) + '%' : it.state === 'warn' ? '100%' : '0%';
  });
}
function agentClick(id) {
  if (id === 'brain' && typeof brainOpen === 'function') return brainOpen();
  if (id === 'learn' && typeof brainOpen === 'function') { brainOpen(); try { bwTopView('ab'); liveSend({ type: 'brain_abilities' }); } catch (_) {} return; }
  handle(id === 'jobs' ? '后台任务进行得怎么样了？' : '昨天的复盘有什么建议？');
}

let done = 0, total = 0;
let TASKS = [];          // filled from the real day plan (schedule messages)
let liveTasks = null;   // from the real day plan: [title, status word, class]
function renderReport() {
  const pct = total ? Math.round(done / total * 100) : 0;
  $('#totalNum').textContent = total;
  $('#gaugeArc').style.strokeDashoffset = (226.2 * (1 - pct / 100)).toFixed(1);
  animateNum($('#gaugeNum'), pct); $('#doneNum').textContent = done;
  $('#segs').innerHTML = Array.from({ length: 8 }, (_, i) => `<i class="${i < Math.floor(pct / 12.5) ? 'on' : i === Math.floor(pct / 12.5) ? 'half' : ''}"></i>`).join('');
  if (liveTasks) $('#tasks').innerHTML = liveTasks.length ? liveTasks.map(([n, w, c]) => `<li class="${c}"><i></i><span>${escapeHtml(n)}</span><b style="font-size:12.5px">${w}</b></li>`).join('') : '<li class="new"><i></i><span style="color:var(--ink-3)">还没有任务，告诉我今天要做什么</span><b></b></li>';
  else $('#tasks').innerHTML = TASKS.map(([n, p]) => `<li class="${p === 100 ? 'ok' : p >= 70 ? 'go' : p >= 40 ? 'slow' : 'new'}"><i></i><span>${n}</span><b>${p}%</b></li>`).join('');
}
function animateNum(el, to) { const from = +el.textContent || 0; const t0 = performance.now(); (function s(now) { const k = Math.min(1, (now - t0) / 1200); el.textContent = Math.round(from + (to - from) * (1 - Math.pow(1 - k, 3))); if (k < 1) requestAnimationFrame(s); })(t0); }
renderReport();

let SCHED = [['09:00', '09:30', '团队站会', '项目进展与重点对齐'], ['09:30', '11:00', '方案评审', '贾维斯交互方案讨论'], ['11:00', '12:00', '深度研究', '多模态模型调研'], ['13:30', '15:00', '开发迭代', 'Agent 工具链集成'], ['15:30', '16:30', '项目复盘', '里程碑与风险梳理'], ['17:00', '18:00', '文档整理', '技术文档与下一步计划']];
const toMin = s => { const [h, m] = s.split(':').map(Number); return h * 60 + m; };
function schedInfo() { const d = new Date(); const now = d.getHours() * 60 + d.getMinutes(); let cur = -1, next = -1; SCHED.forEach((s, i) => { if (now >= toMin(s[0]) && now < toMin(s[1])) cur = i; if (next < 0 && toMin(s[0]) > now) next = i; }); return { now, cur, next }; }
let TODO = [];          // live items without a time
function renderSched() {
  const { now, cur, next } = schedInfo();
  const fi = cur >= 0 ? cur : next;
  const timed = SCHED.map((s, i) => `<li class="${s[4] ? 'past' : i === fi ? 'focus' : toMin(s[1]) <= now ? 'past' : ''}"><i></i><span class="t">${s[0]}${s[5] === false ? '' : ' – ' + s[1]}${i === fi && !s[4] ? '<span class="arr">→</span>' : ''}</span><span><b>${escapeHtml(s[2])}${s[4] ? ' ✓' : ''}</b><span>${escapeHtml(s[3] || '')}</span></span></li>`);
  const untimed = TODO.map(t => `<li class="${t.done ? 'past' : ''}"><i></i><span class="t">待办</span><span><b>${escapeHtml(t.title)}${t.done ? ' ✓' : ''}</b><span>${escapeHtml(t.note || '')}</span></span></li>`);
  $('#sched').innerHTML = timed.concat(untimed).join('') || '<li><i></i><span class="t">今天</span><span><b>还没有安排</b><span>说 “Hey Jarvis”，告诉我今天要做什么</span></span></li>';
}
/* the real day plan from the voice service replaces the demo data */
function applySchedule(items) {
  const hm = m => `${String(Math.floor(m / 60) % 24).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
  const timed = items.filter(x => x.start), now = new Date().getHours() * 60 + new Date().getMinutes();
  SCHED = timed.map(x => [x.start, x.end || hm(toMin(x.start) + 30), x.title || x.title_en, x.note, !!x.done, !!x.end]);
  TODO = items.filter(x => !x.start);
  done = items.filter(x => x.done).length; total = items.length;
  const word = x => x.done ? ['完成', 'ok'] : !x.start ? ['待办', 'new'] : now >= toMin(x.end || hm(toMin(x.start) + 30)) ? ['待确认', 'slow'] : now >= toMin(x.start) ? ['进行中', 'go'] : [x.start, 'new'];
  const order = items.filter(x => !x.done).concat(items.filter(x => x.done));
  liveTasks = order.slice(0, 4).map(x => [x.title || x.title_en, ...word(x)]);
  renderSched(); renderReport();
}
renderSched();

let sysLive = false, cpuHot = 0, wxLive = false;
/* weather from AMap: the icon follows the Chinese weather word */
const WX_ICON = {
  sun: '<svg viewBox="0 0 24 24" fill="none" stroke="#FFC27A" stroke-width="1.4"><circle cx="12" cy="12" r="4" fill="rgba(255,194,122,.25)"></circle><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4L7 17M17 7l1.4-1.4"></path></svg>',
  part: '<svg viewBox="0 0 24 24" fill="none" stroke-width="1.4"><circle cx="15" cy="8" r="3" stroke="#FFC27A" fill="rgba(255,194,122,.25)"></circle><path d="M6 19h9a3.5 3.5 0 0 0 0-7 5 5 0 0 0-9.5 1.4A2.9 2.9 0 0 0 6 19z" stroke="currentColor" fill="rgba(220,239,247,.14)"></path></svg>',
  cloud: '<svg viewBox="0 0 24 24" fill="rgba(220,239,247,.14)" stroke="currentColor" stroke-width="1.4"><path d="M7 18h10a4 4 0 0 0 0-8 5.5 5.5 0 0 0-10.6 1.5A3.3 3.3 0 0 0 7 18z"></path></svg>',
  rain: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M7 15h10a4 4 0 0 0 0-8 5.5 5.5 0 0 0-10.6 1.5A3.3 3.3 0 0 0 7 15z" fill="rgba(220,239,247,.14)"></path><path d="M9 18l-1 2.5M13 18l-1 2.5M17 18l-1 2.5" stroke="#7FDBFF"></path></svg>',
  snow: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M7 15h10a4 4 0 0 0 0-8 5.5 5.5 0 0 0-10.6 1.5A3.3 3.3 0 0 0 7 15z" fill="rgba(220,239,247,.14)"></path><path d="M9 19h.01M13 20h.01M17 19h.01" stroke-width="2.4" stroke-linecap="round"></path></svg>',
  fog: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M4 9h16M6 13h12M4 17h16"></path></svg>'
};
const wxKind = w => /雪/.test(w) ? 'snow' : /雨/.test(w) ? 'rain' : /雾|霾|沙|尘/.test(w) ? 'fog' : /晴/.test(w) ? (/云/.test(w) ? 'part' : 'sun') : /少云|多云/.test(w) ? 'part' : 'cloud';
/* The weather panel shows only what the service reports: real data with its source and observation time, or "不可用" and why.
   There are no placeholder numbers; data older than 3 hours is treated as not available even if the service goes quiet. */
const WX_MAX_AGE = 3 * 3600;
let wxMsg = null;
const wxAge = sec => { const n = Math.max(0, Math.round(sec / 60)); return n < 2 ? '刚刚' : n < 60 ? `${n} 分钟前` : `${Math.floor(n / 60)} 小时前`; };
const WX_NONE = '<svg viewBox="0 0 24 24" fill="none" stroke-width="1.3" aria-hidden="true"><path d="M6 19.5h10a3.8 3.8 0 0 0 0-7.6 5.2 5.2 0 0 0-10 1.6A3 3 0 0 0 6 19.5z" stroke="currentColor" stroke-dasharray="2 2.4" opacity=".5"></path></svg>';
function wxRender() {
  const m = wxMsg, big = $('#pWeather .wx-big'), now = $('#pWeather .wx-now > span'), meta = $('#pWeather .wx-now .meta');
  const days = $('#pWeather .wx-days').children, src = $('#wxSrc');
  const age = m && m.status === 'ok' ? Date.now() / 1000 - m.observed_epoch : 0;
  const ok = m && m.status === 'ok' && age <= WX_MAX_AGE;
  const label = ['今天', '明天', '后天'];
  if (!ok) {
    big.querySelector('b').firstChild.textContent = '--'; big.querySelector('.deg').textContent = '';
    big.querySelector('svg').outerHTML = WX_NONE;
    now.textContent = m && m.status === 'pending' ? '正在获取…' : '天气不可用';
    const why = !m ? '等待语音服务' : m.status === 'ok' ? '数据已过期（超过 3 小时）' : (m.message || '暂时没有天气数据');
    meta.innerHTML = `<span>${escapeHtml(why)}</span>`;
    for (let i = 0; i < 3; i++) days[i].innerHTML = `${label[i]}<span class="wx-ph">--</span><b>--</b>`;
    src.textContent = !m ? '没有连上语音服务，没有天气数据' : m.status === 'pending' ? '来源 Open-Meteo · 正在定位' : (m.can_choose_city ? '没有天气数据 · 点右上角齿轮：使用设备定位或选择城市' : '没有天气数据');
    src.title = ''; src.classList.add('warn');
    return;
  }
  big.querySelector('b').firstChild.textContent = m.temp; big.querySelector('.deg').textContent = '°C';
  big.querySelector('svg').outerHTML = WX_ICON[wxKind(m.now)].replace('<svg ', '<svg aria-hidden="true" ');
  now.textContent = m.now + (m.feels != null && m.feels !== m.temp ? ` · 体感 ${m.feels}°` : '');
  const aqi = m.aqi ? `${m.aqi.scale} ${m.aqi.value} · ${m.aqi.label}` : 'AQI 不可用';
  meta.innerHTML = `<span>${escapeHtml((m.city || '').replace(/市$/, ''))}</span>` + (m.humidity != null ? `<span>湿度 ${m.humidity}%</span>` : '') + `<span>${escapeHtml(aqi)}</span>`;
  for (let i = 0; i < 3; i++) {
    const d = (m.days || [])[i];
    days[i].innerHTML = d ? `${label[i] || WD[+d.week % 7]}${WX_ICON[wxKind(d.day)]}<b>${d.low}°/${d.high}°</b>` : `${label[i]}<span class="wx-ph">--</span><b>--</b>`;
  }
  const how = m.location_source === 'device' ? '设备定位' : '手动选择城市';
  src.textContent = `来源 ${m.source} · ${how} · 更新 ${m.observed_hm}（${wxAge(age)}）` + (m.stale ? ' · 刷新失败，沿用旧数据' : '');
  src.title = `数据观测时间 ${m.observed_hm}（当地时间）；本机拉取于 ${m.fetched_hm}。空气质量为模型估算（US AQI）。`;
  src.classList.toggle('warn', !!m.stale);
}
function wxUpdate(m) { wxLive = true; wxMsg = m; wxRender(); }
setInterval(() => { if (wxMsg) wxRender(); }, 60000);
