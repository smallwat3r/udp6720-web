// The recorder: a canvas line chart per quantity, drawn as a graticule on glass
import { html, useEffect, useRef } from "./vendor/preact-htm-3.1.1.js";

export const WINDOW_S = 300;

function niceMax(m) {
  if (m <= 0) return 1;
  const pow = 10 ** Math.floor(Math.log10(m));
  return [1, 2, 5, 10].find((f) => m <= f * pow) * pow;
}

function drawChart(c, hist, quantity, hover) {
  const dpr = window.devicePixelRatio || 1;
  const w = c.clientWidth;
  const h = c.clientHeight;
  if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) {
    c.width = Math.round(w * dpr);
    c.height = Math.round(h * dpr);
  }
  const css = getComputedStyle(document.documentElement);
  const color = (name) => css.getPropertyValue(name).trim();
  const g = c.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  g.font = `12px ${color("--font")}`;

  const pad = { l: 40, r: 10, t: 18, b: 20 };
  const now = hist.length ? hist.at(-1)[0] : Date.now() / 1000;
  const top = niceMax(Math.max(0, ...hist.map((p) => p[quantity.idx])));
  const x = (t) => pad.l + ((t - (now - WINDOW_S)) / WINDOW_S) * (w - pad.l - pad.r);
  const y = (v) => h - pad.b - (v / top) * (h - pad.t - pad.b);

  // Dotted graticule: three levels, one division per minute
  g.lineWidth = 1;
  g.strokeStyle = color("--glass-grid");
  g.fillStyle = color("--glass-ink");
  g.textBaseline = "middle";
  g.textAlign = "right";
  g.setLineDash([2, 4]);
  g.beginPath();
  for (const v of [0, top / 2, top]) {
    g.moveTo(pad.l, y(v));
    g.lineTo(w - pad.r, y(v));
    g.fillText(String(Number(v.toPrecision(3))), pad.l - 6, y(v));
  }
  for (let minute = 0; minute <= WINDOW_S / 60; minute++) {
    g.moveTo(x(now - minute * 60), pad.t);
    g.lineTo(x(now - minute * 60), h - pad.b);
  }
  g.stroke();
  g.setLineDash([]);
  g.textBaseline = "alphabetic";
  g.textAlign = "left";
  g.fillText("-5 min", pad.l, h - 4);
  g.textAlign = "right";
  g.fillText("now", w - pad.r, h - 4);

  g.lineWidth = 2;
  g.lineJoin = "round";
  g.strokeStyle = color(`--${quantity.q}`);
  g.beginPath();
  hist.forEach((p, i) => {
    // A gap in the samples (device unplugged) breaks the line instead of bridging it
    const gap = i === 0 || p[0] - hist[i - 1][0] > 5;
    g[gap ? "moveTo" : "lineTo"](x(p[0]), y(p[quantity.idx]));
  });
  g.stroke();

  if (hover === null || !hist.length) return;
  const p = hist.reduce((a, b) => (Math.abs(x(b[0]) - hover) < Math.abs(x(a[0]) - hover) ? b : a));
  const px = x(p[0]);
  g.lineWidth = 1;
  g.strokeStyle = color("--glass-ink");
  g.beginPath();
  g.moveTo(px, pad.t);
  g.lineTo(px, h - pad.b);
  g.stroke();
  g.fillStyle = color(`--${quantity.q}`);
  g.strokeStyle = color("--glass");
  g.lineWidth = 2;
  g.beginPath();
  g.arc(px, y(p[quantity.idx]), 5, 0, 2 * Math.PI);
  g.fill();
  g.stroke();
  g.fillStyle = color("--glass-text");
  g.textAlign = px > w / 2 ? "right" : "left";
  const time = new Date(p[0] * 1000).toLocaleTimeString();
  const text = `${p[quantity.idx].toFixed(quantity.digits)} ${quantity.letter} at ${time}`;
  g.fillText(text, px > w / 2 ? px - 8 : px + 8, 12);
}

// hist is one array mutated in place, so the resize listener always draws current data
export function Chart({ quantity, hist }) {
  const canvas = useRef();
  const hover = useRef(null);
  const redraw = () => drawChart(canvas.current, hist, quantity, hover.current);
  useEffect(redraw);
  // The plot area flexes with the window, redraw whenever its size changes
  useEffect(() => {
    const observer = new ResizeObserver(redraw);
    observer.observe(canvas.current);
    return () => observer.disconnect();
  }, []);
  const point = (x) => {
    hover.current = x;
    redraw();
  };
  return html`
    <div class=${`plot ${quantity.q}`}>
      <span class="legend"><i class="swatch"></i>${quantity.title}, ${quantity.letter}</span>
      <div class="plot-area">
        <canvas
          ref=${canvas}
        id=${`chart-${quantity.key}`}
        role="img"
        aria-label=${`Measured ${quantity.title.toLowerCase()} over the last 5 minutes`}
        onPointerMove=${(e) => point(e.offsetX)}
          onPointerLeave=${() => point(null)}
        ></canvas>
      </div>
    </div>
  `;
}
