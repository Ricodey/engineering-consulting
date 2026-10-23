(function () {
  "use strict";

  var header = document.querySelector(".nav");
  var toggle = document.querySelector(".nav-toggle");
  var menu = document.querySelector(".nav-links");
  var year = document.getElementById("year");
  var form = document.getElementById("brief");
  var sent = document.getElementById("sent");
  var preview = document.getElementById("brief-preview");
  var copyButton = document.getElementById("copy-brief");
  var mailLink = document.getElementById("mail-brief");
  var editButton = document.getElementById("edit-brief");
  var studioEmail = "hello@datum.studio";

  if (year) year.textContent = String(new Date().getFullYear());

  function onScroll() {
    if (!header) return;
    header.classList.toggle("at-top", window.scrollY < 12);
  }

  onScroll();
  document.addEventListener("scroll", onScroll, { passive: true });

  function setMenu(open) {
    if (!menu || !toggle) return;
    menu.classList.toggle("is-open", open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    document.body.classList.toggle("nav-open", open);
  }

  if (toggle && menu) {
    toggle.addEventListener("click", function () {
      setMenu(toggle.getAttribute("aria-expanded") !== "true");
    });
    menu.addEventListener("click", function (event) {
      if (event.target.closest("a")) setMenu(false);
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") setMenu(false);
    });
    window.addEventListener("resize", function () {
      if (window.innerWidth > 860) setMenu(false);
    });
  }

  var spyLinks = Array.prototype.slice.call(document.querySelectorAll("[data-spy]"));
  var spyTargets = spyLinks
    .map(function (link) {
      return document.querySelector(link.getAttribute("href"));
    })
    .filter(Boolean);

  if (spyTargets.length && "IntersectionObserver" in window) {
    var spyObserver = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          var id = "#" + entry.target.id;
          spyLinks.forEach(function (link) {
            if (link.getAttribute("href") === id) link.setAttribute("aria-current", "true");
            else link.removeAttribute("aria-current");
          });
        });
      },
      { rootMargin: "-40% 0px -55% 0px", threshold: 0 }
    );
    spyTargets.forEach(function (section) {
      spyObserver.observe(section);
    });
  }

  function fieldError(input, message) {
    var label = input.closest("label");
    var existing = label.querySelector(".field-error");
    if (!message) {
      input.removeAttribute("aria-invalid");
      if (existing) existing.remove();
      return;
    }
    var note = existing;
    if (!note) {
      note = document.createElement("span");
      note.className = "field-error";
      label.appendChild(note);
    }
    note.textContent = message;
    input.setAttribute("aria-invalid", "true");
  }

  function briefText(data) {
    return [
      "Datum — project brief",
      "",
      "Name: " + data.get("name"),
      "Email: " + data.get("email"),
      "Company: " + (data.get("company") || "—"),
      "Horizon: " + data.get("horizon"),
      "",
      "Starting point",
      data.get("starting"),
      "",
      "What the product must do",
      data.get("product"),
    ].join("\n");
  }

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text).then(function () {
        return true;
      }).catch(function () {
        return false;
      });
    }
    return Promise.resolve(false);
  }

  if (form && sent && preview && copyButton && mailLink) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var name = form.elements.name;
      var email = form.elements.email;
      var starting = form.elements.starting;
      var product = form.elements.product;
      var valid = true;

      fieldError(name, name.value.trim() ? "" : "Add your name.");
      if (!name.value.trim()) valid = false;

      var emailValue = email.value.trim();
      var emailOk = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(emailValue);
      fieldError(email, emailOk ? "" : "Add a complete email address.");
      if (!emailOk) valid = false;

      fieldError(starting, starting.value.trim() ? "" : "Tell us the concept, or what you already have.");
      if (!starting.value.trim()) valid = false;

      fieldError(product, product.value.trim() ? "" : "Say what the product must do.");
      if (!product.value.trim()) valid = false;

      if (!valid) {
        var first = form.querySelector("[aria-invalid='true']");
        if (first) first.focus();
        return;
      }

      var text = briefText(new FormData(form));
      preview.textContent = text;
      var subject = "From concept to production";
      mailLink.href = "mailto:" + studioEmail + "?subject=" + encodeURIComponent(subject) + "&body=" + encodeURIComponent(text);
      form.hidden = true;
      sent.hidden = false;
      sent.scrollIntoView({ block: "nearest" });
    });

    copyButton.addEventListener("click", function () {
      var label = copyButton.textContent;
      copyText(preview.textContent).then(function (ok) {
        copyButton.textContent = ok ? "Copied" : "Select the brief above";
        window.setTimeout(function () {
          copyButton.textContent = label;
        }, 1800);
      });
    });

    if (editButton) {
      editButton.addEventListener("click", function () {
        sent.hidden = true;
        form.hidden = false;
        form.elements.name.focus();
      });
    }
  }
})();
