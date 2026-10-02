(function (window) {
  function csrfToken() {
    const match = document.cookie.match(/csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function haversineKm(lat1, lon1, lat2, lon2) {
    const radius = 6371;
    const dLat = ((lat2 - lat1) * Math.PI) / 180;
    const dLon = ((lon2 - lon1) * Math.PI) / 180;
    const a =
      Math.sin(dLat / 2) * Math.sin(dLat / 2) +
      Math.cos((lat1 * Math.PI) / 180) *
        Math.cos((lat2 * Math.PI) / 180) *
        Math.sin(dLon / 2) *
        Math.sin(dLon / 2);
    return Math.round(2 * radius * Math.asin(Math.min(1, Math.sqrt(a))) * 10) / 10;
  }

  function addTilesWithFallback(map, config, statusEl) {
    const layers = [
      {
        url: (config && config.tile_url) || "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        attribution: (config && config.tile_attribution) || "© OpenStreetMap contributors",
      },
    ].concat((config && config.tile_fallbacks) || []);
    let current = null;
    let index = 0;
    let failures = 0;

    function attach(i) {
      if (!layers[i]) {
        if (statusEl) statusEl.textContent = "Map tiles unavailable";
        return;
      }
      if (current) map.removeLayer(current);
      failures = 0;
      current = L.tileLayer(layers[i].url, {
        attribution: layers[i].attribution,
        maxZoom: 19,
        crossOrigin: true,
      });
      current.on("tileerror", function () {
        failures += 1;
        if (failures >= 1 && i + 1 < layers.length) {
          if (statusEl) statusEl.textContent = "Switching map provider…";
          attach(i + 1);
        }
      });
      current.addTo(map);
    }

    attach(index);
    return {
      retry: function () {
        attach(0);
      },
    };
  }

  function persistLocation(url, payload) {
    return fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken(),
      },
      credentials: "same-origin",
      body: JSON.stringify(payload),
    }).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) throw new Error(data.error || "Could not save location");
        return data;
      });
    });
  }

  window.CareNestMaps = {
    csrfToken: csrfToken,
    haversineKm: haversineKm,
    addTilesWithFallback: addTilesWithFallback,
    persistLocation: persistLocation,
  };
})(window);
