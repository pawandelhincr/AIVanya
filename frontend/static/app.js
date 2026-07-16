const TOKEN_KEY = "aivanya_token";

const messagesEl = document.getElementById("messages");
const chipsEl = document.getElementById("chips");
const form = document.getElementById("composer");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");
const accountBox = document.getElementById("account-box");
const brokerBox = document.getElementById("broker-box");
const subBox = document.getElementById("sub-box");
const modePill = document.getElementById("mode-pill");
const authGate = document.getElementById("auth-gate");
const paywall = document.getElementById("paywall");
const appShell = document.getElementById("app-shell");

let currentUser = null;

function getToken() {
  return localStorage.getItem(TOKEN_KEY) || "";
}

function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

function authHeaders(extra = {}) {
  const t = getToken();
  return {
    "Content-Type": "application/json",
    ...(t ? { Authorization: `Bearer ${t}`, "X-Auth-Token": t } : {}),
    ...extra,
  };
}

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

function showAuth() {
  authGate.classList.remove("hidden");
  paywall.classList.add("hidden");
  appShell.classList.add("locked");
}

function showPaywall() {
  authGate.classList.add("hidden");
  paywall.classList.remove("hidden");
  appShell.classList.add("locked");
}

function showApp() {
  authGate.classList.add("hidden");
  paywall.classList.add("hidden");
  appShell.classList.remove("locked");
}

function renderSub(user) {
  if (!user || !subBox) return;
  const label =
    user.status === "trial"
      ? `Trial · ${user.days_left} day(s) left`
      : user.status === "active"
        ? `Pro · ${user.days_left} day(s) left`
        : "Expired — upgrade needed";
  subBox.innerHTML = `
    <div><strong>${user.name}</strong></div>
    <div>${user.email}</div>
    <div>${label}</div>
    <div style="font-size:0.8rem;color:#5c6b64;margin-top:4px">Plan: ₹999 / 3 months after trial</div>
  `;
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: authHeaders(options.headers || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) {
    setToken("");
    currentUser = null;
    showAuth();
    throw new Error(data.detail || "Login required");
  }
  if (res.status === 402) {
    if (currentUser) currentUser.active = false;
    showPaywall();
    throw new Error(data.detail || "Subscription required");
  }
  if (!res.ok) {
    throw new Error(data.detail || `HTTP ${res.status}`);
  }
  return data;
}

async function refreshMe() {
  if (!getToken()) {
    showAuth();
    return null;
  }
  try {
    const data = await api("/api/auth/me");
    currentUser = data.user;
    renderSub(currentUser);
    if (!currentUser.active) {
      showPaywall();
      return currentUser;
    }
    showApp();
    return currentUser;
  } catch {
    showAuth();
    return null;
  }
}

async function refreshAccount() {
  try {
    const [acc, st] = await Promise.all([
      api("/api/account"),
      api("/api/broker/status"),
    ]);
    modePill.textContent = `${(acc.mode || "paper").toUpperCase()} · ${(acc.active_broker || "").toUpperCase()}`;
    const pos = (acc.positions || [])
      .slice(0, 4)
      .map((p) => `${p.symbol} ${p.qty}`)
      .join(", ");
    accountBox.innerHTML = `
      <div><strong>Mode:</strong> ${acc.mode} / ${acc.active_broker}</div>
      <div><strong>Cash:</strong> ₹${Number(acc.cash).toLocaleString("en-IN")}</div>
      <div><strong>Live ready:</strong> ${acc.live_ready ? "yes" : "no"}</div>
      <div><strong>Positions:</strong> ${pos || "none"}</div>
    `;
    const z = st.brokers.zerodha;
    const d = st.brokers.dhan;
    brokerBox.innerHTML = `
      <div><span class="dot ${z.connected ? "on" : "off"}"></span><strong>Zerodha</strong> ${z.connected ? "connected" : "off"}</div>
      <div><span class="dot ${d.connected ? "on" : "off"}"></span><strong>Dhan</strong> ${d.connected ? "connected" : "off"}</div>
    `;
  } catch {
    /* gated / logged out */
  }
}

async function openZerodhaLogin() {
  try {
    const data = await api("/api/broker/zerodha/login");
    if (data.login_url) {
      window.open(data.login_url, "_blank");
      addBubble("bot", "Zerodha login tab khula. Login ke baad callback pe token auto-save hoga.");
    } else {
      addBubble("bot", data.message || "Pehle .env mein KITE_API_KEY set karo.");
    }
  } catch (err) {
    addBubble("bot", `Zerodha login error: ${err.message}`);
  }
}

