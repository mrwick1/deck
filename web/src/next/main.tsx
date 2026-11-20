// The desktop Home in the fluid look, with real data. It is served at /next. The current app at / does not change.
// Screens that are not rebuilt yet (setup review, Deck chats, history) open in the current app.
import { StrictMode, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { AnimatePresence, LayoutGroup, MotionConfig, motion } from "motion/react";
import { ArrowUp, Bot, ChevronDown, ExternalLink, Home, MessageCircle, MoreHorizontal, PanelLeft, Plus, RefreshCw, Search, Sparkles, SquarePen, X } from "lucide-react";
import { Toaster, toast } from "sonner";
import { Highlight, HighlightItem } from "@animate-ui/react/primitives/effects/highlight";
import { Tabs, TabsContent, TabsHighlight, TabsHighlightItem, TabsList, TabsTrigger } from "@animate-ui/react/primitives/tabs";
import { DropdownMenu, Tooltip } from "@animate-ui/react/primitives/radix-ui";
import { ShimmeringText } from "@animate-ui/react/primitives/texts/shimmering";
import { SlidingNumber } from "@animate-ui/react/primitives/texts/sliding-number";
import { Command, CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { cn } from "@/lib/cn";
import * as api from "@/lib/api";
import { describeBot, type Tone } from "@/lib/bot";
import { Dot, TaskIcon, TaskTitle, Tip, iconButton, menuContent, menuItem, pill, soft, spring, toasterOptions } from "../fluid/ui";
import { useWorkspace } from "../experience/useWorkspace";
import { BotChatHost } from "../experience/Chat";
import { Computer } from "../experience/Computer";
import { Requests, RequestsProvider, useRequests } from "../experience/Requests";
import { phaseLabel } from "../experience/setup-progress";
import { duplicateNames, shortId } from "../experience/bot-names";
import "../index.css";
import "../experience/live.css";
import "./legacy.css";

type Workspace = ReturnType<typeof useWorkspace>;
type Item = { id: string; name: string; job?: string; tone: Tone; status: string; now: string | null; queued: number; computer: string; record: api.BotStatus; row?: api.SetupRecord };
type Need = { key: string; bot: string; tone: Tone; what: string; why?: string; action: string; act: () => void };

const computerLabel: Record<api.BotStatus["computer"], string> = { saved: "Asleep", waking: "Waking", awake: "Awake", saving: "Saving", failed: "Failed" };
const tabs = [["chat", "Chat"], ["activity", "Activity"], ["requests", "Requests"], ["computer", "Computer"]] as const;
/** A screen that is not rebuilt yet opens in the current app. */
const classic = (route: string) => location.assign(`/#${route}`);
const ambiguous = (error: unknown) => !(error instanceof api.ApiError) || error.status === 0 || error.status >= 500;
const message = (error: unknown) => error instanceof Error ? error.message : "The action was not confirmed.";

function readSelection() {
  const query = new URLSearchParams(location.search);
  return { bot: query.get("bot") ?? undefined, tab: query.get("tab") ?? "activity" };
}

function Avatar({ size = "md" }: { size?: "sm" | "md" }) {
  return <span aria-hidden="true" className={cn("grid shrink-0 place-items-center bg-raised text-muted-foreground", size === "md" ? "size-10 rounded-[16px] [&_svg]:size-5" : "size-8 rounded-[12px] [&_svg]:size-4")}><Bot /></span>;
}

const tile = "soft-card flex min-w-0 flex-col gap-3 p-5";
const tileLabel = "flex items-center gap-2 text-xs font-medium text-faint-foreground";
const rowButton = "flex w-full items-center gap-3 rounded-[14px] px-2 py-2 text-left outline-none transition-colors hover:bg-accent focus-visible:bg-accent";

/** The live screen of one working bot. It follows the same privacy rules as the pane. */
function LiveScreen({ item, fresh, open, inPane }: { item: Item; fresh: boolean; open: (tab?: string) => void; inPane: boolean }) {
  const requests = useRequests(item.id);
  const { record: status } = item;
  const awake = status.computer === "awake", moving = status.computer === "waking" || status.computer === "saving";
  const hidden = !!item.row?.setup.wait || !!requests.secrets.length || !!requests.error || !requests.loaded;
  const note = (text: string, orb = false) => <div className="grid aspect-video place-items-center rounded-[16px] border border-border bg-background px-6 text-center text-sm text-muted-foreground"><span className="flex flex-col items-center gap-3">{orb && <span className="orb" />}{text}</span></div>;
  return <div className="flex flex-1 flex-col gap-3">
    <span className="flex items-center gap-3">
      <Avatar />
      <span className="flex min-w-0 flex-1 flex-col">
        <button type="button" onClick={() => open()} className="truncate text-left font-medium outline-none hover:underline focus-visible:underline">{item.name}</button>
        <span className="truncate text-sm text-muted-foreground">{item.now ? <ShimmeringText text={item.now} duration={1.6} /> : item.status}</span>
      </span>
      <button type="button" onClick={() => open("computer")} className={cn(pill, "h-8 shrink-0 bg-raised px-3 text-xs hover:bg-accent")}>Open</button>
    </span>
    {!fresh ? note("Reconnect before viewing the computer.")
      : inPane ? note("The screen is open in the pane.")
      : !awake ? note(moving ? "Waiting for the computer." : status.computer === "failed" ? "The computer could not start." : "The computer is asleep.", moving)
      : hidden ? note(!requests.loaded ? "Checking whether the screen can be shown…" : requests.error ? "Screen hidden because privacy status could not be checked." : "Screen hidden while a sign-in or protected input needs attention.")
      : <div className="experience live-tile"><Computer key={item.id} id={item.id} /></div>}
  </div>;
}

function Live({ working, fresh, open, paneScreen }: { working: Item[]; fresh: boolean; open: (id: string, tab?: string) => void; paneScreen?: string }) {
  const [focus, setFocus] = useState(0);
  const hero = working[focus % Math.max(1, working.length)];
  return <section aria-label="Live now" className={cn(tile, "@lg:col-span-2 @4xl:row-span-2")}>
    <span className="flex items-center justify-between gap-3"><span className={tileLabel}>{hero && <Dot tone="busy" />}Live now</span>
      {working.length > 1 && <span className="flex gap-1">{working.map((bot, index) => <Tip key={bot.id} label={bot.name}><button type="button" aria-label={`Show ${bot.name}`} aria-pressed={bot.id === hero?.id} onClick={() => setFocus(index)} className="grid h-6 place-items-center px-0.5"><span className={cn("block h-1.5 rounded-full transition-[width,background-color] duration-200", bot.id === hero?.id ? "w-5 bg-foreground/70" : "w-1.5 bg-raised")} /></button></Tip>)}</span>}</span>
    {hero ? <AnimatePresence mode="popLayout" initial={false}><motion.div key={hero.id} initial={{ opacity: 0, scale: 0.98 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.98 }} transition={soft} className="flex flex-1 flex-col">
      <LiveScreen item={hero} fresh={fresh} open={tab => open(hero.id, tab)} inPane={paneScreen === hero.id} />
    </motion.div></AnimatePresence>
      : <div className="grid flex-1 place-items-center rounded-[16px] border border-border bg-background py-16 text-center text-sm text-muted-foreground"><span className="flex flex-col items-center gap-3"><span className="orb" />No bot is working. Give one a task below.</span></div>}
  </section>;
}

function Needs({ needs }: { needs: Need[] }) {
  const [all, setAll] = useState(false);
  const shown = all ? needs : needs.slice(0, 4);
  return <section aria-label="Needs you" className={cn(tile, "@lg:col-span-2")}>
    <span className={tileLabel}>Needs you{needs.length > 0 && <span className="grid h-5 min-w-5 place-items-center rounded-full bg-brand px-1.5 text-[11px] font-medium text-white tabular-nums">{needs.length}</span>}</span>
    {needs.length ? <ul className="-mx-2 flex flex-col"><AnimatePresence initial={false}>{shown.map(need => <motion.li key={need.key} layout initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, transition: { duration: 0.14 } }} transition={soft} className="flex items-center gap-3 rounded-[14px] px-2 py-2">
      <Dot tone={need.tone} />
      <span className="flex min-w-0 flex-1 flex-col"><span className="truncate text-sm font-medium">{need.what}</span><span className="truncate text-xs text-muted-foreground">{need.bot}{need.why && ` · ${need.why}`}</span></span>
      <button type="button" onClick={need.act} className={cn(pill, "h-8 shrink-0 bg-raised px-3 text-xs hover:bg-accent")}>{need.action}</button>
    </motion.li>)}</AnimatePresence></ul>
      : <p className="flex flex-1 items-center gap-2 text-sm text-muted-foreground"><Dot tone="idle" />Nothing needs you. Your bots are on their own.</p>}
    {needs.length > 4 && <button type="button" onClick={() => setAll(!all)} className={cn(pill, "-mx-2 h-8 self-start px-3 text-xs text-muted-foreground hover:bg-accent")}>{all ? "Show fewer" : `Show ${needs.length - 4} more`}</button>}
  </section>;
}

