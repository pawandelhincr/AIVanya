const messagesEl = document.getElementById("messages");
const chipsEl = document.getElementById("chips");
const form = document.getElementById("composer");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");
const accountBox = document.getElementById("account-box");
const brokerBox = document.getElementById("broker-box");
const modePill = document.getElementById("mode-pill");

function mdLite(text) {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
}

function addBubble(role, text, isHtml = false) {
  const div = document.createElement("div");
  div.className = `bubble ${role}`;
  div.innerHTML = isHtml ? text : mdLite(text);
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return div;
}

function setChips(items = []) {
  chipsEl.innerHTML = "";
  items.forEach((q) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = q;
    b.addEventListener("click", () => {
      input.value = q;
      form.requestSubmit();
    });
    chipsEl.appendChild(b);
  });
}

async function refreshAccount() {
  try {
    const [accRes, brRes] = await Promise.all([
      fetch("/api/account"),
      fetch("/api/broker/status"),
    ]);
    const data = await accRes.json();
    const st = await brRes.json();
    modePill.textContent = `${(data.mode || "paper").toUpperCase()} · ${(data.active_broker || "").toUpperCase()}`;
    const pos = (data.positions || [])
      .slice(0, 4)
      .map((p) => `${p.symbol} ${p.qty}`)
      .join(", ");
    accountBox.innerHTML = `
      <div><strong>Mode:</strong> ${data.mode} / ${data.active_broker}</div>
      <div><strong>Cash:</strong> ₹${Number(data.cash).toLocaleString("en-IN")}</div>
      <div><strong>Live ready:</strong> ${data.live_ready ? "yes" : "no"}</div>
      <div><strong>Positions:</strong> ${pos || "none"}</div>
    `;
    const z = st.brokers.zerodha;
    const d = st.brokers.dhan;
    brokerBox.innerHTML = `
      <div><span class="dot ${z.connected ? "on" : "off"}"></span><strong>Zerodha</strong> ${z.connected ? "connected" : "off"} ${z.user_id ? "(" + z.user_id + ")" : ""}</div>
      <div><span class="dot ${d.connected ? "on" : "off"}"></span><strong>Dhan</strong> ${d.connected ? "connected" : "off"} ${d.client_id ? "(" + d.client_id + ")" : ""}</div>
    `;
  } catch {
    accountBox.textContent = "Account unavailable";
    if (brokerBox) brokerBox.textContent = "Broker status unavailable";
  }
}

async function openZerodhaLogin() {
  try {
    const res = await fetch("/api/broker/zerodha/login");
    const data = await res.json();
    if (data.login_url) {
      window.open(data.login_url, "_blank");
      addBubble("bot", "Zerodha login tab khula. Login ke baad callback pe token auto-save hoga. Phir `use zerodha` → `mode live`.");
    } else {
      addBubble("bot", data.message || "Pehle .env mein KITE_API_KEY / KITE_API_SECRET set karo.\n\n" + (data.steps || []).map((s, i) => `${i + 1}. ${s}`).join("\n"));
    }
  } catch (err) {
    addBubble("bot", `Zerodha login error: ${err.message}`);
  }
}

async function sendMessage(text) {
  const msg = text.trim();
  if (!msg) return;
  addBubble("user", msg);
  input.value = "";
  setChips([]);
  const typing = addBubble("bot", "Analyzing markets…", true);
  typing.classList.add("typing");
  sendBtn.disabled = true;

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: msg }),
    });
    const data = await res.json();
    typing.remove();
    addBubble("bot", data.reply || "No reply");
    setChips(data.suggestions || []);
    if (data.data?.account || data.data?.order || data.data?.broker_status || data.data?.status) {
      refreshAccount();
    }
  } catch (err) {
    typing.remove();
    addBubble("bot", `Error: ${err.message}`);
  } finally {
    sendBtn.disabled = false;
    input.focus();
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  sendMessage(input.value);
});

document.querySelectorAll(".quick button").forEach((btn) => {
  btn.addEventListener("click", () => sendMessage(btn.dataset.q));
});

document.getElementById("btn-zerodha")?.addEventListener("click", openZerodhaLogin);
document.getElementById("btn-dhan")?.addEventListener("click", () => sendMessage("connect dhan"));

addBubble(
  "bot",
  "**Namaste — TradeMind ready.**\n\nBrokers: **Zerodha** + **Dhan**\n• `connect zerodha` / `connect dhan`\n• `use zerodha` ya `use dhan`\n• `mode live` / `mode paper`\n\nPehle paper mode mein practice karo."
);
setChips(["connect zerodha", "connect dhan", "RELIANCE buy?", "weekly stocks"]);
refreshAccount();
