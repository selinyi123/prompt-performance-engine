const fs = require('fs');
const { execFileSync } = require('child_process');
const { chromium } = require('playwright');

const TOKEN = 'chen-li-29-9';
const HOME = 'https://www.zhihu.com/';
const POSTS = `https://www.zhihu.com/people/${TOKEN}/posts`;
const START = Date.parse('2025-01-01T00:00:00+08:00') / 1000;
const END = Date.parse('2026-08-20T00:00:00+08:00') / 1000;
const SIGNER = process.platform === 'win32' ? './signer/target/release/zhihu-sign-cli.exe' : './signer/target/release/zhihu-sign-cli';
const lines = [];
const sleep = ms => new Promise(r => setTimeout(r, ms));

function sign(pathAndQuery, dc0) {
  const out = execFileSync(SIGNER, [pathAndQuery, dc0], { encoding: 'utf8' });
  const h = {};
  for (const line of out.trim().split(/\r?\n/)) {
    const i = line.indexOf('\t');
    if (i > 0) h[line.slice(0, i)] = line.slice(i + 1);
  }
  return h;
}

function normalize(item) {
  const created = Number(item.created || item.created_time || 0);
  return {
    id: String(item.id),
    title: String(item.title || ''),
    created,
    created_iso: created ? new Date(created * 1000).toISOString() : null,
    created_date_taipei: created ? new Date((created + 8 * 3600) * 1000).toISOString().slice(0, 10) : null,
    updated: Number(item.updated || item.updated_time || 0),
    url: `https://zhuanlan.zhihu.com/p/${item.id}`,
    author: item.author?.name || null,
    author_url_token: item.author?.url_token || item.author?.urlToken || null,
  };
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ locale: 'zh-CN', viewport: { width: 1920, height: 1080 } });
  if (fs.existsSync('MediaCrawler/libs/stealth.min.js')) await context.addInitScript({ path: 'MediaCrawler/libs/stealth.min.js' });
  const page = await context.newPage();

  const home = await page.goto(HOME, { waitUntil: 'domcontentloaded', timeout: 45000 }).catch(() => null);
  await page.waitForTimeout(7000);
  lines.push(`platform=${process.platform}`);
  lines.push(`home status=${home?.status() ?? 'navigation-error'} title=${JSON.stringify(await page.title())}`);

  const cookies = await context.cookies([HOME]);
  const dc0 = cookies.find(c => c.name === 'd_c0')?.value;
  const xsrf = cookies.find(c => c.name === '_xsrf')?.value;
  lines.push(`cookie-names=${[...new Set(cookies.map(c => c.name))].sort().join(',')}`);
  lines.push(`has-d_c0=${Boolean(dc0)} has-xsrf=${Boolean(xsrf)}`);
  if (!dc0) throw new Error('d_c0 not obtained');

  const posts = await page.goto(POSTS, { waitUntil: 'domcontentloaded', timeout: 45000 }).catch(() => null);
  await page.waitForTimeout(5000);
  lines.push(`posts status=${posts?.status() ?? 'navigation-error'} title=${JSON.stringify(await page.title())}`);

  let nextUrl = `https://www.zhihu.com/api/v4/members/${TOKEN}/articles?sort_by=created&offset=0&limit=20`;
  const collected = new Map();
  let pages = 0;
  let creator = null;
  let reachedBeforeStart = false;

  while (nextUrl && pages < 400) {
    const u = new URL(nextUrl);
    const pathAndQuery = u.pathname + u.search;
    const sig = sign(pathAndQuery, dc0);
    const result = await page.evaluate(async ({ url, sig, xsrf }) => {
      const headers = {
        accept: 'application/json, text/plain, */*',
        'x-zse-93': sig['x-zse-93'],
        'x-zse-96': sig['x-zse-96'],
        'x-requested-with': sig['x-requested-with'] || 'fetch',
      };
      if (xsrf) headers['x-xsrftoken'] = xsrf;
      try {
        const r = await fetch(url, { method: 'GET', credentials: 'include', headers });
        return { status: r.status, text: await r.text() };
      } catch (e) {
        return { status: 0, text: String(e) };
      }
    }, { url: nextUrl, sig, xsrf });

    lines.push(`page=${pages + 1} offset=${u.searchParams.get('offset') ?? '?'} status=${result.status}`);
    if (result.status !== 200) {
      lines.push(`error-body=${result.text.slice(0, 1200).replace(/\s+/g, ' ')}`);
      fs.writeFileSync('mac-current-error.txt', `HTTP ${result.status}\n${result.text.slice(0, 20000)}\n`);
      break;
    }

    const body = JSON.parse(result.text);
    const data = Array.isArray(body.data) ? body.data : [];
    pages++;
    if (data.length && !creator) {
      const a = data[0].author || {};
      creator = { id: a.id || null, name: a.name || null, url_token: a.url_token || a.urlToken || TOKEN };
    }
    for (const item of data) {
      const n = normalize(item);
      if (n.id && n.title) collected.set(n.id, n);
    }
    const times = data.map(x => Number(x.created || x.created_time || 0)).filter(Boolean);
    const oldest = times.length ? Math.min(...times) : Infinity;
    const newest = times.length ? Math.max(...times) : 0;
    lines.push(`data=${data.length} total=${collected.size} newest=${newest ? new Date(newest * 1000).toISOString() : 'none'} oldest=${Number.isFinite(oldest) ? new Date(oldest * 1000).toISOString() : 'none'} is_end=${Boolean(body.paging?.is_end)}`);

    if (!data.length || body.paging?.is_end) break;
    if (Number.isFinite(oldest) && oldest < START) {
      reachedBeforeStart = true;
      lines.push('stop: reached before requested start date');
      break;
    }
    const candidate = body.paging?.next;
    if (!candidate || candidate === nextUrl) {
      lines.push('stop: paging.next missing or unchanged');
      break;
    }
    nextUrl = candidate.startsWith('http') ? candidate : new URL(candidate, HOME).href;
    await sleep(700);
  }

  const all = [...collected.values()];
  const articles = all.filter(x => x.created >= START && x.created < END).sort((a, b) => b.created - a.created);
  lines.push(`final pages=${pages} captured=${all.length} matched=${articles.length} reachedBeforeStart=${reachedBeforeStart}`);

  if (pages > 0) {
    fs.writeFileSync('mac-current-result.json', JSON.stringify({
      source: `Zhihu articles API with 2026 zhihu_sign v0.1.0 on ${process.platform} GitHub runner; MediaCrawler stealth bootstrap`,
      target: `https://www.zhihu.com/people/${TOKEN}`,
      date_range: { start: '2025-01-01T00:00:00+08:00', end_inclusive: '2026-08-19T23:59:59+08:00' },
      creator,
      pages_fetched: pages,
      captured_count: all.length,
      count: articles.length,
      reached_before_start: reachedBeforeStart,
      articles,
    }, null, 2));
  }
  fs.writeFileSync('mac-current-status.txt', lines.join('\n') + '\n');
  await browser.close();
  if (pages === 0) process.exitCode = 1;
}

main().catch(e => {
  lines.push(`fatal=${e.stack || e}`);
  fs.writeFileSync('mac-current-status.txt', lines.join('\n') + '\n');
  fs.writeFileSync('mac-current-error.txt', String(e.stack || e) + '\n');
  console.error(e);
  process.exit(1);
});
