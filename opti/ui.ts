import { currentGame, defineModule, ui, type ModuleCtx } from "invasor";

// OptiScaler: the selected game's OptiScaler.ini, built from the file itself.
// Game: that file, plus installing OptiScaler in the game (the nightly Global downloaded, or HelixSR
// Scaler as Global compiled it) and the launch line to copy or apply. Global: the OptiScaler nightly the
// module has downloaded (and a button to get the latest), HelixSR (a button to compile it) and the custom DLLs.
// Credits last.
// Everything sits in a folded "All Configuration" group (a switch hides the keys still at OptiScaler's
// defaults), then one foldable group per [Section]
// (X folds the one under the cursor), one control per key: a list when the comments above
// the key name its values, else a text field. Y shows the key's description (the comments)
// under it. Every change is written at once.

interface Key {
  key: string;
  value: string;
  kind: "select" | "text";
  options: { value: string; label: string }[];
  description: string;
  default?: string | null; // Game only: OptiScaler's own value for the key (null: its file hasn't the key)
}

interface Loaded {
  state: "no_prefix" | "no_ini" | "ok";
  path: string | null;
  mtime: string | null;
  sections: { name: string; keys: Key[] }[];
  // Game only: OptiScaler in the game's prefix, the nightly Global has downloaded and the HelixSR Scaler it compiled.
  install?: Install | null;
  build?: Build | null;
  helixsr?: Build | null;
  custom?: Record<string, boolean>; // the custom slots set in Global
}

interface CustomFile {
  name: string;
  product: string | null;
  version: string | null;
}

/** Each slot: the files the module keeps, or null when it isn't set. */
type CustomStatus = Record<string, CustomFile[] | null>;

// DLLs the user brings (kept by the module, turned on per game; HelixSR Scaler gets them when it's installed).
const SLOTS = [
  {
    id: "fsr4",
    title: "FSR 4 (AMD driver DLLs)",
    choose: "Choose amdxcffx64.dll…",
    hint: "amdxcffx64.dll from AMD's driver, and amdvkffx64.dll if it's next to it (for INT8 RDNA2 and INT8 Lite)",
    toggle: "FSR 4 driver DLLs",
  },
];

const SCALER = "helixsr-scaler";
/** What a game's OptiScaler is called, by its flavor. */
const flavorName = (flavor: string | null | undefined) => (flavor === SCALER ? "HelixSR Scaler" : "OptiScaler");
/** "HelixSR Scaler (HelixSR v3.1.0)" or "OptiScaler v10.0.0-pre1 (2026-10-23)" */
function flavorBuild(flavor: string | null | undefined, b: Build) {
  return flavor === SCALER ? `HelixSR Scaler (HelixSR ${b.tag})` : `OptiScaler ${buildName(b)}`;
}

/** "amdxcffx64.dll 2.4.0.4370 · amdvkffx64.dll 2.4.0.4370" */
function filesText(files: CustomFile[]) {
  return files.map((f) => [f.name, f.version].filter(Boolean).join(" ")).join(" · ");
}

interface Install {
  installed: boolean;
  version: Build | null;
  dll: string | null;
  flavor: string | null; // "optiscaler" or "helixsr-scaler" (null: not installed)
  extras?: Record<string, boolean>;
  launch_ok?: boolean | null; // the game's launch options load it as `dll` (null: nothing to tell)
  can_apply?: boolean; // "Apply to the game" can write them (Steam has the call)
}

interface Build {
  tag: string;
  version: string | null;
  date: string | null;
}

// A list is saved once its value stays put this long: going through it with ←→ doesn't write
// every value on the way.
const SAVE_DELAY = 700;

const sigOf = (...parts: unknown[]) => JSON.stringify(parts);
const errorText = (e: unknown) => String((e as Error)?.message ?? e);

/** The part of a game's path worth showing: …/compatdata/… */
function shortPath(path: string | null) {
  if (!path) return "";
  const game = path.indexOf("/compatdata/");
  return game >= 0 ? `…${path.slice(game)}` : path;
}

/** "v10.0.0-pre1 (2026-10-23)" */
function buildName(b: Build) {
  const d = b.date && /^\d{8}$/.test(b.date) ? ` (${b.date.slice(0, 4)}-${b.date.slice(4, 6)}-${b.date.slice(6)})` : "";
  return `${b.version || b.tag}${d}`;
}

