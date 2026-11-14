'use strict';
/* The latest DingTalk minutes (听记), read-only, shown when Jarvis fetched them: title, time, and the link to the source.
   Nothing here is written back to DingTalk; the link is copied, not opened. */
const dwsCard = document.createElement('div');
dwsCard.id = 'dwsCard'; dwsCard.hidden = true;
dwsCard.innerHTML = '<div class="dws-top"><span class="pip"></span>钉钉听记 · 最新一条<button type="button" class="dws-x" aria-label="关闭">×</button></div><div class="dws-title"></div><div class="dws-time"></div><div class="dws-url"></div><div class="dws-foot"><span>来源：钉钉听记（只读）</span><button type="button" class="dws-copy">复制链接</button></div>';
document.body.appendChild(dwsCard);
const dwsQ = s => dwsCard.querySelector(s);
let dwsTimer = null;
function dingtalkShow(m) {
  dwsQ('.dws-title').textContent = m.title || '（无标题）';
  dwsQ('.dws-time').textContent = m.time ? '时间 ' + m.time : '';
  dwsQ('.dws-url').textContent = m.url || '';
  dwsQ('.dws-copy').hidden = !m.url;
  dwsQ('.dws-copy').textContent = '复制链接';
  dwsCard.hidden = false;
  clearTimeout(dwsTimer); dwsTimer = setTimeout(() => { dwsCard.hidden = true; }, 180000);
}
dwsQ('.dws-x').addEventListener('click', () => { dwsCard.hidden = true; });
dwsQ('.dws-copy').addEventListener('click', () => {
  const url = dwsQ('.dws-url').textContent;
  const done = () => { dwsQ('.dws-copy').textContent = '已复制'; };
  try { navigator.clipboard.writeText(url).then(done, () => { dwsQ('.dws-copy').textContent = '请手动选中复制'; }); }
  catch (_) { dwsQ('.dws-copy').textContent = '请手动选中复制'; }
});
