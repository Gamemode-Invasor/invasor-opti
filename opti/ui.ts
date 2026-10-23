import { currentGame, defineModule, ui, type ModuleCtx, type WindowHandle, type WindowSpec } from "invasor";

// OptiScaler: OptiScaler.ini files, built from the files themselves.
// Game: the selected game's, plus installing OptiScaler in it (the build Global downloaded) and the
// launch line to copy. Global: the module's own, the one a game gets, with two resets, under the
// OptiScaler nightly the module has downloaded (and a button to get the latest) and the custom DLLs.
// Credits last.
// Everything sits in a folded "Configuration" group, then one foldable group per [Section]
// (X folds the one under the cursor), one control per key: a list when the comments above
// the key name its values, else a text field. Y shows the key's description (the comments)
// under it. Every change is written at once.

interface Key {
  key: string;
  value: string;
  kind: "select" | "text";
  options: { value: string; label: string }[];
  description: string;
}

interface Loaded {
  state: "no_prefix" | "no_ini" | "ok";
  path: string | null;
  mtime: string | null;
  sections: { name: string; keys: Key[] }[];
  // Game only: OptiScaler in the game's prefix, and the build Global has downloaded.
  install?: Install | null;
  build?: Build | null;
  custom?: Record<string, boolean>; // the custom slots set in Global
}

interface CustomFile {
  name: string;
  product: string | null;
  version: string | null;
}

/** Each slot: the files the module keeps, or null when it isn't set. */
type CustomStatus = Record<string, CustomFile[] | null>;

// DLLs the user brings (kept by the module, turned on per game).
const SLOTS = [
  {
    id: "fsr4",
    title: "FSR 4 (AMD driver DLLs)",
    choose: "Choose amdxcffx64.dll…",
    hint: "amdxcffx64.dll from AMD's driver, and amdvkffx64.dll if it's next to it (for INT8 RDNA2 and INT8 Lite)",
    toggle: "FSR 4 driver DLLs",
  },
  {
    id: "helixsr",
    title: "HelixSR",
    choose: "Choose helixsr.dll…",
    hint: "HelixSR's DLL, and helixsr.ini if it's next to it",
    toggle: "HelixSR",
  },
];

/** "amdxcffx64.dll 2.4.0.4370 · amdvkffx64.dll 2.4.0.4370" */
function filesText(files: CustomFile[]) {
  return files.map((f) => [f.name, f.version].filter(Boolean).join(" ")).join(" · ");
}

interface Install {
  installed: boolean;
  version: Build | null;
  dll: string | null;
  extras?: Record<string, boolean>;
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

/** The part of a path worth showing: …/compatdata/… (a game's), or ~/.local/share/… (the Global one). */
function shortPath(path: string | null) {
  if (!path) return "";
  const game = path.indexOf("/compatdata/");
  if (game >= 0) return `…${path.slice(game)}`;
  const share = path.indexOf("/.local/share/");
  return share >= 0 ? `~${path.slice(share)}` : path;
}

/** "v10.0.0-pre1 (2026-10-23)" */
function buildName(b: Build) {
  const d = b.date && /^\d{8}$/.test(b.date) ? ` (${b.date.slice(0, 4)}-${b.date.slice(4, 6)}-${b.date.slice(6)})` : "";
  return `${b.version || b.tag}${d}`;
}

// Steam's CEF may not give navigator.clipboard to this page: fall back to execCommand.
async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.append(area);
    area.select();
    try {
      return document.execCommand("copy");
    } catch {
      return false;
    } finally {
      area.remove();
    }
  }
}

interface Dir {
  path: string;
  parent: string | null;
  dirs: { name: string; label: string }[];
  files: { name: string }[];
  error: string | null;
}

const join = (dir: string, name: string) => (dir === "/" ? "/" : dir + "/") + name;
let lastFolder: string | undefined; // where the browser opens next time, while the panel lives
let browser: WindowHandle | null = null; // the browser's window, while it's open

