import { fileUrl } from '@/lib/api';
import type { Page } from '@/lib/events';
import { cn } from '@/lib/utils';
import { GlobeIcon, LockIcon } from 'lucide-react';
import { AnimatePresence, motion } from 'motion/react';
import { useState } from 'react';
import { Drawer } from 'vaul';

const spring = { type: 'spring', stiffness: 380, damping: 34 } as const;

function host(url: string) {
  try {
    return new URL(url).host || url;
  } catch {
    return url;
  }
}

function LiveDot({ on }: { on: boolean }) {
  return (
    <span className="relative flex size-2">
      {on && (
        <span className="absolute inline-flex size-full animate-ping rounded-full bg-emerald-500/70 motion-reduce:animate-none" />
      )}
      <span className={cn('relative inline-flex size-2 rounded-full', on ? 'bg-emerald-500' : 'bg-muted-foreground/40')} />
    </span>
  );
}

/** The page the bot is on: an address bar and the latest capture. */
function Screen({ page, running }: { page: Page; running: boolean }) {
  const secure = page.url.startsWith('https:');
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="bg-muted/60 flex items-center gap-2 rounded-full px-3 py-1.5 text-xs">
        {secure ? (
          <LockIcon className="text-muted-foreground size-3.5 shrink-0" aria-label="Secure" />
        ) : (
          <GlobeIcon className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
        )}
        <span className="min-w-0 flex-1 truncate" title={page.url}>
          {page.url}
        </span>
        <LiveDot on={running} />
      </div>
      {page.title && <p className="truncate px-1 text-sm font-medium">{page.title}</p>}
      <div className="bg-muted/40 relative aspect-[16/10] w-full shrink-0 overflow-hidden rounded-xl border">
        <AnimatePresence initial={false}>
          {page.fileId !== undefined ? (
            <motion.img
              key={page.fileId}
              src={fileUrl(page.fileId)}
              alt={`Capture of ${page.title || host(page.url)}`}
              initial={{ opacity: 0, scale: 1.01 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.25 }}
              className="absolute inset-0 size-full object-contain"
            />
          ) : (
            <motion.div
              key="none"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="text-muted-foreground absolute inset-0 flex items-center justify-center p-6 text-center text-sm"
            >
              <span className={cn(running && 'shimmer')}>No capture of this page yet</span>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

/** Desktop: a side panel. It slides in when the chat has a page. */
export function LivePanel({ page, running }: { page?: Page; running: boolean }) {
  return (
    <AnimatePresence initial={false}>
      {page && (
        <motion.aside
          aria-label="Live view"
          initial={{ width: 0, opacity: 0 }}
          animate={{ width: 420, opacity: 1 }}
          exit={{ width: 0, opacity: 0 }}
          transition={spring}
          className="hidden shrink-0 overflow-hidden border-l lg:flex"
        >
          <div className="flex w-[420px] flex-col gap-3 p-4">
            <div className="flex items-center gap-2 text-sm font-medium">
              <LiveDot on={running} />
              {running ? 'Live' : 'Last page'}
            </div>
            <Screen page={page} running={running} />
          </div>
        </motion.aside>
      )}
    </AnimatePresence>
  );
}

/** Phone: a pill under the header. A tap opens the page in a bottom sheet. */
export function LivePill({ page, running }: { page?: Page; running: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <AnimatePresence>
        {page && (
          <motion.button
            type="button"
            onClick={() => setOpen(true)}
            initial={{ y: -12, opacity: 0, scale: 0.95 }}
            animate={{ y: 0, opacity: 1, scale: 1 }}
            exit={{ y: -12, opacity: 0, scale: 0.95 }}
            whileTap={{ scale: 0.96 }}
            transition={spring}
            className="bg-background/80 absolute top-2 left-1/2 z-10 flex max-w-[80%] -translate-x-1/2 items-center gap-2 rounded-full border px-3 py-1.5 text-xs shadow-sm backdrop-blur lg:hidden"
            aria-label={`Live view: ${host(page.url)}`}
          >
            <LiveDot on={running} />
            <span className="font-medium">{running ? 'Live' : 'Last page'}</span>
            <span className="text-muted-foreground min-w-0 truncate">{host(page.url)}</span>
          </motion.button>
        )}
      </AnimatePresence>
      <Drawer.Root open={open && !!page} onOpenChange={setOpen}>
        <Drawer.Portal>
          <Drawer.Overlay className="fixed inset-0 z-40 bg-black/40" />
          <Drawer.Content
            aria-describedby={undefined}
            className="bg-background fixed inset-x-0 bottom-0 z-50 flex max-h-[92dvh] flex-col rounded-t-2xl border-t px-4 pt-3 pb-[max(1rem,env(safe-area-inset-bottom))] outline-none"
          >
            <div className="bg-muted-foreground/30 mx-auto mb-3 h-1.5 w-10 shrink-0 rounded-full" aria-hidden />
            <Drawer.Title className="mb-3 flex items-center gap-2 text-sm font-medium">
              <LiveDot on={running} />
              {running ? 'Live' : 'Last page'}
            </Drawer.Title>
            {page && <Screen page={page} running={running} />}
          </Drawer.Content>
        </Drawer.Portal>
      </Drawer.Root>
    </>
  );
}
