// Development-only Home prototype on the deck design system. Sample data only. No API calls. The build does not include this entry.
import { Fragment, StrictMode, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { AnimatePresence, MotionConfig, motion } from "motion/react";
import { Bot, Home, MessageCircle, Monitor, Plus, RefreshCw, Search, SquarePen, X } from "lucide-react";
import { Toaster, toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Command, CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { BotCard, BotRow, Composer, Kbd, LeaderRow, RequestCard, SectionHeader, Status, StatusDot, TaskLine, type BotSummary } from "@/components/deck/parts";
import type { BotTask } from "@/lib/api";
import type { Tone } from "@/lib/bot";
import "../index.css";

type Need = { id: string; what: string; why?: string; action: string; choices: string[] };
type Message = { role: "owner" | "bot"; text: string };
type ProtoBot = BotSummary & { tasks: BotTask[]; need?: Need; messages: Message[]; computer: string };
type Setup = { id: string; name: string; label: string; what: string };

const task = (id: string, state: BotTask["state"], title: string): BotTask => ({ id, state, title, priority: 1, detail: null, source: "owner" });
const base: ProtoBot[] = [
  { id: "inbox", name: "Morning inbox brief", job: "Read my inbox each morning and summarize messages that need a reply.", tone: "busy", status: "Working", now: "Summarize 23 unread messages from overnight", queued: 2, computer: "Awake",
    tasks: [task("a1", "running", "Summarize 23 unread messages from overnight"), task("a2", "queued", "Draft replies to the two invoices"), task("a3", "queued", "Archive newsletters older than a week"), task("a4", "done", "Monday brief: 4 replies needed, 1 deadline")],
    messages: [{ role: "owner", text: "Summarize what came in overnight." }, { role: "bot", text: "Reading 23 unread messages. I will list the ones that need a reply first." }] },
  { id: "invoices", name: "Vendor invoice tracker", job: "Collect vendor invoices and log them in the sheet.", tone: "attention", status: "Spending ceiling needs your decision", queued: 1, computer: "Asleep",
    need: { id: "n1", what: "Raise the ceiling from $20.00 to $35.00?", why: "The supplier portal charges $0.50 per invoice download.", action: "Review", choices: ["Approve $35.00", "Deny"] },
    tasks: [task("b1", "held", "Download October invoices from the supplier portal"), task("b2", "done", "Log 14 September invoices in the sheet"), task("b3", "done", "Find the missing Acme invoice")],
    messages: [{ role: "bot", text: "The portal charges per download. October needs 30 downloads, which passes the $20.00 ceiling." }] },
  { id: "flights", name: "Flight search: Kochi to Berlin", job: "Find the cheapest direct or one-stop flight for the conference week.", tone: "attention", status: "Waiting for your answer", computer: "Awake",
    need: { id: "n2", what: "Which return date works: 14 or 15 December?", why: "The 15th is $112 cheaper but lands after midnight.", action: "Answer", choices: ["14 December", "15 December"] },
    tasks: [task("c1", "waiting", "Choose the return date"), task("c2", "done", "Compare 31 fares across 4 airlines")],
    messages: [{ role: "bot", text: "I found two good options. Which return date works for you?" }] },
  { id: "prices", name: "Competitor price watch", job: "Check three competitor pricing pages daily.", tone: "asleep", status: "Asleep", computer: "Asleep",
    tasks: [task("d1", "done", "Daily check: 3 price changes on two sites"), task("d2", "failed", "Daily check: one site blocked the browser")], messages: [] },
  { id: "research", name: "Weekly research digest", job: "Collect new papers on browser engines and write a digest.", tone: "idle", status: "Awake, idle", computer: "Awake",
    tasks: [task("e1", "done", "Digest for week 45: 6 papers, 2 worth reading")], messages: [] },
  { id: "receipts", name: "Expense receipts", job: "File receipts from email into the expense folder.", tone: "error", status: "Computer failed to start or save", computer: "Failed",
    tasks: [task("f1", "failed", "Wake the computer")], messages: [] },
];
const tones: Tone[] = ["busy", "idle", "asleep", "attention", "error"];
const statusFor: Record<Tone, string> = { busy: "Working", idle: "Awake, idle", asleep: "Asleep", attention: "Waiting for your answer", error: "Computer failed to start or save" };
const groupFor: Record<Tone, string> = { attention: "Needs you", error: "Failed", busy: "Working", idle: "Idle", asleep: "Asleep" };
function makeFleet(count: number): ProtoBot[] {
  return Array.from({ length: count }, (_, index) => {
    const source = base[index % base.length];
    if (index < base.length) return structuredClone(source);
    const tone = tones[(index * 7) % tones.length];
    return { ...structuredClone(source), id: `${source.id}-${index}`, name: `${source.name} ${Math.floor(index / base.length) + 1}`, tone, status: statusFor[tone], now: tone === "busy" ? source.tasks[0].title : null, need: tone === "attention" ? { ...base[2].need!, id: `n${index}` } : undefined, queued: tone === "busy" ? 1 : 0 };
  });
}
const chats = ["Plan the bot for tax documents", "Why did the price watch fail on Tuesday?", "Compare two VPS providers", "Summarize the bare release notes"];

function Fleet({ bots, open, selected }: { bots: ProtoBot[]; open: (id: string, tab?: string) => void; selected?: string }) {
  const [query, setQuery] = useState("");
  if (bots.length <= 6) return <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-3">{bots.map(bot => <BotCard key={bot.id} bot={bot} tasks={bot.tasks} onOpen={() => open(bot.id)} action={bot.need && <Button size="sm" variant="outline" onClick={() => open(bot.id, "requests")}>{bot.need.action}</Button>} />)}</div>;
  if (bots.length <= 15) return <div className="border border-border bg-card">{bots.map(bot => <BotRow key={bot.id} bot={bot} selected={bot.id === selected} onClick={() => open(bot.id)} />)}</div>;
  const shown = bots.filter(bot => bot.name.toLowerCase().includes(query.toLowerCase()));
  const groups = (["attention", "error", "busy", "idle", "asleep"] as Tone[]).map(tone => [tone, shown.filter(bot => bot.tone === tone)] as const).filter(([, list]) => list.length);
  return <div className="flex flex-col gap-3">
    <div className="relative max-w-sm"><Search aria-hidden="true" className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" /><Input aria-label="Filter bots" placeholder={`Filter ${bots.length} bots`} className="pl-8" value={query} onChange={event => setQuery(event.target.value)} /></div>
    <div className="border border-border bg-card"><Table>
      <TableHeader><TableRow><TableHead className="label-mono">Bot</TableHead><TableHead className="label-mono">Status</TableHead><TableHead className="label-mono">Now</TableHead><TableHead className="label-mono text-right">Queue</TableHead><TableHead className="label-mono">Computer</TableHead></TableRow></TableHeader>
      <TableBody>{groups.map(([tone, list]) => <Fragment key={tone}>
        <TableRow className="hover:bg-transparent"><TableCell colSpan={5} className="bg-surface py-1.5"><span className="label-mono">{groupFor[tone]} · {list.length}</span></TableCell></TableRow>
        {list.map(bot => <TableRow key={bot.id} data-state={bot.id === selected ? "selected" : undefined} className="cursor-pointer" onClick={() => open(bot.id)}>
          <TableCell className="max-w-56"><button type="button" className="block max-w-full truncate text-left text-ui font-medium outline-none focus-visible:underline" onClick={event => { event.stopPropagation(); open(bot.id); }}>{bot.name}</button></TableCell>
          <TableCell className="max-w-60"><Status tone={bot.tone}>{bot.status}</Status></TableCell>
          <TableCell className="max-w-72 truncate text-ui text-muted-foreground">{bot.now ?? "—"}</TableCell>
          <TableCell className="text-right font-mono text-2xs tabular-nums">{bot.queued || "—"}</TableCell>
          <TableCell className="font-mono text-2xs text-muted-foreground">{bot.computer}</TableCell>
        </TableRow>)}
      </Fragment>)}{!shown.length && <TableRow><TableCell colSpan={5} className="py-8 text-center text-ui text-muted-foreground">No bot matches “{query}”.</TableCell></TableRow>}</TableBody>
    </Table></div>
  </div>;
}

function BotPane({ bot, tab, setTab, close, answer, send, wake }: { bot: ProtoBot; tab: string; setTab: (tab: string) => void; close: () => void; answer: (choice: string) => void; send: (text: string) => void; wake: () => void }) {
  const [draft, setDraft] = useState("");
  const pane = useRef<HTMLElement>(null);
  useEffect(() => { pane.current?.focus({ preventScroll: true }); }, [bot.id]);
  return <motion.aside ref={pane} tabIndex={-1} key="pane" aria-label={bot.name} className="flex outline-none w-[44%] max-w-[680px] min-w-[420px] shrink-0 flex-col border-l border-border bg-surface"
    initial={{ opacity: 0, x: 24 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 24, transition: { duration: 0.14 } }}>
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border pr-2 pl-5">
      <span className="flex min-w-0 flex-1 flex-col"><span className="truncate text-sm font-medium">{bot.name}</span><Status tone={bot.tone} className="text-xs">{bot.status}</Status></span>
      <Button variant="ghost" size="icon-sm" aria-label="Close bot pane" onClick={close}><X /></Button>
    </header>
    <Tabs value={tab} onValueChange={setTab} className="min-h-0 flex-1 gap-0">
      <TabsList variant="line" className="h-10 w-full shrink-0 justify-start border-b border-border px-3">
        <TabsTrigger value="chat">Chat</TabsTrigger><TabsTrigger value="activity">Activity</TabsTrigger>
        <TabsTrigger value="requests">Requests{bot.need && <span className="ml-1 font-mono text-2xs text-brand tabular-nums">1</span>}</TabsTrigger><TabsTrigger value="computer">Computer</TabsTrigger>
      </TabsList>
      <TabsContent value="chat" className="flex min-h-0 flex-col">
        <div className="min-h-0 flex-1 overflow-auto px-5 py-4"><ol className="flex flex-col gap-3">
          {!bot.messages.length && <li className="py-10 text-center text-ui text-muted-foreground">No messages yet. Give {bot.name} a task below.</li>}
          {bot.messages.map((message, index) => <li key={index} className={message.role === "owner" ? "deck-enter ml-auto max-w-[80%] bg-accent px-3 py-2 text-sm" : "deck-enter max-w-[85%] text-sm"}>{message.text}</li>)}
        </ol></div>
        <div className="shrink-0 border-t border-border px-5 py-3"><Composer value={draft} onChange={setDraft} onSubmit={() => { send(draft); setDraft(""); }} label={`Task for ${bot.name}`} placeholder={`Give ${bot.name} a task`} hint={<><span>{bot.queued ? `Queued behind ${bot.queued} task${bot.queued === 1 ? "" : "s"}.` : "Starts when the bot is free."}</span><span className="flex items-center gap-1"><Kbd>Ctrl</Kbd><Kbd>Enter</Kbd></span></>} /></div>
      </TabsContent>
      <TabsContent value="activity" className="overflow-auto px-5 py-3"><ul className="flex flex-col divide-y divide-border">{bot.tasks.map(t => <TaskLine key={t.id} task={t} />)}</ul></TabsContent>
      <TabsContent value="requests" className="overflow-auto px-5 py-4">{bot.need ? <div className="flex flex-col gap-3 border border-border border-l-2 border-l-brand bg-card p-4">
        <p className="label-mono">Needs your decision</p><p className="text-sm">{bot.need.what}</p>{bot.need.why && <p className="text-ui text-muted-foreground">{bot.need.why}</p>}
        <div className="flex gap-2">{bot.need.choices.map((choice, index) => <Button key={choice} size="sm" variant={index === 0 ? "default" : "outline"} onClick={() => answer(choice)}>{choice}</Button>)}</div>
      </div> : <p className="py-10 text-center text-ui text-muted-foreground">Nothing needs you here.</p>}</TabsContent>
      <TabsContent value="computer" className="flex flex-col gap-3 overflow-auto px-5 py-4">
        <div className="flex items-center justify-between"><span className="inline-flex items-center gap-2 font-mono text-2xs text-muted-foreground"><Monitor className="size-3.5" />{bot.computer}</span>{bot.computer !== "Awake" && <Button size="sm" variant="outline" disabled={bot.computer === "Waking"} onClick={wake}>{bot.computer === "Waking" ? "Waking…" : "Wake"}</Button>}</div>
        <div className="grid aspect-video place-items-center border border-border bg-background text-ui text-muted-foreground">{bot.computer === "Awake" ? <span className="deck-enter flex flex-col items-center gap-2"><span className="h-24 w-40 border border-border bg-[linear-gradient(135deg,var(--accent),var(--background))]" />Live screen</span> : bot.computer === "Waking" ? "Waking the computer…" : bot.computer === "Failed" ? "The computer could not start. Request wake again." : "The computer is asleep. Wake it to see the screen."}</div>
      </TabsContent>
    </Tabs>
  </motion.aside>;
}

function FirstRun({ draft, setDraft, submit }: { draft: string; setDraft: (value: string) => void; submit: () => void }) {
  const ideas = ["Read my inbox each morning and summarize messages that need a reply.", "Check three competitor pricing pages daily and tell me what changed.", "Research a topic I choose and write a short brief with sources."];
  return <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col justify-center gap-8 px-6 py-16">
    <div className="flex flex-col gap-3"><p className="label-mono">No bots yet</p><h1 className="font-display text-display text-balance">What should your first bot do?</h1><p className="text-sm text-muted-foreground">Describe a job. You review its instructions, permissions and spending before it starts.</p></div>
    <Composer value={draft} onChange={setDraft} onSubmit={submit} label="Describe a job" placeholder="Describe its job" hint={<><span>Credentials never go in this box.</span><span className="flex items-center gap-1"><Kbd>Ctrl</Kbd><Kbd>Enter</Kbd></span></>} />
    <div className="flex flex-col"><SectionHeader label="Ideas" />{ideas.map(idea => <button key={idea} type="button" className="group flex items-center gap-3 border-b border-border py-3 text-left text-ui text-muted-foreground transition-colors duration-(--duration-fast) hover:text-foreground focus-visible:text-foreground focus-visible:outline-none" onClick={() => setDraft(idea)}><span className="flex-1">{idea}</span><span aria-hidden="true" className="font-mono text-2xs opacity-0 transition-opacity duration-(--duration-fast) group-hover:opacity-100 group-focus-visible:opacity-100">Use →</span></button>)}</div>
  </div>;
}

const sizes = [0, 6, 12, 40];
function App() {
  const initial = Number(new URLSearchParams(location.search).get("bots") ?? 6);
  const [size, setSize] = useState(sizes.includes(initial) ? initial : 6);
  const [bots, setBots] = useState(() => makeFleet(size));
  const [setups, setSetups] = useState<Setup[]>(() => size ? [{ id: "calendar", name: "Calendar assistant", label: "Sign-in needs your help", what: "Approve the Google sign-in on your phone" }] : []);
  const [selected, setSelected] = useState<string>();
  const [tab, setTab] = useState("activity");
  const [draft, setDraft] = useState("");
  const [destination, setDestination] = useState("new");
  const [search, setSearch] = useState(false);
  const [allNeeds, setAllNeeds] = useState(false);
  const bot = bots.find(item => item.id === selected);
  const needs = [...bots.filter(item => item.need).map(item => ({ key: item.id, bot: item.name, ...item.need!, open: () => open(item.id, "requests") })), ...setups.map(setup => ({ key: setup.id, bot: `${setup.name} · setup`, what: setup.what, why: undefined, action: "Open setup", open: () => toast("Opens the setup review (sample)") }))];
  const results = useMemo(() => bots.flatMap(item => item.tasks.filter(t => t.state === "done" || t.state === "failed").map(t => ({ t, item }))).slice(0, 6), [bots]);

  function switchSize(next: number) {
    setSize(next); setBots(makeFleet(next)); setSelected(undefined);
    setSetups(next ? [{ id: "calendar", name: "Calendar assistant", label: "Sign-in needs your help", what: "Approve the Google sign-in on your phone" }] : []);
    const url = new URL(location.href); url.searchParams.set("bots", String(next)); history.replaceState(null, "", url);
  }
  function open(id: string, nextTab = "activity") { setSelected(id); setTab(nextTab); }
  const patch = (id: string, change: (item: ProtoBot) => ProtoBot) => setBots(previous => previous.map(item => item.id === id ? change(item) : item));
  function answer(choice: string) {
    if (!bot) return;
    patch(bot.id, item => ({ ...item, need: undefined, tone: "busy", status: "Working", now: item.tasks.find(t => t.state === "waiting" || t.state === "held")?.title ?? item.now, tasks: item.tasks.map(t => t.state === "waiting" || t.state === "held" ? { ...t, state: "running" } : t), messages: [...item.messages, { role: "owner", text: choice }] }));
    toast.success(`Answer sent to ${bot.name}: ${choice}`); setTab("chat");
  }
  function sendToBot(id: string, text: string) {
    patch(id, item => ({ ...item, queued: (item.queued ?? 0) + 1, tasks: [...item.tasks.slice(0, 1), task(`q${Date.now()}`, "queued", text), ...item.tasks.slice(1)], messages: [...item.messages, { role: "owner", text }, { role: "bot", text: "Got it. I added this to my queue." }] }));
  }
  function submitHome() {
    const text = draft.trim(); if (!text) return;
    if (destination === "new" || !bots.length) {
      const id = `setup-${Date.now()}`;
      setSetups(previous => [...previous, { id, name: text.split(/\s+/).slice(0, 4).join(" "), label: "Draft ready for review", what: "Review instructions and spending before approval" }]);
      toast.success("Setup draft created. Ready for review.", { action: { label: "Open", onClick: () => toast("Opens the setup review (sample)") } });
      if (!bots.length) switchSize(6);
    } else if (destination === "deck") toast("Opens a new conversation with Deck (sample)");
    else { sendToBot(destination, text); toast.success(`Task sent to ${bots.find(item => item.id === destination)?.name}`); }
    setDraft("");
  }
  function wake() {
    if (!bot) return;
    const id = bot.id;
    patch(id, item => ({ ...item, computer: "Waking" }));
    setTimeout(() => patch(id, item => ({ ...item, computer: "Awake", tone: item.tone === "asleep" || item.tone === "error" ? "idle" : item.tone, status: item.tone === "asleep" || item.tone === "error" ? "Awake, idle" : item.status })), 1500);
  }

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const typing = event.target instanceof HTMLElement && (/^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName) || event.target.isContentEditable);
      if ((event.key === "k" && (event.metaKey || event.ctrlKey)) || (event.key === "/" && !typing)) { event.preventDefault(); setSearch(true); }
      else if (event.key === "Escape" && selected && !search && !document.querySelector("[role=listbox],[role=dialog]")) setSelected(undefined);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected, search]);

  return <MotionConfig reducedMotion="user" transition={{ duration: 0.2, ease: [0.23, 1, 0.32, 1] }}>
    <div className="flex h-dvh overflow-hidden bg-background text-foreground">
      <aside className="flex w-60 shrink-0 flex-col border-r border-border bg-sidebar">
        <div className="flex h-14 items-center justify-between pr-2 pl-5"><span className="font-display text-xl">deck</span><Button variant="ghost" size="icon-sm" aria-label="Refresh workspace" onClick={() => toast("Workspace is up to date")}><RefreshCw /></Button></div>
        <div className="px-3"><Button className="w-full" onClick={() => { setDestination("new"); document.querySelector<HTMLTextAreaElement>("main textarea, textarea")?.focus(); }}><Plus data-icon="inline-start" />New bot</Button></div>
        <nav aria-label="Main" className="mt-4 flex flex-col gap-px px-2">
          <button type="button" aria-current={!selected ? "page" : undefined} onClick={() => setSelected(undefined)} className="flex h-8 items-center gap-2.5 rounded-md px-3 text-ui text-muted-foreground hover:bg-accent/60 hover:text-foreground aria-[current=page]:bg-accent aria-[current=page]:font-medium aria-[current=page]:text-foreground"><Home className="size-4" />Home{needs.length > 0 && <span className="ml-auto font-mono text-2xs text-brand tabular-nums">{needs.length}</span>}</button>
          <button type="button" onClick={() => toast("Opens Conversations (sample)")} className="flex h-8 items-center gap-2.5 rounded-md px-3 text-ui text-muted-foreground hover:bg-accent/60 hover:text-foreground"><MessageCircle className="size-4" />Conversations</button>
        </nav>
        <div className="mt-6 min-h-0 flex-1 overflow-auto px-2 pb-4">
          {bots.length > 0 && <><p className="label-mono px-3 pb-2">Bots · {bots.length}</p>
            {bots.slice(0, 15).map(item => <button type="button" key={item.id} aria-current={item.id === selected || undefined} onClick={() => open(item.id)} className="flex h-8 w-full items-center gap-2.5 rounded-md px-3 text-left text-ui text-muted-foreground hover:bg-accent/60 hover:text-foreground aria-[current=true]:bg-accent aria-[current=true]:text-foreground"><StatusDot tone={item.tone} /><span className="truncate">{item.name}</span></button>)}
            {bots.length > 15 && <button type="button" className="px-3 pt-1 text-xs text-muted-foreground hover:text-foreground" onClick={() => setSearch(true)}>{bots.length - 15} more · search</button>}</>}
          {setups.length > 0 && <><p className="label-mono mt-5 px-3 pb-2">In setup</p>{setups.map(setup => <button type="button" key={setup.id} onClick={() => toast("Opens the setup review (sample)")} className="flex h-8 w-full items-center gap-2.5 rounded-md px-3 text-left text-ui text-muted-foreground hover:bg-accent/60 hover:text-foreground"><SquarePen className="size-3.5" /><span className="truncate">{setup.name}</span></button>)}</>}
          <p className="label-mono mt-5 px-3 pb-2">Conversations</p>
          {chats.map(chat => <button type="button" key={chat} onClick={() => toast(`Opens “${chat}” (sample)`)} className="flex h-8 w-full items-center rounded-md px-3 text-left text-ui text-muted-foreground hover:bg-accent/60 hover:text-foreground"><span className="truncate">{chat}</span></button>)}
        </div>
        <div className="flex h-10 items-center gap-2 border-t border-border px-5 text-xs text-muted-foreground"><StatusDot tone="idle" />Connected</div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {!bots.length ? <FirstRun draft={draft} setDraft={setDraft} submit={submitHome} /> : <>
          <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border px-6"><h1 className="text-base font-medium">Home</h1><span className="flex-1" /><Button variant="outline" size="sm" className="text-muted-foreground" onClick={() => setSearch(true)}><Search data-icon="inline-start" />Search<Kbd>/</Kbd></Button></header>
          <main className="min-h-0 flex-1 overflow-auto px-6 [scrollbar-gutter:stable]"><div className="mx-auto flex max-w-[1200px] flex-col gap-10 py-6">
            <section className="flex flex-col gap-3" aria-labelledby="needs"><SectionHeader index={0} id="needs" label="Needs you" count={needs.length} />
              {needs.length ? <div className={`grid items-start gap-3 ${bot ? "grid-cols-1" : "grid-cols-[repeat(auto-fill,minmax(280px,1fr))]"}`}><AnimatePresence initial={false}>{(allNeeds ? needs : needs.slice(0, 6)).map(need => <motion.div key={need.key} layout exit={{ opacity: 0, transition: { duration: 0.14 } }}><RequestCard bot={need.bot} what={need.what} why={need.why} action={need.action} onAct={need.open} /></motion.div>)}</AnimatePresence></div>
                : <p className="py-2 text-ui text-muted-foreground">Nothing needs you. Your bots are on their own.</p>}
              {needs.length > 6 && <Button variant="ghost" size="sm" className="self-start" onClick={() => setAllNeeds(!allNeeds)}>{allNeeds ? "Show fewer" : `Show ${needs.length - 6} more`}</Button>}
            </section>
            <section className="flex flex-col gap-3" aria-labelledby="bots"><SectionHeader index={1} id="bots" label="Bots" count={bots.length} /><Fleet bots={bots} open={open} selected={selected} /></section>
            <section className="flex flex-col" aria-labelledby="results"><SectionHeader index={2} id="results" label="Recent results" />
              {results.map(({ t, item }) => <button type="button" key={item.id + t.id} onClick={() => open(item.id)} className="rounded-sm py-1.5 text-left outline-none hover:bg-accent/40 focus-visible:ring-1 focus-visible:ring-ring"><LeaderRow name={<span className={t.state === "failed" ? "text-error-foreground" : undefined}>{t.title}</span>} value={item.name} /></button>)}</section>
          </div></main>
          <div className="shrink-0 border-t border-border px-6 py-3"><div className="mx-auto max-w-[1200px]"><Composer value={draft} onChange={setDraft} onSubmit={submitHome} label="Message" placeholder={destination === "new" ? "Describe a job for a new bot" : destination === "deck" ? "Ask Deck anything" : `Give ${bots.find(item => item.id === destination)?.name} a task`}
            destination={<div className="flex items-center gap-2"><span className="label-mono">To</span><Select value={destination} onValueChange={setDestination}><SelectTrigger size="sm" aria-label="Send to"><SelectValue /></SelectTrigger><SelectContent><SelectGroup><SelectItem value="new">New bot</SelectItem><SelectItem value="deck">Deck</SelectItem>{bots.slice(0, 20).map(item => <SelectItem key={item.id} value={item.id}>{item.name}</SelectItem>)}</SelectGroup></SelectContent></Select></div>}
            hint={<><span>{destination === "new" ? "You review the setup before it starts. Credentials never go in this box." : "Credentials never go in this box."}</span><span className="flex items-center gap-1"><Kbd>Ctrl</Kbd><Kbd>Enter</Kbd> to send</span></>} /></div></div>
        </>}
      </div>

      <AnimatePresence initial={false}>{bot && <BotPane key="pane" bot={bot} tab={tab} setTab={setTab} close={() => setSelected(undefined)} answer={answer} send={text => sendToBot(bot.id, text)} wake={wake} />}</AnimatePresence>
    </div>

    <CommandDialog className="sm:max-w-xl" open={search} onOpenChange={setSearch} title="Search" description="Search bots and conversations">
      <Command><CommandInput placeholder="Search bots and conversations" />
      <CommandList>
        <CommandEmpty>Nothing matches.</CommandEmpty>
        <CommandGroup heading="Bots">{bots.map(item => <CommandItem key={item.id} value={item.name} onSelect={() => { open(item.id); setSearch(false); }}><Bot /><span className="min-w-0 flex-1 truncate">{item.name}</span><Status tone={item.tone} className="max-w-[45%] text-xs">{item.status}</Status></CommandItem>)}</CommandGroup>
        <CommandGroup heading="Conversations">{chats.map(chat => <CommandItem key={chat} value={chat} onSelect={() => { setSearch(false); toast(`Opens “${chat}” (sample)`); }}><MessageCircle /><span className="truncate">{chat}</span></CommandItem>)}</CommandGroup>
      </CommandList></Command>
    </CommandDialog>

    <nav className="proto-picker" aria-label="Fleet size"><span className="proto-picker-label">Bots</span>{sizes.map(value => <button key={value} type="button" className="proto-picker-item" data-active={value === size || undefined} aria-current={value === size || undefined} onClick={() => switchSize(value)}>{value}</button>)}</nav>
    <Toaster position="bottom-right" theme="dark" />
  </MotionConfig>;
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