// File browser in a big window (Companion's): ".." goes up, a folder opens, a .dll is chosen by pressing it.
// ctx.openWindow returns null (and tells the user why) where there's no room.
function openBrowser(ctx: ModuleCtx, title: string, onPick: (path: string) => void) {
  browser?.close();
  const spec: WindowSpec = {
    title,
    async render(box, wctx) {
      async function show(path?: string) {
        let dir: Dir;
        try {
          dir = await wctx.call<Dir>("browse", path ? { path } : {});
        } catch (e) {
          box.replaceChildren(ui.info(errorText(e)));
          return;
        }
        lastFolder = dir.path;
        const items: HTMLElement[] = [ui.info(dir.path)];
        if (dir.error) items.push(ui.info(`Can't read this folder: ${dir.error}`));
        const go = (p: string) => () => void show(p);
        if (dir.parent) items.push(ui.button({ label: "..", onClick: go(dir.parent) }));
        for (const d of dir.dirs) items.push(ui.button({ label: `📁 ${d.label}`, onClick: go(join(dir.path, d.name)) }));
        for (const f of dir.files) {
          items.push(
            ui.button({
              label: f.name,
              onClick: () => {
                onPick(join(dir.path, f.name));
                browser?.close();
              },
            }),
          );
        }
        if (!dir.dirs.length && !dir.files.length && !dir.error) items.push(ui.info("No folders or .dll files here."));
        box.replaceChildren(...items);
      }
      await show(lastFolder);
    },
    onClose: () => void (browser = null),
  };
  browser = ctx.openWindow(spec);
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
  }

  /** False when the page already shows `sig` (nothing to rebuild: the focus ring stays put). */
  changed(sig: string, force: boolean) {
    if (!force && sig === this.sig) return false;
    this.sig = sig;
    return true;
  }

  /** What tells one load from another, for changed(). */
  private sigFor(key: unknown, l: Loaded | null, problem: string | null) {
    return sigOf(key, l?.state, l?.path, l?.mtime, problem, l?.install, l?.build);
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

  /** "Configuration" with every [Section] of the file. */
  configuration(ctx: ModuleCtx, loaded: Loaded, key: unknown, extra: Record<string, unknown>, rerender: () => void): HTMLElement[] {
    const parts: HTMLElement[] = [];
    if (!loaded.sections.length) parts.push(ui.info("This file has no options (key=value lines)."));
    const sections = loaded.sections.map((s) => this.section(ctx, s, key, extra, rerender));
    parts.push(foldable("Configuration", sections, this.configOpen, (open) => void (this.configOpen = open)));
    return parts;
  }

  private section(ctx: ModuleCtx, s: Loaded["sections"][number], key: unknown, extra: Record<string, unknown>, rerender: () => void) {
    const title = s.name || "(no section)";
    const keys = s.keys.map((k) => this.control(ctx, s.name, k, key, extra, rerender));
    return foldable(title, keys, this.openSections.has(title), (open) => void (open ? this.openSections.add(title) : this.openSections.delete(title)));
  }

  private control(ctx: ModuleCtx, section: string, k: Key, pageKey: unknown, extra: Record<string, unknown>, rerender: () => void): HTMLElement {
    let current = k.value;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let latest = 0; // only the newest change may put its result on screen
    const write = async (value: string, undo: (v: string) => void) => {
      try {
        const r = await ctx.call<{ value: string; mtime: string }>(this.setMethod, { ...extra, section, key: k.key, value, mtime: this.shown?.mtime });
        if (this.shown) this.shown.mtime = r.mtime;
        this.sig = this.sigFor(pageKey, this.shown, null); // our own write: no need to rebuild
        current = r.value;
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
      parts.push(...game.configuration(ctx, loaded, t.game.appid, { appid: t.game.appid }, () => void renderGame(ctx, true)));
    }
  }
  el.replaceChildren(...parts);
}

const DLLS = ["dxgi", "d3d12", "dbghelp", "winmm"];
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
    lineInfo = ui.info(line);
    parts.push(
      ui.info("The game's launch options need at least this line, and the game must run with GE-Proton or Proton-CachyOS:"),
      lineInfo,
      ui.button({
        label: "Copy",
        hint: "Copy it, to paste in the game's launch options",
        onClick: async () => ((await copyText(line)) ? ctx.toast("Copied") : ctx.toast("Couldn't copy it", "error")),
      }),
    );
  } else if (inst.installed) {
    parts.push(ui.info("OptiScaler is installed, but no DLL name loads it: pick one under Installation."));
  }

  const children: HTMLElement[] = [];
  let action: ReturnType<typeof ui.button>;
  if (inst.installed) {
    children.push(ui.info(inst.version ? `Installed: OptiScaler ${buildName(inst.version)}` : "Installed: OptiScaler (put there by hand: version unknown)"));
    // A newer (or just another) build in Global, or one put there by hand: it can be brought to Global's.
    if (loaded.build && loaded.build.tag !== inst.version?.tag) {
      children.push(
        ui.button({
          label: `Update to OptiScaler ${buildName(loaded.build)}`,
          hint: "The one downloaded in Global. The game keeps its OptiScaler.ini, its DLL name, its custom DLLs and anything you added",
          onClick: () => void gameAction(ctx, "game_update", { appid }, "OptiScaler updated"),
        }),
      );
    }
    action = ui.button({
      label: "Uninstall OptiScaler",
      hint: "Removes OptiScaler.dll, OptiScaler.ini, the OptiScaler folder and the DLL link",
      onClick: () => void uninstallGame(ctx, appid),
    });
  } else {
    action = ui.button({
      label: "Install OptiScaler",
      hint: loaded.build ? `OptiScaler ${buildName(loaded.build)}, the one downloaded in Global` : "",
      onClick: () => void gameAction(ctx, "game_install", { appid, dll: chosenDll.get(appid) ?? DLLS[0] }, "OptiScaler installed"),
    });
    if (!loaded.build) action.setDisabled(true, "Download OptiScaler in Global first");
  }
  children.push(action);
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
      if (lineInfo) lineInfo.textContent = line;
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

