import { Thread } from '@/components/assistant-ui/elements/thread.aui';
import { ThreadList } from '@/components/assistant-ui/elements/thread-list.aui';
import { Button } from '@/components/ui/button';
import { TooltipProvider } from '@/components/ui/tooltip';
import { useDeck } from '@/lib/useDeck';
import { AssistantRuntimeProvider } from '@assistant-ui/react';
import { MenuIcon } from 'lucide-react';
import { AnimatePresence, motion } from 'motion/react';
import { useState } from 'react';
import { Toaster } from 'sonner';
import { Drawer } from 'vaul';

export default function App() {
  const { runtime, title, connected } = useDeck();
  const [drawer, setDrawer] = useState(false);

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <TooltipProvider>
        <div className="bg-background text-foreground flex h-dvh">
          <aside className="bg-sidebar hidden w-64 shrink-0 flex-col border-r p-2 md:flex">
            <ThreadList />
          </aside>
          <Drawer.Root direction="left" open={drawer} onOpenChange={setDrawer}>
            <Drawer.Portal>
              <Drawer.Overlay className="fixed inset-0 z-40 bg-black/40" />
              <Drawer.Content
                aria-describedby={undefined}
                className="bg-sidebar fixed inset-y-0 left-0 z-50 flex w-72 max-w-[85vw] flex-col p-2 pt-[max(0.5rem,env(safe-area-inset-top))] outline-none"
                // Close the drawer when a chat is picked.
                onClickCapture={(e) => {
                  if ((e.target as HTMLElement).closest('[data-slot^="aui_thread-list-"]:is(button,a), [data-slot="aui_thread-list-item-trigger"]'))
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
            <div className="min-h-0 flex-1 pb-[env(safe-area-inset-bottom)]">
              <Thread />
            </div>
          </main>
        </div>
        <Toaster position="top-center" richColors closeButton />
      </TooltipProvider>
    </AssistantRuntimeProvider>
  );
}
