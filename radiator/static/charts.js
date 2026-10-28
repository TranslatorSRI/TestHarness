// Hover layer for the server-rendered charts and status cells. Tooltips only
// enhance: every value is also in the page's tables. All text is inserted with
// textContent, since names and messages come from uploaded data.
(function () {
  const tip = document.createElement("div");
  tip.className = "tooltip";
  document.body.appendChild(tip);

  function place(event) {
    const pad = 14;
    let x = event.clientX + pad;
    let y = event.clientY + pad;
    const rect = tip.getBoundingClientRect();
    if (x + rect.width > window.innerWidth - 8) x = event.clientX - rect.width - pad;
    if (y + rect.height > window.innerHeight - 8) y = event.clientY - rect.height - pad;
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  }

  function show(title, rows, event) {
    tip.replaceChildren();
    const head = document.createElement("div");
    head.className = "tt-title";
    head.textContent = title;
    tip.appendChild(head);
    for (const row of rows) {
      const line = document.createElement("div");
      line.className = "tt-row";
      if (row.color) {
        const key = document.createElement("span");
        key.className = "tt-key";
        key.style.background = row.color;
        line.appendChild(key);
      }
      const value = document.createElement("span");
      value.className = "tt-value";
      value.textContent = row.value;
      const name = document.createElement("span");
      name.className = "tt-name";
      name.textContent = row.name;
      line.append(value, name);
      tip.appendChild(line);
    }
    tip.style.display = "block";
    place(event);
  }

  function hide() {
    tip.style.display = "none";
  }

  // Line charts: a crosshair that snaps to the nearest run, listing every series.
  for (const wrap of document.querySelectorAll(".chart-wrap[data-hover]")) {
    const data = JSON.parse(wrap.dataset.hover);
    const svg = wrap.querySelector("svg");
    const cross = svg.querySelector(".crosshair");
    svg.addEventListener("pointermove", (event) => {
      const box = svg.getBoundingClientRect();
      const x = ((event.clientX - box.left) / box.width) * data.width;
      let best = 0;
      for (let i = 1; i < data.xs.length; i++) {
        if (Math.abs(data.xs[i] - x) < Math.abs(data.xs[best] - x)) best = i;
      }
      cross.setAttribute("x1", data.xs[best]);
      cross.setAttribute("x2", data.xs[best]);
      cross.setAttribute("visibility", "visible");
      const rows = data.series
        .filter((s) => s.values[best] !== null)
        .map((s) => ({ color: s.color, value: s.values[best], name: s.name }));
      if (!rows.length) rows.push({ value: "no data", name: "" });
      show(data.titles[best], rows, event);
    });
    svg.addEventListener("pointerleave", () => {
      cross.setAttribute("visibility", "hidden");
      hide();
    });
  }

  // Cells and bar segments: each carries its own tooltip.
  function cellTip(event) {
    const el = event.target.closest("[data-tip]");
    if (!el) return;
    const lines = el.dataset.tip.split("\n");
    show(lines[0], lines.slice(1).map((l) => ({ value: l, name: "" })), event);
  }
  document.addEventListener("pointerover", cellTip);
  document.addEventListener("pointermove", (event) => {
    if (event.target.closest("[data-tip]")) place(event);
  });
  document.addEventListener("pointerout", (event) => {
    if (event.target.closest("[data-tip]")) hide();
  });
  document.addEventListener("focusin", (event) => {
    const el = event.target.closest("[data-tip]");
    if (!el) return;
    const box = el.getBoundingClientRect();
    cellTip({ target: el, clientX: box.right, clientY: box.bottom });
  });
  document.addEventListener("focusout", hide);
})();
