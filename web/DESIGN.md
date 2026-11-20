# deck design system

The tokens are in `src/index.css`. The shared parts are in `src/components/deck/parts.tsx`. The showcase is `design.html`. Run `npm run dev` and open `/design.html`. Decision: D102 in `docs/decisions.md`.

## Principles

- Design for the laptop first. Touch screens get 44px targets through `pointer: coarse`.
- Show the state of every bot at a glance. Status is the repeated visual detail.
- Use one accent. Red marks the primary action, focus and things that need the owner.
- Keep it crisp. Use hairlines, not shadows, on dark surfaces.

## Color

- Dark is the default theme. The `dark` class on `<html>` selects it. Light is the second theme.
- Use semantic tokens only. Never write a raw color in a component.
- Surfaces step up: `background`, `surface` (sidebar, panes), `card`, `raised` (inputs), `popover`.
- `brand` is the red for marks, rings and the "Needs you" edge. `primary` is a slightly darker red for button fills, so white text passes 4.5:1.
- Status tones come from `describeBot`: working (`info`), needs you (`warning`), healthy (`success`), failed (`error`), asleep (`faint-foreground`).
- Color is never the only signal. A status always has a word or an icon.
- `faint-foreground` is for icons and decoration only. Text uses `foreground` or `muted-foreground`.

## Type

| Role | Style |
| --- | --- |
| Display | `font-display text-display` (Redaction 40). Empty states and rare large titles only. |
| Title | `text-xl font-medium` (Geist 20) |
| Heading | `text-base font-medium` (Geist 16) |
| Body | `text-sm` (Geist 14) |
| Interface | `text-ui` (Geist 13). Rows, tables, menus. |
| Meta | `text-xs text-muted-foreground` (Geist 12) |
| Label | `label-mono` (Geist Mono 11, upper case, `.08em`) |
| Data | `font-mono text-2xs tabular-nums`. Counts, queues, times, IDs, keys. |

Never use Redaction for data or controls.

## Shape and space

- Surfaces (cards, panes, tables) are square.
- Controls (buttons, inputs, menus) use `rounded-md` (4px).
- Use the Tailwind spacing scale only. Rows are 32px (nav), 56px (bot rows) or table height.
- Separate a surface with one method: a border or a background step. Do not add a shadow on dark surfaces. Overlays use `shadow-overlay`.

## Motion

- Durations: `--duration-fast` 120ms (hover, color), `--duration-base` 180ms (enter, focus), `--duration-slow` 240ms (panes).
- Easing: `--ease-out` for enter and move. `--ease-in-out` for movement on screen. Never `ease-in` on interface motion.
- Animate `opacity` and `transform` only. Never `transition: all`.
- A working bot's dot pulses (`deck-pulse`). Its current task shimmers (`live-tool-shine`). New request cards fade in (`deck-enter`).
- The bot pane slides in 24px from the right and fades. It leaves faster than it enters.
- Every animation stops under `prefers-reduced-motion`. Keyboard actions do not animate.

## Layout

- Sidebar (240px): wordmark, New bot, Home, Conversations, bots with status dots, connection state.
- Home: Needs you, Bots, Recent results, and the composer docked at the bottom.
- Bot pane: opens on the right at 44% of the width, at most 640px. Escape or the close button closes it. Home stays visible.
- Bot list: cards up to 6 bots (`BotCard`), rows up to 15 (`BotRow`), a table grouped by status with a filter above 15.
- Section headers use `SectionHeader`: a red numeral, a mono label, a hairline and a count.

## Parts

| Part | Use |
| --- | --- |
| `StatusDot`, `Status` | A bot or task state. |
| `SectionHeader` | The head of each Home section. |
| `LeaderRow` | A name, a dotted leader and a value. |
| `BotRow`, `BotCard` | A bot in a list or a grid. |
| `TaskLine` | One task with its state icon and word. |
| `RequestCard` | Something the owner must answer. |
| `Composer` | The message box. It shows the destination above the text. |
| `Kbd` | A key hint. |

Use the shadcn components in `src/components/ui` for everything else. Add new ones with `npx shadcn@latest add`.
