// The fluid kit: a live look at the proposed feel, with real bots. It only reads data. Nothing here sends or changes anything.
// It is served at /next/kit. It uses @animate-ui/react (animated Radix primitives and text effects) and motion.
import { StrictMode, useEffect, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { AnimatePresence, LayoutGroup, MotionConfig, motion } from "motion/react";
import { ArrowUp, Bell, Bot, Check, ChevronDown, Home, MessageCircle, MoreHorizontal, PanelLeft, Plus, Search, Settings, Sparkles, X } from "lucide-react";
import { Toaster, toast } from "sonner";
import { Highlight, HighlightItem } from "@animate-ui/react/primitives/effects/highlight";
import { Tabs, TabsContent, TabsContents, TabsHighlight, TabsHighlightItem, TabsList, TabsTrigger } from "@animate-ui/react/primitives/tabs";
import { Dialog, DropdownMenu, Switch, Tooltip } from "@animate-ui/react/primitives/radix-ui";
import { ShimmeringText } from "@animate-ui/react/primitives/texts/shimmering";
import { TypingText, TypingTextCursor } from "@animate-ui/react/primitives/texts/typing";
import { SlidingNumber } from "@animate-ui/react/primitives/texts/sliding-number";
import { RotatingText, RotatingTextContainer } from "@animate-ui/react/primitives/texts/rotating";
import { cn } from "@/lib/cn";
import * as api from "@/lib/api";
import { describeBot, type Tone } from "@/lib/bot";
import { useWorkspace } from "../experience/useWorkspace";
import "../index.css";
import { Dot, Tip, soft, spring } from "../fluid/ui";

const computerLabel: Record<api.BotStatus["computer"], string> = { saved: "Asleep", waking: "Waking", awake: "Awake", saving: "Saving", failed: "Failed" };
type Kit = { id: string; name: string; job: string; tone: Tone; status: string; now: string | null; queued: number; computer: string; tasks: api.BotTask[] };

// Sample bots fill the grid when the server has few, so the motion can be judged with a fuller fleet.
const sample: Kit[] = [
  { id: "s-inbox", name: "Morning inbox brief", job: "Summarize overnight mail", tone: "busy", status: "Working", now: "Reading 23 unread messages", queued: 2, computer: "Awake", tasks: [] },
  { id: "s-flights", name: "Flight search", job: "Kochi to Berlin, conference week", tone: "attention", status: "Waiting for your answer", now: null, queued: 0, computer: "Awake", tasks: [] },
  { id: "s-prices", name: "Competitor price watch", job: "Three pricing pages, daily", tone: "asleep", status: "Asleep", now: null, queued: 0, computer: "Asleep", tasks: [] },
];

function Sidebar({ bots, selected, open, collapsed, setCollapsed }: { bots: Kit[]; selected?: string; open: (id: string) => void; collapsed: boolean; setCollapsed: (value: boolean) => void }) {
  const [nav, setNav] = useState("home");
  return <motion.aside animate={{ width: collapsed ? 68 : 248 }} transition={spring} className="flex shrink-0 flex-col overflow-hidden rounded-[20px] border border-border bg-sidebar">
    <div className="flex h-14 items-center gap-2 px-3">
      <Tip label={collapsed ? "Expand sidebar" : "Collapse sidebar"}><button type="button" aria-label="Toggle sidebar" onClick={() => setCollapsed(!collapsed)} className="grid size-9 shrink-0 place-items-center rounded-[12px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"><PanelLeft className="size-4" /></button></Tip>
      <AnimatePresence initial={false}>{!collapsed && <motion.span key="mark" initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -6 }} className="font-display text-xl">deck</motion.span>}</AnimatePresence>
    </div>
    <div className="px-3"><motion.button type="button" whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.97 }} transition={spring} onClick={() => toast("New bot", { description: "Demo only. Nothing was created." })} className="flex h-10 w-full items-center justify-center gap-2 overflow-hidden rounded-full bg-primary text-sm font-medium text-primary-foreground shadow-[0_6px_20px_-8px_var(--primary)]"><Plus className="size-4 shrink-0" />{!collapsed && <span className="truncate">New bot</span>}</motion.button></div>
    <Highlight controlledItems mode="parent" value={nav} hover className="rounded-[12px] bg-accent" transition={spring}>
      <nav aria-label="Main" className="mt-4 flex flex-col gap-0.5 px-3">
        {[{ id: "home", label: "Home", icon: Home }, { id: "chats", label: "Conversations", icon: MessageCircle }, { id: "inbox", label: "Requests", icon: Bell }].map(item => <HighlightItem key={item.id} value={item.id}>
          <button type="button" aria-current={nav === item.id ? "page" : undefined} onClick={() => setNav(item.id)} className="relative z-10 flex h-9 items-center gap-3 rounded-[12px] px-2.5 text-sm text-muted-foreground transition-colors aria-[current=page]:text-foreground hover:text-foreground"><item.icon className="size-4 shrink-0" />{!collapsed && <span className="truncate">{item.label}</span>}</button>
        </HighlightItem>)}
      </nav>
    </Highlight>
    {!collapsed && <p className="mt-6 px-5 pb-1.5 text-xs font-medium text-faint-foreground">Bots</p>}
    <Highlight controlledItems mode="parent" value={selected ?? null} hover className="rounded-[12px] bg-accent" transition={spring}>
      <div className={cn("flex flex-col gap-0.5 px-3", collapsed && "mt-6")}>
        {bots.map(bot => <HighlightItem key={bot.id} value={bot.id}>
          <button type="button" aria-current={selected === bot.id || undefined} onClick={() => open(bot.id)} className="relative z-10 flex h-9 items-center gap-3 rounded-[12px] px-3 text-left text-sm text-muted-foreground transition-colors aria-[current=true]:text-foreground hover:text-foreground"><Dot tone={bot.tone} />{!collapsed && <span className="truncate">{bot.name}</span>}</button>
        </HighlightItem>)}
      </div>
    </Highlight>
    <div className="mt-auto flex items-center gap-2 p-3"><Tip label="Settings"><button type="button" aria-label="Settings" className="grid size-9 place-items-center rounded-[12px] text-muted-foreground hover:bg-accent hover:text-foreground"><Settings className="size-4" /></button></Tip>{!collapsed && <span className="flex items-center gap-2 text-xs text-muted-foreground"><Dot tone="idle" />Connected</span>}</div>
  </motion.aside>;
}