function Stat({ label, value, note }: { label: string; value: number; note: string }) {
  return <section aria-label={label} className={tile}><span className={tileLabel}>{label}</span><span className="text-3xl font-medium tabular-nums"><SlidingNumber number={value} /></span><span className="text-xs text-muted-foreground">{note}</span></section>;
}

/** Mini tiles up to this fleet size. A visible tile grows into the pane. */
const tileLimit = 8;

function FleetTile({ bots, open, selected, more }: { bots: Item[]; open: (id: string) => void; selected?: string; more: () => void }) {
  return <section aria-label="Bots" className={cn(tile, "@lg:col-span-2")}>
    <span className={tileLabel}>Bots · {bots.length}</span>
    {bots.length ? <div className="grid grid-cols-1 gap-2 @md:grid-cols-2">{bots.slice(0, tileLimit).map((bot, index) => <motion.div key={bot.id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring, delay: Math.min(index, 8) * 0.03 }}>
      {/* The selected bot lives in the pane. A soft outline keeps its place. */}
      {bot.id === selected ? <div aria-hidden="true" className="h-full min-h-14 rounded-[16px] border border-dashed border-border" />
        : <motion.button layoutId={`card-${bot.id}`} type="button" whileHover={{ y: -2 }} transition={spring} onClick={() => open(bot.id)} className="soft-raised flex w-full items-center gap-2.5 p-2.5 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring">
          <Avatar size="sm" />
          <span className="flex min-w-0 flex-1 flex-col"><span className="truncate text-sm">{bot.name}</span><span className="flex items-center gap-1.5 text-xs text-muted-foreground"><Dot tone={bot.tone} className="size-1.5" /><span className="truncate">{bot.status}</span></span></span>
          {bot.queued > 0 && <span className="shrink-0 rounded-full bg-background/60 px-2 py-0.5 text-[11px] text-faint-foreground tabular-nums">{bot.queued}</span>}
        </motion.button>}
    </motion.div>)}</div>
      : <p className="text-sm text-muted-foreground">No bot is running yet. Finish a setup to start one.</p>}
    {bots.length > tileLimit && <button type="button" onClick={more} className={cn(pill, "-mx-2 h-8 self-start px-3 text-xs text-muted-foreground hover:bg-accent")}>{bots.length - tileLimit} more · search</button>}
  </section>;
}

