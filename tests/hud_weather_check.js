// HUD weather panel, DingTalk card and settings box, in headless Chromium (no voice service, no network).
// usage: node tests/hud_weather_check.js URL [shot-prefix]     (serve hud/ first: cd hud && python3 -m http.server 8811)
const { chromium } = require('playwright');
const assert = require('assert');
(async () => {
  const b = await chromium.launch({ args: ['--use-gl=swiftshader', '--enable-webgl', '--ignore-gpu-blocklist'] });
  const p = await b.newPage({ viewport: { width: 1440, height: 900 }, permissions: ['clipboard-read', 'clipboard-write'] });
  const errs = [];
  p.setDefaultTimeout(90000);
  p.on('pageerror', e => errs.push('PAGEERROR ' + (e.stack || e.message).split('\n').slice(0, 3).join(' | ')));
  await p.goto(process.argv[2]);
  await p.waitForTimeout(4000);
  // the HUD dims itself when idle and ignores clicks then; software rendering is slow enough for that to happen mid-test, so keep it awake
  const wake = () => p.evaluate(() => { window.sleepUI = () => {}; clearTimeout(dormT); dormHold = false; document.body.classList.remove('dormant'); });
  const shot = async n => { if (!process.argv[3]) return; await wake(); await p.waitForTimeout(600); await p.screenshot({ path: `${process.argv[3]}-${n}.png` }); };
  const click = async sel => { await wake(); await p.click(sel); };
  const text = sel => p.evaluate(s => document.querySelector(s).innerText, sel);

  // 1. before any message: no demo numbers anywhere in the panel, the page, or the core coordinates
  const html = await p.evaluate(() => document.body.innerText + document.getElementById('pWeather').innerHTML);
  for (const bad of ['上海', '张江', 'AQI 36', '32 分钟', '24 km', '空气优', '18°/26°', '14°/22°', '16°/24°', 'N 31.', 'E 121.'])
    assert(!html.includes(bad), 'demo data still on screen: ' + bad);
  assert((await text('#pWeather .wx-now')).includes('天气不可用'));
  assert((await text('#wxSrc')).includes('没有天气数据'));
  assert((await text('#pWeather .trip')).includes('暂无路线') && (await text('#eta')).includes('--:--'));
  assert.strictEqual((await text('#coords')).trim(), '');
  await shot('1-unavailable');

  // 2. real data: shows the number, where it is for, the source, how it was located and when it was observed
  const now = Math.floor(Date.now() / 1000);
  const ok = { type: 'weather', status: 'ok', mode: 'device', city: 'Testville', location_source: 'device', source: 'Open-Meteo', stale: false,
    temp: 18, feels: 16, now: '多云', humidity: 63, wind: '东北风 3.2 m/s', observed_epoch: now - 600, observed_hm: '14:00', fetched_hm: '14:09',
    aqi: { value: 41, scale: 'US AQI', label: '优' },
    days: [{ week: 6, day: '多云', high: 20, low: 11 }, { week: 0, day: '小雨', high: 18, low: 12 }, { week: 1, day: '晴', high: 24, low: 10 }] };
  await p.evaluate(m => wxUpdate(m), ok);
  let w = await text('#pWeather .wx');
  for (const s of ['18', '°C', '多云', 'Testville', '湿度 63%', 'US AQI 41', '11°/20°', '12°/18°', '10°/24°']) assert(w.includes(s), 'missing ' + s + ' in ' + w);
  const src = await text('#wxSrc');
  for (const s of ['来源 Open-Meteo', '设备定位', '更新 14:00', '10 分钟前']) assert(src.includes(s), 'source line lacks ' + s + ': ' + src);
  await shot('2-ok');
  await p.evaluate(m => wxUpdate(m), { ...ok, aqi: null, stale: true, location_source: 'city' });
  assert((await text('#pWeather .wx')).includes('AQI 不可用'));
  const s2 = await text('#wxSrc');
  assert(s2.includes('手动选择城市') && s2.includes('沿用旧数据'), s2);

  // 3. an unavailable message replaces the numbers, and says why
  await p.evaluate(m => wxUpdate(m), { type: 'weather', status: 'unavailable', reason: 'denied', message: '定位权限被拒绝。', can_choose_city: true, mode: 'device' });
  w = await text('#pWeather .wx');
  assert(w.includes('天气不可用') && w.includes('定位权限被拒绝') && !w.includes('18') && !w.includes('Testville'), w);
  assert((await text('#wxSrc')).includes('选择城市'));
  await shot('3-denied');

  // 4. data that went quiet for more than 3 hours is not shown as current
  await p.evaluate(m => wxUpdate(m), { ...ok, observed_epoch: now - 4 * 3600 });
  w = await text('#pWeather .wx');
  assert(w.includes('数据已过期') && w.includes('天气不可用'), w);

  // 5. settings: open, search (sent to the service), pick, typing a space does not start the voice
  const sent = [];
  await p.evaluate(() => { window.__sent = []; window.liveSend = o => { window.__sent.push(o); return true; }; });
  await wake();
  await click('#wxGear');
  assert(await p.isVisible('#wxSet'));
  await p.fill('#wxQ', 'New York');
  await p.press('#wxQ', 'Space');
  assert.strictEqual(await p.inputValue('#wxQ'), 'New York ', 'Space in the city box must type, not start listening');
  await click('#wxForm button[type=submit]');
  await click('#wxDevice');
  await p.evaluate(() => wxCities({ items: [{ label: 'New York · New York · United States' }, { label: 'New York Mills' }], error: '' }));
  await shot('4-settings');
  await click('#wxCities button[data-i="1"]');
  const msgs = await p.evaluate(() => window.__sent);
  assert.deepStrictEqual(msgs, [{ type: 'weather_search', q: 'New York' }, { type: 'weather_device' }, { type: 'weather_pick', i: 1 }]);
  assert(!(await p.isVisible('#wxSet')));

  // 6. the DingTalk card: title, time, link, copy
  await p.evaluate(() => dingtalkShow({ title: 'Sample minutes', time: '2026-11-12 09:30', url: 'https://example.invalid/m/1' }));
  assert(await p.isVisible('#dwsCard'));
  const card = await text('#dwsCard');
  assert(card.includes('Sample minutes') && card.includes('2026-11-12 09:30') && card.includes('https://example.invalid/m/1') && card.includes('只读'), card);
  await click('#dwsCard .dws-copy');
  assert.strictEqual(await p.evaluate(() => navigator.clipboard.readText()), 'https://example.invalid/m/1');
  await shot('5-dingtalk');
  await click('#dwsCard .dws-x');
  assert(!(await p.isVisible('#dwsCard')));

  console.log(errs.length ? errs.join('\n') : 'ok: hud weather + dingtalk card');
  await b.close();
  process.exit(errs.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