function BotCard({ bot, open }: { bot: Kit; open: () => void }) {
  return <motion.button type="button" layoutId={`card-${bot.id}`} onClick={open} whileHover={{ y: -3 }} whileTap={{ scale: 0.985 }} transition={spring}
    className="soft-card flex min-h-44 w-full flex-col gap-3 p-5 text-left transition-[border-color] hover:border-input">
    <motion.span layout="position" className="flex items-center gap-3">
      <span className="grid size-10 place-items-center rounded-[16px] bg-raised text-muted-foreground"><Bot className="size-5" /></span>
      <span className="flex min-w-0 flex-col"><span className="truncate text-[15px] font-medium">{bot.name}</span><span className="flex items-center gap-2 text-xs text-muted-foreground"><Dot tone={bot.tone} />{bot.status}</span></span>
    </motion.span>
    <span className="line-clamp-2 text-sm text-muted-foreground">{bot.now ? <ShimmeringText text={bot.now} duration={1.6} /> : bot.tasks.find(task => task.state === "done")?.title ?? bot.job}</span>
    <span className="mt-auto flex items-center gap-2 text-xs text-faint-foreground"><span className="rounded-full bg-raised px-2.5 py-1">{bot.computer}</span>{bot.queued > 0 && <span className="flex items-center gap-1 rounded-full bg-raised px-2.5 py-1"><SlidingNumber number={bot.queued} /> queued</span>}</span>
  </motion.button>;
}

