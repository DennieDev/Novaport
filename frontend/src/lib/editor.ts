// SPDX-License-Identifier: AGPL-3.0-or-later
import { yaml } from '@codemirror/lang-yaml';
import { EditorState, type Extension } from '@codemirror/state';
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
		EditorView.theme({
			'&': { fontSize: '13px', height: '100%' },
			'.cm-scroller': { fontFamily: 'var(--mono)' }
		}),
		readOnly ? [EditorState.readOnly.of(true), EditorView.editable.of(false)] : []
	];
}
