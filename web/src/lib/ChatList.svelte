<script lang="ts">
  import type { Chat } from './api';

  let {
    chats,
    selected,
    onselect,
    onnew,
  }: {
    chats: Chat[];
    selected: number | null;
    onselect: (id: number) => void;
    onnew: () => void;
  } = $props();
</script>

<nav aria-label="Chats">
  <button class="primary new" onclick={onnew}>New chat</button>
  <ul>
    {#each chats as c (c.id)}
      <li>
        <button
          class="chat"
          aria-current={c.id === selected ? 'page' : undefined}
          onclick={() => onselect(c.id)}>{c.title}</button
        >
      </li>
    {/each}
  </ul>
</nav>
