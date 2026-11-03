// Chat UI: posts the question to /api/ask and shows the answer with its run
// metadata. Text is inserted with textContent only (no HTML from the model).
const form = document.getElementById("ask");
const input = document.getElementById("question");
const send = document.getElementById("send");
const log = document.getElementById("log");

const MESSAGES = {
  rate_limited: "Too many questions: wait a minute.",
  prompt_injection: "Blocked by the prompt-injection guardrail.",
  daily_budget: "The agent's daily budget is used up. Try again tomorrow.",
  quota_exceeded: "The free daily AI allocation is used up. Try again tomorrow.",
  agent_paused: "The agent is paused by an operator.",
};

function add(cls, text, meta) {
  const div = document.createElement("div");
  div.className = `msg ${cls}`;
  div.textContent = text;
  if (meta) {
    const small = document.createElement("div");
    small.className = "meta";
    small.textContent = meta;
    div.appendChild(small);
  }
  log.prepend(div);
}

async function ask(question) {
  send.disabled = true;
  add("user", question);
  try {
    const resp = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      add("blocked", MESSAGES[data.error?.code] ?? data.error?.message ?? "Request failed.");
    } else if (data.answer) {
      add("agent", data.answer, `${data.steps} steps · $${data.cost_usd.toFixed(5)} · run ${data.run_id}`);
    } else {
      const reason = data.stop_reason;
      add("blocked", MESSAGES[reason] ?? (reason?.startsWith("pii_")
        ? "Blocked: the message contains personal data." : `No answer (${data.status}: ${reason}).`),
        `run ${data.run_id}`);
    }
  } catch {
    add("blocked", "Network error.");
  } finally {
    send.disabled = false;
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const q = input.value.trim();
  if (q) ask(q);
  input.value = "";
});

for (const b of document.querySelectorAll(".examples button")) {
  b.addEventListener("click", () => ask(b.textContent));
}
