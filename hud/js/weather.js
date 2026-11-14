'use strict';
/* Weather settings: the gear in the weather panel opens a small box to use the device location (macOS asks for permission
   the first time) or to search for a city and use that instead. Everything is sent to the voice service, which looks the
   city up with Open-Meteo and remembers the choice. */
const wxGear = $('#wxGear'), wxSet = $('#wxSet'), wxQ = $('#wxQ'), wxCitiesEl = $('#wxCities'), wxNote = $('#wxNote');
const wxNoteDefault = wxNote.textContent;
function wxSetOpen(open) {
  wxSet.hidden = !open; wxGear.setAttribute('aria-expanded', String(open));
  if (open) {
    const m = wxMsg, mode = m && m.mode;
    $('#wxMode').textContent = '当前来源：' + (mode === 'device' ? '设备定位' : mode === 'city' ? '手动城市（不是设备位置，不会跟随你移动）' : mode === 'named' ? '—' : '设备定位（未授权时请选城市）');
    wxQ.focus();
  }
}
wxGear.addEventListener('click', e => { e.stopPropagation(); wxSetOpen(wxSet.hidden); });
['keydown', 'keyup'].forEach(t => wxQ.addEventListener(t, e => e.stopPropagation()));      /* typing a city must not trigger Space-to-talk or the hotkeys */
$('#wxDevice').addEventListener('click', () => {
  if (liveSend({ type: 'weather_device' })) { wxNote.textContent = '正在向系统请求定位…第一次会弹出授权窗口。'; wxCitiesEl.innerHTML = ''; }
  else wxNote.textContent = '没有连上语音服务，不能定位。';
});
$('#wxForm').addEventListener('submit', e => {
  e.preventDefault();
  const q = wxQ.value.trim(); if (!q) return;
  if (liveSend({ type: 'weather_search', q })) { wxNote.textContent = '搜索中…'; wxCitiesEl.innerHTML = ''; }
  else wxNote.textContent = '没有连上语音服务，不能搜索。';
});
function wxCities(m) {
  wxNote.textContent = m.error || (m.items.length ? '点一个城市来使用它：' : wxNoteDefault);
  wxCitiesEl.innerHTML = (m.items || []).map((c, i) => `<li><button type="button" data-i="${i}">${escapeHtml(c.label || c.name)}</button></li>`).join('');
}
wxCitiesEl.addEventListener('click', e => {
  const b = e.target.closest('button[data-i]'); if (!b) return;
  liveSend({ type: 'weather_pick', i: Number(b.dataset.i) });
  wxCitiesEl.innerHTML = ''; wxNote.textContent = wxNoteDefault; wxQ.value = ''; wxSetOpen(false);
});
addEventListener('keydown', e => { if (e.key === 'Escape' && !wxSet.hidden) wxSetOpen(false); });