async function startCheckout() {
  const errEl = document.getElementById("pay-err");
  if (errEl) errEl.textContent = "";
  try {
    const order = await api("/api/auth/subscribe/checkout", { method: "POST", body: "{}" });
    if (order.provider === "razorpay" && order.razorpay_key_id && window.Razorpay) {
      const rzp = new Razorpay({
        key: order.razorpay_key_id,
        amount: order.amount_inr * 100,
        currency: "INR",
        name: "AIVanya Pro",
        description: "3 months access",
        order_id: order.order_id,
        prefill: order.prefill || {},
        handler: async function (response) {
          try {
            const result = await api("/api/auth/subscribe/verify", {
              method: "POST",
              body: JSON.stringify({
                payment_id: order.payment_id,
                razorpay_order_id: response.razorpay_order_id,
                razorpay_payment_id: response.razorpay_payment_id,
                razorpay_signature: response.razorpay_signature,
              }),
            });
            currentUser = result.user;
            renderSub(currentUser);
            showApp();
            addBubble("bot", result.message || "Pro activated!");
            refreshAccount();
          } catch (e) {
            if (errEl) errEl.textContent = e.message;
          }
        },
      });
      rzp.open();
      return;
    }
    // Demo / no razorpay
    if (errEl) errEl.textContent = order.message || "Razorpay not configured — use Demo activate.";
  } catch (e) {
    if (errEl) errEl.textContent = e.message;
  }
}

async function activateDemo() {
  const errEl = document.getElementById("pay-err");
  try {
    const checkout = await api("/api/auth/subscribe/checkout", { method: "POST", body: "{}" });
    const result = await api("/api/auth/subscribe/activate-demo", {
      method: "POST",
      body: JSON.stringify({ payment_id: checkout.payment_id }),
    });
    currentUser = result.user;
    renderSub(currentUser);
    showApp();
    addBubble("bot", result.message || "Demo Pro activated for 90 days.");
    refreshAccount();
  } catch (e) {
    if (errEl) errEl.textContent = e.message;
  }
}

async function logout() {
  try {
    await api("/api/auth/logout", { method: "POST", body: "{}" });
  } catch {
    /* ignore */
  }
  setToken("");
  currentUser = null;
  showAuth();
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
    const data = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message: msg }),
    });
    typing.remove();
    addBubble("bot", data.reply || "No reply");
    setChips(data.suggestions || []);
    if (data.user) {
      currentUser = { ...currentUser, ...data.user, active: true };
      renderSub({ ...currentUser, email: currentUser.email || "" });
    }
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

document.querySelectorAll(".auth-tabs .tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".auth-tabs .tab").forEach((t) => t.classList.remove("on"));
    tab.classList.add("on");
    const which = tab.dataset.tab;
    document.getElementById("login-form").classList.toggle("hidden", which !== "login");
    document.getElementById("register-form").classList.toggle("hidden", which !== "register");
  });
});

document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = document.getElementById("login-err");
  err.textContent = "";
  const fd = new FormData(e.target);
  try {
    const data = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: fd.get("email"),
        password: fd.get("password"),
      }),
    }).then(async (r) => {
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || "Login failed");
      return j;
    });
    setToken(data.token);
    currentUser = data.user;
    renderSub(currentUser);
    if (!currentUser.active) showPaywall();
    else {
      showApp();
      refreshAccount();
      addBubble("bot", `Welcome back, **${currentUser.name}**. ${data.message}`);
    }
  } catch (ex) {
    err.textContent = ex.message;
  }
});

document.getElementById("register-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = document.getElementById("register-err");
  err.textContent = "";
  const fd = new FormData(e.target);
  try {
    const data = await fetch("/api/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: fd.get("name"),
        email: fd.get("email"),
        password: fd.get("password"),
      }),
    }).then(async (r) => {
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || "Register failed");
      return j;
    });
    setToken(data.token);
    currentUser = data.user;
    renderSub(currentUser);
    showApp();
    refreshAccount();
    addBubble(
      "bot",
      `**${currentUser.name}**, welcome to AIVanya!\n\n${data.message}\nTrial days left: **${currentUser.days_left}**`
    );
  } catch (ex) {
    err.textContent = ex.message;
  }
});

document.getElementById("btn-zerodha")?.addEventListener("click", openZerodhaLogin);
document.getElementById("btn-dhan")?.addEventListener("click", () => sendMessage("connect dhan"));
document.getElementById("btn-logout")?.addEventListener("click", logout);
document.getElementById("btn-logout-pay")?.addEventListener("click", logout);
document.getElementById("btn-upgrade")?.addEventListener("click", startCheckout);
document.getElementById("btn-pay")?.addEventListener("click", startCheckout);
document.getElementById("btn-demo-pay")?.addEventListener("click", activateDemo);

(async function boot() {
  const user = await refreshMe();
  if (user && user.active) {
    addBubble(
      "bot",
      `**Namaste ${user.name}** — AIVanya ready.\nStatus: **${user.status}** · ${user.days_left} days left\n\nBrokers: Zerodha + Dhan · Plan after trial: **₹999 / 3 months**`
    );
    setChips(["RELIANCE buy?", "NIFTY options", "weekly stocks", "connect zerodha"]);
    refreshAccount();
  } else if (!getToken()) {
    showAuth();
  }
})();
