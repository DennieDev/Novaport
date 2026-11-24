// SPDX-License-Identifier: AGPL-3.0-or-later
import { expect, it } from 'vitest';
import { bytes, degraded, replicas, shortId } from './format';

it('formats bytes', () => {
	expect(bytes(null)).toBe('–');
	expect(bytes(512)).toBe('512 B');
	expect(bytes(1536)).toBe('1.5 KiB');
	expect(bytes(20 * 1024 ** 3)).toBe('20 GiB');
});

it('shortens ids', () => {
	expect(shortId('0123456789abcdef')).toBe('0123456789ab');
	expect(shortId(null)).toBe('–');
});

it('shows replicas for replicated and global services', () => {
	expect(replicas({ replicas_running: 2, replicas_desired: 3 })).toBe('2/3');
	expect(replicas({ replicas_running: null, replicas_desired: 3 })).toBe('0/3');
	expect(replicas({ replicas_running: 4, replicas_desired: null })).toBe('4');
	expect(replicas({ replicas_running: null, replicas_desired: null })).toBe('–');
});

it('flags services short of replicas or with a stuck update', () => {
	const base = { replicas_running: 3, replicas_desired: 3, update_status: null };
	expect(degraded(base)).toBe(false);
	expect(degraded({ ...base, replicas_running: 2 })).toBe(true);
	expect(degraded({ ...base, update_status: { state: 'paused' } })).toBe(true);
	expect(degraded({ ...base, update_status: { state: 'completed' } })).toBe(false);
});
