/* SayItErmano landing — progressive enhancement only.
   The page is fully readable with this script absent or disabled:
   - reveal states are gated behind the .js class this script adds,
   - copy buttons degrade to selecting the command text. */
(function () {
  "use strict";

  var doc = document.documentElement;
  doc.classList.add("js");

  /* -- scroll reveals (IntersectionObserver) -------------------------- */
  var revealEls = Array.prototype.slice.call(
    document.querySelectorAll("[data-reveal]")
  );

  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  if (reduced || !("IntersectionObserver" in window)) {
    revealEls.forEach(function (el) { el.classList.add("in"); });
  } else {
    var io = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add("in");
            io.unobserve(entry.target);
          }
        });
      },
      { rootMargin: "0px 0px -8% 0px", threshold: 0.08 }
    );
    revealEls.forEach(function (el) { io.observe(el); });
  }

  /* -- copy buttons ---------------------------------------------------- */
  function flash(btn, label) {
    var prev = btn.textContent;
    btn.textContent = label;
    var prevAria = btn.getAttribute("aria-label");
    btn.setAttribute("aria-label", "copied");
    window.setTimeout(function () {
      btn.textContent = prev;
      if (prevAria) btn.setAttribute("aria-label", prevAria);
      else btn.removeAttribute("aria-label");
    }, 1400);
  }

  function selectText(el) {
    var range = document.createRange();
    range.selectNodeContents(el);
    var sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  }

  Array.prototype.forEach.call(
    document.querySelectorAll("[data-copy]"),
    function (btn) {
      btn.addEventListener("click", function () {
        var text = btn.getAttribute("data-copy");
        var done = function () { flash(btn, "copied"); };
        var degrade = function () {
          var code = btn.previousElementSibling;
          if (code && code.tagName === "CODE") selectText(code);
          flash(btn, "selected");
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(done, degrade);
        } else {
          degrade();
        }
      });
    }
  );
})();
