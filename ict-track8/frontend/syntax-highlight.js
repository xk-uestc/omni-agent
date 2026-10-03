(function () {
  const sqlKeywords = new Set(("select from where group by order having limit offset as distinct all union intersect except join inner left right full outer cross on using with recursive insert into values update set delete create alter drop table view index primary key foreign references constraint null not is true false case when then else end and or in exists between like ilike asc desc nulls first last over partition rows range current row preceding following unbounded cast coalesce returning explain replace conflict do default check unique autoincrement integer text real numeric date timestamp boolean varchar char" ).split(/\s+/));
  const pythonKeywords = new Set(("and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield True False None" ).split(/\s+/));
  const jsKeywords = new Set(("async await break case catch class const continue debugger default delete do else export extends finally for from function get if import in instanceof let new of return set static super switch this throw try typeof var void while with yield true false null undefined interface type enum implements public private protected readonly" ).split(/\s+/));
  const shellKeywords = new Set(("if then else elif fi for while do done case esac function in export local readonly return exit set unset source cd pwd echo printf test" ).split(/\s+/));
  const sqlFunctions = new Set(("count sum avg min max coalesce nullif round abs lower upper trim substr substring length date datetime strftime cast replace printf json_extract group_concat" ).split(/\s+/));

  function pushToken(tokens, text, kind) {
    if (text) tokens.push({ text, kind });
  }

  function tokenizeSql(source) {
    const tokens = [];
    const pattern = /--[^\r\n]*|\/\*[\s\S]*?\*\/|'(?:''|[^'])*'|"(?:""|[^"])*"|`[^`]*`|:[A-Za-z_][\w$]*|@[A-Za-z_][\w$]*|\?\d*|\$\d+|\b[A-Za-z_][\w$]*\b|\d+(?:\.\d+)?|<=|>=|<>|!=|==|[-+*/%=<>!]+|[()[\]{}.,;:]/gi;
    let cursor = 0;
    for (const match of source.matchAll(pattern)) {
      const index = match.index;
      pushToken(tokens, source.slice(cursor, index), "plain");
      const value = match[0];
      const lower = value.toLowerCase();
      let kind = "identifier";
      if (value.startsWith("--") || value.startsWith("/*")) kind = "comment";
      else if (["'", '"', "`"].includes(value[0])) kind = "string";
      else if ([":", "@", "?", "$"].includes(value[0])) kind = "parameter";
      else if (/^\d/.test(value)) kind = "number";
      else if (/^[()[\]{}.,;:]$/.test(value)) kind = "punctuation";
      else if (/^[-+*/%=<>!]+$/.test(value)) kind = "operator";
      else if (["true", "false", "null"].includes(lower)) kind = "literal";
      else if (sqlFunctions.has(lower) && /^\s*\(/.test(source.slice(index + value.length))) kind = "function";
      else if (sqlKeywords.has(lower)) kind = "keyword";
      pushToken(tokens, value, kind);
      cursor = index + value.length;
    }
    pushToken(tokens, source.slice(cursor), "plain");
    return tokens;
  }

  function tokenizeJson(source) {
    const tokens = [];
    const pattern = /"(?:\\.|[^"\\])*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\b(?:true|false|null)\b|[{}\[\],:]/g;
    let cursor = 0;
    for (const match of source.matchAll(pattern)) {
      const index = match.index;
      pushToken(tokens, source.slice(cursor, index), "plain");
      const value = match[0];
      const remainder = source.slice(index + value.length);
      let kind = "punctuation";
      if (value[0] === '"') kind = /^\s*:/.test(remainder) ? "property" : "string";
      else if (/^-?\d/.test(value)) kind = "number";
      else if (value === "true" || value === "false") kind = "boolean";
      else if (value === "null") kind = "literal";
      pushToken(tokens, value, kind);
      cursor = index + value.length;
    }
    pushToken(tokens, source.slice(cursor), "plain");
    return tokens;
  }

  function tokenizeGeneral(source, language) {
    const tokens = [];
    const pattern = language === "python"
      ? /#[^\r\n]*|'''[\s\S]*?'''|"""[\s\S]*?"""|'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|@[A-Za-z_]\w*|\b[A-Za-z_]\w*\b|\b\d+(?:\.\d+)?\b|==|!=|<=|>=|:=|->|[-+*/%=<>!&|^~]+|[()[\]{}.,;:@]/g
      : language === "bash" || language === "powershell"
        ? /#[^\r\n]*|'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|\$\{?[A-Za-z_][\w]*\}?|\$[A-Za-z_]\w*|\b[A-Za-z_$][\w$]*\b|\b\d+(?:\.\d+)?\b|===|!==|=>|==|!=|<=|>=|:=|->|\?\?|&&|\|\||>>|<<|[-+*/%=<>!&|^~]+|[()[\]{}.,;:@]/g
        : /\/\/[^\r\n]*|\/\*[\s\S]*?\*\/|`(?:\\.|[^`\\])*`|'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|\$[A-Za-z_]\w*|\b[A-Za-z_$][\w$]*\b|\b\d+(?:\.\d+)?\b|===|!==|=>|==|!=|<=|>=|:=|->|\?\?|&&|\|\||>>|<<|[-+*/%=<>!&|^~]+|[()[\]{}.,;:@]/g;
    const keywords = language === "python" ? pythonKeywords : language === "bash" || language === "powershell" ? shellKeywords : jsKeywords;
    let cursor = 0;
    for (const match of source.matchAll(pattern)) {
      const index = match.index;
      pushToken(tokens, source.slice(cursor, index), "plain");
      const value = match[0];
      const next = source.slice(index + value.length);
      let kind = "identifier";
      if (value.startsWith("#") || value.startsWith("//") || value.startsWith("/*")) kind = "comment";
      else if (["'", '"', "`"].includes(value[0])) kind = "string";
      else if (value[0] === "$" || value[0] === "@") kind = "parameter";
      else if (/^\d/.test(value)) kind = "number";
      else if (/^[()[\]{}.,;:@]$/.test(value)) kind = "punctuation";
      else if (/^[-+*/%=<>!&|^~]+$/.test(value) || ["=>", "===", "!==", "==", "!=", "<=", ">=", "&&", "||", "??", "->", ":="].includes(value)) kind = "operator";
      else if (keywords.has(value)) kind = "keyword";
      else if (/^\s*\(/.test(next)) kind = "function";
      pushToken(tokens, value, kind);
      cursor = index + value.length;
    }
    pushToken(tokens, source.slice(cursor), "plain");
    return tokens;
  }

  function isJson(source) {
    const trimmed = source.trim();
    if (!trimmed || !["{", "["].includes(trimmed[0])) return false;
    try { JSON.parse(trimmed); return true; } catch { return false; }
  }

  function detectLanguage(pre, source) {
    const declared = (pre.dataset.language || "").toLowerCase();
    if (["sql", "json", "python", "javascript", "js", "typescript", "ts", "bash", "shell", "powershell"].includes(declared)) return declared;
    if (pre.classList.contains("sqlbox")) return "sql";
    if (pre.classList.contains("trace-json") || isJson(source)) return "json";
    if (/^\s*(?:#!.*\b(?:ba)?sh\b|(?:\$\s*)?(?:curl|git|python(?:3)?|pip|npm|node|ssh|docker)\s)/m.test(source)) return "bash";
    if (/^\s*(?:from\s+[\w.]+\s+import|import\s+[\w.]+|(?:async\s+)?def\s+\w+\s*\(|class\s+\w+\s*[:(]|print\s*\()/m.test(source)) return "python";
    if (/^\s*(?:import\s+.+\s+from\s+|export\s+(?:default|const|function|class)|(?:const|let|var)\s+\w+\s*=|function\s+\w+\s*\(|async\s+function\s+|interface\s+\w+\s*\{|type\s+\w+\s*=)/m.test(source)) return "javascript";
    if (/^\s*(?:with\s+\w+\s+as\s*\(|select\b|insert\s+into\b|update\s+\w+\s+set\b|delete\s+from\b|create\s+(?:table|view)\b)/im.test(source)) return "sql";
    if (/^\s*(?:Get-[A-Za-z]+|\$[A-Za-z_]\w*\s*=|Write-Host\b)/m.test(source)) return "powershell";
    return null;
  }

  function tokenize(source, language) {
    if (language === "sql") return tokenizeSql(source);
    if (language === "json") return tokenizeJson(source);
    if (language === "js" || language === "ts" || language === "typescript") language = "javascript";
    if (language === "shell") language = "bash";
    return tokenizeGeneral(source, language);
  }

  const api = { detectLanguage, tokenize };
  if (typeof window !== "undefined") window.LatticeSyntaxHighlight = api;
  if (typeof document === "undefined") return;
  const highlightedSources = new WeakMap();

  function appendTokens(parent, source, language) {
    const fragment = document.createDocumentFragment();
    for (const token of tokenize(source, language)) {
      if (token.kind === "plain") fragment.append(document.createTextNode(token.text));
      else {
        const span = document.createElement("span");
        span.className = `tok-${token.kind}`;
        span.textContent = token.text;
        fragment.append(span);
      }
    }
    parent.append(fragment);
  }

  function highlight(pre) {
    if (pre.dataset.queryVisual === "true") return;
    const source = pre.textContent || "";
    const previousSource = highlightedSources.get(pre);
    if (previousSource === source) return;
    const language = detectLanguage(pre, source);
    if (!language) {
      if (previousSource !== undefined) {
        pre.replaceChildren(document.createTextNode(source));
        highlightedSources.delete(pre);
        delete pre.dataset.language;
        pre.classList.remove("syntax-highlighted");
      }
      return;
    }

    pre.dataset.language = language;
    highlightedSources.set(pre, source);
    pre.classList.add("syntax-highlighted");
    const fragment = document.createDocumentFragment();
    const marker = source.match(/\n(--\s*参数\s+)(?=[{[])/);
    if (language === "sql" && marker) {
      const index = marker.index;
      appendTokens(fragment, source.slice(0, index), "sql");
      const label = document.createElement("span");
      label.className = "tok-comment";
      label.textContent = marker[1].trimEnd();
      fragment.append(document.createTextNode("\n"), label, document.createTextNode(marker[1].slice(label.textContent.length)));
      appendTokens(fragment, source.slice(index + marker[0].length), "json");
    } else appendTokens(fragment, source, language);
    pre.replaceChildren(fragment);
  }

  function scan(node) {
    if (!(node instanceof Element)) return;
    if (node.matches("pre")) highlight(node);
    node.querySelectorAll("pre").forEach(highlight);
  }

  scan(document.documentElement);
  new MutationObserver(records => {
    for (const record of records) {
      if (record.target instanceof Element && record.target.matches("pre")) highlight(record.target);
      record.addedNodes.forEach(scan);
    }
  }).observe(document.documentElement, { childList: true, subtree: true });
})();
