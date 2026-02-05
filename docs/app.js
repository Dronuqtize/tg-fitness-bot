const tg = window.Telegram?.WebApp;
const initData = tg?.initData || "";
const API_BASE = (window.APP_CONFIG?.API_BASE || "").trim();

const byId = (id) => document.getElementById(id);

const state = {
  today: null,
  progress: [],
};

function setStatus(text, isError = false) {
  const el = byId("status");
  el.textContent = text;
  el.classList.toggle("error", isError);
}

async function api(path, opts = {}) {
  if (!API_BASE || API_BASE.includes("YOUR-RENDER-API")) {
    throw new Error("API_BASE не настроен");
  }
  const res = await fetch(`${API_BASE}${path}` , {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      "X-TG-INIT-DATA": initData,
      ...(opts.headers || {})
    }
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

async function loadToday() {
  setStatus("Загружаю план на сегодня...");
  const data = await api("/api/today");
  state.today = data;
  renderToday();
  setStatus("Готово");
}

async function loadProgress() {
  const data = await api("/api/progress");
  state.progress = data;
  renderProgress();
}

function renderToday() {
  const t = state.today;
  if (!t) return;
  byId("today-title").textContent = t.day_type === "train" ? "Тренировка" : "Отдых";
  byId("today-macros").textContent = `КБЖУ: ${t.macros.kcal} ккал, Б ${t.macros.protein}, Ж ${t.macros.fat}, У ${t.macros.carbs}`;

  const workoutBox = byId("workout-box");
  workoutBox.innerHTML = "";
  if (t.workout) {
    const title = document.createElement("div");
    title.className = "workout-title";
    title.textContent = t.workout.title;
    workoutBox.appendChild(title);

    ["easy","medium","hard"].forEach(level => {
      const list = t.workout[level] || [];
      if (!list.length) return;
      const block = document.createElement("div");
      block.className = "workout-level";
      block.innerHTML = `<h4>${level}</h4>` + list.map((x,i)=>`<div>${i+1}. ${x.name} — ${x.sets}x${x.reps} ${x.weight?"("+x.weight+")":""}</div>`).join(" ");
      workoutBox.appendChild(block);
    });
  } else {
    workoutBox.innerHTML = "Сегодня отдых";
  }
}

function renderProgress() {
  const tbody = byId("progress-body");
  tbody.innerHTML = "";
  state.progress.forEach(row => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${row.date}</td>
      <td contenteditable="true" data-id="${row.id}" data-field="weight">${row.weight ?? ""}</td>
      <td contenteditable="true" data-id="${row.id}" data-field="waist">${row.waist ?? ""}</td>
      <td contenteditable="true" data-id="${row.id}" data-field="belly">${row.belly ?? ""}</td>
      <td contenteditable="true" data-id="${row.id}" data-field="biceps">${row.biceps ?? ""}</td>
      <td contenteditable="true" data-id="${row.id}" data-field="chest">${row.chest ?? ""}</td>
      <td><button class="btn" data-save="${row.id}">Сохранить</button></td>
    `;
    tbody.appendChild(tr);
  });

  tbody.querySelectorAll("button[data-save]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const id = btn.getAttribute("data-save");
      const cells = tbody.querySelectorAll(`[data-id='${id}']`);
      const payload = {};
      cells.forEach(c => {
        const key = c.getAttribute("data-field");
        const value = c.textContent.trim();
        payload[key] = value === "" ? null : Number(value);
      });
      await api(`/api/progress/${id}`, { method: "PUT", body: JSON.stringify(payload)});
      setStatus("Сохранено");
    });
  });
}

byId("add-progress").addEventListener("click", async () => {
  const weight = Number(byId("p-weight").value);
  const waist = Number(byId("p-waist").value);
  const belly = Number(byId("p-belly").value);
  const biceps = Number(byId("p-biceps").value);
  const chest = Number(byId("p-chest").value);

  try {
    await api("/api/progress", {
      method: "POST",
      body: JSON.stringify({ weight, waist, belly, biceps, chest })
    });
    await loadProgress();
    setStatus("Прогресс сохранен");
  } catch (err) {
    setStatus(err.message || "Ошибка сохранения", true);
  }
});

function setupTabs() {
  document.querySelectorAll(".tab").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      const target = btn.getAttribute("data-tab");
      document.querySelectorAll(".view").forEach(v => {
        v.classList.toggle("hidden", v.getAttribute("data-view") !== target);
      });
    });
  });
}

async function init() {
  if (!tg) {
    setStatus("Открой в Telegram Mini App", true);
    return;
  }
  tg.expand();
  setupTabs();
  try {
    await loadToday();
    await loadProgress();
  } catch (err) {
    setStatus(err.message || "Ошибка загрузки", true);
  }
}

init().catch(err => setStatus(err.message));
