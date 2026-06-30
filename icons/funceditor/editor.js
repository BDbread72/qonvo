/* 함수 에디터 — CodeMirror 5 + QWebChannel(브리지)로 명령 시스템과 연동.
 * 자동완성/검증은 Python(ChatCommandController)에서 받아온다. */
(function () {
  "use strict";

  // JS 오류를 푸터에 표시(헤드리스 디버깅 불가 → 화면에 찍어 원인 파악).
  window.onerror = function (msg, src, line) {
    var f = document.getElementById("foot");
    if (f) {
      f.style.color = "#ff8b8b";
      f.textContent = "JS 오류: " + msg + " @" + (line || "?");
    }
    return false;
  };

  var bridge = null;
  var cm = null;
  var KEYWORDS = ["create", "connect", "run", "delete", "remove", "grep",
    "search", "find", "move", "tp", "goto", "function", "fn", "return",
    "say", "help", "clear"];
  var SUBS = ["list", "show", "set", "add", "remove", "emit", "fade", "ease",
    "run", "all"];

  // ── 명령 DSL 하이라이트(라인 기반 간이 모드) ──
  function defineMode() {
    CodeMirror.defineSimpleMode("qonvocmd", {
      start: [
        { regex: /#.*/, token: "qv-cmt" },
        { regex: /^\s*\$/, token: "qv-macro" },
        { regex: /\$\([^)]*\)/, token: "qv-var" },
        { regex: /"(?:[^\\"]|\\.)*"?/, token: "qv-str" },
        { regex: /\{[^}]*\}?/, token: "qv-tag" },
        {
          regex: new RegExp("\\b(" + KEYWORDS.join("|") + ")\\b"),
          token: "qv-cmd"
        },
        { regex: new RegExp("\\b(" + SUBS.join("|") + ")\\b"), token: "qv-sub" },
        { regex: /-?\d+\.?\d*/, token: "qv-num" }
      ],
      meta: { lineComment: "#" }
    });
  }

  // ── 자동완성: 현재 줄을 Python suggest 로 ──
  function qonvoHint(editor, callback) {
    var cur = editor.getCursor();
    var line = editor.getLine(cur.line);
    bridge.suggest(line, cur.ch, function (json) {
      var data;
      try { data = JSON.parse(json); } catch (e) { data = { from: cur.ch, to: cur.ch, items: [] }; }
      var list = (data.items || []).map(function (it) {
        return {
          text: it.text,
          displayText: it.tooltip ? (it.text + "   — " + it.tooltip) : it.text
        };
      });
      callback({
        list: list,
        from: CodeMirror.Pos(cur.line, data.from),
        to: CodeMirror.Pos(cur.line, data.to)
      });
    });
  }
  qonvoHint.async = true;

  // ── 검증(린트): 줄마다 Python validate ──
  function qonvoLint(text, updateLinting, options, editor) {
    var lines = text.split("\n");
    var anns = [];
    var pending = lines.length;
    if (!pending) { updateLinting(editor, []); return; }
    lines.forEach(function (ln, i) {
      bridge.validateLine(ln, function (err) {
        if (err) {
          anns.push({
            message: err, severity: "error",
            from: CodeMirror.Pos(i, 0),
            to: CodeMirror.Pos(i, Math.max(1, ln.length))
          });
        }
        if (--pending === 0) updateLinting(editor, anns);
      });
    });
  }

  function setStatus(msg, isErr) {
    var el = document.getElementById("status");
    el.textContent = msg || "";
    el.style.color = isErr ? "#e06c75" : "#8b929c";
  }

  function ddClose() {
    document.getElementById("ddlist").classList.remove("open");
  }

  function refreshList(selectName) {
    bridge.functionNames(function (json) {
      var names = [];
      try { names = JSON.parse(json); } catch (e) { names = []; }
      var list = document.getElementById("ddlist");
      list.innerHTML = "";
      if (!names.length) {
        var em = document.createElement("div");
        em.className = "item empty"; em.textContent = "— (없음) —";
        list.appendChild(em);
      }
      names.forEach(function (n) {
        var it = document.createElement("div");
        it.className = "item"; it.textContent = n;
        it.onclick = function () {
          document.getElementById("ddbtn").textContent = n;
          ddClose();
          openFunction(n);
        };
        list.appendChild(it);
      });
      if (selectName) document.getElementById("ddbtn").textContent = selectName;
    });
  }

  function fit() {
    if (cm) cm.refresh();
  }

  function openFunction(name) {
    if (!name) return;
    bridge.loadFunction(name, function (body) {
      document.getElementById("fnname").value = name;
      cm.setValue(body || "");
      cm.refresh();
      setStatus("'" + name + "' 열림");
    });
  }

  function saveFunction() {
    var name = document.getElementById("fnname").value.trim();
    var body = cm.getValue();
    if (!name) { setStatus("이름을 입력하세요", true); return; }
    bridge.saveFunction(name, body, function (err) {
      if (err) { setStatus(err, true); return; }
      setStatus("'" + name + "' 저장됨");
      refreshList(name);
    });
  }

  function deleteFunction() {
    var name = document.getElementById("fnname").value.trim();
    if (!name) { setStatus("삭제할 함수 이름이 없습니다", true); return; }
    bridge.deleteFunction(name, function (err) {
      if (err) { setStatus(err, true); return; }
      setStatus("'" + name + "' 삭제됨");
      document.getElementById("fnname").value = "";
      cm.setValue("");
      refreshList("");
    });
  }

  function init() {
    defineMode();
    cm = CodeMirror.fromTextArea(document.getElementById("editor"), {
      mode: "qonvocmd",
      theme: "material-darker",
      lineNumbers: true,
      autoCloseBrackets: true,
      matchBrackets: true,
      gutters: ["CodeMirror-lint-markers"],
      lint: { async: true, getAnnotations: qonvoLint },
      extraKeys: {
        "Ctrl-Space": function (c) { c.showHint({ hint: qonvoHint, completeSingle: false }); },
        "Ctrl-S": function () { saveFunction(); }
      }
    });
    // 타이핑 중 단어 문자면 자동 힌트(과하지 않게)
    cm.on("inputRead", function (c, change) {
      if (change.text && /[\w$]/.test(change.text[0])) {
        c.showHint({ hint: qonvoHint, completeSingle: false });
      }
    });

    document.getElementById("btnSave").onclick = saveFunction;
    document.getElementById("btnDelete").onclick = deleteFunction;
    document.getElementById("btnNew").onclick = function () {
      document.getElementById("fnname").value = "";
      cm.setValue("# 한 줄에 명령 하나\ncreate chat{name:\"Bot\"}\n");
      cm.focus();
      setStatus("새 함수");
    };
    // 커스텀 드롭다운 토글(네이티브 select 대체)
    document.getElementById("ddbtn").onclick = function (e) {
      e.stopPropagation();
      document.getElementById("ddlist").classList.toggle("open");
    };
    document.addEventListener("click", ddClose);   // 바깥 클릭 시 닫기

    setTimeout(fit, 100);
    window.addEventListener("resize", fit);

    refreshList("");
    // ?open=<name> 또는 window.__OPEN__ 자동 열기
    var open = window.__OPEN__ || "";
    if (!open) {
      var m = /[?&]open=([^&]+)/.exec(location.search || "");
      if (m) open = decodeURIComponent(m[1]);
    }
    if (open) openFunction(open);
    cm.focus();
  }

  // QWebChannel 연결 후 시작
  function boot() {
    if (typeof qt === "undefined" || !qt.webChannelTransport) {
      setStatus("브리지 연결 대기…", true);
      setTimeout(boot, 100);
      return;
    }
    new QWebChannel(qt.webChannelTransport, function (channel) {
      bridge = channel.objects.bridge;
      try {
        init();
      } catch (e) {
        console.log("init THREW: " + e + " @" + (e && e.stack));
        setStatus("init 오류: " + e, true);
      }
    });
  }
  boot();
})();
