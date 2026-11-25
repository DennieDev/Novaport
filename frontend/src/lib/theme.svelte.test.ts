// SPDX-License-Identifier: AGPL-3.0-or-later
import { afterEach, expect, it } from 'vitest';
import { setTheme, theme } from './theme.svelte';

afterEach(() => setTheme('system'));

it('sets and remembers an explicit theme', () => {
	setTheme('dark');
	expect(theme.current).toBe('dark');
	expect(document.documentElement.dataset.theme).toBe('dark');
	expect(localStorage.getItem('swarmer-theme')).toBe('dark');
});

it('goes back to following the system', () => {
	setTheme('light');
	setTheme('system');
	expect(document.documentElement.dataset.theme).toBeUndefined();
	expect(localStorage.getItem('swarmer-theme')).toBeNull();
});
