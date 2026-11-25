// SPDX-License-Identifier: AGPL-3.0-or-later
export type Theme = 'system' | 'light' | 'dark';

// Also read by the script in app.html, which applies the choice before the page paints.
const KEY = 'swarmer-theme';

function stored(): Theme {
	try {
		const value = localStorage.getItem(KEY);
		return value === 'light' || value === 'dark' ? value : 'system';
	} catch {
		return 'system';
	}
}

export const theme = $state({ current: stored() });

export function setTheme(value: Theme) {
	theme.current = value;
	try {
		if (value === 'system') localStorage.removeItem(KEY);
		else localStorage.setItem(KEY, value);
	} catch {
		// Private mode or storage turned off: the choice lasts until the page reloads.
	}
	if (value === 'system') delete document.documentElement.dataset.theme;
	else document.documentElement.dataset.theme = value;
}

export function isDark(): boolean {
	if (theme.current !== 'system') return theme.current === 'dark';
	return window.matchMedia('(prefers-color-scheme: dark)').matches;
}
