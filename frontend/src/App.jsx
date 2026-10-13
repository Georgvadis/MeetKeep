import { useEffect, useRef, useState } from "react";
import { api, platform, shell } from "./api.js";
import { bytes, folderName, when } from "./format.js";
import {
  AlertIcon, CheckIcon, CloseIcon, DownloadIcon, GearIcon, KeyIcon, LinkIcon, Logo, PlayIcon, PuzzleIcon, StopIcon,
} from "./icons.jsx";

const ACTIVE = ["connecting", "downloading", "processing"];
const isWindows = platform === "windows";
const revealLabel = platform === "macos" ? "Показать в Finder" : "Показать в папке";
// Chrome and Edge on Windows encrypt cookies for themselves, so they are reached through the extension instead.
const BROWSER_OPTIONS = isWindows
  ? [["none", "Через расширение Chrome или без входа"], ["firefox", "Firefox"], ["cookies", "Файл cookies.txt"]]
  : [["chrome", "Chrome"], ["safari", "Safari"], ["firefox", "Firefox"], ["edge", "Edge"], ["brave", "Brave"],
    ["cookies", "Файл cookies.txt"], ["none", "Вход не нужен"]];
const QUALITY_OPTIONS = [["best", "Лучшее доступное"], ["1080", "До 1080p"], ["720", "До 720p"], ["480", "До 480p"], ["360", "До 360p"]];

export default function App() {
  const [view, setView] = useState("downloads");
  const [status, setStatus] = useState(null);
  const [history, setHistory] = useState([]);
  const [settings, setSettings] = useState(null);
  const [url, setUrl] = useState("");
  const [notice, setNotice] = useState("");
  const [closed, setClosed] = useState(false);
  const revision = useRef(-1);

  useEffect(() => {
    let stopped = false;
    async function tick() {
      try {
        const next = await api("/api/status");
        if (stopped) return;
        setStatus(next);
        if (next.revision !== revision.current) {
          revision.current = next.revision;
          const [list, values] = await Promise.all([api("/api/history"), api("/api/settings")]);
          if (stopped) return;
          setHistory(list.items);
          setSettings(values);
        }
      } catch {
        // The app is closing or restarting; the next tick will try again.
      }
    }
    tick();
    const timer = setInterval(tick, 800);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, []);

  async function run(action) {
    setNotice("");
    try {
      await action();
    } catch (error) {
      setNotice(error.message);
    }
  }

  const busy = Boolean(status && ACTIVE.includes(status.stage));
  const done = history.filter((item) => item.status === "done");
  const failed = history.filter((item) => item.status !== "done");

  async function submit(event) {
    event.preventDefault();
    await run(async () => {
      await api("/api/start", { url: url.trim() });
      setUrl("");
      setView("downloads");
    });
  }

  function saveSettings(patch) {
    setSettings((current) => ({ ...current, ...patch }));
    run(() => api("/api/settings", patch));
  }

  if (closed) {
    return <div className="closed"><strong>MeetKeep закрыт</strong><span>Эту вкладку можно закрыть.</span></div>;
  }

  const views = {
    downloads: { title: "Загрузки", items: history },
    done: { title: "Готово", items: done },
    errors: { title: "Ошибки", items: failed },
  };

  return (
    <div className="app">
      <Sidebar view={view} setView={setView} busy={busy} doneCount={done.length} failedCount={failed.length} settings={settings} />
      <main className="main">
        <form className="toolbar" onSubmit={submit}>
          <label className="visually-hidden" htmlFor="url">Ссылка на запись встречи</label>
          <div className="url-field">
            <LinkIcon size={15} />
            <input id="url" type="url" value={url} onChange={(event) => setUrl(event.target.value)}
              placeholder="Вставьте ссылку на запись встречи" autoComplete="off" spellCheck="false" autoFocus />
          </div>
          <button className="primary" type="submit" disabled={busy || !url.trim()}>Скачать</button>
        </form>
        {notice && (
          <div className="notice" role="alert">
            <AlertIcon size={15} />
            <span>{notice}</span>
            <button className="notice-close" type="button" aria-label="Скрыть сообщение" onClick={() => setNotice("")}><CloseIcon /></button>
          </div>
        )}
        <div className="content">
          {view === "settings" ? (
            <Settings settings={settings} save={saveSettings} run={run} hasHistory={history.length > 0} onClosed={() => setClosed(true)} />
          ) : (
            <section aria-labelledby="view-title">
              <h1 id="view-title">{views[view].title}</h1>
              {view === "downloads" && busy && <ActiveRow status={status} onStop={() => run(() => api("/api/cancel", {}))} />}
              {views[view].items.map((item) => (
                <HistoryRow key={item.id} item={item}
                  onReveal={() => run(() => api("/api/reveal", { id: item.id }))}
                  onRetry={() => run(async () => { await api("/api/retry", { id: item.id }); setView("downloads"); })}
                  onRemove={() => run(() => api("/api/history/remove", { id: item.id }))} />
              ))}
              {!views[view].items.length && !(view === "downloads" && busy) && <Empty view={view} />}
            </section>
          )}
        </div>
      </main>
    </div>
  );
}

function Sidebar({ view, setView, busy, doneCount, failedCount, settings }) {
  const item = (id, label, icon, count) => (
    <button type="button" className="nav-item" aria-current={view === id ? "page" : undefined} onClick={() => setView(id)}>
      {icon}
      <span>{label}</span>
      {count ? <span className="count">{count}</span> : null}
    </button>
  );
  const browser = settings ? (BROWSER_OPTIONS.find(([value]) => value === settings.browser)?.[1] ?? "") : "";
  return (
    <nav className="sidebar" aria-label="Разделы">
      <div className="brand"><Logo /><span>MeetKeep</span></div>
      {item("downloads", "Загрузки", <DownloadIcon />, busy ? 1 : 0)}
      {item("done", "Готово", <CheckIcon />, doneCount)}
      {item("errors", "Ошибки", <AlertIcon />, failedCount)}
      <div className="nav-label spaced">Вход на сайты</div>
      {isWindows ? (
        <button type="button" className="nav-item" onClick={() => setView("settings")}>
          <PuzzleIcon />
          <span className="nav-text">Расширение</span>
          <span className={settings?.extension_seen ? "dot on" : "dot"} title={settings?.extension_seen ? "Подключено" : "Не настроено"} />
        </button>
      ) : (
        <button type="button" className="nav-item" onClick={() => setView("settings")}>
          <KeyIcon />
          <span className="nav-text">{settings?.browser === "none" ? "Без входа" : browser}</span>
        </button>
      )}
      <div className="spacer" />
      {item("settings", "Настройки", <GearIcon />, 0)}
    </nav>
  );
}

function ActiveRow({ status, onStop }) {
  const percent = Math.max(0, Math.min(100, status.percent || 0));
  const speed = status.speed ? `${bytes(status.speed)}/с` : "";
  let meta = "Подключаюсь к сайту…";
  if (status.stage === "processing") meta = "Собираю видеофайл…";
  if (status.stage === "downloading") {
    const amount = status.fragments
      ? `фрагмент ${status.fragment} из ${status.fragments}`
      : [bytes(status.downloaded), status.total ? `из ${bytes(status.total)}` : ""].filter(Boolean).join(" ");
    meta = [`${Math.round(percent)}%`, amount, speed].filter(Boolean).join(" · ");
  }
  return (
    <div className="row active">
      <div className="thumb"><PlayIcon size={20} /></div>
      <div className="row-body">
        <div className="row-title">{status.title || "Новая запись"}</div>
        <div className={status.stage === "downloading" ? "bar" : "bar indeterminate"} role="progressbar"
          aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(percent)} aria-label="Ход загрузки">
          <div style={status.stage === "downloading" ? { width: `${percent}%` } : undefined} />
        </div>
        <div className="row-meta">{meta}</div>
      </div>
      <button type="button" className="icon-button" aria-label="Остановить загрузку" onClick={onStop}><StopIcon /></button>
    </div>
  );
}

