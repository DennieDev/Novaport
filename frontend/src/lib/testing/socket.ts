// SPDX-License-Identifier: AGPL-3.0-or-later
/** A WebSocket stand-in the tests open, feed, and drop by hand. */
export class FakeSocket {
	static all: FakeSocket[] = [];

	onopen: (() => void) | null = null;
	onmessage: ((e: { data: string }) => void) | null = null;
	onclose: (() => void) | null = null;
	closed = false;

	constructor(readonly url: string) {
		FakeSocket.all.push(this);
	}

	static get last(): FakeSocket {
		return FakeSocket.all[FakeSocket.all.length - 1];
	}

	open() {
		this.onopen?.();
	}

	send(change: object) {
		this.onmessage?.({ data: JSON.stringify(change) });
	}

	drop() {
		this.onclose?.();
	}

	close() {
		this.closed = true;
		this.onclose?.();
	}
}
