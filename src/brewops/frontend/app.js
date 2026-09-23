// BrewOps frontend — vanilla JS, no dependencies, no build step.

async function fetchJSON(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `${response.status} ${response.statusText}`);
  }
  return response.json();
}

// ---- dashboard ----

const filter = { start: "", end: "" };

function rangeQuery() {
  const p = new URLSearchParams();
  if (filter.start) p.set("start", filter.start);
  if (filter.end) p.set("end", filter.end);
  const s = p.toString();
  return s ? `?${s}` : "";
}

function renderDrinkBars(perDrink) {
  const container = document.getElementById("drink-bars");
  container.innerHTML = "";
  const max = Math.max(1, ...perDrink.map((d) => d.count));
  for (const drink of perDrink) {
    const row = document.createElement("div");
    row.className = "bar-row";
    row.innerHTML = `
      <span class="bar-label">${drink.label}</span>
      <span class="bar-track"><span class="bar-fill" style="width:${(drink.count / max) * 100}%"></span></span>
      <span class="bar-count">${drink.count}</span>`;
    container.appendChild(row);
  }
}

function renderTimeline(perDay, filtered) {
  const svg = document.getElementById("timeline");
  svg.innerHTML = "";
  const width = 600;
  const height = 130;

  if (perDay.length === 0) {
    const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
    text.setAttribute("x", width / 2);
    text.setAttribute("y", height / 2);
    text.setAttribute("text-anchor", "middle");
    text.setAttribute("class", "timeline-empty");
    text.textContent = filtered ? "No brews in this range" : "No brews yet";
    svg.appendChild(text);
    return;
  }

  const max = Math.max(1, ...perDay.map((d) => d.count));

  if (perDay.every((d) => d.count === 0)) {
    const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
    text.setAttribute("x", width / 2);
    text.setAttribute("y", height / 2);
    text.setAttribute("text-anchor", "middle");
    text.setAttribute("class", "timeline-empty");
    text.textContent = "No brews in this range";
    svg.appendChild(text);
  }

  const barWidth = width / perDay.length;
  perDay.forEach((day, i) => {
    const barHeight = (day.count / max) * (height - 10);
    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", i * barWidth);
    rect.setAttribute("y", height - barHeight);
    rect.setAttribute("width", Math.max(0.5, barWidth - 0.6));
    rect.setAttribute("height", barHeight);
    rect.setAttribute("class", "timeline-bar");
    const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
    title.textContent = `${day.day}: ${day.count} brews`;
    rect.appendChild(title);
    svg.appendChild(rect);
  });
}

function renderMachineCards(healths, filtered) {
  const container = document.getElementById("machine-cards");
  container.innerHTML = "";
  for (const m of healths) {
    const card = document.createElement("div");
    card.className = "card";
    const maintenance = m.last_maintenance
      ? `${m.last_maintenance.type} on ${m.last_maintenance.timestamp.slice(0, 10)}`
      : "none on record";
    const errors = m.recent_errors.length
      ? `<p class="errors">Recent errors: ${m.recent_errors
          .map((e) => `${e.error_code || "?"} (${e.timestamp.slice(0, 10)})`)
          .join(", ")}</p>`
      : "";
    const brewCountText = filtered
      ? `${m.brew_count} brews in range`
      : `${m.brew_count} brews`;
    const noBrewsText = filtered ? "no brews in range" : "no brews yet";
    const specialtyText = filtered ? "no specialty in range" : "no specialty yet";
    card.innerHTML = `
      <h3 title="${m.name}">${m.name}</h3>
      <p class="badge">${m.has_telemetry ? "telemetry" : "manual log"}</p>
      <p>${brewCountText} · last ${m.last_brew ? m.last_brew.slice(0, 16) : "never"}</p>
      <p>Busiest day: ${m.busiest_day ? `${m.busiest_day} (${m.busiest_day_count} brews)` : noBrewsText}</p>
      <p>Specialty: ${m.specialty ? `${m.specialty} (${m.specialty_count} brews)` : specialtyText}</p>
      <p>Last maintenance: ${maintenance}</p>
      ${errors}`;
    container.appendChild(card);
  }
}

async function loadDashboard() {
  const stats = await fetchJSON("/api/stats" + rangeQuery());
  document.getElementById("total-brews").textContent = stats.total_brews;
  const lastDay = stats.per_day[stats.per_day.length - 1];
  document.getElementById("brews-today").textContent = lastDay ? lastDay.count : 0;

  const filtered = filter.start || filter.end;
  const totalLabel = filtered ? "brews in range" : "brews total";
  const lastDayLabel = filtered ? "brews on last day of range" : "brews on last active day";
  document.getElementById("total-label").textContent = totalLabel;
  document.getElementById("last-day-label").textContent = lastDayLabel;

  renderDrinkBars(stats.per_drink);
  renderTimeline(stats.per_day, filtered);

  const machines = await fetchJSON("/api/machines");
  document.getElementById("machine-count").textContent = machines.length;
  const healths = await Promise.all(machines.map((m) => fetchJSON(`/api/machines/${m.id}${rangeQuery()}`)));
  renderMachineCards(healths, filtered);

  if (stats.total_brews === 0 && filtered) {
    document.getElementById("filter-message").textContent = "No brews in this range.";
    document.getElementById("filter-message").className = "message";
  } else {
    document.getElementById("filter-message").textContent = "";
    document.getElementById("filter-message").className = "message";
  }
}