let lastFolder: string | undefined; // where the DLL picker opens next time, while the panel lives

/** A .dll picked in the core's file picker (ctx.pickPath), or null when the user closed it. */
async function pickDll(ctx: ModuleCtx, title: string): Promise<string | null> {
  const path = await ctx.pickPath({ title, files: true, ext: [".dll"], start: lastFolder });
  if (path) lastFolder = path.slice(0, path.lastIndexOf("/")) || "/";
  return path;
}

/** What the "Hide default values" switch hides: a key's control, and whether its value is OptiScaler's own. */
interface Filterable {
  el: HTMLElement;
  isDefault: boolean;
}

/** True if value is OptiScaler's own for the key (ini values aren't case-sensitive). */
function isDefault(def: string | null | undefined, value: string) {
  return def != null && value.toLowerCase() === def.toLowerCase();
}

/** A foldable group that tells `remember` whether the user left it open (kept while the panel lives). */
function foldable(title: string, children: HTMLElement[], open: boolean, remember: (open: boolean) => void) {
  const el = ui.section(title, children, { open });
  el.addEventListener("toggle", () => remember((el as HTMLDetailsElement).open));
  return el;
}

/** First line of the description, for the hint bar. */
function summary(description: string) {
  return description.split("\n")[0] ?? "";
}

/**
 * One page that edits one file: how it loads and writes it, and what it remembers while the
 * panel lives (which groups are open, the file's mtime, the queue of writes).
 */
class IniPage {
  el: HTMLElement | null = null;
  private sig = "";
  private shown: Loaded | null = null; // the load the page shows (its mtime follows our own writes)
  // Writes go one after another, each with the mtime the one before left (else the second
  // would look like another program's change).
  private writes: Promise<void> = Promise.resolve();
  private openSections = new Set<string>();
  private configOpen = false;
  private hideDefaults = false;
  // What the "Hide default values" switch hides or shows, from the last build of the page.
  private filterable: { section: HTMLElement; keys: Filterable[] }[] = [];
  private allDefault: HTMLElement | null = null;

  constructor(
    private load: (ctx: ModuleCtx) => Promise<Loaded>,
    private setMethod: string,
  ) {}

  reset() {
    this.el = null;
    this.sig = "";
    this.shown = null;
    this.openSections.clear();
    this.configOpen = false;
    this.hideDefaults = false;
  }

  /** False when the page already shows `sig` (nothing to rebuild: the focus ring stays put). */
  changed(sig: string, force: boolean) {
    if (!force && sig === this.sig) return false;
    this.sig = sig;
    return true;
  }

  /** What tells one load from another, for changed(). */
  private sigFor(key: unknown, l: Loaded | null, problem: string | null) {
    return sigOf(key, l?.state, l?.path, l?.mtime, problem, l?.install, l?.build, l?.helixsr);
  }

  /** The file, or null when the page already shows it as it is now. */
  async content(ctx: ModuleCtx, key: unknown, force: boolean): Promise<{ loaded: Loaded | null; problem: string | null } | null> {
    let loaded: Loaded | null = null;
    let problem: string | null = null;
    try {
      loaded = await this.load(ctx);
    } catch (e) {
      problem = errorText(e);
    }
    if (!this.changed(this.sigFor(key, loaded, problem), force)) return null;
    this.shown = loaded;
    return { loaded, problem };
  }

  /** Take a load made after a change of our own as what the page shows (no rebuild for it). */
  accept(key: unknown, loaded: Loaded) {
    this.shown = loaded;
    this.sig = this.sigFor(key, loaded, null);
  }

  /** "All Configuration" with every [Section] of the file. */
  configuration(ctx: ModuleCtx, loaded: Loaded, key: unknown, extra: Record<string, unknown>, rerender: () => void): HTMLElement[] {
    const parts: HTMLElement[] = [];
    if (!loaded.sections.length) parts.push(ui.info("This file has no options (key=value lines)."));
    this.filterable = [];
    this.allDefault = ui.info("Every option has OptiScaler's default value.");
    const sections = loaded.sections.map((s) => this.section(ctx, s, key, extra, rerender));
    parts.push(foldable("All Configuration", [this.allDefault, ...sections], this.configOpen, (open) => void (this.configOpen = open)));
    this.applyFilter();
    return parts;
  }

