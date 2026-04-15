/* D.U.H. — nav.js — Minimal interactions */

(function () {
  "use strict";

  /* ── Mobile hamburger ── */
  document.addEventListener("DOMContentLoaded", function () {
    var hamburger = document.querySelector(".nav-hamburger");
    var navLinks  = document.querySelector(".nav-links");

    if (hamburger && navLinks) {
      hamburger.addEventListener("click", function () {
        navLinks.classList.toggle("open");
        var isOpen = navLinks.classList.contains("open");
        hamburger.setAttribute("aria-expanded", isOpen);
      });

      // Close on outside click
      document.addEventListener("click", function (e) {
        if (!hamburger.contains(e.target) && !navLinks.contains(e.target)) {
          navLinks.classList.remove("open");
        }
      });
    }

    /* ── Active nav link ── */
    var currentPath = window.location.pathname.split("/").pop() || "index.html";
    var links = document.querySelectorAll(".nav-links a");
    links.forEach(function (link) {
      var href = link.getAttribute("href");
      if (href === currentPath || (currentPath === "" && href === "index.html")) {
        link.classList.add("active");
      }
    });

    /* ── Copy buttons ── */
    var copyBtns = document.querySelectorAll(".copy-btn, .install-copy");
    copyBtns.forEach(function (btn) {
      btn.addEventListener("click", function () {
        var target = btn.dataset.target
          ? document.querySelector(btn.dataset.target)
          : btn.closest(".code-block, .install-box");

        var text = "";
        if (btn.classList.contains("install-copy")) {
          var cmdEl = document.querySelector(".install-cmd");
          text = cmdEl ? cmdEl.textContent.trim() : "";
        } else if (target) {
          var pre = target.querySelector("pre");
          text = pre ? pre.textContent.trim() : "";
        }

        if (text) {
          navigator.clipboard
            .writeText(text)
            .then(function () {
              var original = btn.textContent;
              btn.textContent = "Copied!";
              setTimeout(function () {
                btn.textContent = original;
              }, 2000);
            })
            .catch(function () {
              /* clipboard blocked — silently fail */
            });
        }
      });
    });

    /* ── Tab system ── */
    var tabBtns = document.querySelectorAll(".tab-btn");
    tabBtns.forEach(function (btn) {
      btn.addEventListener("click", function () {
        var group = btn.closest("[data-tabs]");
        if (!group) return;

        group.querySelectorAll(".tab-btn").forEach(function (b) {
          b.classList.remove("active");
        });
        group.querySelectorAll(".tab-panel").forEach(function (p) {
          p.classList.remove("active");
        });

        btn.classList.add("active");
        var panelId = btn.dataset.panel;
        var panel = document.getElementById(panelId);
        if (panel) panel.classList.add("active");
      });
    });

    /* ── Smooth scroll for anchor links ── */
    var anchors = document.querySelectorAll('a[href^="#"]');
    anchors.forEach(function (a) {
      a.addEventListener("click", function (e) {
        var id = a.getAttribute("href").slice(1);
        var el = document.getElementById(id);
        if (el) {
          e.preventDefault();
          el.scrollIntoView({ behavior: "smooth" });
        }
      });
    });
  });
})();
