// Small client for the FastAPI backend. Trip endpoints stream NDJSON events.
async function errorText(res) {
  const body = await res.json().catch(() => ({ detail: res.statusText }));
  const d = body.detail;
  return Array.isArray(d) ? d.map((e) => `${e.loc?.at(-1)}: ${e.msg}`).join(", ") : d || res.statusText;
}

async function streamEvents(url, body, onEvent) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await errorText(res));
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let nl;
    while ((nl = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, nl).trim();
      buffer = buffer.slice(nl + 1);
      if (line) onEvent(JSON.parse(line));
    }
  }
}

export const getMeta = () => fetch("/api/meta").then((r) => r.json());
export const startTrip = (form, onEvent) => streamEvents("/api/trips", form, onEvent);
export const sendDecision = (threadId, decision, onEvent) =>
  streamEvents(`/api/trips/${threadId}/decision`, decision, onEvent);
