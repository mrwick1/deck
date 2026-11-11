<script lang="ts">
  import { fileUrl } from './api';
  import type { Item } from './events';
  import Markdown from './Markdown.svelte';

  let { item, live = false }: { item: Exclude<Item, { kind: 'step' }>; live?: boolean } = $props();
</script>

{#if item.kind === 'user'}
  <div class="bubble user">{item.text}</div>
{:else if item.kind === 'assistant'}
  <div class="bubble assistant" aria-live={live ? 'polite' : undefined}>
    <Markdown text={item.text} />
  </div>
{:else if item.mime.startsWith('image/')}
  <a class="img" href={fileUrl(item.fileId)} target="_blank" rel="noopener">
    <img src={fileUrl(item.fileId)} alt={item.name} loading="lazy" />
  </a>
{:else}
  <a class="chip" href={fileUrl(item.fileId)} download={item.name}>
    <span aria-hidden="true">📎</span> {item.name}
  </a>
{/if}