/** The bot beside Home. Chat, requests and the computer reuse the current views. */
function BotPane({ item, data, tab, setTab, close, chatSlot, fresh, morph }: { item: Item; data: Workspace; tab: string; setTab: (tab: string) => void; close: () => void; chatSlot: (id: string, slot: HTMLDivElement | null) => void; fresh: boolean; morph: boolean }) {
  const { id, record: status, row } = item;
  const requests = useRequests(id);
  const [busy, setBusy] = useState(false);
  const guard = useRef(false);
  const pane = useRef<HTMLElement>(null);
  const slot = useCallback((element: HTMLDivElement | null) => chatSlot(id, element), [chatSlot, id]);
  useEffect(() => { pane.current?.focus({ preventScroll: true }); }, [id]);
  const count = requests.items.length + requests.secrets.length + (row?.setup.wait ? 1 : 0);
  const disabled = !fresh || busy;
  const run = async (action: () => Promise<unknown>, done: string) => {
    if (guard.current || !fresh) return;
    guard.current = true; setBusy(true);
    try { await action(); toast.success(done); await data.refresh(); await requests.refresh(); }
    catch (e) { toast.error(ambiguous(e) ? "The response was not confirmed. The request may have applied. Check the status before you try again." : message(e)); }
    finally { guard.current = false; setBusy(false); }
  };
  const awake = status.computer === "awake", moving = status.computer === "waking" || status.computer === "saving";
  const toggleComputer = () => run(() => awake ? api.botSleep(id) : api.botWake(id), awake ? "Sleep requested" : "Wake requested");
  const privateScreen = !!row?.setup.wait || !!requests.secrets.length || !!requests.error || !requests.loaded;
  const active = ["queued", "held", "waiting"];
  const note = "grid place-items-center gap-3 rounded-[16px] border border-border bg-background px-6 text-center text-sm text-muted-foreground";
  return <motion.aside ref={pane} tabIndex={-1} aria-label={item.name} layoutId={morph ? `card-${id}` : undefined} transition={soft}
    initial={morph ? undefined : { opacity: 0, x: 24 }} animate={morph ? undefined : { opacity: 1, x: 0 }} exit={morph ? undefined : { opacity: 0, x: 24, transition: { duration: 0.14 } }}
    className="next-pane soft-card z-10 flex w-[44%] max-w-[720px] min-w-[440px] shrink-0 flex-col overflow-hidden outline-none">
    <header className="flex items-center gap-3 p-5 pb-3">
      <Avatar />
      <span className="flex min-w-0 flex-1 flex-col"><span className="truncate text-base font-medium">{item.name}</span>
        <span className="flex items-center gap-2 text-xs text-muted-foreground"><Dot tone={item.tone} /><AnimatePresence mode="popLayout" initial={false}><motion.span key={item.status} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={spring} className="truncate">{item.status}</motion.span></AnimatePresence></span></span>
      <DropdownMenu.Root>
        <DropdownMenu.Trigger asChild><button type="button" aria-label="Bot controls" className={iconButton}><MoreHorizontal className="size-4" /></button></DropdownMenu.Trigger>
        <DropdownMenu.Portal><DropdownMenu.Content align="end" sideOffset={6} transition={spring} className={cn(menuContent, "w-64")}>
          <DropdownMenu.Item disabled={disabled} onSelect={() => { void run(() => api.botPause(!status.automation_paused, id), status.automation_paused ? "Resume requested" : "Pause requested"); }} className={menuItem}>{status.automation_paused ? "Resume automation" : "Pause automation"}</DropdownMenu.Item>
          <DropdownMenu.Label className="px-3 pb-1.5 text-xs text-muted-foreground">Pause stops new tasks. Current work can continue.</DropdownMenu.Label>
          <DropdownMenu.Item disabled={disabled} onSelect={() => { void run(async () => { const result = await api.botStop(id); if (result.unconfirmed.length) toast.warning(`${result.unconfirmed.length} command outcomes remain unconfirmed. Check Requests.`); }, "Stop requested"); }} className={menuItem}>Stop work</DropdownMenu.Item>
          <DropdownMenu.Item disabled={disabled || moving} onSelect={() => { void toggleComputer(); }} className={menuItem}>{awake ? "Sleep computer" : "Wake computer"}</DropdownMenu.Item>
          <DropdownMenu.Separator className="my-1 h-px bg-border" />
          <DropdownMenu.Item onSelect={() => classic(`bot/${encodeURIComponent(id)}`)} className={cn(menuItem, "text-muted-foreground")}><ExternalLink />Open in the current app</DropdownMenu.Item>
        </DropdownMenu.Content></DropdownMenu.Portal>
      </DropdownMenu.Root>
      <Tip label="Close (Esc)"><button type="button" aria-label="Close bot pane" onClick={close} className={iconButton}><X className="size-4" /></button></Tip>
    </header>
    <Tabs value={tab} onValueChange={setTab} className="flex min-h-0 flex-1 flex-col">
      <TabsHighlight className="absolute inset-0 rounded-full bg-raised shadow-sm" transition={spring}>
        <TabsList className="mx-5 flex shrink-0 gap-1 rounded-full bg-background/60 p-1">
          {tabs.map(([value, label]) => <TabsHighlightItem key={value} value={value} className="flex-1"><TabsTrigger value={value} className="relative z-10 flex h-8 w-full items-center justify-center gap-1.5 rounded-full text-sm text-muted-foreground transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring data-[state=active]:text-foreground">
            {label}{value === "requests" && count > 0 && <span className="grid h-5 min-w-5 place-items-center rounded-full bg-brand px-1.5 text-[11px] font-medium text-white tabular-nums">{count}</span>}
          </TabsTrigger></TabsHighlightItem>)}
        </TabsList>
      </TabsHighlight>
      {/* The chat view is drawn into this slot by its host, so the session outlives tab and bot changes. */}
      <TabsContent value="chat" ref={slot} className="experience mt-3" hidden={tab !== "chat"}>{null}</TabsContent>
      {tab === "activity" && <TabsContent value="activity" className="min-h-0 flex-1 overflow-auto! px-5 py-4">
        {!status.tasks.length && <p className="py-10 text-center text-sm text-muted-foreground">No tasks yet. Give {item.name} a task from Chat.</p>}
        <ul className="flex flex-col gap-2">{status.tasks.map((task, index) => <motion.li key={task.id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ ...soft, delay: Math.min(index, 8) * 0.03 }} className="soft-raised flex flex-col gap-2 px-4 py-3">
          <span className="flex items-center gap-3 text-sm"><TaskIcon state={task.state} /><TaskTitle task={task} className="min-w-0 flex-1 truncate" /></span>
          {task.detail && <p className="pl-9 text-xs text-muted-foreground">{task.detail}</p>}
          {active.includes(task.state) && <span className="flex gap-1 pl-8">
            {task.state === "held" && <button type="button" disabled={disabled} onClick={() => { void run(() => api.botRetryTask(task.id, id), `Retry requested for ${task.title}`); }} className={cn(pill, "h-8 bg-raised px-3 text-xs hover:bg-accent")}>Retry</button>}
            <button type="button" disabled={disabled} onClick={() => { void run(() => api.botCancelTask(task.id, id), `Cancel requested for ${task.title}`); }} className={cn(pill, "h-8 px-3 text-xs text-muted-foreground hover:bg-accent")}>Cancel</button>
          </span>}
        </motion.li>)}</ul>
      </TabsContent>}
      {tab === "requests" && <TabsContent value="requests" className="experience min-h-0 flex-1 overflow-auto! px-5 py-4"><Requests id={id} row={row} protectedInput={data.protectedInput} requests={requests} disabled={!fresh} refresh={data.refresh} /></TabsContent>}
      {tab === "computer" && <TabsContent value="computer" className="experience flex min-h-0 flex-1 flex-col gap-3 overflow-auto! px-5 py-4">
        <div className="flex items-center justify-between"><span className="flex items-center gap-2 text-sm"><Dot tone={status.computer === "failed" ? "error" : awake ? "idle" : moving ? "busy" : "asleep"} />{computerLabel[status.computer]}</span>
          <button type="button" disabled={disabled || moving} onClick={() => { void toggleComputer(); }} className={cn(pill, "bg-raised hover:bg-accent")}>{awake ? "Sleep" : moving ? `${computerLabel[status.computer]}…` : "Wake"}</button></div>
        {!fresh ? <p className="py-10 text-center text-sm text-muted-foreground">Reconnect before viewing the computer.</p>
          : privateScreen ? <div className={cn(note, "py-12")}><p>{!requests.loaded ? "Checking whether the screen can be shown…" : requests.error ? "Screen hidden because privacy status could not be checked." : "Screen hidden while a sign-in or protected input needs attention."}</p><button type="button" onClick={() => setTab("requests")} className={cn(pill, "bg-raised hover:bg-accent")}>Open requests</button></div>
          : awake ? <Computer key={id} id={id} />
          : <div className={cn(note, "aspect-video")}>{moving && <span className="orb" />}<p>{status.computer === "saved" ? "The computer is asleep. Wake it to see the screen." : status.computer === "failed" ? "The computer could not start. You can request wake again." : "Waiting for the computer. The screen appears when it is ready."}</p></div>}
      </TabsContent>}
    </Tabs>
  </motion.aside>;
}

