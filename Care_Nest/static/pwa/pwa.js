(function () {
  if (!("serviceWorker" in navigator)) return;

  var host = location.hostname;
  var isLocal =
    host === "localhost" ||
    host === "127.0.0.1" ||
    host === "0.0.0.0" ||
    host === "10.0.2.2" ||
    host === "[::1]";

  function clearWorker() {
    navigator.serviceWorker.getRegistrations().then(function (regs) {
      regs.forEach(function (reg) {
        reg.unregister();
      });
    });
    if (window.caches) {
      caches.keys().then(function (keys) {
        keys.forEach(function (key) {
          caches.delete(key);
        });
      });
    }
  }

  if (isLocal) {
    clearWorker();
    return;
  }

  window.addEventListener("load", function () {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () {});
  });

  var deferredPrompt = null;
  var bar = document.getElementById("pwa-install-bar");
  var installBtn = document.getElementById("pwa-install-btn");
  var dismissBtn = document.getElementById("pwa-install-dismiss");
  var iosHint = document.getElementById("pwa-ios-hint");

  function dismissed() {
    try {
      return localStorage.getItem("carenest-pwa-dismissed") === "1";
    } catch (err) {
      return false;
    }
  }

  function hideBar() {
    if (bar) bar.classList.add("hidden");
  }

  window.addEventListener("beforeinstallprompt", function (event) {
    event.preventDefault();
    deferredPrompt = event;
    if (!dismissed() && bar) bar.classList.remove("hidden");
  });

  if (installBtn) {
    installBtn.addEventListener("click", function () {
      if (!deferredPrompt) return;
      deferredPrompt.prompt();
      deferredPrompt.userChoice.finally(function () {
        deferredPrompt = null;
        hideBar();
      });
    });
  }

  if (dismissBtn) {
    dismissBtn.addEventListener("click", function () {
      try {
        localStorage.setItem("carenest-pwa-dismissed", "1");
      } catch (err) {}
      hideBar();
    });
  }

  var isIos = /iphone|ipad|ipod/i.test(navigator.userAgent);
  var isStandalone = window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone;
  if (isIos && !isStandalone && !dismissed() && iosHint) {
    iosHint.classList.remove("hidden");
    if (bar) bar.classList.remove("hidden");
  }
})();
