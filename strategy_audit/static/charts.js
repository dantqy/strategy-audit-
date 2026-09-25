/* Dependency-free SVG charts. Numbers come straight from the API; nothing is computed here but pixel positions. */
(function () {
  const NS = "http://www.w3.org/2000/svg";
  let W = 900; const PAD = { l: 64, r: 16, t: 14, b: 30 };
  const svgEl = (tag, attrs) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; };
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

  function niceTicks(min, max, n = 5) {
    if (min === max) { min -= 1; max += 1; }
    const span = max - min, step0 = span / n, mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= step0) || mag * 10;
    const out = []; for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) out.push(+v.toFixed(10));
    return out;
  }

  function frame(el, height, width) {
    W = width || (el.clientWidth > 0 ? Math.max(360, Math.min(1000, el.clientWidth)) : 900);
    el.innerHTML = "";                          // container is ours; no user content is ever inserted here
    el.classList.add("chart");
    const svg = svgEl("svg", { viewBox: `0 0 ${W} ${height}`, role: "img" });
    const tip = document.createElement("div"); tip.className = "tip";
    el.append(svg, tip);
    return { svg, tip };
  }

  function axes(svg, height, y, ticks, fmt, dates, x) {
    const g = svgEl("g", {});
    for (const t of ticks) {
      const yy = y(t);
      g.append(svgEl("line", { x1: PAD.l, x2: W - PAD.r, y1: yy, y2: yy, stroke: css("--border"), "stroke-width": 1 }));
      const lab = svgEl("text", { x: PAD.l - 8, y: yy + 4, "text-anchor": "end", "font-size": 12, fill: css("--muted") });
      lab.textContent = fmt(t); g.append(lab);
    }
    if (dates) {                                 // one label per year boundary
      let lastY = null;
      dates.forEach((d, i) => {
        const yr = d.slice(0, 4);
        if (yr !== lastY) {
          lastY = yr;
          const lab = svgEl("text", { x: x(i), y: height - 8, "text-anchor": "start", "font-size": 12, fill: css("--muted") });
          lab.textContent = yr; g.append(lab);
        }
      });
    }
    svg.append(g);
  }

  function hover(svg, tip, el, n, x, lines) {
    const guide = svgEl("line", { y1: PAD.t, stroke: css("--muted"), "stroke-dasharray": "3 3", visibility: "hidden" });
    guide.setAttribute("y2", svg.viewBox.baseVal.height - PAD.b);
    svg.append(guide);
    const rect = svgEl("rect", { x: PAD.l, y: 0, width: W - PAD.l - PAD.r, height: svg.viewBox.baseVal.height, fill: "transparent" });
    rect.addEventListener("mousemove", (ev) => {
      const b = svg.getBoundingClientRect();
      const px = (ev.clientX - b.left) / b.width * W;
      const i = Math.max(0, Math.min(n - 1, Math.round((px - PAD.l) / (W - PAD.l - PAD.r) * (n - 1))));
      guide.setAttribute("x1", x(i)); guide.setAttribute("x2", x(i)); guide.setAttribute("visibility", "visible");
      tip.textContent = lines(i);
      tip.style.display = "block";
      tip.style.left = (x(i) / W * b.width) + "px"; tip.style.top = "18px";
    });
    rect.addEventListener("mouseleave", () => { tip.style.display = "none"; guide.setAttribute("visibility", "hidden"); });
    svg.append(rect);
  }

  window.Charts = {
    line(el, { dates, series, fmt, height = 280, tipFmt }) {
      const { svg, tip } = frame(el, height);
      const all = series.flatMap(s => s.values).filter(v => v != null);
      const ticks = niceTicks(Math.min(...all), Math.max(...all));
      const lo = Math.min(ticks[0], ...all), hi = Math.max(ticks[ticks.length - 1], ...all);
      const n = dates.length;
      const x = i => PAD.l + (W - PAD.l - PAD.r) * (n <= 1 ? 0 : i / (n - 1));
      const y = v => PAD.t + (height - PAD.t - PAD.b) * (1 - (v - lo) / (hi - lo || 1));
      axes(svg, height, y, ticks, fmt, dates, x);
      for (const s of series) {
        let d = "";
        s.values.forEach((v, i) => { if (v != null) d += (d ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1); });
        svg.append(svgEl("path", { d, fill: "none", stroke: css(s.color), "stroke-width": s.width || 2, "stroke-linejoin": "round" }));
      }
      hover(svg, tip, el, n, x, i => dates[i] + "  " + series.map(s => `${s.name} ${(tipFmt || fmt)(s.values[i])}`).join("  ·  "));
    },

    area(el, { dates, values, fmt, height = 200, color = "--c-dd" }) {
      const { svg, tip } = frame(el, height);
      const lo = Math.min(...values, 0), ticks = niceTicks(lo, 0, 4);
      const n = dates.length;
      const x = i => PAD.l + (W - PAD.l - PAD.r) * (n <= 1 ? 0 : i / (n - 1));
      const y = v => PAD.t + (height - PAD.t - PAD.b) * (1 - (v - Math.min(lo, ticks[0])) / (0 - Math.min(lo, ticks[0]) || 1));
      axes(svg, height, y, ticks, fmt, dates, x);
      let d = `M${x(0)} ${y(0)}`;
      values.forEach((v, i) => d += `L${x(i).toFixed(1)} ${y(v).toFixed(1)}`);
      d += `L${x(n - 1)} ${y(0)}Z`;
      svg.append(svgEl("path", { d, fill: css(color), "fill-opacity": .18, stroke: css(color), "stroke-width": 1.5 }));
      hover(svg, tip, el, n, x, i => `${dates[i]}  drawdown ${fmt(values[i])}`);
    },

    bars(el, { labels, series, fmt, height = 240 }) {
      const { svg, tip } = frame(el, height);
      const all = series.flatMap(s => s.values).filter(v => v != null);
      const ticks = niceTicks(Math.min(0, ...all), Math.max(0, ...all));
      const lo = ticks[0], hi = ticks[ticks.length - 1];
      const y = v => PAD.t + (height - PAD.t - PAD.b) * (1 - (v - lo) / (hi - lo || 1));
      axes(svg, height, y, ticks, fmt, null, null);
      const n = labels.length, gw = (W - PAD.l - PAD.r) / n, bw = Math.min(28, gw / (series.length + 1));
      labels.forEach((lab, i) => {
        series.forEach((s, j) => {
          const v = s.values[i]; if (v == null) return;
          const x0 = PAD.l + gw * i + (gw - bw * series.length) / 2 + bw * j;
          const r = svgEl("rect", { x: x0, width: bw - 3, y: Math.min(y(v), y(0)), height: Math.abs(y(v) - y(0)) || 1, fill: css(s.color), rx: 2 });
          r.addEventListener("mousemove", () => { tip.textContent = `${lab}  ${s.name} ${fmt(v)}`; tip.style.display = "block";
            const b = svg.getBoundingClientRect(); tip.style.left = ((x0 + bw / 2) / W * b.width) + "px"; tip.style.top = "18px"; });
          r.addEventListener("mouseleave", () => tip.style.display = "none");
          svg.append(r);
        });
        const t = svgEl("text", { x: PAD.l + gw * i + gw / 2, y: height - 8, "text-anchor": "middle", "font-size": 12, fill: css("--muted") });
        t.textContent = lab; svg.append(t);
      });
      svg.append(svgEl("line", { x1: PAD.l, x2: W - PAD.r, y1: y(0), y2: y(0), stroke: css("--muted") }));
    },
  };
})();
