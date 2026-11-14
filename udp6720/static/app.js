// Application state, device commands and keyboard shortcuts
import { html, render, useEffect, useLayoutEffect, useRef, useState } from "./vendor/preact-htm-3.1.1.js";
import { Chart, WINDOW_S } from "./chart.js";
import { Confirm, Help, Icon, Preset, Readout } from "./components.js";

const QUANTITIES = [
  { key: "volts", q: "volt", letter: "V", title: "Voltage", idx: 1, digits: 2, ghost: "88.88" },
  { key: "amps", q: "amp", letter: "A", title: "Current", idx: 2, digits: 3, ghost: "8.888" },
  { key: "watts", q: "watt", letter: "W", title: "Power", idx: 3, digits: 2, ghost: "888.88" },
];
const [VOLTS, AMPS] = QUANTITIES;
const MOVE_KEYS = { ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1 };
const FOCUS_KEYS = { v: "in-volts", a: "in-amps", c: "in-amps" };

async function api(path, body, method) {
  const res = await fetch(
    path,
    body
      ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
      : { method: method || "GET" },
  );
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Request rejected (${res.status})`);
  return data;
}

function App() {
  const [snap, setSnap] = useState(null);
  const [serverDown, setServerDown] = useState(false);
  const [presets, setPresets] = useState([]);
  const [editing, setEditing] = useState(false);
  const [message, setMessage] = useState({ text: "" });
  const [help, setHelp] = useState(false);
  const [request, setRequest] = useState(null);
  // What the user typed in each setpoint box, null while a box still mirrors the device
  const [form, setForm] = useState({ volts: null, amps: null });
  const [label, setLabel] = useState("");
  const hist = useRef([]).current; // [ts, volts, amps, watts]
  const refocus = useRef(null);
  const latest = useRef({});

  const s = snap && snap.state;
  const live = Boolean(!serverDown && snap && snap.connected && s);
  const isActive = (p) => Boolean(s) && Math.abs(p.volts - s.set_volts) < 0.005 && Math.abs(p.amps - s.set_amps) < 0.0005;

  const applySnap = (next) => {
    const state = next.state;
    if (next.connected && state) {
      if (!hist.length || state.ts > hist.at(-1)[0]) hist.push([state.ts, state.volts, state.amps, state.watts]);
      while (hist.length && hist[0][0] < state.ts - WINDOW_S) hist.shift();
    }
    setServerDown(false);
    setSnap(next);
  };

  useEffect(() => {
    let timer;
    let stopped = false;
    let period = 1000;
    const tick = async () => {
      try {
        const next = await api("/api/state");
        period = next.poll_ms;
        applySnap(next);
      } catch {
        setServerDown(true);
      }
      if (!stopped) timer = setTimeout(tick, period);
    };
    api("/api/history")
      .then((rows) => hist.push(...rows))
      .catch(() => {})
      .finally(tick);
    api("/api/presets")
      .then(setPresets)
      .catch((err) => setMessage({ text: err.message, error: true }));
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, []);

  const ask = (title, detail, action) => new Promise((resolve) => setRequest({ title, detail, action, resolve }));
  const cleanForm = () => setForm({ volts: null, amps: null });

  const command = async (path, body, done) => {
    try {
      applySnap(await api(path, body));
      setMessage({ text: done });
      return true;
    } catch (err) {
      setMessage({ text: err.message, error: true });
      return false;
    }
  };

  const setOutput = (on) => command("/api/output", { on }, on ? "Output turned on." : "Output turned off.");

  const toggleOutput = async () => {
    if (!live) return;
    // Turning off is always immediate, turning on asks first
    const detail = `${s.set_volts.toFixed(2)} V, limit ${s.set_amps.toFixed(3)} A`;
    if (!s.output && !(await ask("Turn the output on?", detail, "Turn on"))) return;
    setOutput(!s.output);
  };

  const submitSetpoints = async (e) => {
    e.preventDefault();
    const body = {
      volts: document.getElementById("in-volts").valueAsNumber,
      amps: document.getElementById("in-amps").valueAsNumber,
    };
    if (await command("/api/setpoints", body, "Setpoints applied and confirmed by the device.")) {
      cleanForm();
      document.activeElement.blur();
    }
  };

  const applyPreset = async (p) => {
    if (!live) return;
    const values = `${p.volts.toFixed(2)} V, ${p.amps.toFixed(3)} A`;
    // Changing the setpoints under a live load asks first
    if (s.output && !(await ask(`Switch to "${p.label}" while the output is on?`, values, "Switch"))) return;
    if (await command("/api/setpoints", { volts: p.volts, amps: p.amps }, `Preset "${p.label}" applied.`)) cleanForm();
  };

  const presetRequest = async (path, body, method, done) => {
    try {
      setPresets(await api(path, body, method));
      setMessage({ text: done });
      return true;
    } catch (err) {
      setMessage({ text: err.message, error: true });
      return false;
    }
  };

  const movePreset = async (p, offset) => {
    refocus.current = { label: p.label, offset };
    if (!(await presetRequest("/api/presets/move", { label: p.label, offset }, "POST", ""))) refocus.current = null;
  };

  const deletePreset = async (p, values) => {
    if (await ask(`Delete the preset "${p.label}"?`, values, "Delete")) {
      presetRequest(`/api/presets?label=${encodeURIComponent(p.label)}`, null, "DELETE", `Preset "${p.label}" deleted.`);
    }
  };

  const savePreset = async (e) => {
    e.preventDefault();
    // The preset takes the values in the setpoint boxes, applied to the device or not
    if (!document.getElementById("setpoints").reportValidity()) return;
    const body = {
      label,
      volts: document.getElementById("in-volts").valueAsNumber,
      amps: document.getElementById("in-amps").valueAsNumber,
    };
    if (await presetRequest("/api/presets", body, "POST", `Preset "${label.trim()}" saved.`)) {
      setLabel("");
      document.getElementById("preset-label").blur(); // shortcuts work again straight away
    }
  };

  // A moved preset keeps the focus, so repeated presses keep moving it
  useLayoutEffect(() => {
    const want = refocus.current;
    refocus.current = null;
    if (!want) return;
    const item = [...document.querySelectorAll("#presets li")].find((li) => li.dataset.label === want.label);
    const button =
      item && [want.offset, -want.offset].map((o) => item.querySelector(`[data-move="${o}"]`)).find((b) => !b.disabled);
    if (button) button.focus();
  }, [presets]);

  // The key listener is registered once and reads the current state through this ref
  latest.current = { live, editing, presets, isActive, toggleOutput, setOutput, applyPreset, movePreset, cleanForm };
  useEffect(() => {
    const onKey = (e) => {
      const now = latest.current;
      if (e.ctrlKey || e.metaKey || e.altKey || e.repeat || document.querySelector("dialog[open]")) return;
      const typing = e.target instanceof HTMLInputElement;
      if (e.key === "Escape" && typing) {
        // Drop the edit and show the device's values again
        if (e.target.form && e.target.form.id === "setpoints") now.cleanForm();
        e.target.blur();
        return;
      }
      if (typing) return;
      if (e.key === "?") return setHelp(true);
      if (!now.live) return;
      const key = e.key.toLowerCase();
      if (MOVE_KEYS[e.key] && now.editing) {
        // Moves the focused preset, or the ticked one when the focus is elsewhere
        const item = document.activeElement.closest("#presets li");
        const preset = now.presets.find(item ? (p) => p.label === item.dataset.label : now.isActive);
        if (!preset) return;
        e.preventDefault(); // no page scroll
        now.movePreset(preset, MOVE_KEYS[e.key]);
      } else if (FOCUS_KEYS[key]) {
        e.preventDefault(); // keep the letter out of the box
        const box = document.getElementById(FOCUS_KEYS[key]);
        box.focus();
        box.select();
      } else if (key === "n") {
        // The name box only exists in edit mode: open it, then focus the box once it is rendered
        e.preventDefault();
        setEditing(true);
        requestAnimationFrame(() => requestAnimationFrame(() => document.getElementById("preset-label")?.focus()));
      } else if (key === "e") {
        setEditing((on) => !on);
      } else if (key === "o") {
        now.toggleOutput();
      } else if (key === "0") {
        now.setOutput(false);
      } else if (key >= "1" && key <= "9" && now.presets[Number(key) - 1]) {
        now.applyPreset(now.presets[Number(key) - 1]);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  // A box shows the device's setpoint until the user types in it
  const box = (quantity) => {
    const device = s ? s[`set_${quantity.key}`].toFixed(quantity.digits) : "";
    return html`
      <label>
        <span>
          <i class=${`swatch ${quantity.q}`}></i>
          ${quantity === VOLTS ? "Set voltage, V" : "Current limit, A"}
          <kbd>${quantity.letter}</kbd>
        </span>
        <input
          id=${`in-${quantity.key}`}
          aria-keyshortcuts=${quantity === VOLTS ? "V" : "A C"}
          type="number"
          inputmode="decimal"
          min="0"
          max=${snap ? snap[`max_${quantity.key}`] : null}
          step=${quantity === VOLTS ? "0.01" : "0.001"}
          required
          disabled=${!live}
          value=${form[quantity.key] === null ? device : form[quantity.key]}
          onInput=${(e) => setForm((f) => ({ ...f, [quantity.key]: e.target.value }))}
        />
      </label>
    `;
  };

  const problem = serverDown
    ? "Dashboard server unreachable, the values shown are stale."
    : snap && `Power supply not responding: ${snap.error}`;

  const lamp = (lit, tone = "") => (lit ? `lit ${tone}` : "");
  const mode = !s ? "no data" : !s.output ? "output off" : s.mode === "CC" ? "current limiting" : "constant voltage";

  return html`
    <${Confirm}
      request=${request}
      onDone=${(ok) => {
        request.resolve(ok);
        setRequest(null);
      }}
    />
    <${Help} open=${help} onClose=${() => setHelp(false)} />
    <main id="app" class=${live ? "" : "stale"}>
      <header class="nameplate">
        <h1>UDP6720</h1>
        <p class="legend">Programmable DC power supply, 60 V, 5 A, 100 W</p>
        <span id="idn" class="num">${snap && snap.idn}</span>
        <button id="help-button" type="button" aria-keyshortcuts="?" onClick=${() => setHelp(true)}>Keys<kbd>?</kbd></button>
        <span id="link" class=${`legend ${live ? "live" : ""}`} role="status">
          <i class="lamp"></i>${live ? "Live" : snap || serverDown ? "Offline" : "Connecting"}
        </span>
      </header>

      <div id="banner" role="alert" hidden=${live || !problem}>
        <${Icon} name="alert" /><span>${problem}</span>
      </div>

      <section class="glass display" aria-label="Measured output">
        <div class="status-row">
          <div id="mode" class="annunciators" role="status" aria-label=${`Mode: ${mode}`}>
            <span class=${lamp(live && s.output)}>OUT</span>
            <span class=${lamp(live && s.output && s.mode === "CV")}>CV</span>
            <span class=${lamp(live && s.output && s.mode === "CC", "warn")}>CC</span>
            <span class=${lamp(!live && (snap || serverDown), "bad")}>OFFLINE</span>
          </div>
          <span id="message" class=${message.error ? "error" : ""} role="status">${message.text}</span>
        </div>
        <div class="cells">
          <${Readout} quantity=${VOLTS} state=${s} subId="set-volts" sub=${s && `SET ${s.set_volts.toFixed(2)} V`} />
          <${Readout} quantity=${AMPS} state=${s} subId="set-amps" sub=${s && `LIMIT ${s.set_amps.toFixed(3)} A`} />
          <${Readout} quantity=${QUANTITIES[2]} state=${s} sub="RATED 100 W" />
        </div>
      </section>

      <section class="controls">
        <fieldset>
          <legend>Output</legend>
          <div class="output">
            <button
              id="output"
              type="button"
              aria-label=${s && s.output ? "Output ON" : "Output OFF"}
              aria-pressed=${s && s.output ? "true" : "false"}
              aria-keyshortcuts="O"
              disabled=${!live}
              onClick=${toggleOutput}
            >
              <${Icon} name="power" /><span id="output-text">${s && s.output ? "ON" : "OFF"}</span>
            </button>
            <p class="legend"><kbd>O</kbd>toggle<kbd>0</kbd>off</p>
          </div>
        </fieldset>
        <fieldset>
          <legend>Setpoints</legend>
          <form id="setpoints" onSubmit=${submitSetpoints}>
            ${box(VOLTS)} ${box(AMPS)}
            <button id="apply" class="primary" type="submit" disabled=${!live}>Apply</button>
          </form>
        </fieldset>
      </section>

      <fieldset class="recorder">
        <legend>Recorder, last 5 minutes</legend>
        <a class="corner" href="/api/history.csv" download><${Icon} name="download" />CSV</a>
        <div class="glass plots">${QUANTITIES.map((quantity) => html`<${Chart} quantity=${quantity} hist=${hist} />`)}</div>
      </fieldset>

      <fieldset>
        <legend>Memory</legend>
        <button
          id="edit-presets"
          class="corner"
          type="button"
          aria-pressed=${editing ? "true" : "false"}
          aria-keyshortcuts="E"
          onClick=${() => setEditing((on) => !on)}
        >
          ${editing ? "Done" : "Edit"}<kbd>E</kbd>
        </button>
        ${editing &&
        html`
          <form id="save-preset" onSubmit=${savePreset}>
            <input
              id="preset-label"
              aria-label="New preset label"
              aria-keyshortcuts="N"
              type="text"
              maxlength="40"
              required
              placeholder="Name the current setpoints"
              value=${label}
              onInput=${(e) => setLabel(e.target.value)}
            />
            <button type="submit">Store</button>
          </form>
        `}
        <ul id="presets" class=${editing ? "editing" : ""}>
          ${presets.map(
            (preset, index) => html`
              <${Preset}
                key=${preset.label}
                preset=${preset}
                index=${index}
                last=${index === presets.length - 1}
                live=${live}
                active=${isActive(preset)}
                onApply=${applyPreset}
                onMove=${movePreset}
                onDelete=${deletePreset}
              />
            `,
          )}
        </ul>
      </fieldset>

    </main>
  `;
}

render(html`<${App} />`, document.getElementById("root"));
