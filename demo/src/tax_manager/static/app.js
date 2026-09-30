document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  });
});


const filterToggle = document.querySelector("[data-home-filter-toggle]");
if (filterToggle) {
  const monthToggle = document.querySelector("[data-month-toggle]");
  const monthPicker = document.getElementById("month-picker");
  const panel = () => document.getElementById("home-filter-panel");
  let activeRequest = null;

  function showError(message) {
    const filterMessage = document.getElementById("home-filter-message");
    const monthMessage = document.getElementById("month-message");
    filterMessage.hidden = true;
    monthMessage.hidden = true;
    if (!message) return;
    const output = panel().hidden ? monthMessage : filterMessage;
    if (output === monthMessage) {
      monthPicker.hidden = false;
      monthToggle.setAttribute("aria-expanded", "true");
    }
    output.textContent = message;
    output.hidden = false;
  }

  filterToggle.addEventListener("click", () => {
    const filterPanel = panel();
    filterPanel.hidden = !filterPanel.hidden;
    filterToggle.setAttribute("aria-expanded", String(!filterPanel.hidden));
    if (!filterPanel.hidden) filterPanel.querySelector('input[name="q"]')?.focus();
  });

  monthToggle.addEventListener("click", () => {
    monthPicker.hidden = !monthPicker.hidden;
    monthToggle.setAttribute("aria-expanded", String(!monthPicker.hidden));
    if (!monthPicker.hidden) monthPicker.querySelector("[data-month-input]").focus();
  });

  async function loadHomeResults(url, addHistory) {
    activeRequest?.abort();
    const controller = new AbortController();
    activeRequest = controller;
    panel().querySelector('button[type="submit"]').disabled = true;
    monthPicker.querySelector('button[type="submit"]').disabled = true;
    document.getElementById("home-results").setAttribute("aria-busy", "true");
    showError("");
    try {
      const response = await fetch(url, {
        credentials: "same-origin",
        headers: { "X-Requested-With": "XMLHttpRequest" },
        signal: controller.signal,
      });
      if (response.redirected && new URL(response.url).pathname === "/login") {
        window.location.assign(response.url);
        return false;
      }
      if (response.status === 400) {
        const data = await response.json();
        showError(data.error || "筛选条件无效");
        return false;
      }
      if (!response.ok) throw new Error("筛选失败，请稍后重试");
      const nextPage = new DOMParser().parseFromString(await response.text(), "text/html");
      const nextResults = nextPage.getElementById("home-results");
      const nextPanel = nextPage.getElementById("home-filter-panel");
      const nextDescription = nextPage.getElementById("home-description");
      const nextMonthToggle = nextPage.querySelector("[data-month-toggle]");
      const nextMonthInput = nextPage.querySelector("[data-month-input]");
      if (!nextResults || !nextPanel || !nextDescription || !nextMonthToggle || !nextMonthInput) {
        throw new Error("无法读取筛选结果，请重新登录后重试");
      }
      const wasOpen = !panel().hidden;
      nextPanel.hidden = !wasOpen;
      document.getElementById("home-results").replaceWith(nextResults);
      document.getElementById("home-description").textContent = nextDescription.textContent;
      monthToggle.textContent = nextMonthToggle.textContent;
      monthPicker.querySelector("[data-month-input]").value = nextMonthInput.value;
      monthPicker.querySelector("[data-month-input]").max = nextMonthInput.max;
      filterToggle.setAttribute("aria-expanded", String(wasOpen));
      if (addHistory && url.href !== window.location.href) {
        window.history.pushState(null, "", url.href);
      }
      return true;
    } catch (error) {
      if (error.name !== "AbortError") showError(error.message || "筛选失败，请稍后重试");
      return false;
    } finally {
      if (activeRequest === controller) {
        activeRequest = null;
        panel().querySelector('button[type="submit"]').disabled = false;
        monthPicker.querySelector('button[type="submit"]').disabled = false;
        document.getElementById("home-results").removeAttribute("aria-busy");
      }
    }
  }

  document.addEventListener("submit", (event) => {
    if (event.target.matches("[data-home-filter-form]")) {
      event.preventDefault();
      const url = new URL(event.target.action);
      url.search = new URLSearchParams(new FormData(event.target)).toString();
      loadHomeResults(url, true);
    } else if (event.target.matches("[data-month-form]")) {
      event.preventDefault();
      const url = new URL(window.location.href);
      url.searchParams.set("month", monthPicker.querySelector("[data-month-input]").value);
      loadHomeResults(url, true).then((success) => {
        if (success) {
          monthPicker.hidden = true;
          monthToggle.setAttribute("aria-expanded", "false");
        }
      });
    }
  });

  monthPicker.querySelector("[data-month-input]").addEventListener("change", () => {
    monthPicker.requestSubmit();
  });

  document.addEventListener("click", (event) => {
    const clear = event.target.closest("[data-home-filter-clear]");
    if (!clear) return;
    event.preventDefault();
    loadHomeResults(new URL(clear.href), true);
  });

  window.addEventListener("popstate", () => {
    loadHomeResults(new URL(window.location.href), false);
  });
}