  /** The switch that hides the keys still at OptiScaler's default value (and the groups left empty). */
  defaultsSwitch() {
    return ui.toggle({
      label: "Hide default values",
      value: this.hideDefaults,
      hint: "Show only the options changed from OptiScaler's defaults",
      onChange: (on) => {
        this.hideDefaults = on;
        this.applyFilter();
      },
    });
  }

  private applyFilter() {
    let shown = 0;
    for (const s of this.filterable) {
      let any = false;
      for (const k of s.keys) {
        const hide = this.hideDefaults && k.isDefault;
        k.el.style.display = hide ? "none" : "";
        any ||= !hide;
      }
      s.section.style.display = any ? "" : "none";
      if (any) shown++;
    }
    if (this.allDefault) this.allDefault.style.display = this.hideDefaults && this.filterable.length && !shown ? "" : "none";
  }

  private section(ctx: ModuleCtx, s: Loaded["sections"][number], key: unknown, extra: Record<string, unknown>, rerender: () => void) {
    const title = s.name || "(no section)";
    const entries = s.keys.map((k) => {
      // What's written counts the next time the switch is used: nothing vanishes under the cursor.
      const el = this.control(ctx, s.name, k, key, extra, rerender, (value) => void (entry.isDefault = isDefault(k.default, value)));
      const entry: Filterable = { el, isDefault: isDefault(k.default, k.value) };
      return entry;
    });
    const keys = entries.map((e) => e.el);
    const el = foldable(title, keys, this.openSections.has(title), (open) => void (open ? this.openSections.add(title) : this.openSections.delete(title)));
    this.filterable.push({ section: el, keys: entries });
    return el;
  }

  /** One key's control; `written` gets every value written to the file. */
  private control(ctx: ModuleCtx, section: string, k: Key, pageKey: unknown, extra: Record<string, unknown>, rerender: () => void, written: (value: string) => void): HTMLElement {
    let current = k.value;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let latest = 0; // only the newest change may put its result on screen
    const write = async (value: string, undo: (v: string) => void) => {
      try {
        const r = await ctx.call<{ value: string; mtime: string }>(this.setMethod, { ...extra, section, key: k.key, value, mtime: this.shown?.mtime });
        if (this.shown) this.shown.mtime = r.mtime;
        this.sig = this.sigFor(pageKey, this.shown, null); // our own write: no need to rebuild
        current = r.value;
        written(r.value);
        undo(r.value); // shows what was written (e.g. an empty text is "auto")
      } catch (e) {
        undo(current);
        const msg = errorText(e);
        ctx.toast(`${k.key}: ${msg}`, "error");
        if (msg.includes("changed by another program")) rerender();
      }
    };
    const save = (value: string, show: (v: string) => void, delay = 0) => {
      clearTimeout(timer);
      const mine = ++latest;
      const undo = (v: string) => mine === latest && show(v);
      timer = setTimeout(() => void (this.writes = this.writes.then(() => write(value, undo))), delay);
    };
    const hint = [k.description ? "Y description" : "", summary(k.description)].filter(Boolean).join(" · ");
    let control: HTMLElement;
    if (k.kind === "select") {
      const value = k.options.find((o) => o.value.toLowerCase() === k.value.toLowerCase())?.value ?? k.options[0].value;
      const c = ui.select<string>({ label: k.key, value, options: k.options, hint, onChange: (v) => save(v, (w) => c.set(w), SAVE_DELAY) });
      control = c;
    } else {
      const c = ui.text({ label: k.key, value: k.value, placeholder: "auto", maxLength: 1024, hint, onChange: (v) => save(v, (w) => c.set(w)) });
      control = c;
    }
    if (!k.description) return control;

    const box = document.createElement("div");
    const desc = ui.info(k.description);
    desc.style.whiteSpace = "pre-line";
    desc.hidden = true;
    control.addEventListener("invasor:button", (e) => {
      if (e.detail.button !== "Y") return;
      desc.hidden = !desc.hidden;
      e.preventDefault();
    });
    box.append(control, desc);
    return box;
  }
}

// ---------- Game ----------

const game = new IniPage(
  async (ctx) => {
    const t = currentGame(ctx.game());
    return ctx.call<Loaded>("load", { appid: t?.game.appid });
  },
  "set",
);

