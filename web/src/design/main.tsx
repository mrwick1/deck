// Development-only showcase of the deck design system. Sample data only. The build does not include this entry.
import { Fragment, StrictMode, useMemo, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { AnimatePresence, MotionConfig, motion } from "motion/react";
import { Home, MessageCircle, Moon, Plus, RefreshCw, Search, Sun, X } from "lucide-react";
import { Toaster, toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/spinner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { BotCard, BotRow, Composer, Kbd, LeaderRow, RequestCard, SectionHeader, Status, StatusDot, TaskLine, type BotSummary } from "@/components/deck/parts";
import type { BotTask } from "@/lib/api";
import type { Tone } from "@/lib/bot";
import "../index.css";

const task = (id: string, state: BotTask["state"], title: string): BotTask => ({ id, state, title, priority: 1, detail: null, source: "owner" });
const fleet: (BotSummary & { tasks: BotTask[] })[] = [
  { id: "inbox", name: "Morning inbox brief", tone: "busy", status: "Working", now: "Summarize 23 unread messages from overnight", queued: 2, computer: "Awake", tasks: [task("a1", "running", "Summarize 23 unread messages from overnight"), task("a2", "queued", "Draft replies to the two invoices"), task("a3", "queued", "Archive newsletters older than a week"), task("a4", "done", "Monday brief: 4 replies needed, 1 deadline")] },
  { id: "invoices", name: "Vendor invoice tracker", tone: "attention", status: "Spending ceiling needs your decision", queued: 1, computer: "Asleep", tasks: [task("b1", "held", "Download October invoices from the supplier portal"), task("b2", "done", "Log 14 September invoices in the sheet"), task("b3", "done", "Find the missing Acme invoice")] },
  { id: "flights", name: "Flight search: Kochi to Berlin", tone: "attention", status: "Waiting for your answer", computer: "Awake", tasks: [task("c1", "waiting", "Choose the return date"), task("c2", "done", "Compare 31 fares across 4 airlines")] },
  { id: "prices", name: "Competitor price watch", tone: "asleep", status: "Asleep", computer: "Asleep", tasks: [task("d1", "done", "Daily check: 3 price changes on two sites"), task("d2", "failed", "Daily check: one site blocked the browser")] },
  { id: "research", name: "Weekly research digest", tone: "idle", status: "Awake, idle", computer: "Awake", tasks: [task("e1", "done", "Digest for week 45: 6 papers, 2 worth reading")] },
  { id: "broken", name: "Expense receipts", tone: "error", status: "Computer failed to start or save", computer: "Failed", tasks: [task("f1", "failed", "Wake the computer")] },
];
const tones: Tone[] = ["busy", "idle", "asleep", "attention", "error"];
const statusFor: Record<Tone, string> = { busy: "Working", idle: "Awake, idle", asleep: "Asleep", attention: "Waiting for your answer", error: "Computer failed to start or save" };
const groupFor: Record<Tone, string> = { attention: "Needs you", error: "Failed", busy: "Working", idle: "Idle", asleep: "Asleep" };
const many = (count: number) => Array.from({ length: count }, (_, index) => {
  const base = fleet[index % fleet.length], tone = index < fleet.length ? base.tone : tones[(index * 7) % tones.length];
  return { ...base, id: `${base.id}-${index}`, name: index < fleet.length ? base.name : `${base.name} ${Math.floor(index / fleet.length) + 1}`, tone, status: index < fleet.length ? base.status : statusFor[tone], now: tone === "busy" ? base.tasks[0].title : null };
});

function Section({ index, title, note, children }: { index: number; title: string; note?: string; children: ReactNode }) {
  return <section className="flex flex-col gap-6 py-12" aria-labelledby={`s${index}`}>
    <SectionHeader index={index} label={title} id={`s${index}`} />
    {note && <p className="max-w-[65ch] text-ui text-muted-foreground">{note}</p>}
    {children}
  </section>;
}

function Swatch({ name, token, text }: { name: string; token: string; text?: string }) {
  return <div className="flex flex-col gap-2">
    <div className="h-14 border border-border" style={{ background: `var(${token})` }}>{text && <span className="grid h-full place-items-center text-ui" style={{ color: `var(${text})` }}>Aa</span>}</div>
    <div><p className="text-ui">{name}</p><p className="font-mono text-2xs text-muted-foreground">{token}</p></div>
  </div>;
}

/** The bot list picks its form from the fleet size: cards up to 6, rows up to 15, a table above that. */
function Fleet({ bots, select, selected }: { bots: ReturnType<typeof many>; select: (id: string) => void; selected?: string }) {
  const [query, setQuery] = useState("");
  if (bots.length <= 6) return <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-3">{bots.map(bot => <BotCard key={bot.id} bot={bot} tasks={bot.tasks} onOpen={() => select(bot.id)} />)}</div>;
  if (bots.length <= 15) return <div className="border border-border bg-card">{bots.map(bot => <BotRow key={bot.id} bot={bot} selected={bot.id === selected} onClick={() => select(bot.id)} />)}</div>;
  const shown = bots.filter(bot => bot.name.toLowerCase().includes(query.toLowerCase()));
  const groups = (["attention", "error", "busy", "idle", "asleep"] as Tone[]).map(tone => [tone, shown.filter(bot => bot.tone === tone)] as const).filter(([, list]) => list.length);
  return <div className="flex flex-col gap-3">
    <div className="relative max-w-sm"><Search aria-hidden="true" className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" /><Input aria-label="Filter bots" placeholder={`Filter ${bots.length} bots`} className="pl-8" value={query} onChange={event => setQuery(event.target.value)} /></div>
    <div className="border border-border bg-card"><Table>
      <TableHeader><TableRow><TableHead className="label-mono">Bot</TableHead><TableHead className="label-mono">Status</TableHead><TableHead className="label-mono">Now</TableHead><TableHead className="label-mono text-right">Queue</TableHead><TableHead className="label-mono">Computer</TableHead></TableRow></TableHeader>
      <TableBody>{groups.map(([tone, list]) => <Fragment key={tone}>
        <TableRow className="hover:bg-transparent"><TableCell colSpan={5} className="bg-surface py-1.5"><span className="label-mono">{groupFor[tone]} · {list.length}</span></TableCell></TableRow>
        {list.map(bot => <TableRow key={bot.id} data-state={bot.id === selected ? "selected" : undefined} className="cursor-pointer" onClick={() => select(bot.id)}>
          <TableCell className="max-w-56"><button type="button" className="truncate text-left text-ui font-medium outline-none focus-visible:underline" onClick={() => select(bot.id)}>{bot.name}</button></TableCell>
          <TableCell><Status tone={bot.tone}>{bot.status}</Status></TableCell>
          <TableCell className="max-w-72 truncate text-ui text-muted-foreground">{bot.now ?? "—"}</TableCell>
          <TableCell className="text-right font-mono text-2xs tabular-nums">{bot.queued || "—"}</TableCell>
          <TableCell className="font-mono text-2xs text-muted-foreground">{bot.computer}</TableCell>
        </TableRow>)}
      </Fragment>)}{!shown.length && <TableRow><TableCell colSpan={5} className="py-8 text-center text-ui text-muted-foreground">No bot matches “{query}”.</TableCell></TableRow>}</TableBody>
    </Table></div>
  </div>;
}

function LayoutDemo() {
  const [size, setSize] = useState("6");
  const [selected, setSelected] = useState<string | undefined>();
  const [draft, setDraft] = useState("");
  const bots = useMemo(() => many(Number(size)), [size]);
  const bot = bots.find(item => item.id === selected);
  return <div className="flex flex-col gap-4">
    <div className="flex items-center gap-3"><span className="label-mono">Fleet size</span>
      <ToggleGroup type="single" variant="outline" size="sm" value={size} onValueChange={value => { if (value) { setSize(value); setSelected(undefined); } }} aria-label="Fleet size">
        <ToggleGroupItem value="6">6 · cards</ToggleGroupItem><ToggleGroupItem value="12">12 · rows</ToggleGroupItem><ToggleGroupItem value="40">40 · table</ToggleGroupItem>
      </ToggleGroup>
      <span className="text-xs text-muted-foreground">Click a bot. The pane opens beside Home; Home stays visible.</span>
    </div>
    <div className="flex h-[760px] overflow-hidden border border-border bg-background">
      <aside className="flex w-60 shrink-0 flex-col border-r border-border bg-sidebar">
        <div className="flex h-14 items-center justify-between pr-2 pl-5"><span className="font-display text-xl text-foreground">deck</span><Button variant="ghost" size="icon-sm" aria-label="Refresh workspace"><RefreshCw /></Button></div>
        <div className="px-3"><Button className="w-full"><Plus data-icon="inline-start" />New bot</Button></div>
        <nav aria-label="Main" className="mt-4 flex flex-col gap-px px-2">
          <button type="button" aria-current="page" className="flex h-8 items-center gap-2.5 rounded-md bg-accent px-3 text-ui font-medium"><Home className="size-4" />Home<span className="ml-auto font-mono text-2xs text-brand tabular-nums">3</span></button>
          <button type="button" className="flex h-8 items-center gap-2.5 rounded-md px-3 text-ui text-muted-foreground hover:bg-accent/60 hover:text-foreground"><MessageCircle className="size-4" />Conversations</button>
        </nav>
        <div className="mt-6 min-h-0 flex-1 overflow-auto px-2 pb-4">
          <p className="label-mono px-3 pb-2">Bots · {bots.length}</p>
          {bots.slice(0, 14).map(item => <button type="button" key={item.id} aria-current={item.id === selected || undefined} onClick={() => setSelected(item.id)} className="flex h-8 w-full items-center gap-2.5 rounded-md px-3 text-left text-ui text-muted-foreground hover:bg-accent/60 hover:text-foreground aria-[current=true]:bg-accent aria-[current=true]:text-foreground"><StatusDot tone={item.tone} /><span className="truncate">{item.name}</span></button>)}
          {bots.length > 14 && <p className="px-3 pt-1 text-xs text-muted-foreground">{bots.length - 14} more in the table</p>}
        </div>
        <div className="flex h-10 items-center gap-2 border-t border-border px-5 text-xs text-muted-foreground"><StatusDot tone="idle" />Connected</div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border px-6"><h1 className="text-base font-medium">Home</h1><span className="flex-1" /><Button variant="outline" size="sm" className="text-muted-foreground"><Search data-icon="inline-start" />Search<Kbd>/</Kbd></Button></header>
        <div className="min-h-0 flex-1 overflow-auto px-6 [scrollbar-gutter:stable]">
          <div className="flex flex-col gap-8 py-6">
            <section className="flex flex-col gap-3" aria-label="Needs you"><SectionHeader index={0} label="Needs you" count={3} />
              <div className={`grid items-start gap-3 ${bot ? "grid-cols-1" : "grid-cols-3"}`}>
                <RequestCard bot="Vendor invoice tracker" what="Raise the ceiling from $20.00 to $35.00?" why="The supplier portal charges $0.50 per download." action="Review" onAct={() => setSelected(bots.find(item => item.tone === "attention")?.id)} />
                <RequestCard bot="Flight search: Kochi to Berlin" what="Which return date works: 14 or 15 December?" action="Answer" onAct={() => setSelected(bots.filter(item => item.tone === "attention")[1]?.id)} />
                <RequestCard bot="Calendar assistant · setup" what="Approve the Google sign-in on your phone" action="Open setup" onAct={() => toast("Opens the setup (sample)")} />
              </div></section>
            <section className="flex flex-col gap-3" aria-label="Bots"><SectionHeader index={1} label="Bots" count={bots.length} /><Fleet bots={bots} select={setSelected} selected={selected} /></section>
            <section className="flex flex-col gap-1" aria-label="Recent results"><SectionHeader index={2} label="Recent results" />
              {fleet.flatMap(item => item.tasks.filter(t => t.state === "done" || t.state === "failed").map(t => <LeaderRow key={t.id} className="py-1.5" name={t.title} value={item.name} />)).slice(0, 5)}</section>
          </div>
        </div>
        <div className="shrink-0 border-t border-border px-6 py-3"><Composer value={draft} onChange={setDraft} onSubmit={() => { toast.success("Setup draft created (sample)"); setDraft(""); }} label="Describe a job" placeholder="Describe a job for a new bot" destination={<div className="flex items-center gap-2"><span className="label-mono">To</span><Select defaultValue="new"><SelectTrigger size="sm" aria-label="Send to"><SelectValue /></SelectTrigger><SelectContent><SelectGroup><SelectItem value="new">New bot</SelectItem><SelectItem value="deck">Deck</SelectItem>{fleet.slice(0, 3).map(item => <SelectItem key={item.id} value={item.id}>{item.name}</SelectItem>)}</SelectGroup></SelectContent></Select></div>} hint={<><span>Credentials never go in this box.</span><span className="flex items-center gap-1"><Kbd>Ctrl</Kbd><Kbd>Enter</Kbd> to send</span></>} /></div>
      </div>
      <AnimatePresence initial={false}>{bot && <motion.aside key="pane" aria-label={bot.name} className="flex w-[44%] max-w-[640px] shrink-0 flex-col border-l border-border bg-surface" initial={{ opacity: 0, x: 24 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 24, transition: { duration: 0.14 } }} onKeyDown={event => { if (event.key === "Escape") setSelected(undefined); }}>
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border pr-2 pl-5"><span className="flex min-w-0 flex-1 flex-col"><span className="truncate text-sm font-medium">{bot.name}</span><Status tone={bot.tone} className="text-xs">{bot.status}</Status></span><Button variant="ghost" size="icon-sm" aria-label="Close bot pane" onClick={() => setSelected(undefined)}><X /></Button></header>
        <Tabs defaultValue="activity" className="min-h-0 flex-1 gap-0">
          <TabsList variant="line" className="h-10 w-full justify-start border-b border-border px-3"><TabsTrigger value="chat">Chat</TabsTrigger><TabsTrigger value="activity">Activity</TabsTrigger><TabsTrigger value="requests">Requests</TabsTrigger><TabsTrigger value="computer">Computer</TabsTrigger></TabsList>
          <TabsContent value="activity" className="overflow-auto px-5 py-4"><ul className="flex flex-col divide-y divide-border">{bot.tasks.map(t => <TaskLine key={t.id} task={t} />)}</ul></TabsContent>
          <TabsContent value="chat" className="px-5 py-4 text-ui text-muted-foreground">The bot conversation renders here.</TabsContent>
          <TabsContent value="requests" className="px-5 py-4 text-ui text-muted-foreground">Requests for this bot render here.</TabsContent>
          <TabsContent value="computer" className="px-5 py-4 text-ui text-muted-foreground">The live screen renders here while the computer is awake.</TabsContent>
        </Tabs>
      </motion.aside>}</AnimatePresence>
    </div>
  </div>;
}

function Showcase() {
  const [dark, setDark] = useState(true);
  const [draft, setDraft] = useState("");
  const toggle = () => { document.documentElement.classList.toggle("dark", !dark); setDark(!dark); };
  return <MotionConfig reducedMotion="user" transition={{ duration: 0.2, ease: [0.23, 1, 0.32, 1] }}>
    <div className="mx-auto max-w-[1280px] px-10">
      <header className="flex items-end justify-between gap-6 border-b border-border pt-16 pb-10">
        <div className="flex flex-col gap-3"><p className="label-mono">Design system · development only</p><h1 className="font-display text-display text-foreground">deck</h1><p className="max-w-[60ch] text-sm text-muted-foreground">Near black, one red accent, Geist for work, Geist Mono for labels and numbers, Redaction for rare large moments. Square surfaces; controls get a 4px corner.</p></div>
        <Button variant="outline" onClick={toggle} aria-label={dark ? "Use light theme" : "Use dark theme"}>{dark ? <Sun data-icon="inline-start" /> : <Moon data-icon="inline-start" />}{dark ? "Light" : "Dark"}</Button>
      </header>

      <Section index={0} title="Color" note="Surfaces step up from the background. Red is the brand: the primary action, focus and things that need the owner. Status colors are separate and always come with a word.">
        <div className="grid grid-cols-6 gap-4">
          <Swatch name="Background" token="--background" text="--foreground" /><Swatch name="Surface" token="--surface" text="--foreground" /><Swatch name="Card" token="--card" text="--foreground" /><Swatch name="Raised" token="--raised" text="--foreground" /><Swatch name="Popover" token="--popover" text="--foreground" /><Swatch name="Accent (hover)" token="--accent" text="--foreground" />
          <Swatch name="Foreground" token="--foreground" /><Swatch name="Muted text" token="--muted-foreground" /><Swatch name="Faint (icons only)" token="--faint-foreground" /><Swatch name="Border" token="--border" /><Swatch name="Input" token="--input" /><Swatch name="Brand" token="--brand" />
          <Swatch name="Primary" token="--primary" text="--primary-foreground" /><Swatch name="Working" token="--info" /><Swatch name="Needs you" token="--warning" /><Swatch name="Healthy" token="--success" /><Swatch name="Failed" token="--error" /><Swatch name="Ring" token="--ring" />
        </div>
      </Section>

      <Section index={1} title="Type" note="Geist runs the interface at 13 and 14px. Mono carries labels, counts, times and keys. Redaction appears only in large, rare places.">
        <div className="flex flex-col gap-5">
          <LeaderRow name={<span className="font-display text-display">Good morning</span>} value="display · Redaction 40" />
          <LeaderRow name={<span className="text-xl font-medium tracking-tight">Flight search: Kochi to Berlin</span>} value="title · Geist 20 / 500" />
          <LeaderRow name={<span className="text-base font-medium">Home</span>} value="heading · Geist 16 / 500" />
          <LeaderRow name={<span className="text-sm">Which return date works: 14 or 15 December?</span>} value="body · Geist 14" />
          <LeaderRow name={<span className="text-ui">Summarize 23 unread messages from overnight</span>} value="ui · Geist 13" />
          <LeaderRow name={<span className="text-xs text-muted-foreground">Credentials never go in this box.</span>} value="meta · Geist 12" />
          <LeaderRow name={<span className="label-mono">Needs you</span>} value="label · Mono 11 / .08em" />
          <LeaderRow name={<span className="font-mono text-2xs tabular-nums">12 queued · 2026-11-16</span>} value="data · Mono 11 tabular" />
        </div>
      </Section>

      <Section index={2} title="Controls" note="32px for a mouse; 44px on touch screens. Hover is gated to fine pointers by Tailwind. Focus shows a red ring.">
        <div className="flex flex-col gap-6">
          <div className="flex flex-wrap items-center gap-3"><Button>Approve setup</Button><Button variant="outline">Review</Button><Button variant="secondary">Save draft</Button><Button variant="ghost">Cancel</Button><Button variant="destructive">Stop work</Button><Button variant="link">View all</Button><Button disabled>Disabled</Button><Button disabled><Spinner data-icon="inline-start" />Preparing…</Button><Button size="icon" aria-label="Refresh"><RefreshCw /></Button></div>
          <div className="flex flex-wrap items-center gap-3"><Button size="sm">Small</Button><Button size="sm" variant="outline">Small outline</Button><Button size="lg">Large</Button><Badge>3</Badge><Badge variant="secondary">Draft</Badge><Badge variant="outline">Asleep</Badge><Badge variant="destructive">Failed</Badge><Kbd>Ctrl</Kbd><Kbd>K</Kbd></div>
          <div className="grid max-w-3xl grid-cols-3 gap-4">
            <label className="flex flex-col gap-1.5"><span className="label-mono">Bot name</span><Input defaultValue="Morning inbox brief" /></label>
            <label className="flex flex-col gap-1.5"><span className="label-mono">Ceiling</span><Input aria-invalid defaultValue="-5" /><span className="text-xs text-error-foreground">Enter an amount above $20.00.</span></label>
            <label className="flex flex-col gap-1.5"><span className="label-mono">Model</span><Input disabled defaultValue="Set by the server" /></label>
          </div>
          <ToggleGroup type="single" variant="outline" size="sm" defaultValue="all" aria-label="Filter"><ToggleGroupItem value="all">All</ToggleGroupItem><ToggleGroupItem value="needs">Needs you</ToggleGroupItem><ToggleGroupItem value="working">Working</ToggleGroupItem><ToggleGroupItem value="idle">Idle</ToggleGroupItem></ToggleGroup>
        </div>
      </Section>

      <Section index={3} title="Status" note="Five tones from describeBot. A working bot pulses; its current task shimmers. Both stop under reduced motion.">
        <div className="flex flex-wrap gap-8">{tones.map(tone => <Status key={tone} tone={tone}>{statusFor[tone]}</Status>)}</div>
        <ul className="flex max-w-xl flex-col divide-y divide-border border-y border-border">{(["running", "queued", "waiting", "held", "done", "failed", "cancelled"] as BotTask["state"][]).map(state => <TaskLine key={state} task={task(state, state, { running: "Summarize 23 unread messages", queued: "Draft replies to the two invoices", waiting: "Choose the return date", held: "Download October invoices", done: "Log 14 September invoices", failed: "Daily check: one site blocked the browser", cancelled: "Archive old newsletters" }[state])} />)}</ul>
      </Section>

      <Section index={4} title="Deck parts">
        <div className="grid grid-cols-3 items-start gap-4">
          <RequestCard bot="Vendor invoice tracker" what="Raise the ceiling from $20.00 to $35.00?" why="The supplier portal charges $0.50 per invoice download." action="Review request" onAct={() => toast("Opens the request (sample)")} />
          <RequestCard bot="A deliberately long bot name that keeps going past the card edge" what="Which return date works: 14 or 15 December? The 15th is $112 cheaper but lands after midnight." action="Answer" onAct={() => toast("Opens the question (sample)")} />
          <BotCard bot={fleet[0]} tasks={fleet[0].tasks} onOpen={() => toast("Opens the bot (sample)")} />
        </div>
        <div className="max-w-xl border border-border bg-card">{fleet.map(bot => <BotRow key={bot.id} bot={bot} onClick={() => toast(`Opens ${bot.name} (sample)`)} />)}</div>
        <div className="max-w-2xl"><Composer value={draft} onChange={setDraft} onSubmit={() => { toast.success("Sent (sample)"); setDraft(""); }} label="Task for Morning inbox brief" placeholder="Give Morning inbox brief a task" hint={<><span>Queued behind 2 tasks.</span><span className="flex items-center gap-1"><Kbd>Ctrl</Kbd><Kbd>Enter</Kbd></span></>} /></div>
      </Section>

      <Section index={5} title="Layout" note="Sidebar, dashboard, and a bot pane that opens beside Home. The bot list changes form with the fleet size."><LayoutDemo /></Section>
    </div>
    <Toaster position="top-center" theme={dark ? "dark" : "light"} />
  </MotionConfig>;
}

createRoot(document.getElementById("root")!).render(<StrictMode><Showcase /></StrictMode>);
