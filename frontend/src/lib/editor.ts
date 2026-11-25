// SPDX-License-Identifier: AGPL-3.0-or-later
import { yaml } from '@codemirror/lang-yaml';
import { EditorState, Prec, type Extension } from '@codemirror/state';
import { oneDark } from '@codemirror/theme-one-dark';
import { EditorView } from '@codemirror/view';
import { basicSetup } from 'codemirror';
import { isDark } from './theme.svelte';

export function extensionsFor(path: string, readOnly = false): Extension[] {
	const dark = isDark();
	return [
		basicSetup,
		/\.ya?ml$/.test(path) ? yaml() : [],
		dark ? oneDark : [],
		// Above the one-dark theme, so its own grey background gives way to Swarmer's colours.
		Prec.highest(
			EditorView.theme({
			'&': { fontSize: '13px', height: '100%', backgroundColor: 'var(--panel)' },
			'.cm-scroller': { fontFamily: 'var(--mono)' },
			'.cm-gutters': {
				backgroundColor: 'var(--panel-alt)',
				borderRight: '1px solid var(--border)',
				color: 'var(--muted)'
			},
			'.cm-activeLine, .cm-activeLineGutter': { backgroundColor: 'var(--accent-soft)' }
		})
		),
		readOnly ? [EditorState.readOnly.of(true), EditorView.editable.of(false)] : []
	];
}
