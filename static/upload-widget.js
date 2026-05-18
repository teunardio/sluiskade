/*
 * Upload widget: client-side preview, validatie en XHR-upload met
 * progress events.  Verbeterd op zowel /portaal/upload als /sluis/upload.
 *
 * Werkt op elk <form class="upload-widget"> met data-attributes:
 *   data-max-files       - integer (default 10)
 *   data-max-file-bytes  - integer (per-file limit)
 *   data-max-total-bytes - integer (total request limit)
 *
 * Verwacht de volgende child-elementen (selectors met data-*):
 *   input[type="file"][name="photos"]   - de file picker
 *   [data-upload-list]                  - container voor de previews
 *   [data-upload-error]                 - error-message box
 *   [data-upload-progress]              - wrap-container voor progress
 *   [data-upload-progress-bar]          - de balk zelf (gets width %)
 *   [data-upload-progress-label]        - tekstuele percentage
 *   button[type="submit"]               - de submit-knop
 *   [data-upload-submit-text]           - de label van de knop
 *
 * Bij no-JS valt 't terug op een normale form submit.
 */
(function () {
  document.querySelectorAll("form.upload-widget").forEach(initUploadForm);

  function initUploadForm(form) {
    const input = form.querySelector('input[type="file"][name="photos"]');
    if (!input) return;

    const listEl = form.querySelector("[data-upload-list]");
    const errorEl = form.querySelector("[data-upload-error]");
    const progressEl = form.querySelector("[data-upload-progress]");
    const progressBar = form.querySelector("[data-upload-progress-bar]");
    const progressLabel = form.querySelector("[data-upload-progress-label]");
    const submitBtn = form.querySelector('button[type="submit"]');
    const submitText = form.querySelector("[data-upload-submit-text]");
    if (submitText && !submitText.dataset.original) {
      submitText.dataset.original = submitText.textContent;
    }

    const maxFiles = parseInt(form.dataset.maxFiles || "10", 10);
    const maxFileBytes = parseInt(form.dataset.maxFileBytes || "0", 10);
    const maxTotalBytes = parseInt(form.dataset.maxTotalBytes || "0", 10);

    let selectedFiles = [];

    input.addEventListener("change", () => {
      selectedFiles = Array.from(input.files || []);
      renderList();
      validate();
    });

    form.addEventListener("submit", (e) => {
      if (selectedFiles.length === 0) return; // niets te uploaden
      e.preventDefault();
      if (!validate()) return;
      uploadWithProgress();
    });

    function renderList() {
      if (!listEl) return;
      if (selectedFiles.length === 0) {
        listEl.innerHTML = "";
        listEl.classList.remove("visible");
        return;
      }
      listEl.classList.add("visible");
      listEl.innerHTML = selectedFiles
        .map((f, i) => {
          const sizeWarn = maxFileBytes && f.size > maxFileBytes;
          return (
            '<div class="upload-row' + (sizeWarn ? " over-limit" : "") + '">' +
              '<div class="upload-row-name" title="' + escapeAttr(f.name) + '">' + escapeHtml(f.name) + "</div>" +
              '<div class="upload-row-size">' + formatBytes(f.size) + "</div>" +
            "</div>"
          );
        })
        .join("");
    }

    function validate() {
      clearError();
      if (selectedFiles.length === 0) return false;

      if (selectedFiles.length > maxFiles) {
        showError(
          "Maximaal " + maxFiles + " foto's tegelijk. " +
          "Je hebt er " + selectedFiles.length + " geselecteerd."
        );
        return false;
      }

      if (maxFileBytes) {
        const tooLarge = selectedFiles.filter((f) => f.size > maxFileBytes);
        if (tooLarge.length > 0) {
          const names = tooLarge.map((f) => f.name).join(", ");
          showError(
            "Te groot (max " + formatBytes(maxFileBytes) + " per foto): " + names
          );
          return false;
        }
      }

      if (maxTotalBytes) {
        const total = selectedFiles.reduce((s, f) => s + f.size, 0);
        if (total > maxTotalBytes) {
          showError(
            "Samen " + formatBytes(total) +
            ", maximaal " + formatBytes(maxTotalBytes) + " per keer."
          );
          return false;
        }
      }

      return true;
    }

    function uploadWithProgress() {
      const fd = new FormData(form);
      const xhr = new XMLHttpRequest();

      submitBtn.disabled = true;
      if (submitText) submitText.textContent = "Bezig met uploaden...";
      if (progressEl) progressEl.classList.add("visible");
      setProgress(0);

      xhr.upload.addEventListener("progress", (e) => {
        if (e.lengthComputable) {
          const pct = (e.loaded / e.total) * 100;
          setProgress(pct);
        }
      });

      xhr.addEventListener("load", () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          // Succes: parse JSON, redirect naar gallery/bedankt
          let data = null;
          try { data = JSON.parse(xhr.responseText); } catch (_) {}
          if (data && data.ok && data.redirect) {
            setProgress(100);
            if (submitText) submitText.textContent = "Klaar!";
            window.location.href = data.redirect;
            return;
          }
          // Fallback: server stuurde geen JSON, reload de pagina
          window.location.reload();
          return;
        }
        // 4xx/5xx: probeer JSON error te tonen
        let data = null;
        try { data = JSON.parse(xhr.responseText); } catch (_) {}
        showError((data && data.error) || "Er ging iets mis, probeer opnieuw.");
        resetSubmit();
      });

      xhr.addEventListener("error", () => {
        showError("Geen verbinding. Check je internet en probeer opnieuw.");
        resetSubmit();
      });

      xhr.addEventListener("abort", resetSubmit);

      xhr.open("POST", form.action);
      xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");
      xhr.send(fd);
    }

    function setProgress(pct) {
      if (progressBar) progressBar.style.width = pct + "%";
      if (progressLabel) progressLabel.textContent = Math.round(pct) + "%";
    }

    function resetSubmit() {
      submitBtn.disabled = false;
      if (submitText && submitText.dataset.original) {
        submitText.textContent = submitText.dataset.original;
      }
      if (progressEl) progressEl.classList.remove("visible");
      setProgress(0);
    }

    function showError(msg) {
      if (!errorEl) return;
      errorEl.textContent = msg;
      errorEl.classList.add("visible");
    }

    function clearError() {
      if (!errorEl) return;
      errorEl.textContent = "";
      errorEl.classList.remove("visible");
    }

    function formatBytes(bytes) {
      if (bytes < 1024) return bytes + " B";
      if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + " KB";
      return (bytes / (1024 * 1024)).toFixed(1) + " MB";
    }

    function escapeHtml(s) {
      const div = document.createElement("div");
      div.textContent = s;
      return div.innerHTML;
    }

    function escapeAttr(s) {
      return String(s).replace(/"/g, "&quot;");
    }
  }
})();
