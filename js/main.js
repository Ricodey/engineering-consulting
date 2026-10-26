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

  var serviceOptions = [
    "Design Concepts",
    "PCB Design",
    "Mechanical Design",
    "Design for scaled Production",
    "First of kind Prototype",
  ];

  function fieldError(input, message) {
    var host = input.closest("label") || input.closest("fieldset");
    if (!host) return;
    var existing = null;
    var children = host.children;
    for (var i = 0; i < children.length; i++) {
      if (children[i].className === "field-error") existing = children[i];
    }
    if (!message) {
      input.removeAttribute("aria-invalid");
      if (existing) existing.remove();
      return;
    }
    var note = existing;
    if (!note) {
      note = document.createElement("span");
      note.className = "field-error";
      host.appendChild(note);
    }
    note.textContent = message;
    input.setAttribute("aria-invalid", "true");
  }

  function requestText(data) {
    var services = data.getAll("services");
    return [
      "Datum — consultation request",
      "",
      "Name: " + data.get("name"),
      "Email: " + data.get("email"),
      "Company: " + data.get("company"),
      "Services required:",
      services.length ? services.join("\n") : "—",
      "",
      "Description of the task",
      data.get("task"),
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

  function lineProblem(value, limit, emptyMessage, lengthMessage) {
    if (!value) return emptyMessage || "";
    if (/[\u0000-\u001F\u007F]/.test(value)) return "Remove unusual characters from this field.";
    if (value.length > limit) return lengthMessage;
    return "";
  }

  function multiProblem(value, emptyMessage) {
    if (!value) return emptyMessage;
    if (/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/.test(value)) return "Remove unusual characters from this field.";
    if (value.length > 4000) return "Keep this to 4000 characters.";
    return "";
  }

  function emailProblem(value) {
    if (
      value.length > 254 ||
      value.indexOf("..") !== -1 ||
      !/^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/.test(value)
    ) {
      return "Add a complete email address.";
    }
    return "";
  }

  if (form && sent && preview && copyButton && mailLink) {
    var note = form.querySelector(".form-note");
    var noteText = note ? note.textContent : "";
    var submitButton = form.querySelector("button[type='submit']");

    function showNote(message, isError) {
      if (!note) return;
      note.textContent = message;
      note.classList.toggle("is-error", Boolean(isError));
    }

    function showSent() {
      var text = requestText(new FormData(form));
      preview.textContent = text;
      var subject = "Consultation request";
      mailLink.href = "mailto:" + studioEmail + "?subject=" + encodeURIComponent(subject) + "&body=" + encodeURIComponent(text);
      form.hidden = true;
      sent.hidden = false;
      sent.scrollIntoView({ block: "nearest" });
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (form.dataset.sending === "true") return;
      var name = form.elements.name;
      var email = form.elements.email;
      var company = form.elements.company;
      var task = form.elements.task;
      var servicesField = form.querySelector(".services");
      name.value = name.value.trim();
      email.value = email.value.trim();
      company.value = company.value.trim();
      task.value = task.value.trim();

      var services = [];
      var servicesMessage = "";
      Array.prototype.forEach.call(form.querySelectorAll("input[name='services']:checked"), function (input) {
        if (serviceOptions.indexOf(input.value) === -1) {
          servicesMessage = "Choose services from the list.";
          return;
        }
        services.push(input.value);
      });
      var nameMessage = lineProblem(name.value, 120, "Add your name.", "Keep the name to 120 characters.");
      var emailMessage = emailProblem(email.value);
      var companyMessage = lineProblem(company.value, 160, "Add your company.", "Keep the company to 160 characters.");
      var taskMessage = multiProblem(task.value, "Describe the task.");

      fieldError(name, nameMessage);
      fieldError(email, emailMessage);
      fieldError(company, companyMessage);
      fieldError(servicesField, servicesMessage);
      fieldError(task, taskMessage);
      showNote(noteText, false);

      if (nameMessage || emailMessage || companyMessage || servicesMessage || taskMessage) {
        var first = form.querySelector("[aria-invalid='true']");
        if (first) first.focus();
        return;
      }

      form.dataset.sending = "true";
      submitButton.disabled = true;
      fetch("api/brief", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Accept": "application/json" },
        body: JSON.stringify({
          name: name.value,
          email: email.value,
          company: company.value,
          services: services,
          task: task.value,
          fax: form.elements.fax.value,
        }),
      }).then(function (response) {
        return response.text().then(function (text) {
          var body = {};
          if (text) {
            try {
              body = JSON.parse(text);
            } catch (error) {
              body = {};
            }
          }
          return { ok: response.ok, status: response.status, body: body };
        });
      }).then(function (result) {
        if (result.status === 422 && result.body.fields) {
          Object.keys(result.body.fields).forEach(function (key) {
            var input = key === "services" ? form.querySelector(".services") : form.elements[key];
            if (input && input.closest) fieldError(input, result.body.fields[key]);
            else showNote(result.body.fields[key], true);
          });
          var invalid = form.querySelector("[aria-invalid='true']");
          if (invalid) invalid.focus();
          return;
        }
        if (result.status === 429) {
          showNote("Too many requests from this network. Try again later.", true);
          return;
        }
        if (!result.ok) {
          showNote("The request did not send. Try again in a moment.", true);
          return;
        }
        showSent();
      }).catch(function () {
        showNote("The request did not send. Try again in a moment.", true);
      }).then(function () {
        form.dataset.sending = "false";
        submitButton.disabled = false;
      });
    });

    copyButton.addEventListener("click", function () {
      var label = copyButton.textContent;
      copyText(preview.textContent).then(function (ok) {
        copyButton.textContent = ok ? "Copied" : "Select the request above";
        window.setTimeout(function () {
          copyButton.textContent = label;
        }, 1800);
      });
    });

    if (editButton) {
      editButton.addEventListener("click", function () {
        showNote(noteText, false);
        sent.hidden = true;
        form.hidden = false;
        form.elements.name.focus();
      });
    }
  }
})();
