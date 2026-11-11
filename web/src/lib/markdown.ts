import DOMPurify from 'dompurify';
import { marked } from 'marked';

/** Render model text as HTML. The output is sanitized: model text is untrusted. */
export function renderMarkdown(text: string): string {
  return DOMPurify.sanitize(marked.parse(text, { async: false, breaks: true }) as string, {
    USE_PROFILES: { html: true },
  });
}