async function renderGame(ctx: ModuleCtx, force = false) {
  const el = game.el;
  if (!el) return;
  const t = currentGame(ctx.game());
  if (!t) {
    if (game.changed(sigOf(null), force)) el.replaceChildren(ui.info("Open or highlight a game in the Library."));
    return;
  }
  const got = await game.content(ctx, t.game.appid, force);
  if (!got) return;
  const { loaded, problem } = got;
  const parts: HTMLElement[] = [ui.info(`${t.game.name ?? `App ${t.game.appid}`} (${t.how})`)];
  if (problem) parts.push(ui.info(problem));
  else if (loaded?.state === "no_prefix") parts.push(ui.info("This game has no Proton prefix yet: run it once with Proton."));
  else if (loaded) {
    parts.push(ui.info(loaded.state === "ok" ? shortPath(loaded.path) : `No OptiScaler.ini in ${shortPath(loaded.path)}`));
    parts.push(ui.separator(), ...installation(ctx, t.game.appid, loaded), ui.separator());
    if (loaded.state === "ok") {
      parts.push(game.defaultsSwitch(), ...game.configuration(ctx, loaded, t.game.appid, { appid: t.game.appid }, () => void renderGame(ctx, true)));
    }
  }
  el.replaceChildren(...parts);
}

const DLLS = ["dxgi", "d3d12", "dbghelp", "winmm"];
/** The launch line after 🟢 when the game's launch options have it, 🔴 when not (nothing when unknown). */
function launchLineText(line: string, ok: boolean | null) {
  return ok === null ? line : `${ok ? "🟢" : "🔴"} ${line}`;
}
// The DLL picked for a game before installing, and whether "Installation" is open, while the panel lives.
const chosenDll = new Map<string, string>();
let installOpen = false;

