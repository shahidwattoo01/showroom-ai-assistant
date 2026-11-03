/* Embeddable chat widget. Put this on any website:
   <script src="https://YOUR-SERVER/static/widget.js" data-name="Demo Motors"></script>
   It opens the full chat (/) in a floating panel. */
(function () {
  var script = document.currentScript;
  var base = new URL(script.src).origin;
  var name = script.getAttribute("data-name") || "Chat";
  var color = script.getAttribute("data-color") || "#22c55e";

  var btn = document.createElement("div");
  btn.innerHTML = "💬";
  btn.title = "Chat with " + name;
  Object.assign(btn.style, {
    position: "fixed", bottom: "20px", right: "20px", width: "60px", height: "60px",
    borderRadius: "50%", background: color, color: "#06240f", fontSize: "28px",
    display: "grid", placeItems: "center", cursor: "pointer", zIndex: 2147483647,
    boxShadow: "0 8px 24px rgba(0,0,0,.3)"
  });

  var frame = document.createElement("iframe");
  frame.src = base + "/";
  Object.assign(frame.style, {
    position: "fixed", bottom: "90px", right: "20px", width: "380px", height: "600px",
    maxWidth: "calc(100vw - 40px)", maxHeight: "calc(100vh - 120px)", border: "none",
    borderRadius: "16px", boxShadow: "0 12px 40px rgba(0,0,0,.4)", zIndex: 2147483646, display: "none"
  });

  var open = false;
  btn.onclick = function () { open = !open; frame.style.display = open ? "block" : "none"; btn.innerHTML = open ? "✕" : "💬"; };
  document.body.appendChild(frame);
  document.body.appendChild(btn);
})();