type Destination = { value: string; label: string };

function Composer({ value, onChange, onSubmit, busy, disabled, label, placeholder, destinations, destination, setDestination, hint }: { value: string; onChange: (value: string) => void; onSubmit: () => void; busy: boolean; disabled?: boolean; label: string; placeholder: string; destinations?: Destination[]; destination?: string; setDestination?: (value: string) => void; hint: string }) {
  const current = destinations?.find(item => item.value === destination);
  const canSend = !!value.trim() && !busy && !disabled;
  return <div className="mx-auto flex w-full max-w-3xl flex-col gap-2">
    <form onSubmit={event => { event.preventDefault(); if (canSend) onSubmit(); }} className="soft-card flex items-end gap-2 rounded-[22px] p-2 pl-3 transition-shadow focus-within:shadow-[0_0_0_4px_rgb(242_85_90_/_.12)]">
      {destinations && setDestination && <DropdownMenu.Root>
        <DropdownMenu.Trigger asChild><button type="button" aria-label={`Send to: ${current?.label}`} className={cn(pill, "mb-1 h-8 max-w-48 shrink-0 gap-1.5 bg-raised px-3 text-xs text-muted-foreground hover:text-foreground")}>{destination === "new" ? <Sparkles className="size-3.5 shrink-0 text-brand" /> : destination === "deck" ? <MessageCircle className="size-3.5 shrink-0" /> : <Bot className="size-3.5 shrink-0" />}<span className="truncate">{current?.label}</span><ChevronDown className="size-3 shrink-0" /></button></DropdownMenu.Trigger>
        <DropdownMenu.Portal><DropdownMenu.Content side="top" align="start" sideOffset={8} transition={spring} className={cn(menuContent, "max-h-80 overflow-auto")}>
          <DropdownMenu.Label className="px-3 pt-1 pb-1.5 text-xs text-faint-foreground">Send to</DropdownMenu.Label>
          <DropdownMenu.RadioGroup value={destination} onValueChange={setDestination}>{destinations.map(item => <DropdownMenu.RadioItem key={item.value} value={item.value} className={cn(menuItem, "pr-8 data-[state=checked]:text-foreground")}><span className="truncate">{item.label}</span><DropdownMenu.ItemIndicator className="absolute right-3"><Dot tone="error" className="size-1.5" /></DropdownMenu.ItemIndicator></DropdownMenu.RadioItem>)}</DropdownMenu.RadioGroup>
        </DropdownMenu.Content></DropdownMenu.Portal>
      </DropdownMenu.Root>}
      <textarea rows={1} value={value} disabled={disabled} onChange={event => onChange(event.target.value)} onKeyDown={event => { if ((event.metaKey || event.ctrlKey) && event.key === "Enter") { event.preventDefault(); if (canSend) onSubmit(); } }} aria-label={label} aria-describedby="composer-hint" placeholder={placeholder} className="max-h-40 min-h-10 flex-1 resize-none bg-transparent px-1 py-2.5 text-[15px] outline-none [field-sizing:content] placeholder:text-faint-foreground disabled:opacity-60" />
      <motion.button type="submit" aria-label={busy ? "Sending" : "Send"} disabled={!canSend} whileTap={{ scale: 0.9 }} transition={spring} className="grid size-10 shrink-0 place-items-center rounded-full bg-primary text-primary-foreground transition-opacity disabled:opacity-40">
        <AnimatePresence mode="popLayout" initial={false}>{busy ? <motion.span key="busy" initial={{ scale: 0.5, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.5, opacity: 0 }} className="size-4 animate-spin rounded-full border-2 border-white/40 border-t-white" /> : <motion.span key="send" initial={{ y: 8, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: -8, opacity: 0 }}><ArrowUp className="size-4" /></motion.span>}</AnimatePresence>
      </motion.button>
    </form>
    <p id="composer-hint" className="flex justify-between gap-4 px-4 text-xs text-faint-foreground"><span>{hint}</span><span className="shrink-0">Ctrl + Enter to send</span></p>
  </div>;
}

