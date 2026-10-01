/* Monatsdiagramme: aktueller Zeitraum vs. Vorjahr (eine Achse je Diagramm). */
(function () {
  var charts = [];
  var nf = function (d) {
    return new Intl.NumberFormat("de-DE", { minimumFractionDigits: d, maximumFractionDigits: d });
  };

  function css(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function withAlpha(hex, a) {
    var n = parseInt(hex.slice(1), 16);
    return "rgba(" + (n >> 16) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + a + ")";
  }

  function build(canvasId, labels, current, prev, partial, color, fmtValue) {
    var ctx = document.getElementById(canvasId);
    if (!ctx) return;
    var grid = css("--grid"), text2 = css("--text-2"), surface = css("--surface");
    charts.push(new Chart(ctx, {
      type: "bar",
      data: {
        labels: labels,
        datasets: [
          {
            label: "Vorjahr", data: prev, backgroundColor: css("--prev"),
            borderRadius: 4, borderSkipped: "bottom", borderColor: surface, borderWidth: { right: 1 },
            maxBarThickness: 18,
          },
          {
            label: "Aktuell", data: current,
            backgroundColor: current.map(function (_, i) { return partial[i] ? withAlpha(color, 0.45) : color; }),
            borderRadius: 4, borderSkipped: "bottom", maxBarThickness: 18,
          },
        ],
      },
      options: {
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: function (c) {
                if (c.raw === null) return c.dataset.label + ": keine Daten";
                var extra = c.datasetIndex === 1 && partial[c.dataIndex] ? " (vorläufig)" : "";
                return c.dataset.label + ": " + fmtValue(c.raw) + extra;
              },
            },
          },
        },
        scales: {
          x: { grid: { display: false }, ticks: { color: text2, maxRotation: 0, autoSkip: true, font: { size: 11 } } },
          y: {
            beginAtZero: true, border: { display: false }, grid: { color: grid },
            ticks: { color: text2, font: { size: 11 }, callback: function (v) { return nf(0).format(v); } },
          },
        },
      },
    }));
  }

  window.renderCharts = function (medium) {
    var data = JSON.parse(document.getElementById("chartdata").textContent);
    function draw() {
      charts.forEach(function (c) { c.destroy(); });
      charts = [];
      var color = css("--" + medium);
      build("chartConsumption", data.labels, data.consumption, data.consumption_prev, data.partial, color,
        function (v) { return nf(1).format(v) + " " + data.unit; });
      build("chartCost", data.labels, data.cost, data.cost_prev, data.partial, color,
        function (v) { return nf(2).format(v) + " €"; });
    }
    draw();
    document.addEventListener("themechange", draw);
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", draw);
  };
})();
