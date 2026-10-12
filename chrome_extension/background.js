import { PAIR_KEY } from "./config.js";

const ports = Array.from({length: 20}, (_, i) => 8765 + i);

async function proof(nonce) {
  const bytes = Uint8Array.from(PAIR_KEY.match(/../g).map(value => parseInt(value, 16)));
  const key = await crypto.subtle.importKey("raw", bytes, {name: "HMAC", hash: "SHA-256"}, false, ["sign"]);
  const signature = new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(nonce)));
  return Array.from(signature, byte => byte.toString(16).padStart(2, "0")).join("");
}

async function vidloader() {
  if (!/^[a-f0-9]{64}$/.test(PAIR_KEY)) throw new Error("Установите расширение из папки Vidloader");
  const nonce = Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, "0")).join("");
  const expected = await proof(nonce);
  for (const port of ports) {
    try {
      const address = `http://127.0.0.1:${port}`;
      const response = await fetch(`${address}/api/extension/challenge?nonce=${nonce}`, {signal: AbortSignal.timeout(500)});
      if (response.ok && (await response.json()).proof === expected) return address;
    } catch (_) {}
  }
  throw new Error("Запустите Vidloader и повторите попытку");
}

function cookieLine(cookie) {
  const fields = [cookie.domain, cookie.path, cookie.name, cookie.value];
  if (fields.some(value => /[\t\r\n]/.test(value))) return "";
  const domain = cookie.hostOnly ? cookie.domain : `.${cookie.domain.replace(/^\./, "")}`;
  return [
    `${cookie.httpOnly ? "#HttpOnly_" : ""}${domain}`,
    cookie.hostOnly ? "FALSE" : "TRUE",
    cookie.path || "/",
    cookie.secure ? "TRUE" : "FALSE",
    Math.floor(cookie.expirationDate || 0), cookie.name, cookie.value
  ].join("\t");
}

async function siteCookies(tab) {
  const stores = await chrome.cookies.getAllCookieStores();
  const store = stores.find(item => item.tabIds.includes(tab.id));
  const query = store ? {storeId: store.id} : {};
  const urls = [tab.url];
  const page = new URL(tab.url);
  const match = page.hostname === "sbervideo.sberbank.ru" && page.pathname.match(/^\/meetups\/([\w-]+)\/?$/);
  if (match) urls.push(`https://sbervideo.sberbank.ru/access_proxy/meetup/${match[1]}/index.m3u8`);
  const found = new Map();
  for (const url of urls) {
    for (const cookie of await chrome.cookies.getAll({...query, url})) {
      found.set(`${cookie.domain}\t${cookie.path}\t${cookie.name}`, cookie);
    }
  }
  return "# Netscape HTTP Cookie File\n" + Array.from(found.values(), cookieLine).filter(Boolean).join("\n") + "\n";
}

async function badge(tabId, text, title) {
  await chrome.action.setBadgeBackgroundColor({tabId, color: text === "↓" ? "#2ebf79" : "#c54747"});
  await chrome.action.setBadgeText({tabId, text});
  await chrome.action.setTitle({tabId, title});
}

chrome.action.onClicked.addListener(async tab => {
  try {
    if (!tab.url || !/^https?:\/\//.test(tab.url)) throw new Error("Откройте страницу с видео");
    const origin = `${new URL(tab.url).origin}/*`;
    if (!await chrome.permissions.request({origins: [origin]})) throw new Error("Разрешите доступ к cookies этого сайта");
    const address = await vidloader();
    const cookieText = await siteCookies(tab);
    const response = await fetch(`${address}/api/extension/start`, {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Vidloader-Pair-Key": PAIR_KEY},
      body: JSON.stringify({url: tab.url, cookie_text: cookieText})
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Не удалось начать загрузку");
    await badge(tab.id, "↓", "Vidloader: загрузка началась");
  } catch (error) {
    await badge(tab.id, "!", `Vidloader: ${error.message}`);
  }
});
