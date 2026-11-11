<script lang="ts">
  import { renderMarkdown } from './markdown';

  let { text }: { text: string } = $props();
  const html = $derived(renderMarkdown(text));
</script>

<!-- renderMarkdown sanitizes with DOMPurify. -->
<div class="md">{@html html}</div>