function Pane({ bot, close }: { bot: Kit; close: () => void }) {
  const [tab, setTab] = useState("activity");
  return <motion.aside layoutId={`card-${bot.id}`} transition={soft} aria-label={bot.name} className="soft-card z-10 flex w-[42%] max-w-[640px] min-w-[420px] shrink-0 flex-col overflow-hidden">
    <header className="flex items-center gap-3 p-5 pb-3">
      <span className="grid size-10 place-items-center rounded-[16px] bg-raised text-muted-foreground"><Bot className="size-5" /></span>
      <span className="flex min-w-0 flex-1 flex-col"><span className="truncate text-base font-medium">{bot.name}</span><span className="flex items-center gap-2 text-xs text-muted-foreground"><Dot tone={bot.tone} /><AnimatePresence mode="popLayout" initial={false}><motion.span key={bot.status} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={spring}>{bot.status}</motion.span></AnimatePresence></span></span>
      <DropdownMenu.Root>
        <DropdownMenu.Trigger asChild><button type="button" aria-label="Bot controls" className="grid size-9 place-items-center rounded-[12px] text-muted-foreground hover:bg-accent hover:text-foreground"><MoreHorizontal className="size-4" /></button></DropdownMenu.Trigger>
        <DropdownMenu.Portal><DropdownMenu.Content align="end" sideOffset={6} transition={spring} className="fluid z-50 min-w-52 rounded-[16px] border border-border bg-popover p-1.5 shadow-2xl">
          {["Pause automation", "Stop work", "Wake computer"].map(label => <DropdownMenu.Item key={label} onSelect={() => toast(label, { description: "Demo only. Nothing changed." })} className="flex h-9 cursor-default items-center rounded-[12px] px-3 text-sm outline-none data-highlighted:bg-accent">{label}</DropdownMenu.Item>)}
          <DropdownMenu.Separator className="my-1 h-px bg-border" />
          <DropdownMenu.Item onSelect={() => location.assign(`/#bot/${encodeURIComponent(bot.id)}`)} className="flex h-9 cursor-default items-center rounded-[12px] px-3 text-sm text-muted-foreground outline-none data-highlighted:bg-accent">Open in the current app</DropdownMenu.Item>
        </DropdownMenu.Content></DropdownMenu.Portal>
      </DropdownMenu.Root>
      <button type="button" aria-label="Close" onClick={close} className="grid size-9 place-items-center rounded-[12px] text-muted-foreground hover:bg-accent hover:text-foreground"><X className="size-4" /></button>
    </header>
    <Tabs value={tab} onValueChange={setTab} className="flex min-h-0 flex-1 flex-col">
      <TabsHighlight className="absolute inset-0 rounded-full bg-raised shadow-sm" transition={spring}>
        <TabsList className="mx-5 flex gap-1 rounded-full bg-background/60 p-1">
          {["chat", "activity", "requests", "computer"].map(value => <TabsHighlightItem key={value} value={value} className="flex-1"><TabsTrigger value={value} className="relative z-10 h-8 w-full rounded-full text-sm capitalize text-muted-foreground transition-colors data-[state=active]:text-foreground">{value}</TabsTrigger></TabsHighlightItem>)}
        </TabsList>
      </TabsHighlight>
      <TabsContents className="min-h-0 flex-1 overflow-hidden py-4" transition={soft}>
        <TabsContent value="chat" className="px-5"><div className="flex flex-col gap-3">
          <div className="ml-auto max-w-[80%] rounded-[16px] rounded-br-[6px] bg-primary/90 px-4 py-2.5 text-sm text-primary-foreground">What came in overnight?</div>
          <div className="max-w-[85%] rounded-[16px] rounded-bl-[6px] bg-raised px-4 py-2.5 text-sm"><TypingText duration={22} text="Reading 23 unread messages. Four need a reply: two invoices, a contract question and a meeting change."><TypingTextCursor className="ml-0.5 inline-block h-4 w-0.5 translate-y-0.5 bg-foreground" /></TypingText></div>
        </div></TabsContent>
        <TabsContent value="activity" className="px-5"><ul className="flex flex-col gap-2">{(bot.tasks.length ? bot.tasks : [{ id: "x", title: bot.now ?? "No tasks yet", state: bot.now ? "running" : "done", priority: 1, detail: null, source: "" } as api.BotTask]).slice(0, 8).map((task, index) =>
          <motion.li key={task.id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ ...soft, delay: index * 0.04 }} className="soft-raised flex items-center gap-3 px-4 py-3 text-sm">
            <span className={cn("grid size-6 shrink-0 place-items-center rounded-full", task.state === "done" ? "bg-success/15 text-success" : task.state === "failed" ? "bg-brand/15 text-brand" : "bg-info/15 text-info")}>{task.state === "done" ? <Check className="size-3.5" /> : task.state === "failed" ? <X className="size-3.5" /> : <Dot tone="busy" className="size-1.5" />}</span>
            <span className="min-w-0 flex-1 truncate">{task.state === "running" ? <ShimmeringText text={task.title} /> : task.title}</span>
          </motion.li>)}</ul></TabsContent>
        <TabsContent value="requests" className="px-5"><div className="soft-raised flex flex-col gap-3 p-4"><p className="text-sm font-medium">Nothing needs you</p><p className="text-sm text-muted-foreground">When {bot.name} needs a decision, it appears here and on Home.</p></div></TabsContent>
        <TabsContent value="computer" className="px-5"><div className="grid aspect-video place-items-center rounded-[16px] border border-border bg-background text-sm text-muted-foreground">{bot.computer === "Awake" ? <span className="flex flex-col items-center gap-3"><span className="orb" />Connecting to the screen…</span> : "The computer is asleep."}</div></TabsContent>
      </TabsContents>
    </Tabs>
  </motion.aside>;
}

