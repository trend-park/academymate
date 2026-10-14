/**
 * 학원 블로그 주제 연구소 — Cloudflare Worker
 *  POST /generate  { prompt, tier: "quick" | "default" }  → { text }
 *  GET  /blog?url=<네이버 블로그 주소>                    → { blogId, title, description, posts: [{title, summary, date, link}] }
 *
 * 환경 변수 (Cloudflare 대시보드 → Settings → Variables and Secrets)
 *  ANTHROPIC_API_KEY  (Secret, 필수)
 *  ALLOWED_ORIGINS    예: https://hakwonmate.co.kr,https://www.hakwonmate.co.kr   (비우면 모든 출처 허용)
 *  MODEL_DEFAULT      기본 claude-sonnet-5-5
 *  MODEL_QUICK        기본 claude-haiku-5-5
 *  DAILY_LIMIT        IP당 하루 생성 횟수 (KV 바인딩 QUOTA가 있을 때만 동작, 기본 40)
 */
export default {
  async fetch(request, env, ctx) {
    const origin = request.headers.get("Origin") || "";
    const cors = corsHeaders(origin, env);
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    if (!originAllowed(origin, env)) return json({ code: "forbidden", message: "허용되지 않은 출처" }, 403, cors);

    const url = new URL(request.url);
    try {
      if (url.pathname === "/generate" && request.method === "POST") return await generate(request, env, cors);
      if (url.pathname === "/blog" && request.method === "GET") return await blog(url.searchParams.get("url") || "", cors);
      if (url.pathname === "/" ) return json({ ok: true, service: "blog-topic-lab" }, 200, cors);
      return json({ code: "not_found" }, 404, cors);
    } catch (e) {
      return json({ code: "upstream_error", message: String(e && e.message || e).slice(0, 200) }, 500, cors);
    }
  }
};

function originAllowed(origin, env) {
  const list = (env.ALLOWED_ORIGINS || "").split(",").map(s => s.trim()).filter(Boolean);
  if (!list.length) return true;
  if (!origin) return true; // 같은 출처·서버 간 호출
  return list.includes(origin);
}
function corsHeaders(origin, env) {
  const list = (env.ALLOWED_ORIGINS || "").split(",").map(s => s.trim()).filter(Boolean);
  return {
    "Access-Control-Allow-Origin": list.length ? (list.includes(origin) ? origin : list[0]) : "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "content-type",
    "Vary": "Origin"
  };
}
function json(obj, status, cors) {
  return new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json; charset=utf-8", ...cors } });
}

/* ---------- /generate ---------- */
async function generate(request, env, cors) {
  if (!env.ANTHROPIC_API_KEY) return json({ code: "no_key", message: "ANTHROPIC_API_KEY가 설정되지 않았습니다." }, 500, cors);
  let body; try { body = await request.json(); } catch { return json({ code: "bad_request" }, 400, cors); }
  const prompt = String(body.prompt || "");
  if (!prompt || prompt.length > 60000) return json({ code: "bad_request", message: "prompt 길이 오류" }, 400, cors);
  const tier = body.tier === "quick" ? "quick" : "default";

  // 일일 한도 (KV 바인딩 QUOTA가 있을 때만)
  if (env.QUOTA) {
    const ip = request.headers.get("CF-Connecting-IP") || "unknown";
    const day = new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Seoul" });
    const key = `q:${day}:${ip}`;
    const used = Number(await env.QUOTA.get(key) || 0);
    const limit = Number(env.DAILY_LIMIT || 40);
    if (used >= limit) return json({ code: "quota", message: "오늘 사용 한도에 도달했습니다." }, 429, cors);
    await env.QUOTA.put(key, String(used + 1), { expirationTtl: 60 * 60 * 36 });
  }

  const model = tier === "quick" ? (env.MODEL_QUICK || "claude-haiku-5-5") : (env.MODEL_DEFAULT || "claude-sonnet-5-5");
  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: { "content-type": "application/json", "x-api-key": env.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01" },
    body: JSON.stringify({
      model, max_tokens: tier === "quick" ? 2000 : 8000, temperature: 0.8,
      system: "너는 요청받은 JSON만 반환한다. 설명 문장이나 마크다운 코드 펜스 없이 JSON 값 하나만 출력한다.",
      messages: [{ role: "user", content: prompt }]
    })
  });
  if (!res.ok) {
    const t = await res.text();
    const code = res.status === 429 ? "rate_limited" : res.status === 401 ? "no_key" : "upstream_error";
    return json({ code, message: t.slice(0, 300) }, res.status === 429 ? 429 : 502, cors);
  }
  const data = await res.json();
  const text = (data.content || []).filter(c => c.type === "text").map(c => c.text).join("");
  return json({ text, model, usage: data.usage || null }, 200, cors);
}

/* ---------- /blog : 네이버 블로그 RSS ---------- */
function naverBlogId(input) {
  let s = input.trim();
  if (!/^https?:\/\//i.test(s)) s = "https://" + s;
  let u; try { u = new URL(s); } catch { return null; }
  const host = u.hostname.replace(/^m\./, "");
  if (host === "blog.naver.com") {
    const id = u.searchParams.get("blogId") || u.pathname.split("/").filter(Boolean)[0];
    return id && /^[A-Za-z0-9_-]{2,40}$/.test(id) ? id : null;
  }
  if (host === "rss.blog.naver.com") { const m = u.pathname.match(/^\/([A-Za-z0-9_-]+)\.xml$/); return m ? m[1] : null; }
  if (host.endsWith(".blog.me")) return host.replace(/\.blog\.me$/, "");
  return null;
}
async function blog(input, cors) {
  const id = naverBlogId(input);
  if (!id) return json({ code: "blog_not_naver", message: "네이버 블로그 주소가 아닙니다." }, 400, cors);
  const res = await fetch(`https://rss.blog.naver.com/${id}.xml`, { headers: { "user-agent": "Mozilla/5.0 (blog-topic-lab)" }, cf: { cacheTtl: 600 } });
  if (!res.ok) return json({ code: "blog_fail", message: `RSS 응답 ${res.status}` }, 502, cors);
  const xml = await res.text();
  const channel = xml.split(/<item[\s>]/)[0];
  const posts = [];
  const items = xml.split(/<item[\s>]/).slice(1);
  for (const it of items.slice(0, 50)) {
    const title = clean(tag(it, "title"));
    if (!title) continue;
    posts.push({ title, summary: clean(tag(it, "description")).slice(0, 300), date: (tag(it, "pubDate") || "").trim(), link: (tag(it, "link") || "").trim() });
  }
  if (!posts.length) return json({ code: "blog_fail", message: "읽을 수 있는 글이 없습니다 (비공개 블로그이거나 RSS가 꺼져 있을 수 있습니다)." }, 404, cors);
  return json({ blogId: id, title: clean(tag(channel, "title")), description: clean(tag(channel, "description")), posts }, 200, cors);
}
function tag(src, name) {
  const m = src.match(new RegExp(`<${name}[^>]*>([\\s\\S]*?)</${name}>`, "i"));
  return m ? m[1] : "";
}
function clean(s) {
  return String(s || "")
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;|&apos;/g, "'")
    .replace(/\s+/g, " ").trim();
}