function FirstRun({ draft, setDraft, submit, enabled, busy }: { draft: string; setDraft: (value: string) => void; submit: () => void; enabled: boolean; busy: boolean }) {
  const ideas = ["Read my inbox each morning and summarize messages that need a reply.", "Check three competitor pricing pages daily and tell me what changed.", "Research a topic I choose and write a short brief with sources."];
  return <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col justify-center gap-8 px-6 py-16">
    <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={soft} className="flex flex-col gap-3"><span className="orb" /><h1 className="text-3xl font-medium text-balance">What should your first bot do?</h1><p className="text-sm text-muted-foreground">{enabled ? "Describe a job. You review its instructions, permissions and spending before it starts." : "Bot creation is not configured on this server. You can still chat with Deck."}</p></motion.div>
    {enabled && <Composer value={draft} onChange={setDraft} onSubmit={submit} busy={busy} label="Describe a job" placeholder="Describe its job" hint={busy ? "Preparing your setup…" : "Credentials never go in this box."} />}
    {enabled && <div className="flex flex-col gap-2">{ideas.map((idea, index) => <motion.button key={idea} type="button" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ ...soft, delay: 0.1 + index * 0.05 }} whileHover={{ x: 4 }} onClick={() => setDraft(idea)} className="soft-raised px-4 py-3 text-left text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:text-foreground">{idea}</motion.button>)}</div>}
  </div>;
}

function Loading() {
  return <div role="status" aria-label="Loading your workspace" className="mx-auto flex w-full max-w-[1100px] flex-col gap-6 px-8 pt-20">
    <span className="flex items-center gap-3"><span className="orb" /><ShimmeringText className="text-[15px]" text="Loading your workspace…" duration={1.4} /></span>
    <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-4">{[0, 1, 2].map(key => <div key={key} className="soft-card flex flex-col gap-4 p-5"><div className="flex items-center gap-3"><span className="skeleton size-10 rounded-[16px]" /><span className="flex flex-1 flex-col gap-2"><span className="skeleton h-3 w-2/3" /><span className="skeleton h-3 w-1/3" /></span></div><span className="skeleton h-10" /></div>)}</div>
  </div>;
}

const navRow = "relative z-10 flex h-9 w-full items-center gap-3 rounded-[12px] px-2.5 text-left text-sm text-muted-foreground transition-colors outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring aria-[current=page]:text-foreground aria-[current=true]:text-foreground";

function Sidebar({ collapsed, setCollapsed, children, footer, onNew, canCreate }: { collapsed: boolean; setCollapsed: (value: boolean) => void; children: ReactNode; footer: ReactNode; onNew: () => void; canCreate: boolean }) {
  return <motion.aside animate={{ width: collapsed ? 68 : 256 }} initial={false} transition={spring} className="flex shrink-0 flex-col overflow-hidden rounded-[20px] border border-border bg-sidebar">
    <div className="flex h-14 shrink-0 items-center gap-2 px-3">
      <Tip label={collapsed ? "Expand sidebar" : "Collapse sidebar"}><button type="button" aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} aria-expanded={!collapsed} onClick={() => setCollapsed(!collapsed)} className={iconButton}><PanelLeft className="size-4" /></button></Tip>
      <AnimatePresence initial={false}>{!collapsed && <motion.span key="mark" initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -6 }} className="font-display text-xl">deck</motion.span>}</AnimatePresence>
    </div>
    <div className="shrink-0 px-3"><motion.button type="button" disabled={!canCreate} whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.97 }} transition={spring} onClick={onNew} aria-label="New bot" className="flex h-10 w-full items-center justify-center gap-2 overflow-hidden rounded-full bg-primary text-sm font-medium text-primary-foreground shadow-[0_6px_20px_-8px_var(--primary)] disabled:opacity-40 disabled:shadow-none"><Plus className="size-4 shrink-0" />{!collapsed && <span className="truncate">New bot</span>}</motion.button></div>
    <div className="mt-4 min-h-0 flex-1 overflow-x-hidden overflow-y-auto pb-3">{children}</div>
    <div className="shrink-0 border-t border-border p-3">{footer}</div>
  </motion.aside>;
}

