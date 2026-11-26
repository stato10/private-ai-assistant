"use strict";

const find = (id) => document.getElementById(id);
// dir="auto" follows the first strong character, so a Hebrew answer that opens with "RAM" would render
// left-to-right and scramble. Any Hebrew letter makes the block right-to-left.
const direction = (text) => /[\u0590-\u05FF]/.test(text) ? "rtl" : "auto";
const unlock = find("unlock");
const status = find("status");
const prompt = find("prompt");
const messages = find("messages");
const model = find("model");
let token = "";
let threads = [];
let active = [];
let pending;

function setSidebar(open) {
  find("sidebar").hidden = !open;
  find("toggle").setAttribute("aria-expanded", String(open));
}

function renderThreads() {
  const title = active[0]?.content || "שיחה חדשה";
  find("title").textContent = title;
  find("title").dir = direction(title);
  find("threads").replaceChildren(...threads.map((thread) => {
    const button = document.createElement("button");
    button.type = "button";
    button.dir = direction(thread[0].content);
    button.textContent = thread[0].content;
    button.setAttribute("aria-current", String(thread === active));
    button.onclick = () => { openThread(thread); prompt.focus(); };
    return button;
  }));
}

function setLocked(locked) {
  unlock.hidden = !locked;
  find("chat").hidden = locked;
  if (!locked) setSidebar(!matchMedia("(max-width: 640px)").matches);
  (locked ? find("token") : prompt).focus();
}

function openThread(thread = []) {
  pending?.abort();
  pending = undefined;
  active = thread;
  messages.replaceChildren();
  active.forEach(({role, content}) => addMessage(role, content));
  find("welcome").hidden = active.length > 0;
  prompt.value = "";
  status.textContent = "";
  find("send").disabled = false;
  renderThreads();
  if (matchMedia("(max-width: 640px)").matches) setSidebar(false);
}

function addMessage(role, content) {
  const item = document.createElement("li");
  item.dir = direction(content);
  item.className = role;
  item.textContent = content;
  messages.append(item);
  const transcript = find("transcript");
  transcript.scrollTop = transcript.scrollHeight;
  return item;
}

unlock.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = unlock.querySelector("button");
  const feedback = find("unlock-status");
  button.disabled = true;
  try {
    const candidate = find("token").value.trim();
    const result = await fetch("/api/v1/session", {headers: {Authorization: "Bearer " + candidate}});
    if (!result.ok) {
      feedback.textContent = "מפתח הגישה נדחה.";
      return;
    }
    const catalog = await fetch("/api/v1/models", {headers: {Authorization: "Bearer " + candidate}});
    if (!catalog.ok) {
      feedback.textContent = "רשימת המודלים המקומיים אינה זמינה. בדקו ש־Ollama פועל ונסו שוב.";
      return;
    }
    const {models, default: defaultModel} = await catalog.json();
    if (!models.length) {
      feedback.textContent = "לא נמצאו מודלים מקומיים נתמכים.";
      return;
    }
    model.replaceChildren(...models.map(({id, label}) => new Option(label, id)));
    if (models.some(({id}) => id === defaultModel)) model.value = defaultModel;
    model.disabled = false;
    token = candidate;
    find("token").value = "";
    feedback.textContent = "";
    setLocked(false);
  } catch {
    feedback.textContent = "אין חיבור לשרת.";
  } finally {
    button.disabled = false;
  }
});

function lock() {
  threads = [];
  openThread();
  token = "";
  setLocked(true);
}

find("clear").addEventListener("click", () => { openThread(); prompt.focus(); });
find("lock").addEventListener("click", lock);
find("toggle").addEventListener("click", () => setSidebar(find("sidebar").hidden));
find("suggestions").addEventListener("click", (event) => {
  if (event.target.tagName !== "BUTTON") return;
  prompt.value = event.target.textContent + " ";
  prompt.focus();
});
prompt.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
  event.preventDefault();
  find("compose").requestSubmit();
});

find("compose").addEventListener("submit", async (event) => {
  event.preventDefault();
  const content = prompt.value.trim();
  if (!content || pending) return;
  let context = active.slice(-8).map(({role, content}) => ({
    role, content: Array.from(content).slice(0, 1500).join(""),
  }));
  const count = (value) => Array.from(value).length;
  while (context.length && context.reduce((n, item) => n + count(item.content), count(content)) > 2400) {
    context = context.slice(2);
  }
  const request = new AbortController();
  pending = request;
  find("welcome").hidden = true;
  const row = addMessage("user", content);
  const user = {role: "user", content};
  find("send").disabled = true;
  prompt.value = "";
  status.textContent = "ממתין לתשובה…";
  try {
    const result = await fetch("/api/v1/chat", {
      method: "POST", signal: request.signal,
      headers: {Authorization: "Bearer " + token, "Content-Type": "application/json"},
      body: JSON.stringify({messages: [...context, user], model: model.value}),
    });
    if (pending !== request) return;
    if (result.status === 401) {
      lock();
      find("unlock-status").textContent = "יש להזין שוב את מפתח הגישה.";
      return;
    }
    if (!result.ok) {
      const errors = {429: "המודל עסוק. נסו שוב בעוד רגע.", 503: "המודל אינו זמין.", 504: "תם זמן ההמתנה למודל."};
      throw new Error(errors[result.status] || "שליחת ההודעה נכשלה.");
    }
    const {answer} = await result.json();
    if (pending !== request) return;
    addMessage("assistant", answer);
    if (!active.length) threads.unshift(active);
    active.push(user, {role: "assistant", content: answer});
    renderThreads();
    status.textContent = "";
  } catch (error) {
    if (pending !== request) return;
    row.remove();
    find("welcome").hidden = active.length > 0;
    if (!prompt.value) prompt.value = content;
    status.textContent = error instanceof TypeError ? "אין חיבור לשרת." : error.message;
  } finally {
    if (pending === request) {
      pending = undefined;
      find("send").disabled = false;
      prompt.focus();
    }
  }
});