function HistoryRow({ item, onReveal, onRetry, onRemove }) {
  const ok = item.status === "done";
  const extension = (item.filename.split(".").pop() || "").toUpperCase();
  const meta = ok
    ? [extension, item.height ? `${item.height}p` : "", bytes(item.size), when(item.finished_at)].filter(Boolean).join(" · ")
    : item.message || "Загрузка остановлена";
  return (
    <div className="row">
      <div className={ok ? "thumb" : "thumb muted"}><PlayIcon size={20} /></div>
      <div className="row-body">
        <div className="row-title" title={item.title}>{item.title}</div>
        <div className={ok ? "row-meta" : "row-meta error"}>{meta}</div>
      </div>
      {ok
        ? <button type="button" className="link" onClick={onReveal}>{revealLabel}</button>
        : <button type="button" className="secondary" onClick={onRetry}>Повторить</button>}
      <button type="button" className="icon-button remove" aria-label="Убрать из списка" onClick={onRemove}><CloseIcon /></button>
    </div>
  );
}

function Empty({ view }) {
  const texts = {
    downloads: ["Вставьте ссылку на запись встречи",
      isWindows ? "Или откройте встречу в Chrome и нажмите значок MeetKeep." : "Запись сохранится в выбранную папку."],
    done: ["Здесь появятся скачанные записи", "Пока ничего не скачано."],
    errors: ["Ошибок нет", "Если загрузка не удастся, её можно будет повторить отсюда."],
  };
  const [title, text] = texts[view];
  return (
    <div className="empty">
      <div className="empty-icon">{view === "errors" ? <CheckIcon size={26} /> : <DownloadIcon size={26} />}</div>
      <strong>{title}</strong>
      <span>{text}</span>
    </div>
  );
}