function App() {
  const data = useWorkspace();
  const fresh = data.hasData && !data.error;
  const [selection, setSelection] = useState(readSelection);
  const [draft, setDraft] = useState("");
  const [destination, setDestination] = useState("new");
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [search, setSearch] = useState(false);
    const [collapsed, setCollapsedState] = useState(() => localStorage.getItem("deck.next.sidebar") === "collapsed");
  const setCollapsed = (value: boolean) => { setCollapsedState(value); localStorage.setItem("deck.next.sidebar", value ? "collapsed" : "open"); };
  const guard = useRef(false);
  // Each bot chat keeps its session after the pane closes, as in the current app.
  const [visited, setVisited] = useState<string[]>([]);
  // Each pane owns its chat slot. During a pane change the old and the new pane exist together.
  const [slots, setSlots] = useState<Record<string, HTMLDivElement | null>>({});
  const putSlot = useCallback((id: string, element: HTMLDivElement | null) => setSlots(previous => previous[id] === element ? previous : { ...previous, [id]: element }), []);
  const [chatDrafts, setChatDrafts] = useState<Record<string, string>>({});
  const scrolls = useRef(new Map<string, number>());
  const putDraft = useCallback((key: string, change: (current: string) => string) => setChatDrafts(previous => ({ ...previous, [key]: change(previous[key] ?? "") })), []);

  const names = new Map(data.rows.map(row => [row.setup.id, row.setup.draft.name]));
  const fleet: Item[] = data.bots.map(record => {
    const row = data.rows.find(item => item.setup.id === record.bot_id), described = describeBot(record);
    return { id: record.bot_id, name: names.get(record.bot_id) ?? "Default bot", job: row?.setup.draft.instructions, tone: described.tone, status: described.label, now: record.working?.title ?? null, queued: record.queued, computer: computerLabel[record.computer], row, record };
  });
  const setups = data.rows.filter(row => !data.bots.some(bot => bot.bot_id === row.setup.id));
  // Equal names get a short id, across bots and setups, so the owner can tell them apart.
  const dupes = duplicateNames([...fleet, ...setups.map(row => ({ name: row.setup.draft.name }))]);
  const label = (name: string, id: string) => dupes.has(name.trim().toLowerCase()) ? `${name} · ${shortId(id)}` : name;
  for (const bot of fleet) bot.name = label(bot.name, bot.id);
  const setupName = (row: api.SetupRecord) => label(row.setup.draft.name, row.setup.id);
  const selected = fleet.find(bot => bot.id === selection.bot);

  const select = useCallback((bot: string | undefined, tab = "activity") => {
    setSelection({ bot, tab });
    if (bot) setVisited(previous => previous.includes(bot) ? previous : [...previous, bot]);
    const url = new URL(location.href);
    if (bot) { url.searchParams.set("bot", bot); url.searchParams.set("tab", tab); } else { url.searchParams.delete("bot"); url.searchParams.delete("tab"); }
    history.replaceState(null, "", url);
  }, []);
  useEffect(() => { if (selection.bot) setVisited(previous => previous.includes(selection.bot!) ? previous : [...previous, selection.bot!]); }, [selection.bot]);
  const open = (id: string, tab = "activity") => select(id, tab);

  const setupNeed = (row: api.SetupRecord): Need | null => {
    const what = row.setup.wait ? "Sign-in needs your help" : row.phase === "Investigate" ? "An outcome needs investigation" : row.phase === "Exhausted" ? "Setup used all its attempts" : row.phase === "Draft" ? "Setup draft ready for review" : null;
    return what && { key: `setup-${row.setup.id}`, bot: `${setupName(row)} · setup`, tone: row.phase === "Draft" ? "idle" : "attention", what, why: row.error ?? undefined, action: row.phase === "Draft" ? "Review" : "Open setup", act: () => classic(`setup/${encodeURIComponent(row.setup.id)}`) };
  };
  const needs: Need[] = [
    ...fleet.filter(bot => bot.tone === "attention" || bot.tone === "error").map(bot => ({
      key: bot.id, bot: bot.name, tone: bot.tone, what: bot.status,
      why: bot.record.ceiling_request?.reason ?? bot.record.budget_reason ?? bot.record.waiting?.title ?? bot.record.last_error ?? undefined,
      action: bot.tone === "error" ? "Open computer" : "Review", act: () => open(bot.id, bot.tone === "error" ? "computer" : "requests"),
    })),
    ...data.rows.filter(row => row.setup.wait || row.phase === "Investigate" || row.phase === "Exhausted" || (row.phase === "Draft" && setups.includes(row))).map(setupNeed).filter(need => need !== null),
  ];
  const results = fleet.flatMap(bot => bot.record.tasks.filter(task => task.state === "done" || task.state === "failed").map(task => ({ task, bot }))).slice(0, 6);
  const destinationName = fleet.find(bot => bot.id === destination)?.name;
  const effectiveDestination = destination === "new" && !data.enabled ? "deck" : destination;
  const destinations: Destination[] = [...(data.enabled ? [{ value: "new", label: "New bot" }] : []), { value: "deck", label: "Deck" }, ...fleet.map(bot => ({ value: bot.id, label: bot.name }))];

  const submit = async (target = effectiveDestination) => {
    const text = draft.trim();
    if (!text || guard.current || !fresh) return;
    guard.current = true; setBusy(true);
    try {
      if (target === "new") {
        if (uncertain) return;
        let row: api.SetupRecord;
        try { row = await api.createSetup(text); }
        catch (e) { if (ambiguous(e)) { setUncertain(true); void data.refresh(); } throw e; }
        setDraft("");
        toast.success("Setup draft created. Review it before it starts.", { action: { label: "Review", onClick: () => classic(`setup/${encodeURIComponent(row.setup.id)}`) } });
      } else if (target === "deck") {
        const chat = await api.createChat(text.slice(0, 60));
        await api.sendMessage(chat.id, text);
        setDraft("");
        toast.success("Sent to Deck", { action: { label: "Open", onClick: () => classic(`chat/${chat.id}`) } });
      } else {
        const bot = data.bots.find(item => item.bot_id === target);
        if (!bot) throw new Error("This bot is not ready. Refresh before sending.");
        await api.sendMessage(bot.chat_id, text, { mode: "queue" });
        setDraft("");
        toast.success(`Task received by ${fleet.find(item => item.id === target)?.name ?? "the bot"}`, { action: { label: "Open", onClick: () => open(target, "activity") } });
      }
      void data.refresh();
    } catch (e) { toast.error(ambiguous(e) ? "The request was not confirmed. It may have applied. Check before you try again." : message(e)); }
    finally { guard.current = false; setBusy(false); }
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const typing = event.target instanceof HTMLElement && (/^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName) || event.target.isContentEditable);
      if ((event.key === "k" && (event.metaKey || event.ctrlKey)) || (event.key === "/" && !typing)) { event.preventDefault(); setSearch(true); }
      else if (event.key === "Escape" && selection.bot && !search && !document.querySelector("[role=listbox],[role=dialog],[role=menu]")) select(undefined);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selection.bot, search, select]);

  const connection = !data.hasData ? (data.error ? "Not connected" : "Connecting…") : data.error ? "Connection lost" : "Connected";
  const empty = data.hasData && !fleet.length && !setups.length;
  const hour = new Date().getHours();
  const startNew = () => { select(undefined); setDestination("new"); requestAnimationFrame(() => document.querySelector<HTMLTextAreaElement>("textarea[aria-label='Message'], textarea[aria-label='Describe a job']")?.focus()); };
  const busyBots = fleet.filter(bot => bot.tone === "busy");

  return <MotionConfig reducedMotion="user"><Tooltip.Provider delayDuration={300}><LayoutGroup>
    <div className="fluid relative flex h-dvh gap-2 overflow-hidden p-2">
      <Sidebar collapsed={collapsed} setCollapsed={setCollapsed} onNew={startNew} canCreate={data.enabled && fresh}
        footer={<div role="status" className="flex items-center gap-2 text-xs text-muted-foreground">
          <Tip label={data.refreshing ? "Refreshing…" : "Refresh"}><button type="button" aria-label="Refresh workspace" disabled={data.refreshing} onClick={() => { void data.refresh(); }} className={iconButton}><RefreshCw className={cn("size-4", data.refreshing && "animate-spin")} /></button></Tip>
          {!collapsed && <><Dot tone={data.error ? "error" : !data.hasData ? "busy" : "idle"} /><span className="truncate">{connection}</span><a href="/" className="ml-auto shrink-0 rounded-full px-2 py-1 hover:bg-accent hover:text-foreground">Current app</a></>}
        </div>}>
        <Highlight controlledItems mode="parent" value={selected ? null : "home"} hover className="rounded-[12px] bg-accent" transition={spring}>
          <nav aria-label="Main" className="flex flex-col gap-0.5 px-3">
            <HighlightItem value="home"><button type="button" aria-current={!selected ? "page" : undefined} onClick={() => select(undefined)} className={navRow}><Home className="size-4 shrink-0" />{!collapsed && <span className="truncate">Home</span>}{needs.length > 0 && <span className={cn("grid h-5 min-w-5 place-items-center rounded-full bg-brand px-1.5 text-[11px] font-medium text-white tabular-nums", collapsed ? "absolute top-0.5 right-0.5 h-4 min-w-4 px-1 text-[10px]" : "ml-auto")}>{needs.length}</span>}</button></HighlightItem>
            <HighlightItem value="history"><button type="button" onClick={() => classic("history")} className={navRow}><MessageCircle className="size-4 shrink-0" />{!collapsed && <><span className="truncate">Conversations</span><ExternalLink aria-label="Opens the current app" className="ml-auto size-3 text-faint-foreground" /></>}</button></HighlightItem>
          </nav>
        </Highlight>
        {fleet.length > 0 && <>
          {collapsed ? <div className="mx-5 my-4 h-px bg-border" /> : <p className="mt-6 px-5 pb-1.5 text-xs font-medium text-faint-foreground">Bots</p>}
          <Highlight controlledItems mode="parent" value={selected?.id ?? null} hover className="rounded-[12px] bg-accent" transition={spring}>
            <div className="flex flex-col gap-0.5 px-3">{fleet.slice(0, 15).map(bot => <HighlightItem key={bot.id} value={bot.id}>
              <button type="button" aria-label={collapsed ? `${bot.name}, ${bot.status}` : undefined} aria-current={bot.id === selected?.id || undefined} onClick={() => open(bot.id)} className={cn(navRow, "px-3.5")}><Dot tone={bot.tone} />{!collapsed && <span className="truncate">{bot.name}</span>}</button>
            </HighlightItem>)}</div>
          </Highlight>
          {fleet.length > 15 && !collapsed && <button type="button" className="mx-3 mt-1 rounded-[12px] px-3 py-1.5 text-xs text-muted-foreground hover:bg-accent hover:text-foreground" onClick={() => setSearch(true)}>{fleet.length - 15} more · search</button>}
        </>}
        {!collapsed && setups.length > 0 && <><p className="mt-6 px-5 pb-1.5 text-xs font-medium text-faint-foreground">In setup</p>
          <div className="flex flex-col gap-0.5 px-3">{setups.map(row => <button type="button" key={row.setup.id} title={phaseLabel(row)} onClick={() => classic(`setup/${encodeURIComponent(row.setup.id)}`)} className={cn(navRow, "hover:bg-accent")}><SquarePen className="size-3.5 shrink-0" /><span className="truncate">{setupName(row)}</span></button>)}</div></>}
        {!collapsed && data.chats.length > 0 && <><p className="mt-6 px-5 pb-1.5 text-xs font-medium text-faint-foreground">Conversations</p>
          <div className="flex flex-col gap-0.5 px-3">{data.chats.slice(0, 12).map(chat => <button type="button" key={chat.id} onClick={() => chat.bot_id && fleet.some(bot => bot.id === chat.bot_id) ? open(chat.bot_id, "chat") : classic(`chat/${chat.id}`)} className={cn(navRow, "hover:bg-accent")}><span className="truncate">{chat.title || "Untitled conversation"}</span></button>)}</div></>}
      </Sidebar>

      <div className="flex min-w-0 flex-1 flex-col">
        {!data.hasData ? data.error ? <div role="alert" className="flex flex-1 flex-col items-center justify-center gap-3 text-sm text-muted-foreground"><p>Deck could not load your workspace. {data.error}</p><button type="button" onClick={() => { void data.refresh(); }} className={cn(pill, "bg-raised hover:bg-accent")}>Retry</button></div> : <Loading />
        : empty ? <FirstRun draft={draft} setDraft={setDraft} submit={() => { void submit("new"); }} enabled={data.enabled} busy={busy} /> : <>
          <header className="shrink-0 overflow-hidden px-8 [scrollbar-gutter:stable]"><div className="mx-auto flex h-16 max-w-[1100px] items-center gap-3">
            <h1 className="truncate text-xl font-medium whitespace-nowrap">Good {hour < 12 ? "morning" : hour < 18 ? "afternoon" : "evening"}</h1>
            <span className={cn("shrink-0 items-center gap-2 rounded-full bg-raised px-3 py-1 text-xs whitespace-nowrap text-muted-foreground", selected ? "hidden" : "hidden sm:flex")}><Dot tone={busyBots.length ? "busy" : "idle"} /><SlidingNumber number={busyBots.length} /> working · <SlidingNumber number={needs.length} /> need you</span>
            <span className="flex-1" />
            <button type="button" onClick={() => setSearch(true)} className={cn(pill, "bg-raised text-muted-foreground hover:text-foreground")}><Search className="size-4" /><span className={selected ? "sr-only" : undefined}>Search</span><kbd className="rounded-md bg-background/60 px-1.5 font-mono text-[11px]">/</kbd></button>
          </div></header>
          <AnimatePresence>{data.error && <motion.p role="alert" initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="mx-8 shrink-0 overflow-hidden"><span className="mx-auto mb-2 block max-w-[1100px] rounded-[14px] bg-brand/10 px-4 py-2.5 text-sm text-brand">Connection lost. Showing the last known data. Actions are paused until Deck reconnects.</span></motion.p>}</AnimatePresence>
          <main className="@container relative min-h-0 flex-1 overflow-auto px-8 pb-4 [scrollbar-gutter:stable]"><div className="mx-auto flex max-w-[1100px] flex-col gap-3 pt-1">
            {uncertain && <div role="alert" className="flex items-center gap-3 rounded-[16px] bg-warning/10 px-4 py-3 text-sm text-warning"><span className="flex-1">The last request may have created a setup. Check In setup before you start another.</span><button type="button" onClick={() => setUncertain(false)} className={cn(pill, "bg-raised text-foreground hover:bg-accent")}>I checked</button></div>}
            <div className="grid grid-cols-1 gap-3 @lg:grid-cols-2 @4xl:grid-cols-4">
              <Live working={busyBots} fresh={fresh} open={open} paneScreen={selection.tab === "computer" ? selected?.id : undefined} />
              <Needs needs={needs} />
              <Stat label="Queued" value={fleet.reduce((sum, bot) => sum + bot.queued, 0)} note="tasks waiting" />
              <Stat label="Awake" value={fleet.filter(bot => bot.record.computer === "awake").length} note={`of ${fleet.length} computers`} />
              <FleetTile bots={fleet} open={id => open(id)} selected={selected?.id} more={() => setSearch(true)} />
              <section aria-label="Recently done" className={cn(tile, "@lg:col-span-2")}>
                <span className={tileLabel}>Recently done</span>
                {results.length ? <div className="-mx-2 flex flex-col">{results.map(({ task, bot }) => <button type="button" key={bot.id + task.id} onClick={() => open(bot.id, "activity")} className={rowButton}>
                  <TaskIcon state={task.state} /><span className={cn("min-w-0 flex-1 truncate text-sm", task.state === "failed" && "text-brand")}>{task.title}</span><span className="max-w-[40%] shrink-0 truncate text-xs text-faint-foreground">{bot.name}</span>
                </button>)}</div> : <p className="text-sm text-muted-foreground">Finished work appears here.</p>}
              </section>
            </div>
          </div></main>
          <div className="shrink-0 overflow-hidden px-8 pt-1 pb-4 [scrollbar-gutter:stable]"><Composer value={draft} onChange={setDraft} onSubmit={() => { void submit(); }} busy={busy} disabled={!fresh || (effectiveDestination === "new" && uncertain)} label="Message"
            placeholder={effectiveDestination === "new" ? "Describe a job for a new bot" : effectiveDestination === "deck" ? "Ask Deck anything" : `Give ${destinationName} a task`}
            destinations={destinations} destination={effectiveDestination} setDestination={setDestination}
            hint={busy ? effectiveDestination === "new" ? "Preparing your setup…" : "Sending…" : effectiveDestination === "new" ? "You review the setup before it starts. Credentials never go in this box." : effectiveDestination === "deck" ? "Starts a new conversation with Deck. Credentials never go in this box." : "Added to the bot’s queue. Credentials never go in this box."} /></div>
        </>}
      </div>

      <AnimatePresence initial={false}>{selected && <BotPane key={selected.id} item={selected} data={data} tab={selection.tab} setTab={tab => select(selected.id, tab)} close={() => select(undefined)} chatSlot={putSlot} fresh={fresh} morph={fleet.slice(0, tileLimit).some(bot => bot.id === selected.id)} />}</AnimatePresence>
    </div>

    {visited.map(id => { const record = data.bots.find(bot => bot.bot_id === id); return <BotChatHost key={id} id={record?.chat_id ?? null} name={fleet.find(bot => bot.id === id)?.name ?? "Bot"} slot={selected?.id === id ? slots[id] ?? null : null} hidden={selected?.id !== id || selection.tab !== "chat"} disabled={!fresh} draft={chatDrafts[id] ?? ""} setDraft={text => setChatDrafts(previous => ({ ...previous, [id]: text }))} putDraft={(_key, change) => putDraft(id, change)} refresh={data.refresh} scrolls={scrolls.current} scope={`bot/${id}`} />; })}

    <CommandDialog className="fluid rounded-[20px]! border-border bg-popover sm:max-w-xl" open={search} onOpenChange={setSearch} title="Search" description="Search bots, setups and conversations">
      <Command className="bg-transparent"><CommandInput placeholder="Search bots, setups and conversations" />
      <CommandList>
        <CommandEmpty>Nothing matches.</CommandEmpty>
        {fleet.length > 0 && <CommandGroup heading="Bots">{fleet.map(bot => <CommandItem key={bot.id} value={`${bot.name} ${bot.id}`} onSelect={() => { open(bot.id); setSearch(false); }} className="rounded-[12px]"><Dot tone={bot.tone} /><span className="min-w-0 flex-1 truncate">{bot.name}</span><span className="max-w-[45%] truncate text-xs text-muted-foreground">{bot.status}</span></CommandItem>)}</CommandGroup>}
        {setups.length > 0 && <CommandGroup heading="In setup">{setups.map(row => <CommandItem key={row.setup.id} value={`${setupName(row)} ${row.setup.id}`} onSelect={() => classic(`setup/${encodeURIComponent(row.setup.id)}`)} className="rounded-[12px]"><SquarePen /><span className="truncate">{setupName(row)}</span></CommandItem>)}</CommandGroup>}
        {data.chats.length > 0 && <CommandGroup heading="Conversations">{data.chats.map(chat => <CommandItem key={chat.id} value={`${chat.title || "Untitled conversation"} ${chat.id}`} onSelect={() => { setSearch(false); if (chat.bot_id && fleet.some(bot => bot.id === chat.bot_id)) open(chat.bot_id, "chat"); else classic(`chat/${chat.id}`); }} className="rounded-[12px]"><MessageCircle /><span className="truncate">{chat.title || "Untitled conversation"}</span></CommandItem>)}</CommandGroup>}
      </CommandList></Command>
    </CommandDialog>
  </LayoutGroup></Tooltip.Provider>
    <Toaster position="bottom-right" theme="dark" toastOptions={toasterOptions} />
  </MotionConfig>;
}

createRoot(document.getElementById("root")!).render(<StrictMode><RequestsProvider><App /></RequestsProvider></StrictMode>);
