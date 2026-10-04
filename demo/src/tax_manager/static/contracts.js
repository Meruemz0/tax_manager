(() => {
  const workspace = document.querySelector("[data-contract-workspace]");
  if (workspace) {
    const form = workspace.querySelector("[data-contract-form]");
    const paper = document.getElementById("contract-paper");
    const status = document.getElementById("contract-fill-status");
    const error = document.getElementById("contract-error");
    const buttons = [...workspace.querySelectorAll(".contract-toolbar button")];
    let activeRequest = null;
    let exporting = false;
    function showError(message) {
      error.textContent = message;
      error.hidden = !message;
    }
    function values() {
      const result = Object.create(null);
      paper.querySelectorAll("[data-contract-field]").forEach((input) => {
        if (!(input.dataset.contractField in result)) result[input.dataset.contractField] = input.value;
      });
      return result;
    }
    function countBlanks() {
      const items = Object.values(values());
      if (form.elements.version.value) status.textContent = `已填写 ${items.filter((v) => v.trim()).length} / ${items.length} 个空格`;
    }
    function makeInput(field) {
      const input = document.createElement(field.type === "textarea" ? "textarea" : "input");
      input.className = "contract-blank";
      input.name = "value__" + field.key;
      input.dataset.contractField = field.key;
      input.dataset.fieldType = field.type;
      input.setAttribute("aria-label", field.label);
      input.placeholder = field.label;
      input.maxLength = field.type === "textarea" ? 2000 : 500;
      if (field.type === "textarea") {
        input.classList.add("contract-multiline");
        input.rows = 1;
      } else {
        input.type = ["date", "month"].includes(field.type) ? field.type :
          ["money", "day"].includes(field.type) ? "number" : "text";
        if (field.type === "money") {
          input.min = "0"; input.max = "9999999999.99"; input.step = "0.01";
        }
        if (field.type === "day") {
          input.min = "1"; input.max = "31"; input.step = "1";
        }
      }
      return input;
    }
    function renderDocument(doc, preserved = Object.create(null)) {
      const title = document.createElement("h2");
      title.textContent = doc.title;
      const fragment = document.createDocumentFragment();
      fragment.append(title);
      doc.lines.forEach((parts) => {
        const line = document.createElement("p");
        line.className = "contract-line";
        parts.forEach((part) => {
          if ("text" in part) line.append(document.createTextNode(part.text));
          else {
            const input = makeInput(part.field);
            const old = preserved[part.field.key];
            if (old && old.type === part.field.type) input.value = old.value;
            line.append(input);
          }
        });
        fragment.append(line);
      });
      paper.replaceChildren(fragment);
      form.action = doc.generate_url;
      form.elements.version.value = doc.version;
      buttons.forEach((button) => { button.disabled = false; });
      countBlanks();
    }
    workspace.addEventListener("click", async (event) => {
      const link = event.target.closest("[data-contract-select]");
      if (!link) return;
      event.preventDefault();
      if (exporting) return;
      const sameTemplate = link.classList.contains("active");
      const filled = values();
      const message = sameTemplate
        ? "重新加载模板会更新正文，并保留类型未变的空格内容。请核对新条款。继续？"
        : "切换模板会清空本次填写内容，是否继续？";
      if (Object.values(filled).some((v) => v.trim()) && !window.confirm(message)) return;
      const preserved = Object.create(null);
      if (sameTemplate) paper.querySelectorAll("[data-contract-field]").forEach((input) => {
        preserved[input.dataset.contractField] = { value: input.value, type: input.dataset.fieldType };
      });
      activeRequest?.abort();
      const controller = new AbortController();
      activeRequest = controller;
      showError("");
      status.textContent = "正在加载合同…";
      buttons.forEach((button) => { button.disabled = true; });
      try {
        const response = await fetch(link.dataset.documentUrl, {
          credentials: "same-origin", cache: "no-store", signal: controller.signal,
        });
        if (response.redirected) { window.location.assign(response.url); return; }
        if (!response.ok) throw new Error("合同加载失败，请重试");
        const doc = await response.json();
        if (activeRequest !== controller) return;
        renderDocument(doc, preserved);
        workspace.querySelectorAll("[data-contract-select]").forEach((item) => {
          item.classList.toggle("active", item === link);
        });
        history.replaceState(null, "", link.href);
      } catch (exception) {
        if (exception.name !== "AbortError") showError(exception.message || "合同加载失败，请检查网络");
      } finally {
        if (activeRequest === controller) {
          activeRequest = null;
          buttons.forEach((button) => { button.disabled = !form.elements.version.value; });
          countBlanks();
        }
      }
    });
    form.addEventListener("input", (event) => {
      const input = event.target.closest("[data-contract-field]");
      if (!input) return;
      paper.querySelectorAll("[data-contract-field]").forEach((other) => {
        if (other.dataset.contractField === input.dataset.contractField) {
          other.value = input.value;
          other.classList.remove("contract-blank-missing");
          if (other.tagName === "TEXTAREA") {
            other.style.height = "auto";
            other.style.height = Math.min(other.scrollHeight, 240) + "px";
          }
        }
      });
      showError("");
      countBlanks();
    });
    workspace.querySelector("[data-check-contract]").addEventListener("click", () => {
      let first = null;
      paper.querySelectorAll("[data-contract-field]").forEach((input) => {
        const missing = !input.value.trim();
        input.classList.toggle("contract-blank-missing", missing);
        if (missing && !first) first = input;
      });
      if (first) {
        showError("标记的空格尚未填写；可以补全，也可以保留为空白线下载。");
        first.focus();
      } else showError("");
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (exporting || activeRequest || !form.elements.version.value) return;
      const filled = values();
      if (Object.values(filled).some((v) => !v.trim()) &&
          !window.confirm("还有空格未填写，下载时会保留空白线。继续下载？")) return;
      const kind = event.submitter?.value || "docx";
      exporting = true;
      buttons.forEach((button) => { button.disabled = true; });
      showError("");
      try {
        const response = await fetch(form.action, {
          method: "POST", credentials: "same-origin", cache: "no-store",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": form.elements.csrf_token.value },
          body: JSON.stringify({ format: kind, version: form.elements.version.value, values: filled }),
        });
        if (response.redirected) { window.location.assign(response.url); return; }
        if (!response.ok) {
          let message = "下载失败，请重试";
          try { message = (await response.json()).error || message; } catch (_) {}
          throw new Error(message);
        }
        const blob = await response.blob();
        const objectUrl = URL.createObjectURL(blob);
        const download = document.createElement("a");
        const disposition = response.headers.get("Content-Disposition") || "";
        const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i);
        download.download = encoded ? decodeURIComponent(encoded[1]) : "合同." + kind;
        download.href = objectUrl;
        document.body.append(download);
        download.click();
        download.remove();
        setTimeout(() => URL.revokeObjectURL(objectUrl), 60000);
      } catch (exception) {
        showError(exception.message || "下载失败，请检查网络");
      } finally {
        exporting = false;
        buttons.forEach((button) => { button.disabled = false; });
      }
    });
    countBlanks();
  }

  const editor = document.querySelector("[data-template-editor]");
  if (editor) {
    const body = editor.querySelector("[data-template-body]");
    const rows = editor.querySelector("[data-contract-field-rows]");
    const types = { text: "文字", textarea: "多行文字", date: "日期",
      month: "年月", money: "金额", day: "每月日期（1—31）" };
    function insert(key) {
      body.setRangeText("{{" + key + "}}", body.selectionStart, body.selectionEnd, "end");
      body.focus();
    }
    editor.querySelector("[data-add-contract-field]").addEventListener("click", () => {
      if (rows.children.length >= 80) { window.alert("空格配置最多 80 项"); return; }
      const used = new Set([...rows.querySelectorAll('[name="field_key"]')].map((input) => input.value));
      let n = 1;
      while (used.has("field_" + n) || body.value.includes("{{field_" + n + "}}")) n++;
      const key = "field_" + n;
      const row = document.createElement("tr");
      row.dataset.fieldRow = "";
      [["field_key", key], ["field_label", "填写内容 " + n]].forEach(([name, value]) => {
        const cell = document.createElement("td");
        const input = document.createElement("input");
        input.name = name; input.value = value;
        input.setAttribute("aria-label", name === "field_key" ? "空格标识" : "空格名称");
        if (name === "field_key") input.readOnly = true;
        else { input.required = true; input.maxLength = 80; }
        cell.append(input); row.append(cell);
      });
      const typeCell = document.createElement("td");
      const select = document.createElement("select");
      select.name = "field_type"; select.setAttribute("aria-label", "空格类型");
      Object.entries(types).forEach(([value, label]) => {
        const option = document.createElement("option");
        option.value = value; option.textContent = label; select.append(option);
      });
      typeCell.append(select); row.append(typeCell);
      const actionCell = document.createElement("td");
      const actions = document.createElement("div"); actions.className = "inline-form";
      [["insertContractField", "插入"], ["removeContractField", "删除"]].forEach(([name, label]) => {
        const button = document.createElement("button");
        button.type = "button"; button.className = "button small"; button.dataset[name] = "";
        button.textContent = label; actions.append(button);
      });
      actionCell.append(actions); row.append(actionCell); rows.append(row);
      insert(key);
      row.querySelector('[name="field_label"]').focus();
    });
    rows.addEventListener("click", (event) => {
      const row = event.target.closest("[data-field-row]");
      if (!row) return;
      const key = row.querySelector('[name="field_key"]').value;
      if (event.target.closest("[data-insert-contract-field]")) insert(key);
      if (event.target.closest("[data-remove-contract-field]")) {
        const token = new RegExp("{{\\s*" + key + "\\s*}}", "g");
        if (token.test(body.value) && !window.confirm("将删除这个空格及其正文占位位置，是否继续？")) return;
        body.value = body.value.replace(token, "");
        row.remove();
      }
    });
  }
})();
