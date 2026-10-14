// Live updates. A page that can change while it's open puts on <main>
//   data-live="<check url>"   JSON with a "version" (and "running")
//   data-live-interval="10"   seconds between checks
//   data-live-version="..."   the version the page was rendered at
//   data-live-until-done      stop once "running" is false (a run's page)
// and marks the parts that change with data-live-region and an id. When the
// version changes, the page is fetched again and those parts swapped in, so
// filters, pagination and the rest of the page stay as they are. Without
// JavaScript, the page is what it was: reload to see more.
(function () {
  const main = document.querySelector("main[data-live]");
  if (!main) return;
  const checkUrl = main.dataset.live;
  const every = (Number(main.dataset.liveInterval) || 15) * 1000;
  const untilDone = "liveUntilDone" in main.dataset;
  const note = document.querySelector("[data-live-indicator]");
  let version = main.dataset.liveVersion || null;
  let failures = 0;
  let timer = null;
  let stopped = false;

  function say(text) {
    if (!note) return;
    note.hidden = false;
    note.textContent = text;
  }

  function clock() {
    return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  async function refresh() {
    const res = await fetch(location.href, { cache: "no-store", credentials: "same-origin" });
    if (!res.ok) throw new Error(`page: ${res.status}`);
    const doc = new DOMParser().parseFromString(await res.text(), "text/html");
    for (const region of document.querySelectorAll("[data-live-region][id]")) {
      const fresh = doc.getElementById(region.id);
      if (fresh) region.replaceWith(document.importNode(fresh, true));
    }
  }

  async function tick() {
    timer = null;
    if (stopped) return;
    if (document.hidden) return; // picked up again when the tab is shown
    try {
      const res = await fetch(checkUrl, {
        cache: "no-store",
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (res.status === 401 || res.status === 403) {
        stopped = true;
        say("Live updates stopped: log in again to resume.");
        return;
      }
      if (!res.ok) throw new Error(`check: ${res.status}`);
      const data = await res.json();
      // the first check only learns the version, unless the page gave one
      if (version !== null && data.version !== version) await refresh();
      version = data.version;
      failures = 0;
      if (untilDone && !data.running) {
        stopped = true;
        say(`Finished · updated ${clock()}`);
        return;
      }
      say(`Live · updated ${clock()}`);
    } catch (err) {
      failures += 1;
      say(`Live updates paused (${failures} failed ${failures === 1 ? "try" : "tries"}); retrying…`);
    }
    schedule();
  }

  function schedule() {
    if (stopped || timer !== null) return;
    // back off while the radiator can't be reached
    timer = setTimeout(tick, every * Math.min(2 ** failures, 8));
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden || stopped) return;
    clearTimeout(timer);
    timer = null;
    tick();
  });
  say("Live");
  schedule();
})();