function Settings({ settings, save, run, hasHistory, onClosed }) {
  const [copied, setCopied] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [folderDraft, setFolderDraft] = useState(null);
  if (!settings) return null;

  async function loadCookies(event) {
    const file = event.target.files[0];
    if (!file) return;
    await run(async () => {
      if (file.size > 4_000_000) throw new Error("Файл cookies.txt должен быть меньше 4 МБ");
      await api("/api/cookies", { cookie_text: await file.text() });
    });
  }

  async function copyFolder() {
    try {
      await navigator.clipboard.writeText(settings.extension_folder);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }

  function clearHistory() {
    if (!confirmClear) {
      setConfirmClear(true);
      setTimeout(() => setConfirmClear(false), 4000);
      return;
    }
    setConfirmClear(false);
    run(() => api("/api/history/clear", {}));
  }

  return (
    <section className="settings" aria-labelledby="settings-title">
      <h1 id="settings-title">Настройки</h1>

      <div className="section">
        <h2>Вход на сайт</h2>
        <div className="card">
          <label className="field">
            <span>Откуда взять вход</span>
            <select value={settings.browser} onChange={(event) => save({ browser: event.target.value })}>
              {BROWSER_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <p className="hint">
            {settings.browser === "cookies" ? "Выберите файл cookies.txt, выгруженный из браузера, где вы вошли на сайт."
              : settings.browser === "none" ? (isWindows ? "Для закрытых записей откройте встречу в Chrome и нажмите значок MeetKeep." : "Подходит для записей, открытых по ссылке.")
              : "MeetKeep возьмёт вход из этого браузера. Войдите там на сайт заранее."}
          </p>
          {settings.browser === "cookies" && (
            <label className="field">
              <span>Файл cookies.txt {settings.cookies_loaded && <em className="ok-text">· загружен</em>}</span>
              <input type="file" accept=".txt,text/plain" onChange={loadCookies} />
            </label>
          )}
        </div>
      </div>

      {isWindows && (
        <div className="section">
          <h2>Расширение Chrome</h2>
          <div className="card">
            <div className="status-line">
              <span className={settings.extension_seen ? "dot on" : "dot"} />
              {settings.extension_seen ? "Подключено — нажимайте значок MeetKeep на странице встречи" : "Ещё не подключено"}
            </div>
            <ol className="steps">
              <li>Нажмите «Открыть расширения Chrome».</li>
              <li>Включите «Режим разработчика» и нажмите «Загрузить распакованное расширение».</li>
              <li>Выберите папку: <span className="path">{settings.extension_folder}</span></li>
              <li>Закрепите значок MeetKeep на панели Chrome.</li>
            </ol>
            <div className="buttons">
              <button type="button" className="secondary" onClick={() => run(() => api("/api/extension/open-setup", {}))}>Открыть расширения Chrome</button>
              <button type="button" className="link" onClick={copyFolder}>{copied ? "Путь скопирован" : "Скопировать путь"}</button>
            </div>
          </div>
        </div>
      )}

      <div className="section">
        <h2>Сохранение</h2>
        <div className="card">
          <label className="field">
            <span>Качество</span>
            <select value={settings.quality} onChange={(event) => save({ quality: event.target.value })}>
              {QUALITY_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <div className="field">
            <span>Папка для записей</span>
            {shell === "app" ? (
              <div className="folder">
                <span className="path" title={settings.folder}>{folderName(settings.folder)}</span>
                <button type="button" className="secondary" onClick={() => run(() => api("/api/pick-folder", {}))}>Выбрать…</button>
                <button type="button" className="link" onClick={() => run(() => api("/api/open-folder", {}))}>Открыть</button>
              </div>
            ) : (
              <input className="text-input" aria-label="Папка для записей" value={folderDraft ?? settings.folder}
                onChange={(event) => setFolderDraft(event.target.value)}
                onBlur={() => { if (folderDraft !== null) save({ folder: folderDraft }); setFolderDraft(null); }} />
            )}
          </div>
          <label className="check">
            <input type="checkbox" checked={settings.insecure} onChange={(event) => save({ insecure: event.target.checked })} />
            <span>Не проверять сертификат сайта<span className="hint block">Только для внутренних сайтов с собственным сертификатом.</span></span>
          </label>
        </div>
      </div>

      <div className="section">
        <h2>История</h2>
        <div className="card">
          <div className="buttons">
            <button type="button" className="secondary" disabled={!hasHistory} onClick={clearHistory}>
              {confirmClear ? "Нажмите ещё раз, чтобы очистить" : "Очистить историю"}
            </button>
            <span className="hint">Скачанные файлы останутся на месте.</span>
          </div>
        </div>
      </div>

      {shell !== "app" && (
        <div className="section">
          <div className="buttons">
            <button type="button" className="secondary" onClick={() => run(async () => { await api("/api/shutdown", {}); onClosed(); })}>Закрыть MeetKeep</button>
          </div>
        </div>
      )}
    </section>
  );
}
