import * as api from './api';
import {
  applyEvent,
  initialState,
  parseMessages,
  subscribe,
  visibleItems,
  type DeckEvent,
  type Item,
  type ViewState,
} from './events';

/** The live state of one open chat. Call `close()` when the chat is left. */
export class Session {
  history = $state<Item[]>([]);
  view = $state<ViewState>(initialState());
  connected = $state(true);
  error = $state('');
  runError = $state('');
  loaded = $state(false);

  items = $derived(visibleItems(this.history, this.view.run));
  running = $derived(this.view.run?.status === 'running');

  private stop: () => void;
  private refetchTimer: ReturnType<typeof setTimeout> | undefined;

  constructor(readonly chatId: number) {
    this.stop = subscribe(
      (after) => api.eventsUrl(chatId, after),
      () => this.view.lastId,
      (ev) => this.onEvent(ev),
      (ok) => (this.connected = ok),
    );
    this.reload().then(() => (this.loaded = true));
  }

  close() {
    this.stop();
    clearTimeout(this.refetchTimer);
  }

  private async reload() {
    try {
      this.history = parseMessages(await api.listMessages(this.chatId));
      this.error = '';
    } catch (e) {
      this.error = (e as Error).message;
    }
  }

  private onEvent(ev: DeckEvent) {
    this.view = applyEvent(this.view, ev);
    if (ev.kind === 'run_finished') {
      this.runError = ev.data.status === 'failed' ? (ev.data.error ?? 'The run failed') : '';
      // Replay can finish many runs at once. One reload covers them.
      clearTimeout(this.refetchTimer);
      this.refetchTimer = setTimeout(async () => {
        await this.reload();
        if (this.view.run && this.view.run.status !== 'running') {
          this.view = { ...this.view, run: null };
        }
      }, 150);
    }
  }

  async send(text: string): Promise<boolean> {
    this.runError = '';
    try {
      const { run_id } = await api.sendMessage(this.chatId, text);
      this.error = '';
      this.history = [...this.history, { kind: 'user', text }];
      this.view = { ...this.view, run: { runId: run_id, items: [], status: 'running' } };
      return true;
    } catch (e) {
      const err = e as api.ApiError;
      this.error = err.status === 409 ? 'A run is already active' : err.message;
      return false;
    }
  }

  async stopRun() {
    const id = this.view.run?.runId;
    if (id === undefined) return;
    try {
      await api.cancelRun(id);
    } catch (e) {
      this.error = (e as Error).message;
    }
  }
}