/** The launch line (when installed) and the folded "Installation" group. */
function installation(ctx: ModuleCtx, key: unknown, loaded: Loaded): HTMLElement[] {
  const appid = String(key);
  const inst = loaded.install;
  if (!inst) return [];
  const parts: HTMLElement[] = [];
  const launchLine = (dll: string) => `WINE_OPTISCALER_NAME=${dll}.dll %command%`;
  let line = inst.dll ? launchLine(inst.dll) : "";
  let lineInfo: HTMLElement | null = null; // on the page only while a DLL name loads OptiScaler
  if (inst.installed && inst.dll) {
    lineInfo = ui.info(launchLineText(line, inst.launch_ok ?? null));
    parts.push(
      ui.info("The game's launch options need at least this line, and the game must run with GE-Proton or Proton-CachyOS:"),
      lineInfo,
      ui.button({
        label: "Copy",
        hint: "Copy it, to paste in the game's launch options (🟢 they have it, 🔴 they don't)",
        onClick: async () => ((await ctx.copy(line)) ? ctx.toast("Copied") : ctx.toast("Couldn't copy it", "error")),
      }),
    );
    if (inst.can_apply) {
      parts.push(
        ui.button({
          label: "Apply to the game",
          hint: "Write it into the game's launch options in Steam; the rest of the line stays",
          onClick: () => void applyLaunch(ctx, appid),
        }),
      );
    }
  } else if (inst.installed) {
    parts.push(ui.info(`${flavorName(inst.flavor)} is installed, but no DLL name loads it: pick one under Installation.`));
  }

  const children: HTMLElement[] = [];
  if (inst.installed) {
    const name = flavorName(inst.flavor);
    children.push(ui.info(inst.version ? `Installed: ${flavorBuild(inst.flavor, inst.version)}` : `Installed: ${name} (put there by hand: version unknown)`));
    // A newer (or just another) build of its flavor in Global, or one put there by hand: it can be brought to Global's.
    const build = inst.flavor === SCALER ? loaded.helixsr : loaded.build;
    if (build && build.tag !== inst.version?.tag) {
      children.push(
        ui.button({
          label: `Update to ${flavorBuild(inst.flavor, build)}`,
          hint: "The one in Global. The game keeps its OptiScaler.ini, its DLL name, its custom DLLs and anything you added",
          onClick: () => void gameAction(ctx, "game_update", { appid }, `${name} updated`),
        }),
      );
    }
    children.push(
      ui.button({
        label: `Uninstall ${name}`,
        hint: inst.flavor === SCALER
          ? "Removes OptiScaler.dll, OptiScaler.ini, the OptiScaler, HelixSR and Licenses folders and the DLL link"
          : "Removes OptiScaler.dll, OptiScaler.ini, the OptiScaler folder and the DLL link",
        onClick: () => void uninstallGame(ctx, appid, inst.flavor),
      }),
    );
  } else {
    const dll = () => chosenDll.get(appid) ?? DLLS[0];
    const opti = ui.button({
      label: "Install OptiScaler",
      hint: loaded.build ? `OptiScaler ${buildName(loaded.build)}, the one downloaded in Global, with its own OptiScaler.ini` : "",
      onClick: () => void gameAction(ctx, "game_install", { appid, dll: dll() }, "OptiScaler installed"),
    });
    if (!loaded.build) opti.setDisabled(true, "Download OptiScaler in Global first");
    const scaler = ui.button({
      label: "Install HelixSR Scaler",
      hint: loaded.helixsr
        ? `OptiScaler with HLX DLSS and HLX FSR 4 (HelixSR ${loaded.helixsr.tag}, compiled in Global), with its own OptiScaler.ini and the FSR 4 driver DLLs if they're in Global`
        : "",
      onClick: () => void gameAction(ctx, "game_install", { appid, dll: dll(), flavor: SCALER }, "HelixSR Scaler installed"),
    });
    if (!loaded.helixsr) scaler.setDisabled(true, "Compile HelixSR in Global first");
    children.push(opti, scaler);
  }
  let current = (inst.installed && inst.dll) || chosenDll.get(appid) || DLLS[0];
  let timer: ReturnType<typeof setTimeout> | undefined;
  // Installed: the link is renamed once the value stays put (going through the list with ←→
  // doesn't rename it at each step), and only the launch line changes on the page.
  const rename = async (dll: string) => {
    try {
      const r = await ctx.call<Loaded>("game_set_dll", { appid, dll });
      game.accept(key, r);
      current = dll;
      line = launchLine(dll);
      if (lineInfo) lineInfo.textContent = launchLineText(line, r.install?.launch_ok ?? null);
      else void renderGame(ctx, true); // the line wasn't on the page yet
    } catch (e) {
      select.set(current);
      ctx.toast(errorText(e), "error");
    }
  };
  const select = ui.select<string>({
    label: "DLL to load",
    value: current,
    options: DLLS.map((d) => ({ value: d, label: `${d}.dll` })),
    hint: "The name the game loads OptiScaler as",
    onChange: (dll) => {
      chosenDll.set(appid, dll);
      if (!inst.installed) return;
      clearTimeout(timer);
      timer = setTimeout(() => void rename(dll), SAVE_DELAY);
    },
  });
  children.push(select);
  if (inst.installed) {
    for (const slot of SLOTS) {
      const on = !!inst.extras?.[slot.id];
      const toggle = ui.toggle({
        label: slot.toggle,
        value: on,
        hint: slot.hint,
        onChange: (v) => void gameAction(ctx, "game_extra", { appid, slot: slot.id, on: v }, `${slot.toggle}: ${v ? "on" : "off"}`),
      });
      if (!on && !loaded.custom?.[slot.id]) toggle.setDisabled(true, "Choose the DLL in Global first (Custom DLLs)");
      children.push(toggle);
    }
  }
  parts.push(foldable("Installation", children, installOpen, (open) => void (installOpen = open)));
  return parts;
}

async function applyLaunch(ctx: ModuleCtx, appid: string) {
  try {
    const r = await ctx.call<{ verified: boolean }>("game_apply_launch", { appid });
    ctx.toast(r.verified ? "Launch options applied" : "Applied, but Steam couldn't confirm it");
  } catch (e) {
    ctx.toast(errorText(e), "error");
  }
  void renderGame(ctx, true);
}

async function gameAction(ctx: ModuleCtx, method: string, args: Record<string, unknown>, done: string) {
  try {
    await ctx.call(method, args);
    ctx.toast(done);
  } catch (e) {
    ctx.toast(errorText(e), "error");
  }
  void renderGame(ctx, true);
}

async function uninstallGame(ctx: ModuleCtx, appid: string, flavor: string | null) {
  const name = flavorName(flavor);
  const folders = flavor === SCALER ? "the whole OptiScaler, HelixSR and Licenses folders go" : "the whole OptiScaler folder goes";
  if (!(await ui.confirm(`Remove ${name} from this game? Its OptiScaler.ini and ${folders} too (with anything you added there).`, { ok: "Uninstall" }))) return;
  await gameAction(ctx, "game_uninstall", { appid }, `${name} uninstalled`);
}

