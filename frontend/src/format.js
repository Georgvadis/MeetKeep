const UNITS = ["Б", "КБ", "МБ", "ГБ", "ТБ"];

export function bytes(value) {
  if (!value) return "";
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < UNITS.length - 1) {
    size /= 1024;
    unit += 1;
  }
  const digits = unit > 0 && size < 10 ? 1 : 0;
  return `${size.toLocaleString("ru-RU", { maximumFractionDigits: digits })} ${UNITS[unit]}`;
}

function sameDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

export function when(seconds) {
  const date = new Date(seconds * 1000);
  const now = new Date();
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  const time = date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  if (sameDay(date, now)) return `сегодня, ${time}`;
  if (sameDay(date, yesterday)) return `вчера, ${time}`;
  return `${date.toLocaleDateString("ru-RU", { day: "numeric", month: "long" })}, ${time}`;
}

export function folderName(path) {
  const parts = (path || "").split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] || path;
}
