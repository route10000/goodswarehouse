/* «Аналитика»: Chart.js charts from the JSON embedded by the page (#analytics-data).
   Colours come from CSS variables (tokens.css); text always wears text tokens, never a
   series colour. Every chart also has a server-rendered table («Показать таблицей»). */
(function () {
  "use strict";

  var dataEl = document.getElementById("analytics-data");
  var root = document.querySelector("[data-analytics]");
  if (!dataEl || !root) return;

  // «Показать таблицей» ↔ «Скрыть таблицу» (works without Chart.js too).
  root.querySelectorAll("[data-chart-table]").forEach(function (details) {
    var summary = details.querySelector("summary");
    details.addEventListener("toggle", function () {
      summary.textContent = details.open ? "Скрыть таблицу" : "Показать таблицей";
    });
  });

  if (!window.Chart) return;
  var Chart = window.Chart;
  var charts = JSON.parse(dataEl.textContent);

  var css = getComputedStyle(root);
  function token(name) {
    return css.getPropertyValue("--" + name).trim();
  }
  var C = {
    grid: token("l-line"),
    muted: token("l-muted"),
    text: token("l-text"),
    surface: token("l-card"),
    tipBg: token("black"),
    tipText: token("ivory"),
  };
  var FONT = token("font") || "system-ui, sans-serif";
  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var nf = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 });

  function plural(n, forms) {
    var a = Math.abs(n) % 100;
    var b = a % 10;
    if (a > 10 && a < 20) return forms[2];
    if (b > 1 && b < 5) return forms[1];
    if (b === 1 && Math.floor(n) === n) return forms[0];
    return Math.floor(n) === n ? forms[2] : forms[1];
  }
  var PLACES = ["место", "места", "мест"];
  var CALLS = ["звонок", "звонка", "звонков"];
  var UNITS = { flow: PLACES, stock: PLACES, top: PLACES, calls: CALLS, fill: "%", transit: "ч" };

  function withUnit(id, value) {
    var unit = UNITS[id];
    if (!unit) return nf.format(value);
    if (typeof unit === "string") return nf.format(value) + " " + unit;
    return nf.format(value) + " " + plural(value, unit);
  }

  function hover(color) {
    return Chart.helpers.color(color).alpha(0.78).rgbString();
  }

  Chart.defaults.font.family = FONT;
  Chart.defaults.font.size = 11;
  Chart.defaults.color = C.muted;
  Chart.defaults.borderColor = C.grid;
  if (reduced) Chart.defaults.animation = false;

  // ---------- plugins (drawn on the canvas, text in text tokens) ----------

  // Vertical hairline that snaps to the hovered index (line charts).
  var crosshair = {
    id: "gwCrosshair",
    afterDatasetsDraw: function (chart, _args, opts) {
      if (!opts || !opts.enabled) return;
      var active = chart.tooltip && chart.tooltip.getActiveElements();
      if (!active || !active.length) return;
      var x = active[0].element.x;
      var area = chart.chartArea;
      var ctx = chart.ctx;
      ctx.save();
      ctx.strokeStyle = C.muted;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(Math.round(x) + 0.5, area.top);
      ctx.lineTo(Math.round(x) + 0.5, area.bottom);
      ctx.stroke();
      ctx.restore();
    },
  };

  // Values at bar tips / column caps. ``pick(values)`` returns the indexes to label.
  var valueLabels = {
    id: "gwValues",
    afterDatasetsDraw: function (chart, _args, opts) {
      if (!opts || !opts.enabled) return;
      var horizontal = chart.options.indexAxis === "y";
      var totals = opts.totals; // stacked: label the stack total above the top segment
      var meta = chart.getDatasetMeta(totals ? chart.data.datasets.length - 1 : 0);
      var values = totals || chart.data.datasets[0].data;
      var indexes = opts.pick ? opts.pick(values) : values.map(function (_v, i) { return i; });
      var ctx = chart.ctx;
      ctx.save();
      ctx.font = "600 11px " + FONT;
      ctx.fillStyle = C.text;
      indexes.forEach(function (i) {
        var bar = meta.data[i];
        var value = values[i];
        if (!bar || !value) return;
        var text = opts.format ? opts.format(value) : nf.format(value);
        if (horizontal) {
          ctx.textAlign = "left";
          ctx.textBaseline = "middle";
          ctx.fillText(text, bar.x + 6, bar.y);
        } else {
          var top = bar.y;
          if (totals) {
            chart.data.datasets.forEach(function (_d, di) {
              var el = chart.getDatasetMeta(di).data[i];
              if (el && el.y < top) top = el.y;
            });
          }
          ctx.textAlign = "center";
          ctx.textBaseline = "bottom";
          ctx.fillText(text, bar.x, top - 4);
        }
      });
      ctx.restore();
    },
  };

  // Line end: a ringed dot and «value · series» beside it; the text is skipped when the
  // ends collide or the card is too narrow (``labelled`` is false).
  var endLabels = {
    id: "gwEndLabels",
    afterDatasetsDraw: function (chart, _args, opts) {
      if (!opts || !opts.enabled) return;
      var ctx = chart.ctx;
      var ends = chart.data.datasets.map(function (dataset, di) {
        var points = chart.getDatasetMeta(di).data;
        var last = points[points.length - 1];
        return { x: last.x, y: last.y, color: dataset.borderColor, value: dataset.data[dataset.data.length - 1], label: dataset.label };
      });
      var collide = !opts.labelled || (ends.length > 1 && Math.abs(ends[0].y - ends[1].y) < 14);
      ctx.save();
      ends.forEach(function (end) {
        ctx.beginPath();
        ctx.arc(end.x, end.y, 4, 0, Math.PI * 2);
        ctx.fillStyle = end.color;
        ctx.fill();
        ctx.lineWidth = 2;
        ctx.strokeStyle = C.surface;
        ctx.stroke();
        if (collide) return;
        ctx.font = "600 11px " + FONT;
        ctx.fillStyle = C.text;
        ctx.textAlign = "left";
        ctx.textBaseline = "middle";
        ctx.fillText(nf.format(end.value) + " · " + end.label, end.x + 9, end.y);
      });
      ctx.restore();
    },
  };

  // Dashed reference line with a muted caption (fill chart: 100 % of capacity), drawn
  // under the bars so value labels stay on top.
  var reference = {
    id: "gwReference",
    beforeDatasetsDraw: function (chart, _args, opts) {
      if (!opts || opts.value == null) return;
      var y = chart.scales.y.getPixelForValue(opts.value);
      var area = chart.chartArea;
      var ctx = chart.ctx;
      ctx.save();
      ctx.strokeStyle = C.muted;
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(area.left, Math.round(y) + 0.5);
      ctx.lineTo(area.right, Math.round(y) + 0.5);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.font = "600 11px " + FONT;
      ctx.fillStyle = C.muted;
      ctx.textAlign = "right";
      ctx.textBaseline = "bottom";
      ctx.fillText(opts.label, area.right, y - 3);
      ctx.restore();
    },
  };

  // ---------- shared options ----------

  function pickFew(limit) {
    // All values when there are few bars; otherwise only the largest one.
    return function (values) {
      if (values.length <= limit) return values.map(function (_v, i) { return i; });
      var max = 0;
      values.forEach(function (v, i) { if (v > values[max]) max = i; });
      return [max];
    };
  }

  function tooltip(chart, extra) {
    return Object.assign(
      {
        backgroundColor: C.tipBg,
        titleColor: C.tipText,
        bodyColor: C.tipText,
        footerColor: C.tipText,
        titleFont: { weight: "600", size: 12 },
        bodyFont: { weight: "600", size: 12 },
        footerFont: { weight: "500", size: 11 },
        padding: 10,
        cornerRadius: 8,
        boxWidth: 12,
        boxHeight: 2,
        boxPadding: 6,
        caretSize: 5,
        callbacks: {
          title: function (items) {
            return items.length ? chart.titles[items[0].dataIndex] : "";
          },
          label: function (ctx) {
            var value = ctx.chart.options.indexAxis === "y" ? ctx.parsed.x : ctx.parsed.y;
            var text = withUnit(chart.id, value);
            return ctx.chart.data.datasets.length > 1 ? text + " · " + ctx.dataset.label : text;
          },
          labelColor: function (ctx) {
            var color = Array.isArray(ctx.dataset.backgroundColor)
              ? ctx.dataset.backgroundColor[ctx.dataIndex]
              : ctx.dataset.borderColor || ctx.dataset.backgroundColor;
            return { borderColor: color, backgroundColor: color, borderWidth: 0 };
          },
        },
      },
      extra || {}
    );
  }

  function valueAxis(extra) {
    return Object.assign(
      {
        beginAtZero: true,
        grid: { color: C.grid, lineWidth: 1, drawTicks: false },
        border: { display: false },
        ticks: { color: C.muted, font: { size: 11 }, padding: 6, precision: 0, maxTicksLimit: 6, callback: function (v) { return nf.format(v); } },
      },
      extra || {}
    );
  }

  function categoryAxis(extra) {
    return Object.assign(
      {
        grid: { display: false },
        border: { color: C.grid },
        ticks: { color: C.muted, font: { size: 11 }, maxRotation: 0, autoSkip: true, autoSkipPadding: 12 },
      },
      extra || {}
    );
  }

  function base(chart, plugins, extra) {
    var options = {
      responsive: true,
      maintainAspectRatio: false,
      animation: reduced ? false : { duration: 400 },
      layout: { padding: { top: 18, right: 8 } },
      plugins: Object.assign({ legend: { display: false }, tooltip: tooltip(chart) }, plugins || {}),
    };
    return Object.assign(options, extra || {});
  }

  function colorsOf(series) {
    return series.colors ? series.colors.map(token) : token(series.color);
  }

  // ---------- builders by chart type ----------

  var build = {
    line: function (chart, canvas) {
      // End labels need room on the right; skip them on narrow cards and when the two
      // ends sit too close to tell apart (the legend and tooltip carry identity then).
      var all = [].concat.apply([], chart.series.map(function (s) { return s.data; }));
      var top = Math.max.apply(null, all.concat([1]));
      var lasts = chart.series.map(function (s) { return s.data[s.data.length - 1]; });
      var apart = lasts.length < 2 || Math.abs(lasts[0] - lasts[1]) >= top * 0.12;
      var labelled = apart && canvas.parentNode.clientWidth >= 420;
      return {
        type: "line",
        data: {
          labels: chart.labels,
          datasets: chart.series.map(function (s) {
            var color = token(s.color);
            return {
              label: s.label,
              data: s.data,
              borderColor: color,
              backgroundColor: color,
              borderWidth: 2,
              borderJoinStyle: "round",
              borderCapStyle: "round",
              pointRadius: 0,
              pointHoverRadius: 4,
              pointHoverBorderWidth: 2,
              pointHoverBorderColor: C.surface,
              pointHitRadius: 12,
              tension: 0,
              fill: false,
            };
          }),
        },
        options: base(
          chart,
          { gwCrosshair: { enabled: true }, gwEndLabels: { enabled: true, labelled: labelled } },
          {
            interaction: { mode: "index", intersect: false },
            layout: { padding: { top: 18, right: labelled ? 96 : 8 } },
            scales: { x: categoryAxis({ ticks: Object.assign(categoryAxis().ticks, { maxTicksLimit: 8 }) }), y: valueAxis() },
          }
        ),
        plugins: [crosshair, endLabels],
      };
    },

    hbar: function (chart, canvas) {
      var s = chart.series[0];
      // Narrow cards: the client code moves to the tooltip and table only.
      var narrow = canvas.parentNode.clientWidth < 420;
      var colors = colorsOf(s);
      return {
        type: "bar",
        data: {
          labels: chart.labels,
          datasets: [
            {
              label: s.label,
              data: s.data,
              backgroundColor: colors,
              hoverBackgroundColor: Array.isArray(colors) ? colors.map(hover) : hover(colors),
              borderRadius: 4,
              borderSkipped: "start",
              maxBarThickness: 24,
              categoryPercentage: 0.8,
              barPercentage: 0.9,
            },
          ],
        },
        options: base(
          chart,
          { gwValues: { enabled: true } },
          {
            indexAxis: "y",
            interaction: { mode: "nearest", axis: "y", intersect: false },
            layout: { padding: { top: 4, right: 40 } },
            scales: {
              x: valueAxis(),
              y: categoryAxis({
                ticks: {
                  color: C.muted,
                  font: { size: 11 },
                  autoSkip: false,
                  callback: function (value) {
                    // Shorten the name, never the client code after «·».
                    var label = this.getLabelForValue(value);
                    var parts = label.split(" · ");
                    var name = parts[0];
                    var code = parts.length > 1 ? " · " + parts[1] : "";
                    if (narrow && chart.id === "top") {
                      return name.length > 16 ? name.slice(0, 15) + "…" : name;
                    }
                    if (name.length + code.length <= 28) return label;
                    return name.slice(0, Math.max(6, 27 - code.length)) + "…" + code;
                  },
                },
              }),
            },
          }
        ),
        plugins: [valueLabels],
      };
    },

    bar: function (chart) {
      var s = chart.series[0];
      var colors = colorsOf(s);
      var extraScales = {};
      var plugins = { gwValues: { enabled: true, pick: pickFew(12) } };
      if (chart.id === "fill") {
        var max = Math.max.apply(null, s.data.concat([100]));
        extraScales = { max: Math.ceil((max + 10) / 10) * 10, ticks: Object.assign(valueAxis().ticks, { callback: function (v) { return nf.format(v) + " %"; } }) };
        plugins.gwValues.format = function (v) { return nf.format(v) + " %"; };
        plugins.gwReference = { value: 100, label: "100 %" };
        plugins.tooltip = tooltip(chart, {});
        plugins.tooltip.callbacks.afterLabel = function (ctx) {
          return chart.low && chart.low[ctx.dataIndex] ? "Ниже " + chart.threshold + " % вместимости" : "";
        };
      }
      return {
        type: "bar",
        data: {
          labels: chart.labels,
          datasets: [
            {
              label: s.label,
              data: s.data,
              backgroundColor: colors,
              hoverBackgroundColor: Array.isArray(colors) ? colors.map(hover) : hover(colors),
              borderRadius: 4,
              borderSkipped: "start",
              maxBarThickness: 24,
              categoryPercentage: 0.8,
              barPercentage: 0.9,
            },
          ],
        },
        options: base(chart, plugins, {
          interaction: { mode: "index", intersect: false },
          scales: { x: categoryAxis(), y: valueAxis(extraScales) },
        }),
        plugins: [valueLabels, reference],
      };
    },

    stacked: function (chart) {
      var bottom = chart.series[0];
      var top = chart.series[1];
      var totals = bottom.data.map(function (v, i) { return v + top.data[i]; });
      var ROUND = { topLeft: 4, topRight: 4, bottomLeft: 0, bottomRight: 0 };
      var SQUARE = { topLeft: 0, topRight: 0, bottomLeft: 0, bottomRight: 0 };
      function dataset(s, isTop) {
        var color = token(s.color);
        return {
          label: s.label,
          data: s.data,
          backgroundColor: color,
          hoverBackgroundColor: hover(color),
          borderSkipped: false,
          // Data-end rounding: the top segment, or the bottom one when nothing is above it.
          borderRadius: function (ctx) {
            if (isTop) return ROUND;
            return top.data[ctx.dataIndex] ? SQUARE : ROUND;
          },
          // 2px surface gap between the two segments.
          borderColor: C.surface,
          borderWidth: function (ctx) {
            if (isTop && bottom.data[ctx.dataIndex]) return { top: 0, right: 0, bottom: 2, left: 0 };
            return 0;
          },
          maxBarThickness: 24,
          categoryPercentage: 0.8,
          barPercentage: 0.9,
        };
      }
      var tip = tooltip(chart, {});
      tip.callbacks.footer = function (items) {
        if (!items.length) return "";
        var i = items[0].dataIndex;
        var all = totals[i];
        return all ? "Дозвон " + nf.format(Math.round((bottom.data[i] * 100) / all)) + " %" : "";
      };
      return {
        type: "bar",
        data: { labels: chart.labels, datasets: [dataset(bottom, false), dataset(top, true)] },
        options: base(
          chart,
          { tooltip: tip, gwValues: { enabled: true, totals: totals, pick: pickFew(12) } },
          {
            interaction: { mode: "index", intersect: false },
            scales: { x: categoryAxis({ stacked: true }), y: valueAxis({ stacked: true }) },
          }
        ),
        plugins: [valueLabels],
      };
    },
  };

  charts.forEach(function (chart) {
    if (chart.empty) return;
    var canvas = root.querySelector('canvas[data-chart="' + chart.id + '"]');
    if (!canvas || !build[chart.type]) return;
    new Chart(canvas, build[chart.type](chart, canvas));
  });
})();