// ---------- Global ----------

interface NightlyStatus {
  installed: Build | null;
  busy: boolean;
  error: string | null;
}

const POLL = 2000;

interface HelixStatus {
  installed: Build | null;
  busy: boolean;
  error: string | null;
}

interface HelixCheck {
  installed: string | null;
  latest: string | null;
  update: boolean;
}

const BUILD_LABEL = "Download and compile";

/** The build button's label and the line above it, from the last check (null: not checked yet). */
function buildText(check: HelixCheck | null): { label: string; line: string } {
  if (!check?.latest) return { label: BUILD_LABEL, line: "" };
  if (!check.installed) return { label: BUILD_LABEL, line: `Latest stable HelixSR: ${check.latest}` };
  if (check.update) return { label: `Update to ${check.latest}`, line: `HelixSR ${check.latest} is out` };
  return { label: "Recompile", line: `Up to date: ${check.latest} is the latest stable HelixSR` };
}

/**
 * The "OptiScaler" group at the top of Global: the nightly downloaded and a button to get the
 * latest. Built once per panel and kept across the page's rebuilds: it only changes its own text.
 */
class NightlyBox {
  el: HTMLElement | null = null;
  private open = true;
  private version: HTMLElement | null = null;
  private problem: HTMLElement | null = null;
  private button: ReturnType<typeof ui.button> | null = null;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private busy = false;
  private last: [Build | null, boolean, string | null] | null = null; // for an element built after the status came

  element(ctx: ModuleCtx) {
    if (this.el) return this.el;
    this.version = ui.info("Looking for the downloaded version…");
    this.problem = ui.info("");
    this.problem.hidden = true;
    this.button = ui.button({
      label: "Download the latest nightly",
      hint: "From github.com/optiscaler/OptiScaler-nightly, into this module's files",
      onClick: () => void this.download(ctx),
    });
    this.el = foldable("OptiScaler", [this.version, this.button, this.problem], this.open, (open) => void (this.open = open));
    if (this.last) this.show(...this.last);
    return this.el;
  }

  reset() {
    clearTimeout(this.timer);
    this.el = this.version = this.problem = this.button = null;
    this.last = null;
    this.open = true;
    this.busy = false;
  }

  async refresh(ctx: ModuleCtx) {
    clearTimeout(this.timer);
    let s: NightlyStatus;
    try {
      s = await ctx.call<NightlyStatus>("nightly_status");
    } catch (e) {
      this.show(null, false, errorText(e));
      return;
    }
    const finished = this.busy && !s.busy;
    this.show(s.installed, s.busy, s.error);
    if (finished) ctx.toast(s.error ? `OptiScaler: ${s.error}` : `OptiScaler ${s.installed ? buildName(s.installed) : ""}`, s.error ? "error" : "ok");
    if (s.busy) this.timer = setTimeout(() => void this.refresh(ctx), POLL);
  }

  private show(build: Build | null, busy: boolean, error: string | null) {
    this.busy = busy;
    this.last = [build, busy, error];
    if (!this.version || !this.problem || !this.button) return;
    this.version.textContent = build ? `Downloaded: OptiScaler ${buildName(build)}` : "No OptiScaler version downloaded yet.";
    this.problem.textContent = error ?? "";
    this.problem.hidden = !error || busy;
    this.button.setLabel(busy ? "Downloading…" : "Download the latest nightly");
    this.button.setDisabled(busy, busy ? "Downloading: see the Tasks panel (Select)" : undefined);
  }

  private async download(ctx: ModuleCtx) {
    try {
      const r = await ctx.call<{ started: boolean; busy: boolean }>("nightly_download");
      if (r.started) {
        this.busy = true;
        ctx.toast("Downloading the latest OptiScaler nightly");
      }
    } catch (e) {
      ctx.toast(errorText(e), "error");
    }
    void this.refresh(ctx);
  }
}

const nightlyBox = new NightlyBox();

/**
 * The "HelixSR" group in Global: the HelixSR Scaler compiled (with HLX DLSS and HLX FSR 4) and a button to
 * compile the latest stable HelixSR (the build runs in the backend, followed until it ends). Kept across rebuilds.
 */