const steps = ["Open Gmail", "Read 23 unread messages", "Group them by sender", "Write the summary", "Send the brief"];
function AiStates() {
  const [step, setStep] = useState(0);
  const [count, setCount] = useState(3);
  const [cost, setCost] = useState(0.42);
  useEffect(() => { const timer = setInterval(() => setStep(value => (value + 1) % (steps.length + 2)), 1400); return () => clearInterval(timer); }, []);
  const card = "soft-card flex flex-col gap-4 p-5";
  return <div className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-4">
    <div className={card}><p className="text-xs font-medium text-faint-foreground">Thinking</p><div className="flex items-center gap-3"><span className="orb" /><ShimmeringText className="text-[15px]" text="Thinking about your request…" duration={1.4} /></div></div>
    <div className={card}><p className="text-xs font-medium text-faint-foreground">Current step</p><div className="flex items-center gap-3 text-[15px]"><span className="dots flex gap-1 text-info"><span /><span /><span /></span><RotatingTextContainer text={["Opening Gmail", "Reading 23 messages", "Writing the summary"]} duration={2200} y={-14} className="h-6 overflow-hidden"><RotatingText /></RotatingTextContainer></div></div>
    <div className={card}><p className="text-xs font-medium text-faint-foreground">Streaming reply</p><p className="min-h-16 text-[15px] leading-relaxed"><TypingText loop holdDelay={2500} duration={28} text={["Your Monday brief is ready. Four messages need a reply.", "The flight on the 15th is $112 cheaper but lands after midnight."]}><TypingTextCursor className="ml-0.5 inline-block h-4 w-0.5 translate-y-0.5 bg-brand" /></TypingText></p></div>
    <div className={cn(card, "row-span-2")}><p className="text-xs font-medium text-faint-foreground">Multi-step task</p><ol className="flex flex-col gap-3">{steps.map((label, index) => { const done = index < step, now = index === step; return <li key={label} className="flex items-center gap-3 text-sm">
      <motion.span animate={{ scale: now ? 1.1 : 1, backgroundColor: done ? "rgb(70 196 134 / .18)" : now ? "rgb(106 167 255 / .18)" : "rgb(255 255 255 / .05)" }} transition={spring} className="grid size-6 shrink-0 place-items-center rounded-full">
        <AnimatePresence mode="popLayout" initial={false}>{done ? <motion.span key="d" initial={{ scale: 0.4, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ opacity: 0 }} transition={spring}><Check className="size-3.5 text-success" /></motion.span> : now ? <motion.span key="n" className="size-2 rounded-full bg-info breathe" /> : <motion.span key="w" className="size-1.5 rounded-full bg-faint-foreground" />}</AnimatePresence>
      </motion.span>
      <span className={cn("transition-colors", done ? "text-muted-foreground" : now ? "text-foreground" : "text-faint-foreground")}>{now ? <ShimmeringText text={label} /> : label}</span>
    </li>; })}</ol>
      <div className="h-1.5 overflow-hidden rounded-full bg-raised"><motion.div className="h-full rounded-full bg-gradient-to-r from-brand to-[#ff9a6b]" animate={{ width: `${Math.min(100, (step / steps.length) * 100)}%` }} transition={soft} /></div></div>
    <div className={card}><p className="text-xs font-medium text-faint-foreground">Loading a card</p><div className="flex items-center gap-3"><span className="skeleton size-10 rounded-[16px]" /><span className="flex flex-1 flex-col gap-2"><span className="skeleton h-3 w-2/3" /><span className="skeleton h-3 w-1/3" /></span></div><span className="skeleton h-12" /></div>
    <div className={card}><p className="text-xs font-medium text-faint-foreground">Live numbers</p><div className="flex items-end gap-6"><span className="flex flex-col"><span className="text-3xl font-medium tabular-nums"><SlidingNumber number={count} /></span><span className="text-xs text-muted-foreground">queued</span></span><span className="flex flex-col"><span className="text-3xl font-medium tabular-nums">$<SlidingNumber number={cost} decimalPlaces={2} /></span><span className="text-xs text-muted-foreground">spent today</span></span></div>
      <div className="flex gap-2"><motion.button type="button" whileTap={{ scale: 0.94 }} onClick={() => { setCount(value => value + 1); setCost(value => +(value + 0.07).toFixed(2)); }} className="pill bg-raised px-4 py-1.5 text-sm">Add a task</motion.button><motion.button type="button" whileTap={{ scale: 0.94 }} onClick={() => setCount(value => Math.max(0, value - 1))} className="pill px-4 py-1.5 text-sm text-muted-foreground hover:bg-accent">Finish one</motion.button></div></div>
  </div>;
}