async function gameAction(ctx: ModuleCtx, method: string, args: Record<string, unknown>, done: string) {
  try {
    await ctx.call(method, args);
    ctx.toast(done);
  } catch (e) {
    ctx.toast(errorText(e), "error");
  }
  void renderGame(ctx, true);
}

async function uninstallGame(ctx: ModuleCtx, appid: string) {
  if (!(await ui.confirm("Remove OptiScaler from this game? Its OptiScaler.ini and the whole OptiScaler folder go too (with anything you added there).", { ok: "Uninstall" }))) return;
  await gameAction(ctx, "game_uninstall", { appid }, "OptiScaler uninstalled");
}

// ---------- Global ----------

interface NightlyStatus {
  installed: Build | null;
  busy: boolean;
  error: string | null;
}

const POLL = 2000;

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
        onClick: () => void openBrowser(ctx, slot.choose, (path) => void this.store(ctx, slot.id, path)),
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

const global = new IniPage((ctx) => ctx.call<Loaded>("global_load"), "global_set");

async function resetGlobal(ctx: ModuleCtx, source: "optiscaler" | "invasor", what: string) {
  if (!(await ui.confirm(`Replace every value of the Global OptiScaler.ini with ${what}?`, { ok: "Reset" }))) return;
  try {
    await ctx.call("global_reset", { source });
    ctx.toast(`Global: ${what}`);
  } catch (e) {
    ctx.toast(errorText(e), "error");
  }
  void renderGlobal(ctx, true);
}

// "Reset config", folded until the user opens it (kept while the panel lives).
let resetOpen = false;

async function renderGlobal(ctx: ModuleCtx, force = false) {
  const el = global.el;
  if (!el) return;
  const got = await global.content(ctx, "global", force);
  if (!got) return;
  const { loaded, problem } = got;
  const parts: HTMLElement[] = [nightlyBox.element(ctx), customBox.element(ctx), ui.separator(), ui.info("The OptiScaler.ini a game gets when OptiScaler is set up for it.")];
  if (problem) parts.push(ui.info(problem));
  else if (loaded?.state === "ok") {
    parts.push(ui.info(shortPath(loaded.path)));
    parts.push(...global.configuration(ctx, loaded, "global", {}, () => void renderGlobal(ctx, true)));
  }
  const reset = foldable(
    "Reset config",
    [
      ui.button({
        label: "Reset to OptiScaler defaults",
        hint: "The file as this OptiScaler version ships it (every value auto)",
        onClick: () => void resetGlobal(ctx, "optiscaler", "OptiScaler's defaults"),
      }),
      ui.button({
        label: "Reset to invasor-opti defaults",
        hint: "The file this module starts with",
        onClick: () => void resetGlobal(ctx, "invasor", "invasor-opti's defaults"),
      }),
    ],
    resetOpen,
    (open) => void (resetOpen = open),
  );
  parts.push(reset);
  el.replaceChildren(...parts);
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
        global.el = el;
      },
      onShow: (ctx) => {
        void renderGlobal(ctx).then(() => customBox.refresh(ctx)); // its rows are made by renderGlobal
        void nightlyBox.refresh(ctx);
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
    global.reset();
    chosenDll.clear();
    installOpen = false;
    nightlyBox.reset();
    customBox.reset();
    lastFolder = undefined;
    browser?.close();
    resetOpen = false;
  },
});