class HelixBox {
  el: HTMLElement | null = null;
  private open = true;
  private version: HTMLElement | null = null;
  private build: ReturnType<typeof ui.button> | null = null;
  private latest: HTMLElement | null = null;
  private problem: HTMLElement | null = null;
  private installed: Build | null = null;
  private check: HelixCheck | null = null;
  private error: string | null = null;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private busy = false;

  element(ctx: ModuleCtx) {
    if (this.el) return this.el;
    this.version = ui.info("Looking for the compiled version…");
    this.build = ui.button({
      label: BUILD_LABEL,
      hint: "The latest stable HelixSR from github.com/lonewolf0622/HelixSR, built on this device: HelixSR Scaler with HLX DLSS and HLX FSR 4",
      onClick: () => void this.startBuild(ctx),
    });
    this.latest = ui.info("");
    this.latest.hidden = true;
    this.problem = ui.info("");
    this.problem.hidden = true;
    this.el = foldable("HelixSR", [this.version, this.latest, this.build, this.problem], this.open, (open) => void (this.open = open));
    this.showBuild(this.busy, this.error);
    return this.el;
  }

  reset() {
    clearTimeout(this.timer);
    this.el = this.version = this.build = this.latest = this.problem = null;
    this.installed = this.check = this.error = null;
    this.open = true;
    this.busy = false;
  }

  refresh(ctx: ModuleCtx) {
    void this.refreshBuild(ctx);
    void this.refreshCheck(ctx);
  }

  /** Is there a newer HelixSR? The backend asks GitHub at most once an hour; offline, the button just builds. */
  private async refreshCheck(ctx: ModuleCtx) {
    try {
      this.check = await ctx.call<HelixCheck>("helixsr_check");
    } catch {
      this.check = null;
    }
    this.showBuild(this.busy, this.error);
  }

  private async refreshBuild(ctx: ModuleCtx) {
    clearTimeout(this.timer);
    let s: HelixStatus;
    try {
      s = await ctx.call<HelixStatus>("helixsr_status");
    } catch (e) {
      this.showBuild(false, errorText(e));
      return;
    }
    const finished = this.busy && !s.busy;
    this.installed = s.installed;
    this.showBuild(s.busy, s.error);
    if (finished) {
      ctx.toast(s.error ? `HelixSR: ${s.error}` : `HelixSR ${s.installed?.tag ?? ""} compiled`, s.error ? "error" : "ok");
      if (!s.error) {
        void this.refreshCheck(ctx);
        void renderGame(ctx, true); // its Install button depends on it
      }
    }
    if (s.busy) this.timer = setTimeout(() => void this.refreshBuild(ctx), POLL);
  }

  private showBuild(busy: boolean, error: string | null) {
    this.busy = busy;
    this.error = error;
    if (!this.build || !this.problem || !this.version) return;
    this.version.textContent = this.installed
      ? `Compiled: HelixSR Scaler (HelixSR ${this.installed.tag})`
      : "HelixSR isn't compiled yet.";
    this.problem.textContent = error ?? "";
    this.problem.hidden = !error || busy;
    const text = buildText(this.check);
    if (this.latest) {
      this.latest.textContent = text.line;
      this.latest.hidden = !text.line || busy;
    }
    this.build.setLabel(busy ? "Compiling…" : text.label);
    this.build.setDisabled(busy, busy ? "Compiling: see the Tasks panel (Select)" : undefined);
  }

  private async startBuild(ctx: ModuleCtx) {
    const ok = await ui.confirm(
      "Download the latest stable HelixSR and compile it on this device? It takes several minutes. To build it, NVIDIA's DLSS DLL and AMD's driver package (about 420 MB, only its FSR 4 file is read) are downloaded and used once, under their licenses. The DLLs you get are for this device only: don't share them. It replaces the module's HelixSR Scaler; games that have it keep theirs until you update them.",
      { ok: "Compile" },
    );
    if (!ok) return;
    try {
      const r = await ctx.call<{ started: boolean; busy: boolean }>("helixsr_build");
      if (r.started) {
        this.busy = true;
        ctx.toast("Compiling HelixSR");
      }
    } catch (e) {
      ctx.toast(errorText(e), "error");
    }
    void this.refreshBuild(ctx);
  }
}

const helixBox = new HelixBox();

/** "Custom DLLs" in Global: one line per slot with what's kept, Choose… and Remove. Kept across rebuilds. */
class CustomBox {
  el: HTMLElement | null = null;
  private open = false;
  private rows = new Map<string, { text: HTMLElement; remove: ReturnType<typeof ui.button> }>();

