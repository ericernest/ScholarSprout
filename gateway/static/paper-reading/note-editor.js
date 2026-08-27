(function () {
  "use strict";

  class PaperMarkdownEditor {
    constructor({ source, normal, onChange, onModeChange }) {
      this.source = source;
      this.normal = normal;
      this.onChange = onChange || (() => {});
      this.onModeChange = onModeChange || (() => {});
      this.mode = "normal";
      this.markdown = "";
      source.addEventListener("input", () => {
        this.markdown = source.value;
        this.onChange(this.markdown);
      });
      source.addEventListener("keydown", (event) => this.handleShortcut(event));
      normal.addEventListener("input", () => this.syncFromNormal(true));
      normal.addEventListener("change", () => this.syncFromNormal(true));
      normal.addEventListener("keydown", (event) => this.handleShortcut(event));
      normal.addEventListener("dblclick", (event) => {
        const formula = event.target.closest("[data-paper-latex]");
        if (formula) this.editFormulaSource(formula);
      });
    }

    setMarkdown(markdown) {
      this.markdown = String(markdown || "");
      this.source.value = this.markdown;
      this.renderNormal();
    }

    getMarkdown() {
      if (this.mode === "normal") this.syncFromNormal(false);
      return this.markdown;
    }

    setMode(mode) {
      if (!['normal', 'source'].includes(mode)) return;
      if (mode === this.mode) return;
      if (this.mode === "normal") this.syncFromNormal(false);
      else this.markdown = this.source.value;
      this.mode = mode;
      if (mode === "normal") this.renderNormal();
      else this.source.value = this.markdown;
      this.normal.hidden = mode !== "normal";
      this.source.hidden = mode !== "source";
      this.onModeChange(mode);
      window.setTimeout(() => (mode === "normal" ? this.normal : this.source).focus(), 0);
    }

    focus() {
      (this.mode === "normal" ? this.normal : this.source).focus();
    }

    apply(action) {
      if (this.mode === "source") this.applySourceAction(action);
      else this.applyNormalAction(action);
    }

    handleShortcut(event) {
      if (!(event.ctrlKey || event.metaKey)) return false;
      let action = "";
      if (/^[0-6]$/.test(event.key) && !event.altKey && !event.shiftKey) {
        action = event.key === "0" ? "paragraph" : `heading${event.key}`;
      } else if (event.key.toLowerCase() === "b") action = "bold";
      else if (event.key.toLowerCase() === "i") action = "italic";
      else if (event.key.toLowerCase() === "k") action = "link";
      else if (event.shiftKey && event.code === "Digit8") action = "bullet";
      else if (event.shiftKey && event.code === "Digit7") action = "ordered";
      if (!action) return false;
      event.preventDefault();
      this.apply(action);
      return true;
    }

    applySourceAction(action) {
      const input = this.source;
      const start = input.selectionStart;
      const end = input.selectionEnd;
      const selected = input.value.slice(start, end);
      const inline = {
        bold: ["**", "**", "加粗文字"],
        italic: ["*", "*", "斜体文字"],
        code: ["`", "`", "代码"],
        link: ["[", "](https://)", "链接文字"],
        inline_math: ["$", "$", "E = mc^2"],
        block_math: ["$$\n", "\n$$", "\\int_a^b f(x)\\,dx"],
      }[action];
      if (inline) {
        const content = selected || inline[2];
        input.setRangeText(`${inline[0]}${content}${inline[1]}`, start, end, "end");
        input.setSelectionRange(start + inline[0].length, start + inline[0].length + content.length);
      } else if (action === "table") {
        const table = "| 列 1 | 列 2 | 列 3 |\n| --- | --- | --- |\n| 内容 | 内容 | 内容 |";
        input.setRangeText(table, start, end, "end");
      } else {
        const lineStart = input.value.lastIndexOf("\n", Math.max(0, start - 1)) + 1;
        const nextLine = input.value.indexOf("\n", end);
        const lineEnd = nextLine === -1 ? input.value.length : nextLine;
        const block = input.value.slice(lineStart, lineEnd);
        if (action === "paragraph" || action.startsWith("heading")) {
          const level = action === "paragraph" ? 0 : Number(action.slice(-1));
          const formatted = block.split("\n").map((line) => {
            const clean = line.replace(/^#{1,6}\s+/, "");
            return level ? `${"#".repeat(level)} ${clean}` : clean;
          }).join("\n");
          input.setRangeText(formatted, lineStart, lineEnd, "select");
        } else {
          const prefixes = { quote: "> ", bullet: "- ", ordered: "1. ", task: "- [ ] " };
          const prefix = prefixes[action];
          if (!prefix) return;
          const formatted = block.split("\n").map((line, index) => action === "ordered" ? `${index + 1}. ${line}` : `${prefix}${line}`).join("\n");
          input.setRangeText(formatted, lineStart, lineEnd, "select");
        }
      }
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
    }

    applyNormalAction(action) {
      this.ensureNormalSelection();
      if (action === "bold" || action === "italic") {
        document.execCommand(action, false);
      } else if (action === "bullet" || action === "ordered") {
        document.execCommand(action === "bullet" ? "insertUnorderedList" : "insertOrderedList", false);
      } else if (action === "paragraph" || action.startsWith("heading")) {
        const tag = action === "paragraph" ? "P" : `H${Number(action.slice(-1))}`;
        document.execCommand("formatBlock", false, tag);
      } else if (action === "quote") {
        document.execCommand("formatBlock", false, "BLOCKQUOTE");
      } else if (action === "code") {
        this.wrapNormalSelection("code", "代码");
      } else if (action === "link") {
        const anchor = document.createElement("a");
        anchor.href = "https://";
        anchor.textContent = this.selectedNormalText() || "链接文字";
        this.replaceNormalSelection(anchor);
      } else if (action === "task") {
        const list = document.createElement("ul");
        const item = document.createElement("li");
        item.dataset.task = "true";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.contentEditable = "false";
        item.append(checkbox, document.createTextNode(this.selectedNormalText() || "待办事项"));
        list.append(item);
        this.replaceNormalSelection(list, true);
      } else if (action === "table") {
        this.insertNormalTable();
      } else if (action === "inline_math" || action === "block_math") {
        const displayMode = action === "block_math";
        const formula = this.createFormula(this.selectedNormalText() || (displayMode ? "\\int_a^b f(x)\\,dx" : "E = mc^2"), displayMode);
        this.replaceNormalSelection(formula, displayMode);
      } else {
        return;
      }
      this.syncFromNormal(true);
      this.normal.focus();
    }

    ensureNormalSelection() {
      const selection = window.getSelection();
      if (selection?.rangeCount && this.normal.contains(selection.getRangeAt(0).commonAncestorContainer)) return;
      this.normal.focus();
      const range = document.createRange();
      range.selectNodeContents(this.normal);
      range.collapse(false);
      selection.removeAllRanges();
      selection.addRange(range);
    }

    selectedNormalText() {
      const selection = window.getSelection();
      if (!selection?.rangeCount) return "";
      const range = selection.getRangeAt(0);
      return this.normal.contains(range.commonAncestorContainer) ? selection.toString() : "";
    }

    replaceNormalSelection(node, block = false) {
      const selection = window.getSelection();
      if (!selection?.rangeCount) return;
      const range = selection.getRangeAt(0);
      if (block) {
        range.deleteContents();
        let anchor = range.startContainer.nodeType === Node.ELEMENT_NODE
          ? range.startContainer
          : range.startContainer.parentElement;
        while (anchor?.parentElement && anchor.parentElement !== this.normal) {
          anchor = anchor.parentElement;
        }
        const paragraph = document.createElement("p");
        paragraph.append(document.createElement("br"));
        if (anchor === this.normal || !anchor) this.normal.append(node, paragraph);
        else anchor.after(node, paragraph);
        const nextRange = document.createRange();
        nextRange.selectNodeContents(paragraph);
        nextRange.collapse(true);
        selection.removeAllRanges();
        selection.addRange(nextRange);
        return;
      }
      range.deleteContents();
      range.insertNode(node);
      range.setStartAfter(node);
      range.collapse(true);
      selection.removeAllRanges();
      selection.addRange(range);
    }

    wrapNormalSelection(tag, fallback) {
      const selection = window.getSelection();
      const range = selection.getRangeAt(0);
      const node = document.createElement(tag);
      if (range.collapsed) node.textContent = fallback;
      else node.append(range.extractContents());
      range.insertNode(node);
      range.selectNodeContents(node);
      selection.removeAllRanges();
      selection.addRange(range);
    }

    insertNormalTable() {
      const wrapper = document.createElement("div");
      wrapper.className = "paper-note-table-wrap";
      const table = document.createElement("table");
      const thead = document.createElement("thead");
      const tbody = document.createElement("tbody");
      const header = document.createElement("tr");
      for (let column = 1; column <= 3; column += 1) {
        const cell = document.createElement("th");
        cell.textContent = `列 ${column}`;
        header.append(cell);
      }
      thead.append(header);
      for (let rowIndex = 0; rowIndex < 2; rowIndex += 1) {
        const row = document.createElement("tr");
        for (let column = 0; column < 3; column += 1) {
          const cell = document.createElement("td");
          cell.textContent = "内容";
          row.append(cell);
        }
        tbody.append(row);
      }
      table.append(thead, tbody);
      wrapper.append(table);
      this.replaceNormalSelection(wrapper, true);
    }

    renderNormal() {
      this.normal.replaceChildren(...this.parseMarkdown(this.markdown).childNodes);
    }

    parseMarkdown(markdown) {
      const root = document.createElement("div");
      const lines = String(markdown || "").replace(/\r\n?/g, "\n").split("\n");
      let paragraph = [];
      let list = null;
      let listType = "";
      let code = null;
      const flushParagraph = () => {
        if (!paragraph.length) return;
        const node = document.createElement("p");
        this.appendInline(node, paragraph.join(" ").trim());
        root.append(node);
        paragraph = [];
      };
      const flushList = () => {
        if (list) root.append(list);
        list = null;
        listType = "";
      };
      const flushCode = () => {
        if (!code) return;
        const pre = document.createElement("pre");
        const codeNode = document.createElement("code");
        codeNode.textContent = code.lines.join("\n");
        pre.dataset.language = code.language;
        pre.append(codeNode);
        root.append(pre);
        code = null;
      };
      for (let index = 0; index < lines.length; index += 1) {
        const raw = lines[index];
        const line = raw.trimEnd();
        const fence = line.trim().match(/^```(.*)$/);
        if (fence) {
          flushParagraph();
          flushList();
          if (code) flushCode();
          else code = { language: fence[1].trim(), lines: [] };
          continue;
        }
        if (code) {
          code.lines.push(raw);
          continue;
        }
        if (line.trim().startsWith("$$")) {
          flushParagraph();
          flushList();
          let latex = line.trim().slice(2);
          if (latex.endsWith("$$") && latex.length > 2) latex = latex.slice(0, -2);
          else {
            const chunks = [latex];
            while (index + 1 < lines.length) {
              index += 1;
              if (lines[index].trim() === "$$") break;
              chunks.push(lines[index]);
            }
            latex = chunks.join("\n");
          }
          root.append(this.createFormula(latex.trim(), true));
          continue;
        }
        if (!line.trim()) {
          flushParagraph();
          flushList();
          continue;
        }
        const nextLine = lines[index + 1] || "";
        if (line.includes("|") && this.isTableDivider(nextLine)) {
          flushParagraph();
          flushList();
          const headers = this.splitTableRow(line);
          const wrapper = document.createElement("div");
          wrapper.className = "paper-note-table-wrap";
          const table = document.createElement("table");
          const thead = document.createElement("thead");
          const headerRow = document.createElement("tr");
          headers.forEach((value) => {
            const cell = document.createElement("th");
            this.appendInline(cell, value);
            headerRow.append(cell);
          });
          thead.append(headerRow);
          const tbody = document.createElement("tbody");
          index += 2;
          while (index < lines.length && lines[index].trim() && lines[index].includes("|")) {
            const row = document.createElement("tr");
            const values = this.splitTableRow(lines[index]);
            headers.forEach((_, cellIndex) => {
              const cell = document.createElement("td");
              this.appendInline(cell, values[cellIndex] || "");
              row.append(cell);
            });
            tbody.append(row);
            index += 1;
          }
          index -= 1;
          table.append(thead, tbody);
          wrapper.append(table);
          root.append(wrapper);
          continue;
        }
        if (/^\s*(?:\*{3,}|-{3,}|_{3,})\s*$/.test(line)) {
          flushParagraph();
          flushList();
          root.append(document.createElement("hr"));
          continue;
        }
        const heading = line.match(/^\s*(#{1,6})\s+(.+)$/);
        if (heading) {
          flushParagraph();
          flushList();
          const node = document.createElement(`h${heading[1].length}`);
          this.appendInline(node, heading[2]);
          root.append(node);
          continue;
        }
        const quote = line.match(/^\s*>\s?(.*)$/);
        if (quote) {
          flushParagraph();
          flushList();
          const node = document.createElement("blockquote");
          this.appendInline(node, quote[1]);
          root.append(node);
          continue;
        }
        const bullet = line.match(/^\s*[-+*]\s+(.+)$/);
        const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
        if (bullet || ordered) {
          flushParagraph();
          const nextType = ordered ? "ol" : "ul";
          if (!list || listType !== nextType) {
            flushList();
            list = document.createElement(nextType);
            listType = nextType;
          }
          const item = document.createElement("li");
          let content = (bullet || ordered)[1];
          const task = content.match(/^\[([ xX])\]\s+(.*)$/);
          if (task) {
            item.dataset.task = "true";
            const checkbox = document.createElement("input");
            checkbox.type = "checkbox";
            checkbox.checked = task[1].toLowerCase() === "x";
            checkbox.contentEditable = "false";
            item.append(checkbox);
            content = task[2];
          }
          this.appendInline(item, content);
          list.append(item);
          continue;
        }
        paragraph.push(line.trim());
      }
      flushParagraph();
      flushList();
      flushCode();
      return root;
    }

    appendInline(target, text) {
      const value = String(text || "");
      const pattern = /(\$[^$\n]+\$|\[[^\]\n]+\]\([^\s)]+\)|`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*|_[^_\n]+_)/g;
      let cursor = 0;
      for (const match of value.matchAll(pattern)) {
        if (match.index > cursor) target.append(document.createTextNode(value.slice(cursor, match.index)));
        const token = match[0];
        if (token.startsWith("$")) {
          target.append(this.createFormula(token.slice(1, -1), false));
        } else {
          const link = token.match(/^\[([^\]]+)\]\(([^\s)]+)\)$/);
          if (link) {
            const anchor = document.createElement("a");
            anchor.textContent = link[1];
            anchor.href = this.safeHref(link[2]);
            target.append(anchor);
          } else if (token.startsWith("`")) {
            const node = document.createElement("code");
            node.textContent = token.slice(1, -1);
            target.append(node);
          } else if (token.startsWith("**") || token.startsWith("__")) {
            const node = document.createElement("strong");
            node.textContent = token.slice(2, -2);
            target.append(node);
          } else {
            const node = document.createElement("em");
            node.textContent = token.slice(1, -1);
            target.append(node);
          }
        }
        cursor = match.index + token.length;
      }
      if (cursor < value.length) target.append(document.createTextNode(value.slice(cursor)));
    }

    createFormula(latex, displayMode) {
      const node = document.createElement(displayMode ? "div" : "span");
      node.className = displayMode ? "paper-note-math-block" : "paper-note-math-inline";
      node.dataset.paperLatex = latex;
      node.dataset.displayMode = String(displayMode);
      node.contentEditable = "false";
      node.title = "双击切换到源码编辑公式";
      if (window.katex?.render) {
        node.setAttribute("aria-label", latex);
        window.katex.render(latex, node, { displayMode, throwOnError: false, strict: "ignore", trust: false, output: "html" });
      } else {
        node.textContent = displayMode ? `$$${latex}$$` : `$${latex}$`;
      }
      return node;
    }

    editFormulaSource(formula) {
      const latex = formula.dataset.paperLatex || "";
      const displayMode = formula.dataset.displayMode === "true";
      this.setMode("source");
      const token = displayMode ? `$$\n${latex}\n$$` : `$${latex}$`;
      const index = this.source.value.indexOf(token);
      if (index >= 0) this.source.setSelectionRange(index + (displayMode ? 3 : 1), index + token.length - (displayMode ? 3 : 1));
    }

    syncFromNormal(notify) {
      this.markdown = Array.from(this.normal.childNodes)
        .map((node) => this.blockToMarkdown(node))
        .filter((value) => value !== "")
        .join("\n\n")
        .replace(/\n{3,}/g, "\n\n")
        .trim();
      this.source.value = this.markdown;
      if (notify) this.onChange(this.markdown);
    }

    blockToMarkdown(node) {
      if (node.nodeType === Node.TEXT_NODE) return node.textContent.trim();
      if (node.nodeType !== Node.ELEMENT_NODE) return "";
      if (node.matches(".paper-note-math-block")) return `$$\n${node.dataset.paperLatex || ""}\n$$`;
      if (/^H[1-6]$/.test(node.tagName)) return `${"#".repeat(Number(node.tagName[1]))} ${this.inlineToMarkdown(node)}`;
      if (node.tagName === "P") return this.inlineToMarkdown(node);
      if (node.tagName === "BLOCKQUOTE") return this.inlineToMarkdown(node).split("\n").map((line) => `> ${line}`).join("\n");
      if (node.tagName === "PRE") return `\`\`\`${node.dataset.language || ""}\n${node.textContent || ""}\n\`\`\``;
      if (node.tagName === "HR") return "---";
      if (node.tagName === "UL" || node.tagName === "OL") {
        return Array.from(node.children).map((item, index) => {
          const prefix = node.tagName === "OL" ? `${index + 1}. ` : "- ";
          const task = item.dataset.task === "true" ? `[${item.querySelector('input[type="checkbox"]')?.checked ? "x" : " "}] ` : "";
          return `${prefix}${task}${this.inlineToMarkdown(item)}`;
        }).join("\n");
      }
      if (node.matches(".paper-note-table-wrap") || node.tagName === "TABLE") return this.tableToMarkdown(node.querySelector("table") || node);
      if (node.tagName === "DIV") return this.inlineToMarkdown(node);
      return this.inlineToMarkdown(node);
    }

    inlineToMarkdown(node) {
      return Array.from(node.childNodes).map((child) => {
        if (child.nodeType === Node.TEXT_NODE) return child.textContent || "";
        if (child.nodeType !== Node.ELEMENT_NODE) return "";
        if (child.matches("[data-paper-latex]")) {
          const latex = child.dataset.paperLatex || "";
          return child.dataset.displayMode === "true" ? `$$\n${latex}\n$$` : `$${latex}$`;
        }
        if (child.matches('input[type="checkbox"]')) return "";
        if (child.tagName === "STRONG" || child.tagName === "B") return `**${this.inlineToMarkdown(child)}**`;
        if (child.tagName === "EM" || child.tagName === "I") return `*${this.inlineToMarkdown(child)}*`;
        if (child.tagName === "CODE") return `\`${child.textContent || ""}\``;
        if (child.tagName === "A") return `[${this.inlineToMarkdown(child)}](${child.getAttribute("href") || ""})`;
        if (child.tagName === "BR") return "  \n";
        return this.inlineToMarkdown(child);
      }).join("");
    }

    tableToMarkdown(table) {
      const rows = Array.from(table.rows);
      if (!rows.length) return "";
      const values = rows.map((row) => Array.from(row.cells).map((cell) => this.inlineToMarkdown(cell).replace(/\|/g, "\\|")));
      const width = Math.max(...values.map((row) => row.length));
      const header = values[0].concat(Array(Math.max(0, width - values[0].length)).fill(""));
      const divider = Array(width).fill("---");
      return [header, divider, ...values.slice(1)].map((row) => `| ${row.concat(Array(Math.max(0, width - row.length)).fill("")).join(" | ")} |`).join("\n");
    }

    splitTableRow(line) {
      return String(line || "").trim().replace(/^\||\|$/g, "").split(/(?<!\\)\|/).map((cell) => cell.replace(/\\\|/g, "|").trim());
    }

    isTableDivider(line) {
      const cells = this.splitTableRow(line);
      return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
    }

    safeHref(raw) {
      try {
        const url = new URL(raw, window.location.origin);
        return ["http:", "https:", "mailto:"].includes(url.protocol) ? url.href : "";
      } catch {
        return "";
      }
    }
  }

  function renderPaperMarkdown(markdown, className = "paper-note-rendered") {
    const renderer = Object.create(PaperMarkdownEditor.prototype);
    const root = renderer.parseMarkdown(markdown);
    root.className = className;
    return root;
  }

  window.PaperMarkdownEditor = PaperMarkdownEditor;
  window.renderPaperMarkdown = renderPaperMarkdown;
})();
