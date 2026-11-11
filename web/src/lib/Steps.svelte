<script lang="ts">
  import type { Item } from './events';

  type Step = Extract<Item, { kind: 'step' }>;
  let { steps, active }: { steps: Step[]; active: boolean } = $props();

  let expanded = $state(false);
  let open = $state<Record<string, boolean>>({});
  const show = $derived(active || expanded);

  function brief(args: unknown): string {
    const s = typeof args === 'string' ? args : JSON.stringify(args ?? {});
    return s.length > 80 ? s.slice(0, 80) + '…' : s;
  }
</script>

<div class="rail">
  {#if !active}
    <button class="toggle" aria-expanded={expanded} onclick={() => (expanded = !expanded)}>
      {steps.length} {steps.length === 1 ? 'step' : 'steps'}
      <span aria-hidden="true">{expanded ? '▴' : '▾'}</span>
    </button>
  {/if}
  {#if show}
    <ol>
      {#each steps as s (s.id)}
        <li class:fail={s.done && !s.ok}>
          <span
            class="dot"
            class:pulse={!s.done && active}
            class:ok={s.done && s.ok}
            class:bad={s.done && !s.ok}
            aria-hidden="true"
          ></span>
          <button
            class="step"
            aria-expanded={!!open[s.id]}
            disabled={!s.done}
            onclick={() => (open[s.id] = !open[s.id])}
          >
            <span class="name">{s.name}</span>
            <span class="args">{brief(s.args)}</span>
            <span class="sr">{!s.done ? 'running' : s.ok ? 'ok' : 'failed'}</span>
          </button>
          {#if open[s.id] && s.summary}
            <pre class="summary">{s.summary}</pre>
          {/if}
        </li>
      {/each}
    </ol>
  {/if}
</div>