// ---- forms ----

function localNow() {
  const now = new Date();
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
  return now.toISOString().slice(0, 16); // datetime-local format
}

function fillSelect(select, items, valueKey, labelKey) {
  select.innerHTML = "";
  for (const item of items) {
    const option = document.createElement("option");
    option.value = item[valueKey];
    option.textContent = item[labelKey];
    select.appendChild(option);
  }
}

async function setupFilter() {
  const startInput = document.getElementById("filter-start");
  const endInput = document.getElementById("filter-end");
  const messageEl = document.getElementById("filter-message");
  const presetButtons = document.querySelectorAll(".preset");

  function updateFilterFromInputs() {
    filter.start = startInput.value;
    filter.end = endInput.value;

    messageEl.textContent = "";
    messageEl.className = "message";

    if (filter.start && filter.end && filter.start > filter.end) {
      messageEl.textContent = "From date must not be after To date.";
      messageEl.className = "message error";
      return;
    }

    presetButtons.forEach((btn) => btn.classList.remove("active"));
    loadDashboard().catch((error) => {
      messageEl.textContent = error.message;
      messageEl.className = "message error";
    });
  }

  startInput.addEventListener("change", () => {
    presetButtons.forEach((btn) => btn.classList.remove("active"));
    updateFilterFromInputs();
  });

  endInput.addEventListener("change", () => {
    presetButtons.forEach((btn) => btn.classList.remove("active"));
    updateFilterFromInputs();
  });

  presetButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const days = btn.dataset.days;

      if (days === "all") {
        startInput.value = "";
        endInput.value = "";
        filter.start = "";
        filter.end = "";
      } else {
        const today = new Date();
        today.setMinutes(today.getMinutes() - today.getTimezoneOffset());
        const endDate = today.toISOString().slice(0, 10);
        const startDate = new Date(today);
        startDate.setDate(startDate.getDate() - (parseInt(days) - 1));
        startDate.setMinutes(startDate.getMinutes() - startDate.getTimezoneOffset());
        const startDateStr = startDate.toISOString().slice(0, 10);

        startInput.value = startDateStr;
        endInput.value = endDate;
        filter.start = startDateStr;
        filter.end = endDate;
      }

      presetButtons.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      messageEl.textContent = "";
      messageEl.className = "message";
      loadDashboard().catch((error) => {
        messageEl.textContent = error.message;
        messageEl.className = "message error";
      });
    });
  });

  const exportLink = document.getElementById("export-csv");
  exportLink.addEventListener("click", (event) => {
    if (filter.start && filter.end && filter.start > filter.end) {
      event.preventDefault();
      messageEl.textContent = "From date must not be after To date.";
      messageEl.className = "message error";
      return;
    }
    exportLink.href = "/api/brews/export.csv" + rangeQuery();
  });
}

async function setupForms() {
  const machines = await fetchJSON("/api/machines");
  const drinks = await fetchJSON("/api/drink-types");
  fillSelect(document.getElementById("brew-machine"), machines, "id", "name");
  fillSelect(document.getElementById("brew-drink"), drinks, "name", "label");
  fillSelect(document.getElementById("maintenance-machine"), machines, "id", "name");
  document.getElementById("brew-timestamp").value = localNow();
  document.getElementById("maintenance-timestamp").value = localNow();

  document.getElementById("brew-form").addEventListener("submit", (event) =>
    submitForm(event, "/api/brews", "brew-message", () => ({
      machine_id: Number(document.getElementById("brew-machine").value),
      drink_type: document.getElementById("brew-drink").value,
      timestamp: document.getElementById("brew-timestamp").value,
    }))
  );

  document.getElementById("maintenance-form").addEventListener("submit", (event) =>
    submitForm(event, "/api/maintenance", "maintenance-message", () => ({
      machine_id: Number(document.getElementById("maintenance-machine").value),
      type: document.getElementById("maintenance-type").value,
      timestamp: document.getElementById("maintenance-timestamp").value,
      note: document.getElementById("maintenance-note").value || null,
    }))
  );
}

async function submitForm(event, url, messageId, buildPayload) {
  event.preventDefault();
  const message = document.getElementById(messageId);
  message.textContent = "";
  message.className = "message";
  try {
    await fetchJSON(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildPayload()),
    });
    message.textContent = "Logged.";
    message.classList.add("ok");
    await loadDashboard();
  } catch (error) {
    message.textContent = error.message;
    message.classList.add("error");
  }
}

loadDashboard().catch((error) => {
  document.getElementById("total-brews").textContent = "!";
  console.error("Dashboard failed to load:", error);
});
setupFilter().catch((error) => console.error("Filter setup failed:", error));
setupForms().catch((error) => console.error("Form setup failed:", error));
