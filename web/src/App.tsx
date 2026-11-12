import { Thread } from "@/components/assistant-ui/elements/thread.aui";
import { ThreadList } from "@/components/assistant-ui/elements/thread-list.aui";
import {
  BotBar,
  BotHome,
  BotScreen,
  InboxPanel,
  SendOptions,
} from "@/components/BotBar";
import { ComposerDock, DockContext } from "@/components/ComposerDock";
import { LivePanel, LivePill } from "@/components/LiveView";
import { StepGroup, StepRow } from "@/components/StepRow";
import { Button } from "@/components/ui/button";
import { Welcome } from "@/components/Welcome";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useBot } from "@/lib/bot";
import { useDeck } from "@/lib/useDeck";
import { AssistantRuntimeProvider } from "@assistant-ui/react";
import { MenuIcon } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useState } from "react";
import { Toaster } from "sonner";
import { Drawer } from "vaul";

const THREAD = {
  Welcome,
  ToolFallback: StepRow,
  ToolGroup: StepGroup,
  ComposerTop: ComposerDock,
};

export default function App() {
  const { status: bot, inbox, refreshInbox } = useBot();
  const [mode, setMode] = useState<"steer" | "queue">("steer");
  const [priority, setPriority] = useState(1);
  const {
    runtime,
    chatId,
    openHome,
    openChat,
    title,
    page,
    browserActive,
    connected,
    dock,
  } = useDeck({
    chatId: bot?.chat_id ?? null,
    mode,
    priority,
    home: true,
  });
  const running = dock.running;
  const [drawer, setDrawer] = useState(false);
  const [screen, setScreen] = useState(false);
  const [inboxOpen, setInboxOpen] = useState(false);
  const inChat = bot !== null && chatId === bot.chat_id;
  // The screen shows while the owner asks for it, or when a task waits for the owner.
  const showScreen = inChat && bot.computer === "awake" && (screen || browserActive || !!bot.waiting);

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <DockContext value={dock}>
        <TooltipProvider>
          <div className="bg-background text-foreground flex h-dvh">
            <aside className="bg-sidebar hidden w-64 shrink-0 flex-col border-r p-2 md:flex">
              <ThreadList />
            </aside>
            <Drawer.Root
              direction="left"
              open={drawer}
              onOpenChange={setDrawer}
            >
              <Drawer.Portal>
                <Drawer.Overlay className="fixed inset-0 z-40 bg-black/40" />
                <Drawer.Content
                  aria-describedby={undefined}
                  className="bg-sidebar fixed inset-y-0 left-0 z-50 flex w-72 max-w-[85vw] flex-col p-2 pt-[max(0.5rem,env(safe-area-inset-top))] outline-none"
                  // Close the drawer when a chat is picked.
                  onClickCapture={(e) => {
                    if (
                      (e.target as HTMLElement).closest(
                        '[data-slot^="aui_thread-list-"]:is(button,a), [data-slot="aui_thread-list-item-trigger"]',
                      )
                    )
                      setDrawer(false);
                  }}
                >
                  <Drawer.Title className="sr-only">Chats</Drawer.Title>
                  <ThreadList />
                </Drawer.Content>
              </Drawer.Portal>
            </Drawer.Root>
            <main className="flex min-w-0 flex-1 flex-col">
              <header className="flex h-12 shrink-0 items-center gap-2 border-b px-2 pt-[env(safe-area-inset-top)] md:px-4">
                <Button
                  variant="ghost"
                  size="icon"
                  className="md:hidden"
                  aria-label="Chats"
                  onClick={() => setDrawer(true)}
                >
                  <MenuIcon />
                </Button>
                <AnimatePresence mode="wait" initial={false}>
                  <motion.h1
                    key={title}
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -6 }}
                    transition={{ duration: 0.15 }}
                    className="min-w-0 flex-1 truncate text-sm font-medium"
                  >
                    {title}
                  </motion.h1>
                </AnimatePresence>
                <AnimatePresence>
                  {!connected && (
                    <motion.span
                      role="status"
                      initial={{ opacity: 0, scale: 0.9 }}
                      animate={{ opacity: 1, scale: 1 }}
                      exit={{ opacity: 0, scale: 0.9 }}
                      className="text-muted-foreground bg-muted rounded-full px-2 py-0.5 text-xs"
                    >
                      Reconnecting…
                    </motion.span>
                  )}
                </AnimatePresence>
              </header>
              {bot && chatId !== null && (
                <BotBar
                  status={bot}
                  inboxCount={inbox.length}
                  screenOpen={showScreen}
                  onScreen={() => setScreen((v) => !v)}
                  onInbox={() => setInboxOpen((v) => !v)}
                  onHome={openHome}
                />
              )}
              {bot && inboxOpen && (
                <InboxPanel
                  items={inbox}
                  refresh={refreshInbox}
                  onClose={() => setInboxOpen(false)}
                />
              )}
              {showScreen && <BotScreen />}
              {bot && chatId === null && (
                <BotHome
                  status={bot}
                  inboxCount={inbox.length}
                  onOpen={() => openChat(bot.chat_id)}
                />
              )}
              {(!bot || chatId !== null) && (
                <div className="relative min-h-0 flex-1 pb-[env(safe-area-inset-bottom)]">
                  <LivePill page={page} running={running} />
                  <Thread components={THREAD} />
                </div>
              )}
              {inChat && (
                <SendOptions
                  mode={mode}
                  priority={priority}
                  setMode={setMode}
                  setPriority={setPriority}
                  working={!!bot.working}
                />
              )}
            </main>
            <LivePanel page={page} running={running} />
          </div>
          <Toaster position="top-center" richColors closeButton />
        </TooltipProvider>
      </DockContext>
    </AssistantRuntimeProvider>
  );
}
