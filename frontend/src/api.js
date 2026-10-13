const token = document.querySelector('meta[name="meetkeep-token"]')?.content ?? "";

// "windows" | "macos" | "linux", and "app" (own window) or "browser" (fallback tab).
export const platform = document.documentElement.dataset.platform;
export const shell = document.documentElement.dataset.shell;

export async function api(path, data) {
  const response = await fetch(path, {
    method: data === undefined ? "GET" : "POST",
    headers: { "X-App-Token": token, "Content-Type": "application/json" },
    body: data === undefined ? undefined : JSON.stringify(data),
  });
  let result = {};
  try {
    result = await response.json();
  } catch {
    // An empty or non-JSON body still has a status code to report.
  }
  if (!response.ok) throw new Error(result.error || "Ошибка приложения");
  return result;
}
