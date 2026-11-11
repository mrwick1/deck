<script lang="ts">
  let {
    running,
    onsend,
    onstop,
  }: { running: boolean; onsend: (text: string) => Promise<boolean>; onstop: () => void } =
    $props();

  let text = $state('');
  let field: HTMLTextAreaElement;
  const touch = typeof matchMedia === 'function' && matchMedia('(pointer: coarse)').matches;

  async function submit() {
    const t = text.trim();
    if (!t || running) return;
    if (await onsend(t)) {
      text = '';
      field.style.height = '';
    }
  }

  function keydown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey && !touch && !e.isComposing) {
      e.preventDefault();
      submit();
    }
  }

  function grow() {
    field.style.height = 'auto';
    field.style.height = Math.min(field.scrollHeight, 200) + 'px';
  }
</script>

<form
  class="composer"
  onsubmit={(e) => {
    e.preventDefault();
    submit();
  }}
>
  <textarea
    bind:this={field}
    bind:value={text}
    oninput={grow}
    onkeydown={keydown}
    rows="1"
    placeholder={running ? 'deck is working…' : 'Message deck'}
    aria-label="Message"
    disabled={running}
  ></textarea>
  {#if running}
    <button type="button" class="danger" onclick={onstop} aria-label="Stop the run">Stop</button>
  {:else}
    <button type="submit" class="primary" aria-label="Send" disabled={!text.trim()}>Send</button>
  {/if}
</form>
