<script lang="ts">
  import { tick } from 'svelte';
  import type { Item } from './events';
  import Message from './Message.svelte';
  import Steps from './Steps.svelte';

  let { items, running }: { items: Item[]; running: boolean } = $props();

  type Step = Extract<Item, { kind: 'step' }>;
  type Block =
    | { t: 'steps'; steps: Step[] }
    | { t: 'item'; item: Exclude<Item, { kind: 'step' }> };

  // Consecutive steps share one rail.
  const blocks = $derived.by(() => {
    const out: Block[] = [];
    for (const item of items) {
      const last = out[out.length - 1];
      if (item.kind !== 'step') out.push({ t: 'item', item });
      else if (last?.t === 'steps') last.steps.push(item);
      else out.push({ t: 'steps', steps: [item] });
    }
    return out;
  });

  let box: HTMLElement;
  let stick = true;
  const onScroll = () => (stick = box.scrollHeight - box.scrollTop - box.clientHeight < 80);

  $effect(() => {
    items; // track
    if (stick) tick().then(() => box.scrollTo({ top: box.scrollHeight }));
  });
</script>

<section class="messages" bind:this={box} onscroll={onScroll} aria-label="Messages">
  {#each blocks as b, i}
    {#if b.t === 'steps'}
      <Steps steps={b.steps} active={running && i === blocks.length - 1} />
    {:else}
      <Message item={b.item} live={running && i === blocks.length - 1} />
    {/if}
  {:else}
    <p class="empty">Ask deck to do something.</p>
  {/each}
</section>