function Controls() {
  const [on, setOn] = useState(true);
  return <div className="soft-card flex flex-wrap items-center gap-3 p-5">
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild><button type="button" className="pill flex h-10 items-center gap-2 bg-raised px-4 text-sm">Menu<ChevronDown className="size-4 text-muted-foreground" /></button></DropdownMenu.Trigger>
      <DropdownMenu.Portal><DropdownMenu.Content sideOffset={8} transition={spring} className="fluid z-50 min-w-48 rounded-[16px] border border-border bg-popover p-1.5 shadow-2xl">{["Rename", "Duplicate", "Retire"].map(label => <DropdownMenu.Item key={label} className="flex h-9 cursor-default items-center rounded-[12px] px-3 text-sm outline-none data-highlighted:bg-accent">{label}</DropdownMenu.Item>)}</DropdownMenu.Content></DropdownMenu.Portal>
    </DropdownMenu.Root>
    <Dialog.Root>
      <Dialog.Trigger asChild><button type="button" className="pill h-10 bg-raised px-4 text-sm">Dialog</button></Dialog.Trigger>
      <Dialog.Portal><Dialog.Overlay className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm" /><Dialog.Content from="bottom" className="fluid soft-card fixed top-1/2 left-1/2 z-50 flex w-[420px] -translate-x-1/2 -translate-y-1/2 flex-col gap-3 p-6">
        <Dialog.Title className="text-lg font-medium">Raise the spending ceiling?</Dialog.Title>
        <Dialog.Description className="text-sm text-muted-foreground">The supplier portal charges $0.50 per download. October needs 30 downloads.</Dialog.Description>
        <div className="mt-2 flex justify-end gap-2"><Dialog.Close asChild><button type="button" className="pill h-10 px-4 text-sm text-muted-foreground hover:bg-accent">Not now</button></Dialog.Close><Dialog.Close asChild><button type="button" onClick={() => toast.success("Approved", { description: "Demo only. Nothing changed." })} className="pill h-10 bg-primary px-5 text-sm font-medium text-primary-foreground">Approve $35</button></Dialog.Close></div>
      </Dialog.Content></Dialog.Portal>
    </Dialog.Root>
    <Tip label="Tooltips spring in"><button type="button" className="pill h-10 bg-raised px-4 text-sm">Tooltip</button></Tip>
    <label className="flex h-10 items-center gap-3 rounded-full bg-raised pr-2 pl-4 text-sm">Automation<Switch.Root checked={on} onCheckedChange={setOn} className="relative flex h-6 w-10 items-center rounded-full bg-background p-0.5 transition-colors data-[state=checked]:bg-success"><Switch.Thumb className="block size-5 rounded-full bg-foreground shadow" /></Switch.Root></label>
    <button type="button" onClick={() => toast.success("Task received by Morning inbox brief", { description: "It starts after 2 queued tasks." })} className="pill h-10 bg-raised px-4 text-sm">Toast</button>
  </div>;
}

function Composer() {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const send = () => { if (!text.trim() || sending) return; setSending(true); setTimeout(() => { setSending(false); setText(""); toast("Demo only", { description: "The kit does not send messages." }); }, 1400); };
  return <form onSubmit={event => { event.preventDefault(); send(); }} className="soft-card mx-auto flex w-full max-w-3xl items-end gap-2 rounded-[22px] p-2 pl-4 transition-shadow focus-within:shadow-[0_0_0_4px_rgb(242_85_90_/_.12)]">
    <button type="button" className="pill mb-1 flex h-8 shrink-0 items-center gap-1.5 bg-raised px-3 text-xs text-muted-foreground"><Sparkles className="size-3.5 text-brand" />New bot<ChevronDown className="size-3" /></button>
    <textarea rows={1} value={text} onChange={event => setText(event.target.value)} onKeyDown={event => { if ((event.metaKey || event.ctrlKey) && event.key === "Enter") send(); }} aria-label="Message" placeholder="Describe a job, or ask Deck anything" className="max-h-40 min-h-10 flex-1 resize-none bg-transparent py-2.5 text-[15px] outline-none [field-sizing:content] placeholder:text-faint-foreground" />
    <motion.button type="submit" aria-label="Send" disabled={!text.trim()} whileTap={{ scale: 0.9 }} transition={spring} className="grid size-10 shrink-0 place-items-center rounded-full bg-primary text-primary-foreground disabled:opacity-40">
      <AnimatePresence mode="popLayout" initial={false}>{sending ? <motion.span key="s" initial={{ scale: 0.5, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.5, opacity: 0 }} className="size-4 animate-spin rounded-full border-2 border-white/40 border-t-white" /> : <motion.span key="a" initial={{ y: 8, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: -8, opacity: 0 }}><ArrowUp className="size-4" /></motion.span>}</AnimatePresence>
    </motion.button>
  </form>;
}

