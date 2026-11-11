<script lang="ts">
  import './app.css';
  import * as api from './lib/api';
  import ChatList from './lib/ChatList.svelte';
  import Composer from './lib/Composer.svelte';
  import MessageList from './lib/MessageList.svelte';
  import { Session } from './lib/session.svelte';

  let chats = $state<api.Chat[]>([]);
  let chatId = $state<number | null>(null);
  let session = $state<Session | null>(null);
  let drawer = $state(false);
  let error = $state('');

  const title = $derived(chats.find((c) => c.id === chatId)?.title ?? 'deck');

  async function refresh() {
    try {
      chats = (await api.listChats()).sort((a, b) => b.id - a.id);
      error = '';
    } catch (e) {
      error = (e as Error).message;
    }
  }

  $effect(() => {
    refresh();
  });

  $effect(() => {
    const id = chatId;
    if (id === null) return;
    const s = new Session(id);
    session = s;
    return () => {
      s.close();
      session = null;
    };
  });

  function select(id: number) {
    chatId = id;
    drawer = false;
  }

  function newChat() {
    chatId = null;
    drawer = false;
  }

  async function send(text: string): Promise<boolean> {
    if (chatId === null) {
      try {
        const chat = await api.createChat(text.slice(0, 60));
        chats = [chat, ...chats];
        chatId = chat.id;
      } catch (e) {
        error = (e as Error).message;
        return false;
      }
      // The session starts in an effect. Wait for it.
      while (!session || session.chatId !== chatId) await new Promise((r) => setTimeout(r, 10));
    }
    return session!.send(text);
  }

  const banner = $derived(error || session?.error || '');
</script>

<div class="app">
  <aside class:open={drawer}>
    <ChatList {chats} selected={chatId} onselect={select} onnew={newChat} />
  </aside>
  {#if drawer}
    <button class="scrim" aria-label="Close the chat list" onclick={() => (drawer = false)}
    ></button>
  {/if}
  <main>
    <header>
      <button class="menu" aria-label="Chats" aria-expanded={drawer} onclick={() => (drawer = !drawer)}>☰</button>
      <h1>{title}</h1>
      {#if session && !session.connected}<span class="reconnect" role="status">Reconnecting…</span>{/if}
    </header>
    {#if banner}
      <div class="banner" role="alert">{banner}</div>
    {/if}
    <MessageList items={session?.items ?? []} running={session?.running ?? false} />
    {#if session?.runError}
      <div class="banner" role="alert">Run failed: {session.runError}</div>
    {/if}
    <Composer running={session?.running ?? false} onsend={send} onstop={() => session?.stopRun()} />
  </main>
</div>
