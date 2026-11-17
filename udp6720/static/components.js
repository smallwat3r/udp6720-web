// Presentational pieces of the faceplate, they hold no application state
import { html, useEffect, useRef } from "./vendor/preact-htm-3.1.1.js";

export const Icon = ({ name }) => html`<svg class="icon" aria-hidden="true"><use href=${`#i-${name}`} /></svg>`;

// One window of the display: the value is drawn over a ghost of every segment
export function Readout({ quantity, state, sub, subId }) {
  const blank = quantity.ghost.replace(/8/g, "-");
  return html`
    <div class=${`cell ${quantity.q}`}>
      <div class="cell-head">
        <span class="legend">${quantity.title}</span>
        <span class="num" id=${subId}>${sub}</span>
      </div>
      <div class="segments">
        <span class="digits">
          <span class="ghost" aria-hidden="true">${quantity.ghost}</span>
          <span class="lit" id=${quantity.key}>${state ? state[quantity.key].toFixed(quantity.digits) : blank}</span>
        </span>
        <small>${quantity.letter}</small>
      </div>
    </div>
  `;
}

export function Confirm({ request, onDone }) {
  const dialog = useRef();
  useEffect(() => {
    if (!request) return;
    dialog.current.returnValue = "";
    dialog.current.showModal();
    dialog.current.querySelector(".primary").focus();
  }, [request]);
  return html`
    <dialog
      id="confirm"
      ref=${dialog}
      aria-labelledby="confirm-title"
      aria-describedby="confirm-detail"
      onClose=${() => onDone(dialog.current.returnValue === "ok")}
    >
      <h2 id="confirm-title">${request && request.title}</h2>
      <p id="confirm-detail" class="num" hidden=${!(request && request.detail)}>${request && request.detail}</p>
      <form method="dialog">
        <button value="cancel">Cancel<kbd>Esc</kbd></button>
        <button class="primary" value="ok">${request && request.action}<kbd>Enter</kbd></button>
      </form>
    </dialog>
  `;
}

export function Preset({ preset, index, last, live, active, onApply, onMove, onDelete }) {
  const values = `${preset.volts.toFixed(2)} V, ${preset.amps.toFixed(3)} A`;
  const shortcut = index < 9 ? String(index + 1) : null;
  return html`
    <li data-label=${preset.label}>
      <button
        type="button"
        class="use"
        disabled=${!live}
        title=${`${preset.label}: ${values}`}
        aria-current=${active ? "true" : null}
        aria-keyshortcuts=${shortcut}
        onClick=${() => onApply(preset)}
      >
        ${shortcut && html`<kbd>${shortcut}</kbd>`}
        <div><span>${preset.label}</span><span class="sub">${values}</span></div>
        <i class=${`lamp ${active ? "lit" : ""}`}></i>
      </button>
      <button
        type="button"
        class="edit"
        data-move="-1"
        title="Move earlier"
        aria-label=${`Move earlier: ${preset.label}`}
        disabled=${index === 0}
        onClick=${() => onMove(preset, -1)}
      >
        <${Icon} name="left" />
      </button>
      <button
        type="button"
        class="edit"
        data-move="1"
        title="Move later"
        aria-label=${`Move later: ${preset.label}`}
        disabled=${last}
        onClick=${() => onMove(preset, 1)}
      >
        <${Icon} name="right" />
      </button>
      <button
        type="button"
        class="edit"
        title="Delete preset"
        aria-label=${`Delete preset: ${preset.label}`}
        onClick=${() => onDelete(preset, values)}
      >
        <${Icon} name="trash" />
      </button>
    </li>
  `;
}

const SHORTCUTS = [
  [["O"], "Toggle the output, turning on asks first"],
  [["0"], "Output off immediately"],
  [["V"], "Edit the voltage setpoint"],
  [["A"], "Edit the current limit"],
  [["Enter"], "Apply the setpoints"],
  [["Esc"], "Cancel an edit or close a dialog"],
  [["1", "9"], "Apply that preset, the output goes off"],
  [["N"], "Name and store the current setpoints"],
  [["E"], "Edit presets: store, reorder, delete"],
  [["←", "→"], "Move the selected preset while editing"],
  [["?"], "Show this list"],
];

export function Help({ open, onClose }) {
  const dialog = useRef();
  useEffect(() => {
    if (open) dialog.current.showModal();
  }, [open]);
  return html`
    <dialog id="help" ref=${dialog} aria-labelledby="help-title" onClose=${onClose}>
      <h2 id="help-title">Keyboard shortcuts</h2>
      <dl>
        ${SHORTCUTS.map(
          ([keys, text]) => html`
            <div>
              <dt>${keys.map((key) => html`<kbd>${key}</kbd>`)}</dt>
              <dd>${text}</dd>
            </div>
          `,
        )}
      </dl>
      <form method="dialog"><button class="primary">Close<kbd>Esc</kbd></button></form>
    </dialog>
  `;
}