  element(ctx: ModuleCtx) {
    if (this.el) return this.el;
    const children: HTMLElement[] = [];
    for (const slot of SLOTS) {
      const text = ui.info(`${slot.title}: …`);
      const choose = ui.button({
        label: slot.choose,
        hint: slot.hint,
        onClick: async () => {
          const path = await pickDll(ctx, slot.choose);
          if (path) void this.store(ctx, slot.id, path);
        },
      });
      const remove = ui.button({
        label: `Remove ${slot.title}`,
        hint: "The module's copy goes; games that have it keep theirs",
        onClick: () => void this.remove(ctx, slot.id, slot.title),
      });
      this.rows.set(slot.id, { text, remove });
      children.push(text, choose, remove);
    }
    this.el = foldable("Custom DLLs", children, this.open, (open) => void (this.open = open));
    return this.el;
  }

  reset() {
    this.el = null;
    this.rows.clear();
    this.open = false;
  }

  async refresh(ctx: ModuleCtx) {
    try {
      this.show(await ctx.call<CustomStatus>("custom_status"));
    } catch (e) {
      ctx.toast(errorText(e), "error");
    }
  }

  private show(status: CustomStatus) {
    for (const slot of SLOTS) {
      const row = this.rows.get(slot.id);
      if (!row) continue;
      const files = status[slot.id];
      row.text.textContent = `${slot.title}: ${files ? filesText(files) : "not set"}`;
      row.remove.style.display = files ? "" : "none";
    }
  }

  private async store(ctx: ModuleCtx, slot: string, path: string) {
    try {
      this.show(await ctx.call<CustomStatus>("custom_set", { slot, path }));
      ctx.toast("Kept");
      void renderGame(ctx, true); // its toggles depend on it
    } catch (e) {
      ctx.toast(errorText(e), "error");
    }
  }

  private async remove(ctx: ModuleCtx, slot: string, title: string) {
    if (!(await ui.confirm(`Remove the module's copy of ${title}? Games that have it keep theirs.`, { ok: "Remove" }))) return;
    try {
      this.show(await ctx.call<CustomStatus>("custom_remove", { slot }));
      void renderGame(ctx, true);
    } catch (e) {
      ctx.toast(errorText(e), "error");
    }
  }
}

const customBox = new CustomBox();

// The Global tab's element: its three groups, built once per panel (they keep themselves up to date).
let globalEl: HTMLElement | null = null;

function renderGlobal(ctx: ModuleCtx) {
  if (!globalEl || globalEl.childElementCount) return;
  globalEl.replaceChildren(
    ui.info("What the games get: the OptiScaler nightly, HelixSR Scaler and the DLLs you bring. Each game's OptiScaler.ini is set in Game."),
    nightlyBox.element(ctx),
    helixBox.element(ctx),
    customBox.element(ctx),
  );
}

export default defineModule({
  tabs: [
    {
      label: "Game",
      // onShow comes right after the first render too: it's the one that loads. Each time, because
      // another program (Companion, OptiScaler's own menu) may have changed the file meanwhile.
      render(el) {
        game.el = el;
      },
      onShow: (ctx) => void renderGame(ctx),
    },
    {
      label: "Global",
      render(el) {
        globalEl = el;
      },
      onShow: (ctx) => {
        renderGlobal(ctx);
        void customBox.refresh(ctx); // its rows are made by renderGlobal
        void nightlyBox.refresh(ctx);
        helixBox.refresh(ctx);
      },
    },
    {
      label: "Credits",
      render(el) {
        el.append(
          ui.info("Author: FranjeGueje"),
          ui.separator(),
          ui.info("Thanks to OptiScaler (github.com/optiscaler/OptiScaler) and every one of its contributors, and to HelixSR (github.com/lonewolf0622/HelixSR). Each keeps its own license."),
          ui.separator(),
          ui.info("This module is not affiliated with OptiScaler or HelixSR. Please don't report problems with this module to them."),
        );
      },
    },
  ],
  onGameChange: (_game, ctx) => void renderGame(ctx),
  destroy: () => {
    game.reset();
    globalEl = null;
    chosenDll.clear();
    installOpen = false;
    nightlyBox.reset();
    helixBox.reset();
    customBox.reset();
    lastFolder = undefined;
  },
});