function Section({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return <section className="flex flex-col gap-4"><div className="flex items-baseline gap-3"><h2 className="text-lg font-medium">{title}</h2>{note && <p className="text-sm text-muted-foreground">{note}</p>}</div>{children}</section>;
}

function App() {
  const data = useWorkspace();
  const [selected, setSelected] = useState<string>();
  const [collapsed, setCollapsed] = useState(false);
  const names = new Map(data.rows.map(row => [row.setup.id, row.setup.draft.name]));
  const real: Kit[] = data.bots.map(record => {
    const described = describeBot(record);
    return { id: record.bot_id, name: names.get(record.bot_id) ?? "Default bot", job: data.rows.find(row => row.setup.id === record.bot_id)?.setup.draft.instructions ?? "", tone: described.tone, status: described.label, now: record.working?.title ?? null, queued: record.queued, computer: computerLabel[record.computer], tasks: record.tasks };
  });
  const bots = [...real, ...sample];
  const bot = bots.find(item => item.id === selected);
  useEffect(() => { const onKey = (event: KeyboardEvent) => { if (event.key === "Escape" && !document.querySelector("[role=dialog],[role=menu]")) setSelected(undefined); }; addEventListener("keydown", onKey); return () => removeEventListener("keydown", onKey); }, []);
  const hour = new Date().getHours();
  return <MotionConfig reducedMotion="user"><Tooltip.Provider delayDuration={300}>
    <LayoutGroup>
      <div className="fluid relative flex h-dvh gap-2 overflow-hidden p-2">
        <Sidebar bots={bots} selected={selected} open={setSelected} collapsed={collapsed} setCollapsed={setCollapsed} />
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="shrink-0 px-8"><div className="mx-auto flex h-16 max-w-[1100px] items-center gap-3"><h1 className="text-xl font-medium">Good {hour < 12 ? "morning" : hour < 18 ? "afternoon" : "evening"}</h1><span className="rounded-full bg-raised px-2.5 py-1 text-xs text-muted-foreground">Fluid kit · demo, nothing is sent</span><span className="flex-1" /><button type="button" className="pill flex h-9 items-center gap-2 bg-raised px-4 text-sm text-muted-foreground"><Search className="size-4" />Search</button></div></header>
          <main className="relative min-h-0 flex-1 overflow-auto px-8 pb-6"><div className="mx-auto flex max-w-[1100px] flex-col gap-10 pt-2">
            <Section title="Your bots" note={`${real.length} real, ${sample.length} sample. Click one: the card grows into the pane.`}>
              <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-4">{bots.map((item, index) => <motion.div key={item.id} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ ...soft, delay: index * 0.05 }}>{item.id === selected ? <div className="min-h-44 rounded-[18px] border border-dashed border-border" /> : <BotCard bot={item} open={() => setSelected(item.id)} />}</motion.div>)}</div>
            </Section>
            <Section title="AI states" note="Loading and working states for an AI product."><AiStates /></Section>
            <Section title="Controls" note="Menus, dialogs, tooltips and switches spring in."><Controls /></Section>
          </div></main>
          <div className="shrink-0 px-8 pb-5"><Composer /></div>
        </div>
        <AnimatePresence>{bot && <Pane key={bot.id} bot={bot} close={() => setSelected(undefined)} />}</AnimatePresence>
      </div>
    </LayoutGroup>
    <Toaster position="bottom-right" theme="dark" toastOptions={{ className: "!rounded-[16px] !border-border !bg-popover" }} />
  </Tooltip.Provider></MotionConfig>;
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
